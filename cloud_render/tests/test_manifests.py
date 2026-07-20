from __future__ import annotations

import math

import pytest

from cloud_render.manifests import (
    PACKAGE_KIND,
    ManifestError,
    canonical_json_bytes,
    canonical_sha256,
    safe_relative_key,
    seal_manifest,
    validate_package_manifest,
    validate_sealed_manifest,
)
from cloud_render.models import IdentityBundle


def test_canonical_json_is_order_independent() -> None:
    first = {"z": [3, 2, 1], "a": {"b": True}}
    second = {"a": {"b": True}, "z": [3, 2, 1]}
    assert canonical_json_bytes(first) == canonical_json_bytes(second)
    assert canonical_sha256(first) == canonical_sha256(second)


def test_canonical_json_rejects_nonfinite_numbers() -> None:
    with pytest.raises(ManifestError, match="finite"):
        canonical_json_bytes({"bad": math.nan})


def test_sealed_manifest_detects_drift() -> None:
    sealed = seal_manifest({"schemaVersion": "1.0.0", "kind": "example", "value": 1})
    assert validate_sealed_manifest(sealed, expected_kind="example")["value"] == 1
    sealed["value"] = 2
    with pytest.raises(ManifestError, match="SHA-256"):
        validate_sealed_manifest(sealed, expected_kind="example")


def test_package_manifest_validates_exact_identities_and_range(
    package_manifest: object,
    identities: IdentityBundle,
) -> None:
    manifest = package_manifest()
    actual_identities, frame_range = validate_package_manifest(manifest)
    assert actual_identities == identities
    assert (frame_range.start, frame_range.end, frame_range.count) == (1, 4, 4)


def test_package_manifest_rejects_audio_or_local_path(package_manifest: object) -> None:
    with pytest.raises(ManifestError, match="exclude private audio"):
        validate_package_manifest(package_manifest(privateAudioIncluded=True))
    manifest = package_manifest(notes=r"C:\private\source.wav")
    with pytest.raises(ManifestError, match="private or local-only"):
        validate_package_manifest(manifest)


def test_package_manifest_allows_explicit_zero_byte_generic_file(
    package_manifest: object,
) -> None:
    manifest = package_manifest()
    manifest["files"].append(
        {"path": "tools/__init__.py", "sha256": "E" * 64, "sizeBytes": 0}
    )
    validate_package_manifest(seal_manifest(manifest))


@pytest.mark.parametrize("size_bytes", [None, False, 0.5, -1])
def test_package_manifest_rejects_invalid_file_sizes(
    package_manifest: object,
    size_bytes: object,
) -> None:
    manifest = package_manifest()
    manifest["files"][0]["sizeBytes"] = size_bytes
    with pytest.raises(ManifestError, match="nonnegative integer"):
        validate_package_manifest(seal_manifest(manifest))


@pytest.mark.parametrize("key", ["../escape", "/absolute", r"bad\windows", "a/./b"])
def test_relative_object_keys_reject_escapes(key: str) -> None:
    with pytest.raises(ManifestError):
        safe_relative_key(key)


def test_package_kind_is_enforced(package_manifest: object) -> None:
    manifest = package_manifest(kind="other")
    with pytest.raises(ManifestError, match="kind"):
        validate_sealed_manifest(manifest, expected_kind=PACKAGE_KIND)
