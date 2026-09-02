from __future__ import annotations

import json
from pathlib import Path

from app.mission_control.config import MissionControlConfig
from app.mission_control.discovery import MissionDiscovery, sha256_file


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _profile(
    *,
    profile_id: str,
    scene_sha256: str,
    variant_id: str,
    width: int,
    height: int,
    required: bool | None,
) -> dict[str, object]:
    variant: dict[str, object] = {
        "id": variant_id,
        "enabled": True,
        "width": width,
        "height": height,
        "fps": 30,
        "compositionMode": "authored",
        "compositionProfileId": f"{variant_id}-composition-v1",
    }
    if required is not None:
        variant["required"] = required
    return {
        "schemaVersion": "1.0.0",
        "kind": "trackprompt-render-profile",
        "project": "synthetic-project",
        "preset": "synthetic-story",
        "profileId": profile_id,
        "displayName": profile_id,
        "approvedSceneSha256": scene_sha256,
        "frameStart": 1,
        "frameEnd": 12,
        "fps": 30,
        "resolution": {"width": width, "height": height},
        "outputVariant": variant,
        "authorization": {
            "project": "SYNTHETIC-PROJECT",
            "preset": "SYNTHETIC-STORY",
            "profile": profile_id,
        },
    }


def test_package_manifest_resolves_immutable_variant_scenes_and_defaults(
    tmp_path: Path,
) -> None:
    repository_root = tmp_path / "repository"
    profile_root = repository_root / "render-profiles"
    production_root = repository_root / "production" / "synthetic"
    artifact_root = repository_root / "test-output" / "synthetic-release"
    horizontal_scene = artifact_root / "horizontal.blend"
    vertical_scene = artifact_root / "vertical.blend"
    horizontal_scene.parent.mkdir(parents=True)
    horizontal_scene.write_bytes(b"BLENDER synthetic horizontal scene")
    vertical_scene.write_bytes(b"BLENDER synthetic vertical scene")
    horizontal_hash = sha256_file(horizontal_scene)
    vertical_hash = sha256_file(vertical_scene)

    horizontal_profile = profile_root / "horizontal.json"
    vertical_profile = profile_root / "vertical.json"
    _write_json(
        horizontal_profile,
        _profile(
            profile_id="SYNTHETIC-HORIZONTAL",
            scene_sha256=horizontal_hash,
            variant_id="horizontal-16x9",
            width=1920,
            height=1080,
            required=None,
        ),
    )
    _write_json(
        vertical_profile,
        _profile(
            profile_id="SYNTHETIC-VERTICAL-OPTIONAL",
            scene_sha256=vertical_hash,
            variant_id="vertical-9x16",
            width=1080,
            height=1920,
            required=False,
        ),
    )
    profile_hashes_before = {
        horizontal_profile: sha256_file(horizontal_profile),
        vertical_profile: sha256_file(vertical_profile),
    }
    _write_json(
        production_root / "package-manifest-v2.json",
        {
            "schemaVersion": "2.0.0",
            "artifacts": [
                {
                    "role": "horizontal-scene",
                    "path": horizontal_scene.relative_to(repository_root).as_posix(),
                    "sha256": horizontal_hash,
                },
                {
                    "role": "vertical-scene",
                    "path": vertical_scene.relative_to(repository_root).as_posix(),
                    "sha256": vertical_hash,
                },
            ],
        },
    )
    config = MissionControlConfig(
        repository_root=repository_root,
        state_root=repository_root / ".state",
        profile_root=profile_root,
        calibration_root=repository_root / "calibration",
        default_output_root=repository_root / "output",
    )

    discovery = MissionDiscovery(config)
    profiles = {profile.id: profile for profile in discovery.list_profiles()}
    scenes = {scene.id: scene for scene in discovery.list_scenes()}

    horizontal = profiles["SYNTHETIC-HORIZONTAL"]
    assert horizontal.scene_id == "synthetic-story-horizontal-16x9"
    assert horizontal.scene_sha256 == horizontal_hash
    assert len(horizontal.output_variants) == 1
    assert horizontal.output_variants[0].required is True
    assert horizontal.output_variants[0].enabled_by_default is True

    vertical = profiles["SYNTHETIC-VERTICAL-OPTIONAL"]
    assert vertical.scene_id == "synthetic-story-vertical-9x16"
    assert vertical.scene_sha256 == vertical_hash
    assert len(vertical.output_variants) == 1
    assert vertical.output_variants[0].required is False
    assert vertical.output_variants[0].enabled_by_default is False

    assert set(scenes) == {
        "synthetic-story-horizontal-16x9",
        "synthetic-story-vertical-9x16",
    }
    assert Path(scenes[horizontal.scene_id].path) == horizontal_scene
    assert Path(scenes[vertical.scene_id].path) == vertical_scene
    assert {
        path: sha256_file(path) for path in profile_hashes_before
    } == profile_hashes_before
