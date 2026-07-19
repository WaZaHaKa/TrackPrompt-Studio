from __future__ import annotations

import hashlib
import json
import re
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


def verify_demucs_model_manifest(directory: Path, model_name: str) -> tuple[list[Path], str]:
    """Verify the exact local Demucs repository selected by configuration."""
    try:
        root = directory.resolve()
        manifest = root / "demucs-models.json"
        if (
            not manifest.is_file()
            or manifest.resolve().parent != root
            or manifest.stat().st_size > 1_000_000
        ):
            return [], "The Demucs checksum manifest is missing."
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        models = payload.get("models") if isinstance(payload, dict) else None
        model = models.get(model_name) if isinstance(models, dict) else None
        files = model.get("files") if isinstance(model, dict) else None
        if not isinstance(files, dict) or not files:
            return [], "The selected Demucs model has no manifested files."
        verified: list[Path] = []
        listed: set[Path] = set()
        for relative_name, expected_hash in files.items():
            if (
                not isinstance(relative_name, str)
                or not isinstance(expected_hash, str)
                or re.fullmatch(r"[0-9a-fA-F]{64}", expected_hash) is None
            ):
                return [], "The Demucs checksum manifest is invalid."
            relative = Path(relative_name)
            if relative.is_absolute() or ".." in relative.parts:
                return [], "The Demucs checksum manifest contains an unsafe entry."
            candidate = (root / relative).resolve()
            if not candidate.is_relative_to(root) or candidate == manifest:
                return [], "The Demucs checksum manifest contains an unsafe path."
            before = candidate.stat()
            digest = _hash_at_signature(
                str(candidate),
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
            )
            after = candidate.stat()
            if (
                not candidate.is_file()
                or (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                or digest.casefold() != expected_hash.casefold()
            ):
                return [], "A Demucs model file failed SHA-256 verification."
            verified.append(candidate)
            listed.add(candidate)
        actual = {
            path.resolve()
            for path in root.rglob("*")
            if path.is_file() and path.resolve() != manifest
        }
        if actual != listed:
            return [], "The Demucs model directory contains unmanifested files."
        return verified, "The complete local Demucs manifest is verified."
    except (OSError, ValueError, json.JSONDecodeError):
        return [], "The local Demucs manifest could not be verified."
