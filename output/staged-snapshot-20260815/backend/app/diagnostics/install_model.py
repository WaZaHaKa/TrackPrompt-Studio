from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from pathlib import Path

from huggingface_hub import snapshot_download

from ..config import Settings
from ..privacy import secure_private_directory, secure_private_file


def _write_manifest(directory: Path, model_id: str, revision: str) -> None:
    cache = directory / ".cache"
    if cache.exists() and cache.resolve().is_relative_to(directory.resolve()):
        shutil.rmtree(cache)
    files: dict[str, str] = {}
    for path in sorted(item for item in directory.rglob("*") if item.is_file() and item.name != "manifest.json"):
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        files[path.relative_to(directory).as_posix()] = digest.hexdigest()
        secure_private_file(path)
    if not files:
        raise RuntimeError("The explicit model download produced no files.")
    manifest = directory / "manifest.json"
    manifest.write_text(
        json.dumps({"modelId": model_id, "revision": revision, "files": files}, indent=2) + "\n",
        encoding="utf-8",
    )
    secure_private_file(manifest)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("kind", choices=("genre", "lyrics"))
    parser.add_argument("--force", action="store_true")
    arguments = parser.parse_args()
    settings = Settings.from_env()
    if arguments.kind == "genre":
        model_id, revision, destination = (
            settings.genre_model_id,
            settings.genre_model_revision,
            settings.genre_model_dir,
        )
    else:
        model_id, revision, destination = (
            settings.lyrics_model_name,
            settings.lyrics_model_revision,
            settings.lyrics_model_dir,
        )
    root = settings.model_cache_dir.resolve()
    resolved = destination.resolve()
    if not resolved.is_relative_to(root) or resolved == root:
        raise RuntimeError("The configured model destination is unsafe.")
    if arguments.force and resolved.exists():
        shutil.rmtree(resolved)
    resolved.mkdir(parents=True, exist_ok=True)
    secure_private_directory(resolved)
    os.environ["HF_HUB_OFFLINE"] = "0"
    os.environ["TRANSFORMERS_OFFLINE"] = "0"
    snapshot_download(
        repo_id=model_id,
        revision=revision,
        local_dir=resolved,
        local_dir_use_symlinks=False,
    )
    _write_manifest(resolved, model_id, revision)
    print(json.dumps({"kind": arguments.kind, "modelId": model_id, "revision": revision, "path": str(resolved), "manifest": "verified-on-next-capability-check"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
