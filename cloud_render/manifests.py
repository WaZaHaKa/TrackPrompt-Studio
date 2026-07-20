from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from dataclasses import asdict, is_dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from pathlib import PurePosixPath
from typing import Any, Mapping, cast

from .models import FrameRange, IdentityBundle, require_sha256

SCHEMA_VERSION = "1.0.0"
PACKAGE_KIND = "trackprompt-cloud-render-package"
CHUNK_OUTPUT_KIND = "trackprompt-cloud-chunk-output"
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SENSITIVE_TERMS = (
    "audio",
    "wav",
    "lyric",
    "transcript",
    "prompt",
    "credential",
    "password",
    "secret",
    "token",
    "privatekey",
    "sourcefilename",
    "sourcepath",
)
_SAFE_PRIVACY_KEYS = {"privateaudioincluded", "audiomuxlocation"}


class ManifestError(ValueError):
    """Raised when an immutable cloud manifest is invalid or has drifted."""


def _json_value(value: Any) -> Any:
    if is_dataclass(value):
        return _json_value(asdict(cast(Any, value)))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ManifestError("manifest datetimes must be timezone-aware")
        return value.isoformat()
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ManifestError("manifest decimals must be finite")
        return format(value, "f")
    if isinstance(value, Mapping):
        return {str(key): _json_value(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(child) for child in value]
    if isinstance(value, float) and not math.isfinite(value):
        raise ManifestError("manifest floats must be finite")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ManifestError(f"unsupported manifest value: {type(value).__name__}")


def canonical_json_bytes(payload: Mapping[str, Any]) -> bytes:
    normalized = _json_value(payload)
    return json.dumps(
        normalized,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_sha256(payload: Mapping[str, Any], *, hash_field: str = "manifestSha256") -> str:
    unsigned = {key: value for key, value in payload.items() if key != hash_field}
    return hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest().upper()


def seal_manifest(payload: Mapping[str, Any], *, hash_field: str = "manifestSha256") -> dict[str, Any]:
    sealed = copy.deepcopy(dict(payload))
    sealed[hash_field] = canonical_sha256(sealed, hash_field=hash_field)
    return sealed


def validate_sealed_manifest(
    payload: Mapping[str, Any],
    *,
    expected_kind: str,
    hash_field: str = "manifestSha256",
) -> dict[str, Any]:
    normalized = _json_value(payload)
    if not isinstance(normalized, dict):
        raise ManifestError("manifest must be an object")
    if normalized.get("schemaVersion") != SCHEMA_VERSION:
        raise ManifestError("unsupported manifest schemaVersion")
    if normalized.get("kind") != expected_kind:
        raise ManifestError("unexpected manifest kind")
    supplied = normalized.get(hash_field)
    if not isinstance(supplied, str) or supplied != canonical_sha256(normalized, hash_field=hash_field):
        raise ManifestError("manifest SHA-256 does not match canonical content")
    return normalized


def safe_identifier(value: str, label: str) -> str:
    if not _SAFE_ID.fullmatch(value):
        raise ManifestError(f"{label} contains unsafe characters")
    return value


def safe_relative_key(value: str) -> str:
    if not value or "\\" in value:
        raise ManifestError("object key must use non-empty POSIX-relative syntax")
    raw_parts = value.split("/")
    if any(part in {"", ".", ".."} for part in raw_parts):
        raise ManifestError("object key must not contain empty, dot, or parent segments")
    path = PurePosixPath(value)
    if path.is_absolute():
        raise ManifestError("object key must not be absolute or traverse parents")
    return path.as_posix()


def sensitive_paths(payload: Any, prefix: str = "$") -> list[str]:
    """Return paths whose keys or values violate the cloud privacy contract."""

    issues: list[str] = []
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            normalized = re.sub(r"[^a-z0-9]", "", str(key).casefold())
            child_path = f"{prefix}.{key}"
            if normalized not in _SAFE_PRIVACY_KEYS and any(
                term in normalized for term in _SENSITIVE_TERMS
            ):
                issues.append(child_path)
            issues.extend(sensitive_paths(value, child_path))
    elif isinstance(payload, (list, tuple)):
        for index, value in enumerate(payload):
            issues.extend(sensitive_paths(value, f"{prefix}[{index}]"))
    elif isinstance(payload, str):
        lowered = payload.casefold()
        if re.search(r"(?:[a-z]:[\\/]|^\\\\|^/home/|^/users/)", payload, re.IGNORECASE):
            issues.append(prefix)
        elif any(lowered.endswith(suffix) for suffix in (".wav", ".mp3", ".flac", ".m4a")):
            issues.append(prefix)
    return sorted(set(issues))


def validate_package_manifest(payload: Mapping[str, Any]) -> tuple[IdentityBundle, FrameRange]:
    manifest = validate_sealed_manifest(payload, expected_kind=PACKAGE_KIND)
    if manifest.get("privateAudioIncluded") is not False:
        raise ManifestError("cloud package must explicitly exclude private audio")
    if manifest.get("audioMuxLocation") != "LOCAL_ONLY":
        raise ManifestError("cloud package must require local-only audio muxing")
    identities_raw = manifest.get("identities")
    frame_raw = manifest.get("frameRange")
    if not isinstance(identities_raw, dict) or not isinstance(frame_raw, dict):
        raise ManifestError("package identities and frameRange are required")
    identities = IdentityBundle(
        scene_sha256=require_sha256(str(identities_raw.get("sceneSha256", "")), "sceneSha256"),
        profile_sha256=require_sha256(str(identities_raw.get("profileSha256", "")), "profileSha256"),
        package_sha256=require_sha256(str(identities_raw.get("packageSha256", "")), "packageSha256"),
    )
    frame_range = FrameRange(start=int(frame_raw.get("start", 0)), end=int(frame_raw.get("end", 0)))
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise ManifestError("package file manifest cannot be empty")
    for item in files:
        if not isinstance(item, dict):
            raise ManifestError("package file entries must be objects")
        safe_relative_key(str(item.get("path", "")))
        require_sha256(str(item.get("sha256", "")), "package file sha256")
        size_bytes = item.get("sizeBytes")
        if (
            not isinstance(size_bytes, int)
            or isinstance(size_bytes, bool)
            or size_bytes < 0
        ):
            raise ManifestError("package file sizeBytes must be a nonnegative integer")
    privacy_copy = {key: value for key, value in manifest.items() if key not in {"manifestSha256"}}
    if sensitive_paths(privacy_copy):
        raise ManifestError("package manifest contains private or local-only fields")
    return identities, frame_range
