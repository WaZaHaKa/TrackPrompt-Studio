from __future__ import annotations

import importlib
import json
import math
from pathlib import Path
from typing import Any, Mapping, Protocol, cast

from .manifests import (
    PACKAGE_KIND,
    SCHEMA_VERSION,
    ManifestError,
    canonical_json_bytes,
    safe_relative_key,
    seal_manifest,
    sensitive_paths,
    validate_package_manifest,
)
from .models import FrameRange, IdentityBundle, require_sha256
from .storage.base import sha256_path

REMOTE_PACKAGE_KIND = "trackprompt-remote-render-package"
REMOTE_PACKAGE_SCHEMA = "1.0.0"


class RemotePackageValidator(Protocol):
    def validate_package(self, root: Path) -> dict[str, Any]: ...


class PackageBridgeError(ManifestError):
    """The established sanitized package cannot safely become a cloud package."""


def _remote_tooling() -> RemotePackageValidator:
    module = importlib.import_module("tools.remote_render_tooling")
    return cast(RemotePackageValidator, module)


def _object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PackageBridgeError(f"{label} must be an object")
    return cast(dict[str, Any], value)


def _string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PackageBridgeError(f"{label} must be a non-empty string")
    return value


def _positive_int(value: object, label: str) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        result = value
    elif isinstance(value, float) and math.isfinite(value) and value.is_integer():
        result = int(value)
    elif isinstance(value, str) and value.isdecimal():
        result = int(value)
    else:
        raise PackageBridgeError(f"{label} must be a positive integer")
    if result < 1:
        raise PackageBridgeError(f"{label} must be a positive integer")
    return result


def _nonnegative_int(value: object, label: str) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    if isinstance(value, float) and math.isfinite(value) and value.is_integer() and value >= 0:
        return int(value)
    if isinstance(value, str) and value.isdecimal():
        return int(value)
    raise PackageBridgeError(f"{label} must be a nonnegative integer")


def _nonnegative_json_int(value: object, label: str) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    raise PackageBridgeError(f"{label} must be a nonnegative JSON integer")


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PackageBridgeError(f"{label} is unreadable or invalid JSON") from exc
    return _object(value, label)


def _assert_no_links(root: Path) -> None:
    if root.is_symlink():
        raise PackageBridgeError("remote package root must not be a symbolic link")
    for path in root.rglob("*"):
        if path.is_symlink():
            raise PackageBridgeError("remote package must not contain symbolic links")


def _validated_remote_payloads(
    remote_package: Path,
) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    if remote_package.is_symlink():
        raise PackageBridgeError("remote package root must not be a symbolic link")
    root = remote_package.resolve(strict=True)
    if not root.is_dir():
        raise PackageBridgeError("remote package must be a directory")
    _assert_no_links(root)
    try:
        validation = _remote_tooling().validate_package(root)
    except Exception as exc:
        raise PackageBridgeError("established remote package validation failed") from exc
    if validation.get("ok") is not True or validation.get("issues") not in (None, []):
        raise PackageBridgeError("established remote package validation reported issues")
    manifest = _read_json_object(root / "package-manifest.json", "remote package manifest")
    if (
        manifest.get("kind") != REMOTE_PACKAGE_KIND
        or manifest.get("schemaVersion") != REMOTE_PACKAGE_SCHEMA
    ):
        raise PackageBridgeError("remote package kind or schema is unsupported")
    if manifest.get("privateAudioIncluded") is not False:
        raise PackageBridgeError("remote package must explicitly exclude private audio")
    if manifest.get("networkUploadAuthorized") is not False:
        raise PackageBridgeError("remote package must not authorize implicit network upload")
    if manifest.get("encodingAllowed") is not False:
        raise PackageBridgeError("remote package must prohibit cloud encoding")
    private_fields = sensitive_paths(manifest)
    if private_fields:
        raise PackageBridgeError("remote package manifest contains private fields")
    checksum_name = safe_relative_key(
        _string(manifest.get("checksumManifest"), "remote checksum manifest path")
    )
    checksum = _read_json_object(
        root / Path(*checksum_name.split("/")), "remote checksum manifest"
    )
    return root, manifest, checksum


def _file_contract(
    root: Path,
    remote_manifest: Mapping[str, Any],
    checksum_manifest: Mapping[str, Any],
) -> list[dict[str, object]]:
    raw_files = checksum_manifest.get("files")
    if not isinstance(raw_files, list) or not raw_files:
        raise PackageBridgeError("remote checksum manifest has no files")
    records: dict[str, dict[str, object]] = {}
    for raw in raw_files:
        entry = _object(raw, "remote checksum file entry")
        relative = safe_relative_key(_string(entry.get("path"), "remote file path"))
        if relative in records:
            raise PackageBridgeError("remote checksum manifest contains duplicate paths")
        records[relative] = {
            "path": relative,
            "sha256": require_sha256(
                _string(entry.get("sha256"), "remote file SHA-256"),
                "remote file sha256",
            ),
            "sizeBytes": _nonnegative_json_int(
                entry.get("sizeBytes"), "remote file size"
            ),
        }
    checksum_name = safe_relative_key(
        _string(remote_manifest.get("checksumManifest"), "remote checksum manifest path")
    )
    for relative in (checksum_name, "package-manifest.json"):
        path = root / Path(*relative.split("/"))
        records[relative] = {
            "path": relative,
            "sha256": sha256_path(path),
            "sizeBytes": path.stat().st_size,
        }
    for record in records.values():
        path = root / Path(*str(record["path"]).split("/"))
        if not path.is_file():
            raise PackageBridgeError("remote package file contract is incomplete")
        if (
            path.stat().st_size != record["sizeBytes"]
            or sha256_path(path) != record["sha256"]
        ):
            raise PackageBridgeError("remote package file contract changed during preparation")
    scene = _object(remote_manifest.get("scene"), "remote scene identity")
    profile = _object(remote_manifest.get("profile"), "remote profile identity")
    critical_paths = {
        checksum_name,
        "package-manifest.json",
        safe_relative_key(_string(scene.get("relativePath"), "remote scene path")),
        safe_relative_key(_string(profile.get("relativePath"), "remote profile path")),
        "blender/render_remote_chunk.py",
    }
    for relative in critical_paths:
        critical_record = records.get(relative)
        critical_size = (
            critical_record.get("sizeBytes") if critical_record is not None else None
        )
        if (
            not isinstance(critical_size, int)
            or isinstance(critical_size, bool)
            or critical_size <= 0
        ):
            raise PackageBridgeError("critical remote render inputs must be non-empty")
    return [records[key] for key in sorted(records)]


def prepare_cloud_manifest(remote_package: Path) -> dict[str, Any]:
    """Validate and deterministically bridge one established sanitized package."""

    root, remote, checksum = _validated_remote_payloads(remote_package)
    scene = _object(remote.get("scene"), "remote scene identity")
    profile = _object(remote.get("profile"), "remote profile identity")
    contract = _object(remote.get("frameContract"), "remote frame contract")
    frame_range = FrameRange(
        _positive_int(contract.get("frameStart"), "frame start"),
        _positive_int(contract.get("frameEnd"), "frame end"),
    )
    if _positive_int(contract.get("frameCount"), "frame count") != frame_range.count:
        raise PackageBridgeError("remote frame count differs from its inclusive range")
    image_format = _string(contract.get("format"), "image format").upper()
    filename_pattern = _string(contract.get("filenamePattern"), "filename pattern")
    if image_format != "PNG" or filename_pattern != "frame_%06d.png":
        raise PackageBridgeError("cloud worker currently requires canonical frame_%06d.png output")
    bit_depth = _positive_int(contract.get("bitDepth"), "image bit depth")
    color_mode = _string(contract.get("colorMode"), "image color mode").upper()
    if bit_depth not in {8, 16} or color_mode != "RGB":
        raise PackageBridgeError("cloud PNG worker requires 8/16-bit opaque RGB frames")
    identities = IdentityBundle(
        require_sha256(_string(scene.get("sha256"), "scene SHA-256"), "scene sha256"),
        require_sha256(
            _string(profile.get("sha256"), "profile SHA-256"), "profile sha256"
        ),
        require_sha256(
            _string(remote.get("packageSha256"), "remote package SHA-256"),
            "package sha256",
        ),
    )
    source_profile_sha = require_sha256(
        _string(
            profile.get("sourceProductionProfileSha256"),
            "source production profile SHA-256",
        ),
        "source production profile sha256",
    )
    source_package: dict[str, Any] = {
        "kind": REMOTE_PACKAGE_KIND,
        "packageId": _string(remote.get("packageId"), "remote package ID"),
        "remotePackageSha256": identities.package_sha256,
        "sourceProductionProfileSha256": source_profile_sha,
        "validated": True,
    }
    source_scene_value = scene.get("sourceSceneSha256")
    if source_scene_value is not None:
        source_package["sourceSceneSha256"] = require_sha256(
            _string(source_scene_value, "source scene SHA-256"),
            "source scene sha256",
        )
    payload: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": PACKAGE_KIND,
        "privateAudioIncluded": False,
        "audioMuxLocation": "LOCAL_ONLY",
        "sourcePackage": source_package,
        "identities": {
            "sceneSha256": identities.scene_sha256,
            "profileSha256": identities.profile_sha256,
            "packageSha256": identities.package_sha256,
        },
        "frameRange": {"start": frame_range.start, "end": frame_range.end},
        "blenderVersion": _string(remote.get("blenderVersion"), "Blender version"),
        "resolution": {
            "width": _positive_int(contract.get("width"), "frame width"),
            "height": _positive_int(contract.get("height"), "frame height"),
        },
        "image": {
            "format": image_format,
            "extension": "png",
            "bitDepth": bit_depth,
            "colorMode": color_mode,
            "filenamePattern": filename_pattern,
        },
        "renderContract": {
            "fps": _positive_int(contract.get("fps"), "frames per second"),
            "deterministicSeed": _nonnegative_int(
                remote.get("deterministicSeed", 0), "deterministic seed"
            ),
            "encodingAllowed": False,
            "networkUploadAuthorized": False,
        },
        "files": _file_contract(root, remote, checksum),
    }
    issues = sensitive_paths(payload)
    if issues:
        raise PackageBridgeError("derived cloud manifest contains private fields")
    sealed = seal_manifest(payload)
    validate_package_manifest(sealed)
    return sealed


def validate_bridge_against_remote(
    cloud_manifest: Mapping[str, Any], remote_package: Path
) -> tuple[IdentityBundle, FrameRange]:
    """Bind a worker to the exact deterministic manifest for its remote package."""

    identities, frame_range = validate_package_manifest(cloud_manifest)
    expected = prepare_cloud_manifest(remote_package)
    if canonical_json_bytes(cloud_manifest) != canonical_json_bytes(expected):
        raise PackageBridgeError("cloud manifest does not exactly match the sanitized package")
    return identities, frame_range
