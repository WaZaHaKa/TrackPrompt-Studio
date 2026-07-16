from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=256)
def _hash_at_signature(path_text: str, _size: int, _mtime_ns: int, _ctime_ns: int) -> str:
    digest = hashlib.sha256()
    with Path(path_text).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_model_manifest(directory: Path, model_id: str, revision: str) -> tuple[bool, str]:
    """Verify an exact, complete SHA-256 allowlist for one application-managed model."""
    try:
        root = directory.resolve()
        manifest = root / "manifest.json"
        if not manifest.is_file() or manifest.stat().st_size > 2_000_000:
            return False, "The model checksum manifest is missing."
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        if payload.get("modelId") != model_id or payload.get("revision") != revision:
            return False, "The model manifest identifier or revision does not match configuration."
        files = payload.get("files")
        if not isinstance(files, dict) or not files:
            return False, "The model checksum manifest has no files."
        listed: set[Path] = set()
        for relative_name, expected in files.items():
            if not isinstance(relative_name, str) or not isinstance(expected, str):
                return False, "The model checksum manifest is invalid."
            relative = Path(relative_name)
            if relative.is_absolute() or ".." in relative.parts or len(expected) != 64:
                return False, "The model checksum manifest contains an unsafe entry."
            candidate = (root / relative).resolve()
            if not candidate.is_relative_to(root) or not candidate.is_file():
                return False, "A manifested model file is missing."
            signature = candidate.stat()
            digest = _hash_at_signature(
                str(candidate),
                signature.st_size,
                signature.st_mtime_ns,
                signature.st_ctime_ns,
            )
            if digest.casefold() != expected.casefold():
                return False, "A model file failed SHA-256 verification."
            listed.add(candidate)
        actual = {path.resolve() for path in root.rglob("*") if path.is_file() and path != manifest}
        if actual != listed:
            return False, "The model directory contains unmanifested files."
        return True, "The complete local model manifest is verified."
    except (OSError, ValueError, json.JSONDecodeError):
        return False, "The local model manifest could not be verified."
