from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MANIFEST_NAME = "recovery-backup-manifest.json"
BUFFER_BYTES = 4 * 1024 * 1024


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(BUFFER_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError:
        pass


def _private_file(path: Path) -> None:
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _copy_database(source: Path, destination: Path) -> None:
    _private_directory(destination.parent)
    source_uri = f"{source.as_uri()}?mode=ro"
    with sqlite3.connect(source_uri, uri=True) as source_connection:
        with sqlite3.connect(destination) as destination_connection:
            source_connection.backup(destination_connection)
    _private_file(destination)


def _database_integrity(path: Path) -> bool:
    uri = f"{path.as_uri()}?mode=ro&immutable=1"
    with sqlite3.connect(uri, uri=True) as connection:
        row = connection.execute("PRAGMA integrity_check").fetchone()
    return row is not None and str(row[0]).casefold() == "ok"


def _copy_tree(
    source: Path,
    destination: Path,
    *,
    manifest_prefix: Path,
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    if not source.exists():
        return entries
    for source_file in sorted(path for path in source.rglob("*") if path.is_file()):
        relative = source_file.relative_to(source)
        destination_file = destination / relative
        _private_directory(destination_file.parent)
        shutil.copy2(source_file, destination_file)
        _private_file(destination_file)
        source_hash = _sha256(source_file)
        copied_hash = _sha256(destination_file)
        if copied_hash != source_hash or destination_file.stat().st_size != source_file.stat().st_size:
            raise OSError(f"Backup verification failed for {relative.as_posix()}")
        entries.append(
            {
                "path": (manifest_prefix / relative).as_posix(),
                "byteSize": destination_file.stat().st_size,
                "sha256": copied_hash,
                "copyVerified": True,
            }
        )
    return entries


def create_recovery_backup(
    data_dir: Path,
    *,
    project_id: str,
    job_id: str,
    destination: Path | None = None,
) -> dict[str, Any]:
    data_root = data_dir.resolve()
    if not data_root.is_dir():
        raise FileNotFoundError("TrackPrompt data directory does not exist")
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_root = (
        destination.resolve()
        if destination is not None
        else data_root / "backups" / "static-into-signal-recovery" / timestamp
    )
    allowed_root = (data_root / "backups").resolve()
    if not _inside(backup_root, allowed_root):
        raise ValueError("Recovery backups must remain inside the private TrackPrompt backup root")
    if backup_root.exists():
        raise FileExistsError("Backup destination already exists")
    staging = backup_root.with_name(f".{backup_root.name}.partial-{os.getpid()}")
    if staging.exists():
        raise FileExistsError("Backup staging directory already exists")

    primary_database = data_root / "trackprompt.sqlite3"
    mission_database = data_root / "mission-control" / "mission-control.sqlite3"
    video_job = data_root / "video-generation" / project_id / job_id
    for required in (primary_database, mission_database, video_job):
        if not required.exists():
            raise FileNotFoundError(f"Required recovery source is unavailable: {required.name}")

    estimated_bytes = sum(path.stat().st_size for path in (primary_database, mission_database))
    estimated_bytes += sum(path.stat().st_size for path in video_job.rglob("*") if path.is_file())
    archive_root = data_root / "archive"
    estimated_bytes += sum(path.stat().st_size for path in archive_root.rglob("*") if path.is_file())
    if shutil.disk_usage(allowed_root.parent).free < estimated_bytes * 2:
        raise OSError("Insufficient free space for a verified private recovery backup")

    _private_directory(staging)
    entries: list[dict[str, Any]] = []
    try:
        database_targets = (
            (primary_database, staging / "databases" / "trackprompt.sqlite3"),
            (mission_database, staging / "databases" / "mission-control.sqlite3"),
        )
        for source, target in database_targets:
            _copy_database(source, target)
            if not _database_integrity(target):
                raise OSError(f"SQLite integrity verification failed for {source.name}")
            entries.append(
                {
                    "path": target.relative_to(staging).as_posix(),
                    "byteSize": target.stat().st_size,
                    "sha256": _sha256(target),
                    "sqliteIntegrity": "ok",
                }
            )

        entries.extend(
            _copy_tree(
                video_job,
                staging / "video-job",
                manifest_prefix=Path("video-job"),
            )
        )
        entries.extend(
            _copy_tree(
                archive_root,
                staging / "archive",
                manifest_prefix=Path("archive"),
            )
        )
        clip_entries = [entry for entry in entries if entry["path"].endswith("provider.mp4")]
        manifest = {
            "schemaVersion": "1.0.0",
            "createdAt": datetime.now(UTC).isoformat(),
            "projectId": project_id,
            "videoJobId": job_id,
            "sqliteBackupMethod": "sqlite-online-backup-api",
            "includesArchive": archive_root.exists(),
            "fileCount": len(entries),
            "clipCount": len(clip_entries),
            "files": entries,
        }
        manifest_path = staging / MANIFEST_NAME
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False),
            encoding="utf-8",
        )
        _private_file(manifest_path)
        manifest_hash = _sha256(manifest_path)
        checksum_path = staging / f"{MANIFEST_NAME}.sha256"
        checksum_path.write_text(f"{manifest_hash}  {MANIFEST_NAME}\n", encoding="ascii")
        _private_file(checksum_path)
        _private_directory(backup_root.parent)
        staging.replace(backup_root)
    except BaseException:
        if staging.exists():
            shutil.rmtree(staging)
        raise

    verification = verify_recovery_backup(backup_root)
    if not verification["valid"]:
        raise OSError("The completed recovery backup failed final verification")
    return {"destination": str(backup_root), **verification}


def verify_recovery_backup(backup_root: Path) -> dict[str, Any]:
    root = backup_root.resolve()
    manifest_path = root / MANIFEST_NAME
    checksum_path = root / f"{MANIFEST_NAME}.sha256"
    errors: list[str] = []
    if not manifest_path.is_file() or not checksum_path.is_file():
        return {"valid": False, "fileCount": 0, "clipCount": 0, "errors": ["manifest_missing"]}
    expected_manifest_hash = checksum_path.read_text(encoding="ascii").split()[0]
    if _sha256(manifest_path) != expected_manifest_hash:
        errors.append("manifest_checksum")
    parsed = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = parsed.get("files") if isinstance(parsed, dict) else None
    if not isinstance(entries, list):
        return {"valid": False, "fileCount": 0, "clipCount": 0, "errors": ["manifest_invalid"]}
    verified_bytes = 0
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            errors.append("entry_invalid")
            continue
        relative = Path(entry["path"])
        target = (root / relative).resolve()
        if relative.is_absolute() or ".." in relative.parts or not _inside(target, root):
            errors.append("entry_path_invalid")
            continue
        if not target.is_file():
            errors.append(f"missing:{relative.as_posix()}")
            continue
        verified_bytes += target.stat().st_size
        if target.stat().st_size != int(entry.get("byteSize", -1)) or _sha256(target) != entry.get("sha256"):
            errors.append(f"checksum:{relative.as_posix()}")
    for database_name in ("trackprompt.sqlite3", "mission-control.sqlite3"):
        database = root / "databases" / database_name
        if database.is_file() and not _database_integrity(database):
            errors.append(f"database_integrity:{database_name}")
    clip_count = sum(
        1 for entry in entries
        if isinstance(entry, dict) and str(entry.get("path", "")).endswith("provider.mp4")
    )
    return {
        "valid": not errors,
        "fileCount": len(entries),
        "clipCount": clip_count,
        "verifiedBytes": verified_bytes,
        "manifestSha256": expected_manifest_hash,
        "errors": errors,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create or verify a private Static Into Signal recovery backup")
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create")
    create.add_argument("--data-dir", type=Path, default=Path(".trackprompt-data"))
    create.add_argument("--project-id", default="static-into-signal")
    create.add_argument("--job-id", required=True)
    create.add_argument("--destination", type=Path)
    verify = subparsers.add_parser("verify")
    verify.add_argument("backup", type=Path)
    return parser


def main(arguments: list[str] | None = None) -> int:
    args = _parser().parse_args(arguments)
    result = (
        create_recovery_backup(
            args.data_dir,
            project_id=args.project_id,
            job_id=args.job_id,
            destination=args.destination,
        )
        if args.command == "create"
        else verify_recovery_backup(args.backup)
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("valid", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
