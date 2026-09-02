from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..config import Settings
from ..privacy import secure_private_directory, secure_private_file
from . import CATALOGUE_SCHEMA_VERSION
from .store import CatalogueStore

MANIFEST_NAME = "trackprompt-backup-manifest.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _archive_files(settings: Settings) -> list[tuple[Path, Path]]:
    if not settings.archive_dir.exists():
        return []
    return [
        (path, Path("archive") / path.relative_to(settings.archive_dir))
        for path in settings.archive_dir.rglob("*")
        if path.is_file()
    ]


def create_backup(settings: Settings, destination: Path, *, dry_run: bool = False) -> dict[str, Any]:
    store = CatalogueStore(settings)
    destination = destination.resolve()
    if destination.exists():
        raise FileExistsError("Backup destination already exists; no files were overwritten.")
    sources = _archive_files(settings)
    estimated = settings.database_path.stat().st_size + sum(path.stat().st_size for path, _ in sources)
    parent = destination.parent
    parent.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(parent).free < estimated + settings.minimum_free_disk_bytes:
        raise OSError("Backup destination cannot preserve the configured free-space reserve.")
    if dry_run:
        return {
            "dryRun": True,
            "destination": str(destination),
            "estimatedBytes": estimated,
            "fileCount": len(sources) + 1,
        }
    destination.mkdir(parents=False)
    secure_private_directory(destination)
    database_copy = destination / "trackprompt.sqlite3"
    source_connection = store.connect()
    try:
        target_connection = sqlite3.connect(database_copy)
        try:
            source_connection.backup(target_connection)
        finally:
            target_connection.close()
    finally:
        source_connection.close()
    secure_private_file(database_copy)
    entries: list[dict[str, Any]] = []
    for source, relative in [(database_copy, Path("trackprompt.sqlite3")), *sources]:
        target = destination / relative
        if source != database_copy:
            target.parent.mkdir(parents=True, exist_ok=True)
            secure_private_directory(target.parent)
            shutil.copyfile(source, target)
            secure_private_file(target)
        entries.append(
            {
                "path": relative.as_posix(),
                "byteSize": target.stat().st_size,
                "sha256": _sha256(target),
            }
        )
    manifest = {
        "schemaVersion": "1.0.0",
        "catalogueSchemaVersion": CATALOGUE_SCHEMA_VERSION,
        "createdAt": datetime.now(UTC).isoformat(),
        "includesModels": False,
        "includesTemporaryJobs": False,
        "files": entries,
    }
    manifest_path = destination / MANIFEST_NAME
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    secure_private_file(manifest_path)
    result = verify_backup(destination)
    if not result["valid"]:
        raise OSError("The newly created backup failed checksum verification.")
    return {"dryRun": False, "destination": str(destination), **result}


def verify_backup(destination: Path) -> dict[str, Any]:
    destination = destination.resolve()
    manifest_path = destination / MANIFEST_NAME
    if not manifest_path.is_file() or manifest_path.stat().st_size > 20_000_000:
        return {"valid": False, "fileCount": 0, "verifiedBytes": 0, "errors": ["manifest_missing"]}
    parsed = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = parsed.get("files") if isinstance(parsed, dict) else None
    if not isinstance(entries, list):
        return {"valid": False, "fileCount": 0, "verifiedBytes": 0, "errors": ["manifest_invalid"]}
    errors: list[str] = []
    verified_bytes = 0
    expected_files = {MANIFEST_NAME}
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            errors.append("entry_invalid")
            continue
        relative = Path(entry["path"])
        if relative.is_absolute() or ".." in relative.parts:
            errors.append("entry_path_invalid")
            continue
        expected_files.add(relative.as_posix())
        path = (destination / relative).resolve()
        if destination not in path.parents and path != destination:
            errors.append("entry_path_escape")
            continue
        if not path.is_file():
            errors.append(f"missing:{relative.as_posix()}")
            continue
        size = path.stat().st_size
        verified_bytes += size
        if size != int(entry.get("byteSize", -1)) or _sha256(path) != entry.get("sha256"):
            errors.append(f"checksum:{relative.as_posix()}")
    database = destination / "trackprompt.sqlite3"
    if database.is_file():
        try:
            # The snapshot retains WAL mode in its header. Open it immutable so
            # verification itself cannot create unmanifested -wal/-shm files.
            database_uri = f"{database.as_uri()}?mode=ro&immutable=1"
            with sqlite3.connect(database_uri, uri=True) as connection:
                integrity = connection.execute("PRAGMA integrity_check").fetchone()
            if integrity is None or str(integrity[0]).lower() != "ok":
                errors.append("database_integrity")
        except sqlite3.Error:
            errors.append("database_unreadable")
    actual_files = {
        path.relative_to(destination).as_posix()
        for path in destination.rglob("*")
        if path.is_file()
    }
    for unexpected in sorted(actual_files - expected_files):
        errors.append(f"unexpected:{unexpected}")
    return {
        "valid": not errors,
        "fileCount": len(entries),
        "verifiedBytes": verified_bytes,
        "errors": errors,
    }


def restore_backup(
    source: Path,
    destination_data_dir: Path,
    *,
    dry_run: bool = False,
    settings: Settings | None = None,
) -> dict[str, Any]:
    source = source.resolve()
    destination_data_dir = destination_data_dir.resolve()
    verification = verify_backup(source)
    if not verification["valid"]:
        raise ValueError("Backup verification failed; restore was not started.")
    if destination_data_dir.exists() and any(destination_data_dir.iterdir()):
        raise FileExistsError("Restore destination is not empty; no files were overwritten.")
    manifest = json.loads((source / MANIFEST_NAME).read_text(encoding="utf-8"))
    entries = manifest["files"]
    total = sum(int(entry["byteSize"]) for entry in entries)
    destination_data_dir.parent.mkdir(parents=True, exist_ok=True)
    runtime_settings = settings or Settings.from_env()
    if shutil.disk_usage(destination_data_dir.parent).free < (
        total + runtime_settings.minimum_free_disk_bytes
    ):
        raise OSError("Restore destination cannot preserve the configured free-space reserve.")
    if dry_run:
        return {"dryRun": True, "destination": str(destination_data_dir), **verification}
    destination_data_dir.mkdir(parents=True, exist_ok=True)
    secure_private_directory(destination_data_dir)
    for entry in entries:
        relative = Path(str(entry["path"]))
        source_file = source / relative
        target_relative = Path("trackprompt.sqlite3") if relative == Path("trackprompt.sqlite3") else relative
        target = destination_data_dir / target_relative
        target.parent.mkdir(parents=True, exist_ok=True)
        secure_private_directory(target.parent)
        shutil.copyfile(source_file, target)
        secure_private_file(target)
    restored_settings = Settings(
        **{
            **settings_to_values(runtime_settings),
            "data_dir": destination_data_dir,
            "model_cache_dir": destination_data_dir / "models",
        }
    )
    restored = CatalogueStore(restored_settings)
    audit_rows = 0
    with restored.connect() as connection:
        audit_rows = int(connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0])
    return {
        "dryRun": False,
        "destination": str(destination_data_dir),
        "auditEventCount": audit_rows,
        **verification,
    }


def settings_to_values(settings: Settings) -> dict[str, Any]:
    values = {
        field_name: getattr(settings, field_name)
        for field_name in settings.__dataclass_fields__
    }
    return values


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create, verify, or restore a private TrackPrompt catalogue backup.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create")
    create.add_argument("destination", type=Path)
    create.add_argument("--dry-run", action="store_true")
    verify = subparsers.add_parser("verify")
    verify.add_argument("source", type=Path)
    restore = subparsers.add_parser("restore")
    restore.add_argument("source", type=Path)
    restore.add_argument("destination", type=Path)
    restore.add_argument("--dry-run", action="store_true")
    return parser


def main(arguments: list[str] | None = None) -> int:
    args = _parser().parse_args(arguments)
    settings = Settings.from_env()
    if args.command == "create":
        result = create_backup(settings, args.destination, dry_run=args.dry_run)
    elif args.command == "verify":
        result = verify_backup(args.source)
    else:
        result = restore_backup(
            args.source,
            args.destination,
            dry_run=args.dry_run,
            settings=Settings.from_env(),
        )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("valid", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
