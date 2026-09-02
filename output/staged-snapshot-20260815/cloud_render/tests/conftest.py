from __future__ import annotations

from collections.abc import Callable

import pytest

from cloud_render.manifests import PACKAGE_KIND, SCHEMA_VERSION, seal_manifest
from cloud_render.models import IdentityBundle


@pytest.fixture
def identities() -> IdentityBundle:
    return IdentityBundle("A" * 64, "B" * 64, "C" * 64)


@pytest.fixture
def package_manifest(
    identities: IdentityBundle,
) -> Callable[..., dict[str, object]]:
    def build(**updates: object) -> dict[str, object]:
        payload: dict[str, object] = {
            "schemaVersion": SCHEMA_VERSION,
            "kind": PACKAGE_KIND,
            "identities": {
                "sceneSha256": identities.scene_sha256,
                "profileSha256": identities.profile_sha256,
                "packageSha256": identities.package_sha256,
            },
            "frameRange": {"start": 1, "end": 4},
            "privateAudioIncluded": False,
            "audioMuxLocation": "LOCAL_ONLY",
            "blenderVersion": "5.2.0",
            "resolution": {"width": 16, "height": 16},
            "image": {"format": "PNG", "bitDepth": 8, "extension": "png"},
            "files": [
                {
                    "path": "scene/sanitized.blend",
                    "sha256": "D" * 64,
                    "sizeBytes": 100,
                }
            ],
        }
        payload.update(updates)
        return seal_manifest(payload)

    return build
