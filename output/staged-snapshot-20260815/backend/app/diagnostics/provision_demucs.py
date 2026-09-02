from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path
from uuid import uuid4

from ..config import Settings
from ..model_cache import verify_demucs_model_manifest


def _private_permissions(root: Path) -> None:
    for directory in [root, *(path for path in root.rglob("*") if path.is_dir())]:
        directory.chmod(0o700)
    for file_path in (path for path in root.rglob("*") if path.is_file()):
        file_path.chmod(0o600)


def provision_demucs_repository(
    source: Path,
    destination: Path,
    model_cache_root: Path,
    model_name: str,
    *,
    force: bool = False,
) -> bool:
    """Provision a verified host seed atomically; return whether files changed."""
    source_root = source.resolve()
    destination_root = destination.resolve()
    cache_root = model_cache_root.resolve()
    if destination_root == cache_root or not destination_root.is_relative_to(cache_root):
        raise RuntimeError("The configured Demucs destination is unsafe.")
    source_files, source_reason = verify_demucs_model_manifest(source_root, model_name)
    if not source_files:
        raise RuntimeError(source_reason)
    existing_files, _existing_reason = verify_demucs_model_manifest(destination_root, model_name)
    if existing_files and not force:
        return False

    cache_root.mkdir(parents=True, exist_ok=True)
    stage = cache_root / f".demucs-provision-{uuid4().hex}"
    replaced = cache_root / f".demucs-replaced-{uuid4().hex}"
    try:
        stage.mkdir(mode=0o700)
        shutil.copy2(source_root / "demucs-models.json", stage / "demucs-models.json")
        for source_file in source_files:
            relative = source_file.relative_to(source_root)
            target = stage / relative
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            shutil.copy2(source_file, target)
        _private_permissions(stage)
        staged_files, staged_reason = verify_demucs_model_manifest(stage, model_name)
        if not staged_files:
            raise RuntimeError(staged_reason)
        if destination_root.exists():
            os.replace(destination_root, replaced)
        os.replace(stage, destination_root)
        _private_permissions(destination_root)
        if replaced.exists():
            shutil.rmtree(replaced)
    except Exception:
        if stage.exists():
            shutil.rmtree(stage)
        if replaced.exists() and not destination_root.exists():
            os.replace(replaced, destination_root)
        raise
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Provision a reviewed local Demucs repository.")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--force", action="store_true")
    arguments = parser.parse_args()
    settings = Settings.from_env()
    changed = provision_demucs_repository(
        arguments.source,
        settings.demucs_model_dir,
        settings.model_cache_dir,
        settings.demucs_model_name,
        force=bool(arguments.force),
    )
    print(
        json.dumps(
            {
                "diagnostic": "provision-demucs",
                "status": "ok",
                "model": settings.demucs_model_name,
                "changed": changed,
                "cachePreserved": not changed,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
