from __future__ import annotations

import hashlib
import json
import shutil
import struct
import subprocess
import sys
import zlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from blender import render_final_chunk as blender_chunk
from tools import final_render_tooling as tooling

DELIVERY_COLOR_FILTER = (
    "colorspace=ispace=gbr:iprimaries=bt709:itrc=iec61966-2-1:irange=pc:space=bt709:"
    "primaries=bt709:trc=bt709:range=tv:format=yuv420p"
)
MASTER_COLOR_FILTER = (
    "colorspace=ispace=gbr:iprimaries=bt709:itrc=iec61966-2-1:irange=pc:space=bt709:"
    "primaries=bt709:trc=bt709:range=tv:format=yuv422p10,format=yuv422p10le"
)


def _chunk(chunk_type: bytes, payload: bytes) -> bytes:
    checksum = zlib.crc32(chunk_type)
    checksum = zlib.crc32(payload, checksum)
    return struct.pack(">I", len(payload)) + chunk_type + payload + struct.pack(">I", checksum & 0xFFFFFFFF)


def _write_png(path: Path, width: int = 16, height: int = 16, bit_depth: int = 16) -> None:
    bytes_per_channel = bit_depth // 8
    rows = b"".join(b"\0" + bytes(width * 3 * bytes_per_channel) for _ in range(height))
    header = struct.pack(">IIBBBBB", width, height, bit_depth, 2, 0, 0, 0)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(rows))
        + _chunk(b"IEND", b"")
    )


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _profile_payload(scene_hash: str, *, frame_end: int = 6, audio_hash: str | None = None) -> dict[str, object]:
    return {
        "schemaVersion": "1.0.0",
        "project": "trip-to-andromeda",
        "preset": "space-journey",
        "profileId": "TEST-30-SDR",
        "blenderVersion": "5.2.0 LTS",
        "frameStart": 1,
        "frameEnd": frame_end,
        "fps": 30,
        "resolution": {"width": 16, "height": 16, "percentage": 100},
        "imageSequence": {
            "format": "PNG",
            "extension": "png",
            "bitDepth": 16,
            "colorMode": "RGB",
            "compression": 15,
            "filenamePattern": "frame_%06d.png",
            "colorManagement": {"displayTransformBaked": True},
        },
        "approvedSceneSha256": scene_hash,
        "chunking": {"framesPerChunk": 2, "rationale": "Tiny synthetic checkpoint test."},
        "storage": {
            "plannedFrameSequenceGiB": 71.281,
            "projectedMasterGiB": 15.677,
            "projectedDeliveryGiB": 0.487,
            "supportReserveGiB": 2.0,
            "contingencyMultiplier": 1.5,
            "minimumLaunchFreeGiB": 140.0,
        },
        "render": {
            "engine": "BLENDER_EEVEE",
            "samples": 64,
            "shadowPoolSize": "512",
            "motionBlur": False,
            "useCompositing": True,
            "filmTransparent": False,
            "ditherIntensity": 1.0,
        },
        "colorManagement": {
            "displayDevice": "sRGB",
            "viewTransform": "AgX",
            "look": "AgX - Medium High Contrast",
            "exposure": 0.0,
            "gamma": 1.0,
            "sequencerColorSpace": "sRGB",
        },
        "audio": {
            "sha256": audio_hash or ("A" * 64),
            "sampleRate": 48000,
            "channels": 2,
            "durationSeconds": frame_end / 30,
        },
        "encoding": {
            "master": {
                "container": "mov",
                "fileExtension": ".mov",
                "videoCodec": "prores_ks",
                "expectedVideoCodec": "prores",
                "profile": "3",
                "displayToDeliveryFilter": MASTER_COLOR_FILTER,
                "pixelFormat": "yuv422p10le",
                "audioCodec": "pcm_s24le",
                "color": {"primaries": "bt709", "transfer": "bt709", "space": "bt709", "range": "tv"},
            },
            "delivery": {
                "container": "mp4",
                "fileExtension": ".mp4",
                "videoCodec": "libx264",
                "expectedVideoCodec": "h264",
                "profile": "high",
                "displayToDeliveryFilter": DELIVERY_COLOR_FILTER,
                "preset": "slow",
                "crf": 16,
                "pixelFormat": "yuv420p",
                "audioCodec": "aac",
                "audioBitrate": "320k",
                "color": {"primaries": "bt709", "transfer": "bt709", "space": "bt709", "range": "tv"},
            },
        },
        "visualQa": {
            "namedFrames": [{"frame": 1, "role": "opening"}, {"frame": frame_end, "role": "outro"}],
            "sectionAndTransitionFrames": [2, 4],
            "highMotionRanges": [{"startFrame": 3, "endFrame": 4}],
        },
    }


def _builder_profile_payload(scene: Path, *, width: int = 16, height: int = 16) -> dict[str, object]:
    payload = _profile_payload(_sha(scene))
    payload.update(
        {
            "schemaVersion": "1.1.0",
            "kind": "trackprompt-render-profile",
            "id": "11111111-1111-1111-1111-111111111111",
            "displayName": "Synthetic Builder Profile",
            "templateId": "CUSTOM",
            "timestamps": {"createdAt": "2026-07-20T00:00:00Z", "updatedAt": "2026-07-20T00:00:00Z"},
            "createdAt": "2026-07-20T00:00:00Z",
            "updatedAt": "2026-07-20T00:00:00Z",
            "approvedScene": {
                "path": str(scene.absolute()),
                "sha256": _sha(scene),
                "manifestPath": None,
                "manifestSha256": None,
            },
            "timeline": {"frameStart": 1, "frameEnd": 6, "fps": 30, "durationSeconds": 0.2},
            "production": {
                "framesPerChunk": 2,
                "resumeEnabled": True,
                "resumePolicy": "validated-missing-frames-only",
                "verifyExistingFrames": True,
                "overwriteInvalidFrames": False,
                "overwriteValidFrames": False,
                "atomicChunkCommit": True,
                "stopOnValidationFailure": True,
                "maximumFramesPerChunk": 1200,
            },
            "dashboard": {"autoLaunch": False, "refreshSeconds": 10},
            "estimates": {"frameCount": 6, "chunkCount": 3},
            "validation": {"status": "valid"},
            "warnings": [],
            "authorization": {
                "status": "pending-operator-approval",
                "project": "TRIP-TO-ANDROMEDA",
                "preset": "SPACE-JOURNEY",
                "profile": "TEST-30-SDR",
                "reason": "Exact external scene/profile authorization is required.",
            },
            "profileSha256": "B" * 64,
            "integrity": {
                "algorithm": "SHA-256",
                "canonicalization": "sorted-json-v1",
                "profileSha256": "B" * 64,
            },
        }
    )
    payload["resolution"] = {
        "width": width,
        "height": height,
        "percentage": 100,
        "pixelAspect": "1:1",
    }
    payload["imageSequence"]["filenamePattern"] = "frame_{frame:000000}.png"  # type: ignore[index]
    return payload


@pytest.fixture
def render_inputs(tmp_path: Path) -> tuple[tooling.RenderProfile, Path, Path]:
    scene = tmp_path / "candidate.blend"
    scene.write_bytes(b"synthetic blend identity")
    audio = tmp_path / "approved-audio.wav"
    audio.write_bytes(b"synthetic audio identity")
    profile_path = tmp_path / "render-profile.final.json"
    profile_path.write_text(json.dumps(_profile_payload(_sha(scene), audio_hash=_sha(audio))), encoding="utf-8")
    return tooling.load_render_profile(profile_path), scene, profile_path


def _write_range(frames: Path, profile: tooling.RenderProfile, values: list[int]) -> None:
    frames.mkdir(parents=True, exist_ok=True)
    for frame in values:
        _write_png(frames / profile.image.filename(frame), profile.width, profile.height, profile.image.bit_depth)


def test_operator_stop_records_resumable_state_without_touching_frames(render_inputs: tuple[tooling.RenderProfile, Path, Path]) -> None:
    profile, scene, _ = render_inputs
    output = scene.parent / "operator-stop-output"
    tooling.render_plan(
        profile,
        scene,
        output,
        initialize=True,
        authorization_token=profile.authorization_token,
        require_authorization=True,
        chunk_size_override=None,
        chunk_rationale_override=None,
        workers=1,
    )
    before = list((output / "frames").iterdir())
    result = tooling.record_operator_stop(profile, scene, output, completed_start=None, completed_end=None)
    after = list((output / "frames").iterdir())
    manifest = json.loads((output / "manifests" / "render-manifest.json").read_text(encoding="utf-8"))
    assert result["status"] == "stopped-after-current-chunk-by-operator"
    assert manifest["runState"]["status"] == "stopped-after-current-chunk-by-operator"
    assert before == after == []


def test_builder_schema_1_1_normalizes_without_weakening_exact_file_identity(tmp_path: Path) -> None:
    scene = tmp_path / "builder-candidate.blend"
    scene.write_bytes(b"synthetic builder scene")
    profile_path = tmp_path / "builder-profile.json"
    profile_path.write_text(json.dumps(_builder_profile_payload(scene)), encoding="utf-8")

    profile = tooling.load_render_profile(profile_path)
    summary = tooling.profile_validation_summary(profile, scene)
    assert profile.schema_version == "1.1.0"
    assert profile.image.filename_pattern == "frame_%06d.png"
    assert profile.canonical_sha256 == "B" * 64
    assert summary["exactSavedFileSha256"] == _sha(profile_path)
    assert summary["informationalProfileSha256"] == "B" * 64
    assert summary["informationalHashIsAuthorizationIdentity"] is False
    assert summary["approvedScene"]["verified"] is True
    assert profile.authorization_token.startswith(
        "AUTHORIZE FULL RENDER: TRIP-TO-ANDROMEDA | SPACE-JOURNEY | TEST-30-SDR | "
    )
    assert f"PROFILE {_sha(profile_path)[:12]}" in profile.authorization_token

    original_token = profile.authorization_token
    payload = json.loads(profile_path.read_text(encoding="utf-8"))
    payload["warnings"] = ["Informational warning changed after the prior authorization."]
    profile_path.write_text(json.dumps(payload), encoding="utf-8")
    changed = tooling.load_render_profile(profile_path)
    assert changed.canonical_sha256 == profile.canonical_sha256
    assert changed.source_sha256 != profile.source_sha256
    assert changed.authorization_token != original_token


def test_builder_schema_rejects_disagreeing_integrity_metadata(tmp_path: Path) -> None:
    scene = tmp_path / "candidate.blend"
    scene.write_bytes(b"synthetic builder scene")
    payload = _builder_profile_payload(scene)
    payload["integrity"]["profileSha256"] = "C" * 64  # type: ignore[index]
    profile_path = tmp_path / "bad-integrity.json"
    profile_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(tooling.ToolingError, match="must agree"):
        tooling.load_render_profile(profile_path)


def test_builder_schema_rejects_unavailable_blender_52_setting(tmp_path: Path) -> None:
    scene = tmp_path / "candidate.blend"
    scene.write_bytes(b"synthetic builder scene")
    payload = _builder_profile_payload(scene)
    payload["render"]["highQualityNormals"] = True  # type: ignore[index]
    profile_path = tmp_path / "unsupported-setting.json"
    profile_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(tooling.ToolingError, match="no high-quality-normals"):
        tooling.load_render_profile(profile_path)


def test_builder_schema_requires_stop_on_validation_failure(tmp_path: Path) -> None:
    scene = tmp_path / "candidate.blend"
    scene.write_bytes(b"synthetic builder scene")
    payload = _builder_profile_payload(scene)
    payload["production"]["stopOnValidationFailure"] = False  # type: ignore[index]
    profile_path = tmp_path / "unsafe-stop-policy.json"
    profile_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(tooling.ToolingError, match="stopOnValidationFailure"):
        tooling.load_render_profile(profile_path)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("shadowRayCount", 5),
        ("shadowResolutionScale", 1.1),
        ("rayTracing", "yes"),
        ("rayTracingMethod", "BROKEN"),
        ("volumetricTileSize", "3"),
        ("volumetricSamples", 0),
        ("volumetricShadowSamples", 129),
        ("volumetricRayDepth", 17),
        ("volumetricShadows", "yes"),
        ("filmTransparent", True),
    ],
)
def test_builder_schema_rejects_renderer_incompatible_render_settings(
    tmp_path: Path, field: str, value: object
) -> None:
    scene = tmp_path / "candidate.blend"
    scene.write_bytes(b"synthetic builder scene")
    payload = _builder_profile_payload(scene)
    payload["render"][field] = value  # type: ignore[index]
    profile_path = tmp_path / f"invalid-{field}.json"
    profile_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(tooling.ToolingError):
        tooling.load_render_profile(profile_path)


def test_zero_shadow_resolution_scale_matches_blender_contract(tmp_path: Path) -> None:
    scene = tmp_path / "candidate.blend"
    scene.write_bytes(b"synthetic builder scene")
    payload = _builder_profile_payload(scene)
    payload["render"]["shadowResolutionScale"] = 0.0  # type: ignore[index]
    profile_path = tmp_path / "zero-shadow-scale.json"
    profile_path.write_text(json.dumps(payload), encoding="utf-8")
    assert tooling.load_render_profile(profile_path).raw["render"]["shadowResolutionScale"] == 0.0


def test_fog_glow_requires_enabled_compositor(tmp_path: Path) -> None:
    scene = tmp_path / "candidate.blend"
    scene.write_bytes(b"synthetic builder scene")
    payload = _builder_profile_payload(scene)
    payload["render"]["useCompositing"] = False  # type: ignore[index]
    payload["compositor"] = {
        "enabled": False,
        "name": "TP_SPACE_COMPOSITOR",
        "fogGlow": True,
        "fogGlowEnabled": True,
        "fogGlowQuality": "HIGH",
        "fogGlowThreshold": 1.0,
        "fogGlowStrength": 0.4,
        "fogGlowSize": 0.7,
        "fogGlowIterations": 3,
    }
    profile_path = tmp_path / "disabled-compositor-glow.json"
    profile_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(tooling.ToolingError, match="must be false"):
        tooling.load_render_profile(profile_path)


def test_builder_pixel_aspect_and_frames_subdirectory_are_resume_contract_fields(tmp_path: Path) -> None:
    scene = tmp_path / "candidate.blend"
    scene.write_bytes(b"synthetic builder scene")
    payload = _builder_profile_payload(scene)
    resolution = payload["resolution"]  # type: ignore[assignment]
    resolution["pixelAspectX"] = 1.5  # type: ignore[index]
    resolution["pixelAspectY"] = 1.25  # type: ignore[index]
    payload["output"] = {"framesSubdirectory": "published-frames"}
    profile_path = tmp_path / "anamorphic-profile.json"
    profile_path.write_text(json.dumps(payload), encoding="utf-8")
    profile = tooling.load_render_profile(profile_path)

    assert profile.pixel_aspect_x == 1.5
    assert profile.pixel_aspect_y == 1.25
    assert profile.frames_subdirectory == "published-frames"
    output = tmp_path / "managed-output"
    plan = tooling.render_plan(
        profile,
        scene,
        output,
        initialize=True,
        authorization_token=profile.authorization_token,
        require_authorization=True,
        chunk_size_override=None,
        chunk_rationale_override=None,
        workers=1,
    )
    assert Path(plan["framesDirectory"]) == output / "published-frames"
    assert (output / "published-frames").is_dir()
    assert not (output / "frames").exists()
    manifest_path = output / "manifests" / "render-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["frameContract"]["pixelAspectX"] == 1.5
    assert manifest["frameContract"]["pixelAspectY"] == 1.25
    assert manifest["frameContract"]["framesSubdirectory"] == "published-frames"

    manifest["frameContract"]["pixelAspectX"] = 1.0
    manifest["frameContract"]["framesSubdirectory"] = "frames"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    codes = {
        mismatch["code"]
        for mismatch in tooling.output_compatibility(profile, scene, output)["mismatches"]
    }
    assert {"pixel-aspect-mismatch", "frames-subdirectory-mismatch"} <= codes


@pytest.mark.parametrize("pixel_aspect", [0, -1, float("inf"), "1.0"])
def test_builder_profile_rejects_invalid_pixel_aspect(tmp_path: Path, pixel_aspect: object) -> None:
    scene = tmp_path / "candidate.blend"
    scene.write_bytes(b"synthetic builder scene")
    payload = _builder_profile_payload(scene)
    payload["resolution"]["pixelAspectX"] = pixel_aspect  # type: ignore[index]
    profile_path = tmp_path / "invalid-pixel-aspect.json"
    profile_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(tooling.ToolingError, match="pixelAspectX"):
        tooling.load_render_profile(profile_path)


@pytest.mark.parametrize("subdirectory", ["../frames", "frames/other", "logs", "CON", ".hidden"])
def test_builder_profile_rejects_unsafe_frames_subdirectory(tmp_path: Path, subdirectory: str) -> None:
    scene = tmp_path / "candidate.blend"
    scene.write_bytes(b"synthetic builder scene")
    payload = _builder_profile_payload(scene)
    payload["output"] = {"framesSubdirectory": subdirectory}
    profile_path = tmp_path / "invalid-frame-directory.json"
    profile_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(tooling.ToolingError, match="filesystem-safe"):
        tooling.load_render_profile(profile_path)


def test_blender_applies_profile_color_management_and_fog_glow_inputs() -> None:
    color_scene = SimpleNamespace(
        display_settings=SimpleNamespace(display_device="Display P3"),
        view_settings=SimpleNamespace(view_transform="Standard", look="None", exposure=3.0, gamma=2.0),
        sequencer_colorspace_settings=SimpleNamespace(name="Linear"),
    )
    color_settings = {
        "displayDevice": "sRGB",
        "viewTransform": "AgX",
        "look": "AgX - Medium High Contrast",
        "exposure": 0.25,
        "gamma": 1.1,
        "sequencerColorSpace": "sRGB",
    }
    blender_chunk._apply_color_management(color_scene, color_settings)  # noqa: SLF001
    assert color_scene.display_settings.display_device == "sRGB"
    assert color_scene.view_settings.view_transform == "AgX"
    assert color_scene.view_settings.look == "AgX - Medium High Contrast"
    assert color_scene.view_settings.exposure == 0.25
    assert color_scene.view_settings.gamma == 1.1
    assert color_scene.sequencer_colorspace_settings.name == "sRGB"

    sockets = {
        name: SimpleNamespace(default_value=None)
        for name in ("Type", "Quality", "Threshold", "Strength", "Size", "Iterations")
    }
    glow = SimpleNamespace(
        type="GLARE",
        bl_idname="CompositorNodeGlare",
        name="TP_CONTROLLED_GLOW",
        inputs=sockets,
        mute=True,
    )
    compositor_scene = SimpleNamespace(
        render=SimpleNamespace(use_compositing=False),
        node_tree=None,
        compositing_node_group=SimpleNamespace(name="TP_SPACE_COMPOSITOR", nodes=[glow]),
    )
    compositor = {
        "enabled": True,
        "name": "TP_SPACE_COMPOSITOR",
        "fogGlow": True,
        "fogGlowEnabled": True,
        "fogGlowQuality": "HIGH",
        "fogGlowThreshold": 1.026,
        "fogGlowStrength": 0.418,
        "fogGlowSize": 0.704,
        "fogGlowIterations": 3,
    }
    blender_chunk._apply_compositor_profile(  # noqa: SLF001
        compositor_scene,
        {"useCompositing": True},
        {"compositor": compositor},
    )
    assert compositor_scene.render.use_compositing is True
    assert glow.mute is False
    assert sockets["Type"].default_value == "Fog Glow"
    assert sockets["Quality"].default_value == "High"
    assert sockets["Threshold"].default_value == 1.026
    assert sockets["Strength"].default_value == 0.418
    assert sockets["Size"].default_value == 0.704
    assert sockets["Iterations"].default_value == 3

    compositor["fogGlow"] = False
    compositor["fogGlowEnabled"] = False
    blender_chunk._apply_compositor_profile(  # noqa: SLF001
        compositor_scene,
        {"useCompositing": True},
        {"compositor": compositor},
    )
    assert glow.mute is True


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("displayDevice", "ACES", "displayDevice"),
        ("viewTransform", "Khronos PBR Neutral", "viewTransform"),
        ("sequencerColorSpace", "Linear", "sequencerColorSpace"),
    ],
)
def test_profile_rejects_unavailable_blender_52_color_management_values(
    tmp_path: Path,
    field: str,
    value: str,
    message: str,
) -> None:
    scene = tmp_path / "candidate.blend"
    scene.write_bytes(b"synthetic builder scene")
    payload = _builder_profile_payload(scene)
    payload["colorManagement"][field] = value  # type: ignore[index]
    profile_path = tmp_path / "invalid-color-management.json"
    profile_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(tooling.ToolingError, match=message):
        tooling.load_render_profile(profile_path)


def test_profile_rejects_look_incompatible_with_view_transform(tmp_path: Path) -> None:
    scene = tmp_path / "candidate.blend"
    scene.write_bytes(b"synthetic builder scene")
    payload = _builder_profile_payload(scene)
    payload["colorManagement"].update(  # type: ignore[index]
        {"viewTransform": "Standard", "look": "AgX - Medium High Contrast"}
    )
    profile_path = tmp_path / "invalid-view-look.json"
    profile_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(tooling.ToolingError, match="incompatible"):
        tooling.load_render_profile(profile_path)


def test_blender_fog_glow_reports_unsupported_explicit_input() -> None:
    glow = SimpleNamespace(
        type="GLARE",
        name="TP_CONTROLLED_GLOW",
        inputs={"Type": SimpleNamespace(default_value=None)},
        mute=False,
    )
    scene = SimpleNamespace(
        render=SimpleNamespace(use_compositing=False),
        node_tree=SimpleNamespace(name="TP_SPACE_COMPOSITOR", nodes=[glow]),
    )
    with pytest.raises(tooling.ToolingError) as captured:
        blender_chunk._apply_compositor_profile(  # noqa: SLF001
            scene,
            {"useCompositing": True},
            {
                "compositor": {
                    "enabled": True,
                    "name": "TP_SPACE_COMPOSITOR",
                    "fogGlowEnabled": True,
                    "fogGlowStrength": 0.4,
                }
            },
        )
    assert captured.value.code == "unsupported-compositor-setting"


def test_validate_profile_cli_reports_raw_and_informational_hashes(tmp_path: Path) -> None:
    scene = tmp_path / "candidate.blend"
    scene.write_bytes(b"synthetic builder scene")
    profile_path = tmp_path / "builder-profile.json"
    profile_path.write_text(json.dumps(_builder_profile_payload(scene)), encoding="utf-8")
    completed = subprocess.run(
        [
            sys.executable,
            str(Path(tooling.__file__).resolve()),
            "validate-profile",
            "--profile",
            str(profile_path),
            "--scene",
            str(scene),
        ],
        shell=False,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["ok"] is True
    assert payload["schemaVersion"] == "1.1.0"
    assert payload["exactSavedFileSha256"] == _sha(profile_path)
    assert payload["informationalProfileSha256"] == "B" * 64
    assert payload["informationalHashIsAuthorizationIdentity"] is False


def test_output_compatibility_reports_exact_resume_contract_mismatches(
    render_inputs: tuple[tooling.RenderProfile, Path, Path]
) -> None:
    profile, scene, _profile_path = render_inputs
    output = scene.parent / "compatibility-output"
    tooling.render_plan(
        profile,
        scene,
        output,
        initialize=True,
        authorization_token=profile.authorization_token,
        require_authorization=True,
        chunk_size_override=None,
        chunk_rationale_override=None,
        workers=1,
    )
    matching = tooling.output_compatibility(profile, scene, output)
    assert matching["compatible"] is True
    assert matching["status"] == "matching-output"

    manifest_path = output / "manifests" / "render-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for legacy_default_field in ("pixelAspectX", "pixelAspectY", "framesSubdirectory"):
        del manifest["frameContract"][legacy_default_field]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    assert tooling.output_compatibility(profile, scene, output)["compatible"] is True
    manifest["frameContract"].update(
        {"width": 3840, "height": 2160, "fps": 60, "format": "OPEN_EXR", "frameEnd": 7, "frameCount": 7}
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    incompatible = tooling.output_compatibility(profile, scene, output)
    codes = {item["code"] for item in incompatible["mismatches"]}
    assert incompatible["compatible"] is False
    assert {"resolution-mismatch", "fps-mismatch", "image-format-mismatch", "frame-range-mismatch"} <= codes
    with pytest.raises(tooling.ToolingError, match="does not match"):
        tooling.render_plan(
            profile,
            scene,
            output,
            initialize=False,
            authorization_token=None,
            require_authorization=False,
            chunk_size_override=None,
            chunk_rationale_override=None,
            workers=1,
        )


def test_output_compatibility_rejects_changed_profile_file_hash(
    render_inputs: tuple[tooling.RenderProfile, Path, Path]
) -> None:
    profile, scene, profile_path = render_inputs
    output = scene.parent / "profile-hash-output"
    tooling.render_plan(
        profile,
        scene,
        output,
        initialize=True,
        authorization_token=profile.authorization_token,
        require_authorization=True,
        chunk_size_override=None,
        chunk_rationale_override=None,
        workers=1,
    )
    payload = json.loads(profile_path.read_text(encoding="utf-8"))
    payload["chunking"]["rationale"] = "Edited profile content."  # type: ignore[index]
    profile_path.write_text(json.dumps(payload), encoding="utf-8")
    changed = tooling.load_render_profile(profile_path)
    compatibility = tooling.output_compatibility(changed, scene, output)
    assert compatibility["compatible"] is False
    assert "profile-hash-mismatch" in {item["code"] for item in compatibility["mismatches"]}


def test_argument_parser_and_exact_authorization_gate(render_inputs: tuple[tooling.RenderProfile, Path, Path]) -> None:
    profile, scene, profile_path = render_inputs
    arguments = tooling._build_parser().parse_args(  # noqa: SLF001 - focused CLI contract test
        [
            "render-plan",
            "--profile",
            str(profile_path),
            "--scene",
            str(scene),
            "--output",
            str(scene.parent / "output"),
        ]
    )
    assert arguments.command == "render-plan"
    assert profile.authorization_token == (
        "AUTHORIZE FULL RENDER: TRIP TO ANDROMEDA | SPACE-JOURNEY | TEST-30-SDR | "
        f"SCENE {_sha(scene)[:12]} | PROFILE {profile.source_sha256[:12]}"
    )
    with pytest.raises(tooling.ToolingError, match="exact scene-specific"):
        tooling.validate_authorization(profile, profile.authorization_token + " ")
    tooling.validate_authorization(profile, profile.authorization_token)


def test_executable_path_allows_windows_execution_only_package_acl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = (tmp_path / "ffprobe.exe").absolute()
    executable.write_bytes(b"fixture")
    original_resolve = Path.resolve

    def permission_denied_resolve(path: Path, strict: bool = False) -> Path:
        if path == executable:
            raise PermissionError("synthetic WinGet ACL")
        return original_resolve(path, strict=strict)

    monkeypatch.setattr(Path, "resolve", permission_denied_resolve)
    assert tooling._absolute_executable_path(executable, "ffprobe executable") == executable  # noqa: SLF001


@pytest.mark.parametrize("filter_value", [None, "colorspace=ispace=gbr;movie=unreviewed.mov"])
def test_png_profile_requires_bounded_display_to_delivery_filter(tmp_path: Path, filter_value: str | None) -> None:
    scene = tmp_path / "candidate.blend"
    scene.write_bytes(b"synthetic blend identity")
    payload = _profile_payload(_sha(scene))
    master = payload["encoding"]["master"]  # type: ignore[index]
    if filter_value is None:
        del master["displayToDeliveryFilter"]  # type: ignore[index]
    else:
        master["displayToDeliveryFilter"] = filter_value  # type: ignore[index]
    profile_path = tmp_path / "invalid-filter-profile.json"
    profile_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(tooling.ToolingError, match="displayToDeliveryFilter"):
        tooling.load_render_profile(profile_path)


def test_storage_policy_exact_projection_and_boundaries(tmp_path: Path) -> None:
    scene = tmp_path / "candidate.blend"
    scene.write_bytes(b"synthetic blend identity")
    profile_path = tmp_path / "production-profile.json"
    profile_path.write_text(json.dumps(_profile_payload(_sha(scene), frame_end=13029)), encoding="utf-8")
    profile = tooling.load_render_profile(profile_path)
    assert profile.storage.as_dict() == {
        "plannedFrameSequenceBytes": 76537390957,
        "projectedMasterBytes": 16833050575,
        "projectedDeliveryBytes": 522912269,
        "supportReserveBytes": 2147483648,
        "contingencyMultiplier": 1.5,
        "minimumLaunchFreeBytes": 150323855360,
    }
    assert tooling.storage_requirement(profile, 13029)["requiredFreeBytes"] == 144061256174
    assert tooling.storage_requirement(profile, 12729)["requiredFreeBytes"] == 141417781931
    assert tooling.storage_requirement(profile, 0)["requiredFreeBytes"] == 29255169738
    with pytest.raises(tooling.ToolingError, match="Remaining frame count"):
        tooling.storage_requirement(profile, 13030)


def test_dry_run_is_read_only_and_chunk_boundaries_are_inclusive(
    render_inputs: tuple[tooling.RenderProfile, Path, Path]
) -> None:
    profile, scene, _profile_path = render_inputs
    output = scene.parent / "dry-run-output"
    result = tooling.render_plan(
        profile,
        scene,
        output,
        initialize=False,
        authorization_token=None,
        require_authorization=False,
        chunk_size_override=None,
        chunk_rationale_override=None,
        workers=1,
    )
    assert not output.exists()
    assert result["chunks"] == [
        {"startFrame": 1, "endFrame": 2, "frameCount": 2},
        {"startFrame": 3, "endFrame": 4, "frameCount": 2},
        {"startFrame": 5, "endFrame": 6, "frameCount": 2},
    ]
    assert result["expectedAuthorizationToken"] == profile.authorization_token
    assert len(result["storage"]["chunkLaunchRequirements"]) == len(result["chunks"])
    assert result["storage"]["chunkLaunchRequirements"][0]["requiredFreeBytes"] == 144061256174


def test_initialization_requires_authorization_and_chunk_override_cannot_grow_profile(
    render_inputs: tuple[tooling.RenderProfile, Path, Path]
) -> None:
    profile, scene, _profile_path = render_inputs
    unauthorized_output = scene.parent / "unauthorized-output"
    with pytest.raises(tooling.ToolingError, match="requires exact operator authorization"):
        tooling.render_plan(
            profile,
            scene,
            unauthorized_output,
            initialize=True,
            authorization_token=None,
            require_authorization=False,
            chunk_size_override=None,
            chunk_rationale_override=None,
            workers=1,
        )
    assert not unauthorized_output.exists()
    with pytest.raises(tooling.ToolingError, match="exceeds the reviewed profile chunk size"):
        tooling.render_plan(
            profile,
            scene,
            scene.parent / "oversized-chunks",
            initialize=False,
            authorization_token=None,
            require_authorization=False,
            chunk_size_override=profile.chunk_size + 1,
            chunk_rationale_override="Unreviewed larger chunks.",
            workers=1,
        )


def test_authorized_initialization_creates_manifest_without_rendering(
    render_inputs: tuple[tooling.RenderProfile, Path, Path]
) -> None:
    profile, scene, _profile_path = render_inputs
    output = scene.parent / "production"
    result = tooling.render_plan(
        profile,
        scene,
        output,
        initialize=True,
        authorization_token=profile.authorization_token,
        require_authorization=True,
        chunk_size_override=None,
        chunk_rationale_override=None,
        workers=1,
    )
    manifest = json.loads((output / "manifests" / "render-manifest.json").read_text(encoding="utf-8"))
    assert result["mode"] == "initialized"
    assert manifest["status"] == "incomplete"
    assert manifest["authorization"]["status"] == "operator-token-accepted"
    assert "AUTHORIZE FULL RENDER" not in json.dumps(manifest)
    assert (output / "frames").is_dir()
    assert list(output.rglob("*.png")) == []


def test_pending_authorization_blocks_chunk_mutation_and_encoding(
    render_inputs: tuple[tooling.RenderProfile, Path, Path]
) -> None:
    profile, scene, _profile_path = render_inputs
    output = scene.parent / "pending-auth-output"
    tooling.render_plan(
        profile,
        scene,
        output,
        initialize=True,
        authorization_token=profile.authorization_token,
        require_authorization=True,
        chunk_size_override=None,
        chunk_rationale_override=None,
        workers=1,
    )
    manifest_path = output / "manifests" / "render-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["authorization"]["status"] = "pending-operator-approval"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    unchanged = manifest_path.read_bytes()
    with pytest.raises(tooling.ToolingError, match="does not record acceptance"):
        tooling.record_chunk_failure(
            profile,
            scene,
            output,
            start=1,
            end=2,
            exit_code=1,
            stdout_log=None,
            stderr_log=None,
        )
    with pytest.raises(tooling.ToolingError, match="does not record acceptance"):
        tooling.encode_preflight(
            profile,
            scene,
            output,
            scene.parent / "approved-audio.wav",
            scene,
            output / "delivery" / "blocked.mp4",
            kind="delivery",
            workers=1,
        )
    assert manifest_path.read_bytes() == unchanged


def test_changed_profile_cannot_reuse_authorized_manifest(
    render_inputs: tuple[tooling.RenderProfile, Path, Path]
) -> None:
    profile, scene, profile_path = render_inputs
    output = scene.parent / "profile-bound-output"
    tooling.render_plan(
        profile,
        scene,
        output,
        initialize=True,
        authorization_token=profile.authorization_token,
        require_authorization=True,
        chunk_size_override=None,
        chunk_rationale_override=None,
        workers=1,
    )
    manifest_path = output / "manifests" / "render-manifest.json"
    unchanged = manifest_path.read_bytes()
    payload = json.loads(profile_path.read_text(encoding="utf-8"))
    payload["chunking"]["rationale"] = "Changed after authorization."
    profile_path.write_text(json.dumps(payload), encoding="utf-8")
    changed_profile = tooling.load_render_profile(profile_path)
    assert changed_profile.authorization_token != profile.authorization_token
    with pytest.raises(tooling.ToolingError, match="renderProfile hash"):
        tooling.render_plan(
            changed_profile,
            scene,
            output,
            initialize=False,
            authorization_token=None,
            require_authorization=False,
            chunk_size_override=None,
            chunk_rationale_override=None,
            workers=1,
        )
    assert manifest_path.read_bytes() == unchanged


def test_missing_corrupt_wrong_resolution_and_boundary_detection(
    render_inputs: tuple[tooling.RenderProfile, Path, Path]
) -> None:
    profile, _scene, _profile_path = render_inputs
    frames = profile.source_path.parent / "frames"
    _write_range(frames, profile, [1, 2, 6])
    corrupt = frames / profile.image.filename(2)
    data = bytearray(corrupt.read_bytes())
    data[-8] ^= 0x01
    corrupt.write_bytes(data)
    _write_png(frames / profile.image.filename(6), width=17, height=16, bit_depth=16)
    scan = tooling.scan_frames(frames, profile, workers=1)
    assert 1 in scan.valid
    assert {item.frame: item.error for item in scan.invalid} == {2: "corrupt-frame", 6: "wrong-frame-dimensions"}
    assert scan.missing == [2, 3, 4, 5, 6]
    assert scan.summary()["missingRanges"] == [{"startFrame": 2, "endFrame": 6, "frameCount": 5}]


def test_duplicate_and_noncanonical_frame_names_are_rejected(
    render_inputs: tuple[tooling.RenderProfile, Path, Path]
) -> None:
    profile, _scene, _profile_path = render_inputs
    frames = profile.source_path.parent / "duplicates"
    _write_range(frames, profile, [1])
    _write_png(frames / "frame_1.png")
    scan = tooling.scan_frames(frames, profile, workers=1)
    assert scan.duplicates == {1: ["frame_000001.png", "frame_1.png"]}
    assert "frame_1.png" in scan.unexpected
    assert not scan.complete


def _quarantine_profile(tmp_path: Path, *, enabled: bool) -> tuple[tooling.RenderProfile, Path, Path]:
    scene = tmp_path / "quarantine-candidate.blend"
    scene.write_bytes(b"synthetic quarantine scene")
    payload = _builder_profile_payload(scene)
    payload["production"]["overwriteInvalidFrames"] = enabled  # type: ignore[index]
    profile_path = tmp_path / "quarantine-profile.json"
    profile_path.write_text(json.dumps(payload), encoding="utf-8")
    return tooling.load_render_profile(profile_path), scene, profile_path


def _initialize_quarantine_output(profile: tooling.RenderProfile, scene: Path, output: Path) -> None:
    tooling.render_plan(
        profile,
        scene,
        output,
        initialize=True,
        authorization_token=profile.authorization_token,
        require_authorization=True,
        chunk_size_override=None,
        chunk_rationale_override=None,
        workers=1,
    )


def test_authorized_invalid_frame_quarantine_is_recoverable_and_resume_safe(tmp_path: Path) -> None:
    profile, scene, _profile_path = _quarantine_profile(tmp_path, enabled=True)
    output = tmp_path / "quarantine-output"
    _initialize_quarantine_output(profile, scene, output)
    frames = output / profile.frames_subdirectory
    _write_range(frames, profile, [1, 2])
    valid_hash = _sha(frames / profile.image.filename(1))
    corrupt = frames / profile.image.filename(2)
    corrupt.write_bytes(corrupt.read_bytes()[:-8] + b"not-a-png")
    corrupt_hash = _sha(corrupt)

    result = tooling.quarantine_invalid_frames(
        profile,
        scene,
        output,
        profile.authorization_token,
        workers=1,
    )

    assert result["quarantinedFrameCount"] == 1
    assert result["quarantinedFrames"][0]["frame"] == 2
    assert result["quarantinedFrames"][0]["sha256"] == corrupt_hash
    assert _sha(frames / profile.image.filename(1)) == valid_hash
    assert not corrupt.exists()
    quarantine_manifest = Path(result["quarantineManifest"])
    assert quarantine_manifest.is_file()
    quarantine_payload = json.loads(quarantine_manifest.read_text(encoding="utf-8"))
    moved = output / quarantine_payload["frames"][0]["quarantinePath"]
    assert moved.is_file()
    assert _sha(moved) == corrupt_hash
    manifest = json.loads((output / "manifests" / "render-manifest.json").read_text(encoding="utf-8"))
    assert manifest["quarantines"][-1]["frames"] == [2]

    resumed = tooling.render_plan(
        profile,
        scene,
        output,
        initialize=False,
        authorization_token=None,
        require_authorization=False,
        chunk_size_override=None,
        chunk_rationale_override=None,
        workers=1,
    )
    assert resumed["renderable"] is True
    assert resumed["chunks"][0] == {"startFrame": 2, "endFrame": 3, "frameCount": 2}


def test_disabled_invalid_frame_quarantine_leaves_corrupt_frame_and_blocks_plan(tmp_path: Path) -> None:
    profile, scene, _profile_path = _quarantine_profile(tmp_path, enabled=False)
    output = tmp_path / "disabled-quarantine-output"
    _initialize_quarantine_output(profile, scene, output)
    corrupt = output / profile.frames_subdirectory / profile.image.filename(2)
    corrupt.write_bytes(b"corrupt canonical frame")
    original = corrupt.read_bytes()

    result = tooling.quarantine_invalid_frames(
        profile,
        scene,
        output,
        profile.authorization_token,
        workers=1,
    )
    assert result["enabled"] is False
    assert result["quarantinedFrameCount"] == 0
    assert corrupt.read_bytes() == original
    assert not list((output / "checkpoints").glob("quarantine-invalid-*"))
    blocked = tooling.render_plan(
        profile,
        scene,
        output,
        initialize=False,
        authorization_token=None,
        require_authorization=False,
        chunk_size_override=None,
        chunk_rationale_override=None,
        workers=1,
    )
    assert blocked["renderable"] is False
    assert blocked["chunks"] == []


def test_invalid_frame_quarantine_rejects_wrong_authorization_without_mutation(tmp_path: Path) -> None:
    profile, scene, _profile_path = _quarantine_profile(tmp_path, enabled=True)
    output = tmp_path / "wrong-token-output"
    _initialize_quarantine_output(profile, scene, output)
    corrupt = output / profile.frames_subdirectory / profile.image.filename(2)
    corrupt.write_bytes(b"corrupt canonical frame")
    original = corrupt.read_bytes()
    with pytest.raises(tooling.ToolingError, match="exact scene-specific"):
        tooling.quarantine_invalid_frames(profile, scene, output, "wrong token", workers=1)
    assert corrupt.read_bytes() == original
    assert not list((output / "checkpoints").glob("quarantine-invalid-*"))


def test_invalid_frame_quarantine_refuses_ambiguous_names_before_moving(tmp_path: Path) -> None:
    profile, scene, _profile_path = _quarantine_profile(tmp_path, enabled=True)
    output = tmp_path / "ambiguous-quarantine-output"
    _initialize_quarantine_output(profile, scene, output)
    frames = output / profile.frames_subdirectory
    canonical = frames / profile.image.filename(2)
    canonical.write_bytes(b"corrupt canonical frame")
    _write_png(frames / "frame_2.png")
    original = canonical.read_bytes()
    with pytest.raises(tooling.ToolingError, match="duplicate or noncanonical"):
        tooling.quarantine_invalid_frames(
            profile,
            scene,
            output,
            profile.authorization_token,
            workers=1,
        )
    assert canonical.read_bytes() == original
    assert (frames / "frame_2.png").is_file()
    assert not list((output / "checkpoints").glob("quarantine-invalid-*"))


def test_invalid_frame_quarantine_rejects_linked_checkpoints_before_moving(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile, scene, _profile_path = _quarantine_profile(tmp_path, enabled=True)
    output = tmp_path / "linked-checkpoint-output"
    _initialize_quarantine_output(profile, scene, output)
    frames = output / profile.frames_subdirectory
    corrupt = frames / profile.image.filename(2)
    corrupt.write_bytes(b"corrupt canonical frame")
    original = corrupt.read_bytes()
    checkpoints = output / "checkpoints"
    original_is_symlink = Path.is_symlink

    def report_linked(path: Path) -> bool:
        return path == checkpoints or original_is_symlink(path)

    monkeypatch.setattr(Path, "is_symlink", report_linked)
    with pytest.raises(tooling.ToolingError, match="direct regular directory"):
        tooling.quarantine_invalid_frames(
            profile,
            scene,
            output,
            profile.authorization_token,
            workers=1,
        )
    assert corrupt.read_bytes() == original
    assert not list(checkpoints.glob("quarantine-invalid-*"))


def test_frame_13029_and_resume_planning(render_inputs: tuple[tooling.RenderProfile, Path, Path]) -> None:
    _profile, scene, profile_path = render_inputs
    profile_path.write_text(json.dumps(_profile_payload(_sha(scene), frame_end=13029)), encoding="utf-8")
    boundary_profile = tooling.load_render_profile(profile_path)
    frames = profile_path.parent / "boundary-frames"
    _write_range(frames, boundary_profile, [1, 13029])
    scan = tooling.scan_frames(frames, boundary_profile, workers=1)
    assert {1, 13029}.issubset(scan.valid)
    assert scan.summary()["missingRanges"] == [{"startFrame": 2, "endFrame": 13028, "frameCount": 13027}]

    tiny_profile_path = profile_path.parent / "tiny-profile.json"
    tiny_profile_path.write_text(json.dumps(_profile_payload(_sha(scene))), encoding="utf-8")
    tiny_profile = tooling.load_render_profile(tiny_profile_path)
    output = profile_path.parent / "resume-output"
    tooling.render_plan(
        tiny_profile,
        scene,
        output,
        initialize=True,
        authorization_token=tiny_profile.authorization_token,
        require_authorization=True,
        chunk_size_override=None,
        chunk_rationale_override=None,
        workers=1,
    )
    _write_range(output / "frames", tiny_profile, [1, 2, 5])
    resumed = tooling.render_plan(
        tiny_profile,
        scene,
        output,
        initialize=False,
        authorization_token=None,
        require_authorization=False,
        chunk_size_override=None,
        chunk_rationale_override=None,
        workers=1,
    )
    assert resumed["chunks"] == [
        {"startFrame": 3, "endFrame": 4, "frameCount": 2},
        {"startFrame": 6, "endFrame": 6, "frameCount": 1},
    ]


def test_hash_containment_and_no_overwrite_guards(render_inputs: tuple[tooling.RenderProfile, Path, Path]) -> None:
    profile, scene, _profile_path = render_inputs
    original = scene.read_bytes()
    scene.write_bytes(original + b"changed")
    with pytest.raises(tooling.ToolingError, match="hash"):
        tooling.validate_scene(profile, scene)
    scene.write_bytes(original)

    unsafe_output = scene.parent
    with pytest.raises(tooling.ToolingError, match="inside the production output"):
        tooling.render_plan(
            profile,
            scene,
            unsafe_output,
            initialize=False,
            authorization_token=None,
            require_authorization=False,
            chunk_size_override=None,
            chunk_rationale_override=None,
            workers=1,
        )
    unmanaged = scene.parent / "unmanaged"
    unmanaged.mkdir()
    (unmanaged / "operator-owned.txt").write_text("preserve", encoding="utf-8")
    with pytest.raises(tooling.ToolingError, match="refusing to reuse"):
        tooling.render_plan(
            profile,
            scene,
            unmanaged,
            initialize=True,
            authorization_token=profile.authorization_token,
            require_authorization=True,
            chunk_size_override=None,
            chunk_rationale_override=None,
            workers=1,
        )
    assert (unmanaged / "operator-owned.txt").read_text(encoding="utf-8") == "preserve"


def test_commit_chunk_is_atomic_and_refuses_existing_destination(
    render_inputs: tuple[tooling.RenderProfile, Path, Path]
) -> None:
    profile, scene, _profile_path = render_inputs
    output = scene.parent / "commit-output"
    tooling.render_plan(
        profile,
        scene,
        output,
        initialize=True,
        authorization_token=profile.authorization_token,
        require_authorization=True,
        chunk_size_override=None,
        chunk_rationale_override=None,
        workers=1,
    )
    inflight = output / "checkpoints" / ".inflight-test" / "frames"
    _write_range(inflight, profile, [1, 2])
    result = tooling.commit_chunk(
        profile,
        scene,
        output,
        inflight,
        start=1,
        end=2,
        stdout_log=None,
        stderr_log=None,
        workers=1,
    )
    assert result["publishedFrames"] == [1, 2]
    assert (output / "frames" / "frame_000001.png").is_file()
    assert list((output / "checkpoints").glob("chunk_000001_000002_*.json"))
    manifest = json.loads((output / "manifests" / "render-manifest.json").read_text(encoding="utf-8"))
    assert set(manifest["frameIndex"]) == {"000001", "000002"}

    second = output / "checkpoints" / ".inflight-second" / "frames"
    _write_range(second, profile, [1, 2])
    with pytest.raises(tooling.ToolingError, match="already exists"):
        tooling.commit_chunk(
            profile,
            scene,
            output,
            second,
            start=1,
            end=2,
            stdout_log=None,
            stderr_log=None,
            workers=1,
        )
    assert (output / "frames" / "frame_000001.png").is_file()


def test_encoding_refuses_incomplete_sequence_before_starting_ffmpeg(
    render_inputs: tuple[tooling.RenderProfile, Path, Path]
) -> None:
    profile, scene, _profile_path = render_inputs
    output = scene.parent / "encode-incomplete"
    tooling.render_plan(
        profile,
        scene,
        output,
        initialize=True,
        authorization_token=profile.authorization_token,
        require_authorization=True,
        chunk_size_override=None,
        chunk_rationale_override=None,
        workers=1,
    )
    with pytest.raises(tooling.ToolingError) as captured:
        tooling.encode_preflight(
            profile,
            scene,
            output,
            scene.parent / "missing-audio.wav",
            scene.parent / "missing-ffprobe.exe",
            output / "delivery" / "final.mp4",
            kind="delivery",
            workers=1,
        )
    assert captured.value.code == "incomplete-frame-sequence"


def test_encode_arguments_preserve_frames_and_audio_without_shortest(
    render_inputs: tuple[tooling.RenderProfile, Path, Path]
) -> None:
    profile, scene, _profile_path = render_inputs
    frames = scene.parent / "encode-frames"
    frames.mkdir()
    audio = scene.parent / "audio.wav"
    audio.write_bytes(b"synthetic audio identity")
    temporary = scene.parent / "temporary.mp4"
    arguments = tooling.build_encode_arguments(profile, frames, audio, temporary, kind="delivery")
    assert arguments[0] == "-n"
    assert "-shortest" not in arguments
    assert [arguments[index + 1] for index, value in enumerate(arguments) if value == "-map"] == ["0:v:0", "1:a:0"]
    assert arguments[-1] == str(temporary.resolve())
    assert "-frames:v" in arguments and str(profile.frame_count) in arguments
    assert arguments[arguments.index("-vf") + 1] == DELIVERY_COLOR_FILTER
    assert arguments[arguments.index("-profile:v") + 1] == "high"
    assert arguments[arguments.index("-color_range") + 1] == "tv"
    assert arguments[arguments.index("-movflags") + 1] == "+faststart"


def test_disabled_encoding_blocks_validate_but_rejects_encode_invocation(tmp_path: Path) -> None:
    scene = tmp_path / "candidate.blend"
    scene.write_bytes(b"synthetic builder scene")
    payload = _builder_profile_payload(scene)
    payload["encoding"] = {"master": {"enabled": False}, "delivery": {"enabled": False}}
    profile_path = tmp_path / "encoding-disabled.json"
    profile_path.write_text(json.dumps(payload), encoding="utf-8")
    profile = tooling.load_render_profile(profile_path)

    for kind in ("master", "delivery"):
        with pytest.raises(tooling.ToolingError) as captured:
            tooling.build_encode_arguments(
                profile,
                tmp_path / "frames-does-not-need-to-exist",
                tmp_path / "audio-does-not-need-to-exist.wav",
                tmp_path / f"disabled-{kind}.partial",
                kind=kind,
            )
        assert captured.value.code == "encoding-disabled"


def test_encoding_toggles_suppress_faststart_and_rec709_metadata(tmp_path: Path) -> None:
    scene = tmp_path / "candidate.blend"
    scene.write_bytes(b"synthetic blend identity")
    payload = _profile_payload(_sha(scene))
    delivery = payload["encoding"]["delivery"]  # type: ignore[index]
    delivery["fastStart"] = False  # type: ignore[index]
    delivery["requireRec709Metadata"] = False  # type: ignore[index]
    delivery["color"] = {}  # type: ignore[index]
    profile_path = tmp_path / "metadata-toggle-profile.json"
    profile_path.write_text(json.dumps(payload), encoding="utf-8")
    profile = tooling.load_render_profile(profile_path)
    frames = tmp_path / "frames"
    frames.mkdir()
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"synthetic")
    arguments = tooling.build_encode_arguments(
        profile,
        frames,
        audio,
        tmp_path / "temporary.mp4",
        kind="delivery",
    )
    assert "-movflags" not in arguments
    assert "-color_primaries" not in arguments
    assert "-color_trc" not in arguments
    assert "-colorspace" not in arguments
    assert "-color_range" not in arguments
    probe = {
        "formatName": "mov,mp4",
        "formatDurationSeconds": profile.duration_seconds,
        "sizeBytes": 100,
        "videoPresent": True,
        "audioPresent": True,
        "videoCodec": "h264",
        "videoProfile": "High",
        "audioCodec": "aac",
        "width": profile.width,
        "height": profile.height,
        "fps": profile.fps,
        "videoDurationSeconds": profile.duration_seconds,
        "audioDurationSeconds": profile.duration_seconds,
        "frameCount": profile.frame_count,
        "pixelFormat": "yuv420p",
        "colorRange": None,
        "colorPrimaries": None,
        "colorTransfer": None,
        "colorSpace": None,
        "sampleRate": 48000,
        "channels": 2,
    }
    assert tooling.verify_media_contract(profile, probe, kind="delivery") == []


@pytest.mark.parametrize("crf", [-1, 31, True, 16.5])
def test_profile_validation_enforces_delivery_crf_range(tmp_path: Path, crf: object) -> None:
    scene = tmp_path / "candidate.blend"
    scene.write_bytes(b"synthetic blend identity")
    payload = _profile_payload(_sha(scene))
    payload["encoding"]["delivery"]["crf"] = crf  # type: ignore[index]
    profile_path = tmp_path / "invalid-crf.json"
    profile_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(tooling.ToolingError, match="crf"):
        tooling.load_render_profile(profile_path)


def test_ffv1_matroska_and_libx265_main10_codec_contracts(tmp_path: Path) -> None:
    scene = tmp_path / "candidate.blend"
    scene.write_bytes(b"synthetic blend identity")
    payload = _profile_payload(_sha(scene))
    payload["encoding"]["master"] = {  # type: ignore[index]
        "enabled": True,
        "container": "matroska",
        "fileExtension": ".mkv",
        "videoCodec": "ffv1",
        "expectedVideoCodec": "ffv1",
        "displayToDeliveryFilter": MASTER_COLOR_FILTER,
        "pixelFormat": "rgb48le",
        "audioCodec": "flac",
        "requireRec709Metadata": True,
        "color": {"primaries": "bt709", "transfer": "bt709", "space": "bt709", "range": "tv"},
    }
    payload["encoding"]["delivery"].update(  # type: ignore[index]
        {
            "videoCodec": "libx265",
            "expectedVideoCodec": "hevc",
            "profile": "Main10",
            "pixelFormat": "yuv420p10le",
            "crf": 20,
        }
    )
    profile_path = tmp_path / "codec-profile.json"
    profile_path.write_text(json.dumps(payload), encoding="utf-8")
    profile = tooling.load_render_profile(profile_path)
    frames = tmp_path / "frames"
    frames.mkdir()
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"synthetic")

    master_args = tooling.build_encode_arguments(profile, frames, audio, tmp_path / "master.mkv", kind="master")
    assert master_args[master_args.index("-c:v") + 1] == "ffv1"
    assert master_args[master_args.index("-f", master_args.index("-c:v")) + 1] == "matroska"
    delivery_args = tooling.build_encode_arguments(
        profile,
        frames,
        audio,
        tmp_path / "delivery.mp4",
        kind="delivery",
    )
    assert delivery_args[delivery_args.index("-c:v") + 1] == "libx265"
    assert delivery_args[delivery_args.index("-profile:v") + 1] == "main10"
    assert delivery_args[delivery_args.index("-pix_fmt") + 1] == "yuv420p10le"


@pytest.mark.parametrize(
    ("codec", "profile_name", "pixel_format"),
    [("libx264", "high", "yuv420p10le"), ("libx265", "Main10", "yuv420p")],
)
def test_profile_validation_rejects_incompatible_delivery_profile_pixel_format(
    tmp_path: Path,
    codec: str,
    profile_name: str,
    pixel_format: str,
) -> None:
    scene = tmp_path / "candidate.blend"
    scene.write_bytes(b"synthetic blend identity")
    payload = _profile_payload(_sha(scene))
    payload["encoding"]["delivery"].update(  # type: ignore[index]
        {"videoCodec": codec, "profile": profile_name, "pixelFormat": pixel_format}
    )
    profile_path = tmp_path / "invalid-codec-coupling.json"
    profile_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(tooling.ToolingError, match="must use"):
        tooling.load_render_profile(profile_path)


def test_media_contract_requires_count_profile_range_and_untruncated_audio(
    render_inputs: tuple[tooling.RenderProfile, Path, Path]
) -> None:
    profile, _scene, _profile_path = render_inputs
    probe: dict[str, object] = {
        "formatName": "mov,mp4",
        "formatDurationSeconds": profile.duration_seconds,
        "sizeBytes": 100,
        "videoPresent": True,
        "audioPresent": True,
        "videoCodec": "h264",
        "videoProfile": "High",
        "audioCodec": "aac",
        "width": profile.width,
        "height": profile.height,
        "fps": profile.fps,
        "videoDurationSeconds": profile.duration_seconds,
        "audioDurationSeconds": profile.duration_seconds,
        "frameCount": profile.frame_count,
        "pixelFormat": "yuv420p",
        "colorRange": "tv",
        "colorPrimaries": "bt709",
        "colorTransfer": "bt709",
        "colorSpace": "bt709",
        "sampleRate": 48000,
        "channels": 2,
    }
    assert tooling.verify_media_contract(profile, probe, kind="delivery") == []
    probe["frameCount"] = None
    assert "frame-count-mismatch" in {
        issue["code"] for issue in tooling.verify_media_contract(profile, probe, kind="delivery")
    }
    probe["frameCount"] = profile.frame_count
    probe["audioDurationSeconds"] = profile.duration_seconds - (2.0 / profile.fps)
    assert "output-audio-duration-mismatch" in {
        issue["code"] for issue in tooling.verify_media_contract(profile, probe, kind="delivery")
    }
    probe["audioDurationSeconds"] = profile.duration_seconds
    probe["videoProfile"] = "Main"
    probe["colorRange"] = "pc"
    issue_codes = {issue["code"] for issue in tooling.verify_media_contract(profile, probe, kind="delivery")}
    assert {"video-profile-mismatch", "color-range-mismatch"}.issubset(issue_codes)
    master_probe = {
        **probe,
        "videoProfile": "HQ",
        "videoCodec": "prores",
        "audioCodec": "pcm_s24le",
        "pixelFormat": "yuv422p10le",
        "colorRange": "tv",
    }
    assert tooling.verify_media_contract(profile, master_probe, kind="master") == []
    master_probe["videoProfile"] = "Standard"
    assert "video-profile-mismatch" in {
        issue["code"] for issue in tooling.verify_media_contract(profile, master_probe, kind="master")
    }


def test_final_verification_behavior_with_tiny_synthetic_sequence(
    render_inputs: tuple[tooling.RenderProfile, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    profile, scene, _profile_path = render_inputs
    output = scene.parent / "verify-output"
    tooling.render_plan(
        profile,
        scene,
        output,
        initialize=True,
        authorization_token=profile.authorization_token,
        require_authorization=True,
        chunk_size_override=None,
        chunk_rationale_override=None,
        workers=1,
    )
    _write_range(output / "frames", profile, list(range(1, 7)))
    complete = tooling.render_plan(
        profile,
        scene,
        output,
        initialize=True,
        authorization_token=profile.authorization_token,
        require_authorization=True,
        chunk_size_override=None,
        chunk_rationale_override=None,
        workers=1,
    )
    assert complete["complete"] is True
    media = output / "delivery" / "tiny-final.mp4"
    media.write_bytes(b"synthetic encoded media")
    media_hash = _sha(media)
    audio = scene.parent / "approved-audio.wav"
    approved_audio = {
        "fileName": audio.name,
        "sizeBytes": audio.stat().st_size,
        "sha256": _sha(audio),
        "durationSeconds": profile.duration_seconds,
        "videoClockDurationSeconds": profile.duration_seconds,
        "durationDifferenceSeconds": 0.0,
        "durationToleranceSeconds": (1.0 / profile.fps) + 1e-6,
        "codec": "aac",
        "sampleRate": 48000,
        "channels": 2,
    }
    encode_manifest = output / "manifests" / "tiny-final.mp4.encode-manifest.json"
    encode_manifest.write_text(
        json.dumps(
            {
                "schemaVersion": "1.0.0",
                "kind": tooling.ENCODE_MANIFEST_KIND,
                "outputKind": "delivery",
                "scene": {"sha256": profile.approved_scene_sha256},
                "renderProfile": {"sha256": profile.source_sha256},
                "renderFrameSetSha256": complete["frameScan"]["frameSetSha256"],
                "approvedAudio": approved_audio,
                "clockPolicy": {
                    "videoFrameCount": profile.frame_count,
                    "shortestAllowed": False,
                    "approvedAudioDurationSeconds": profile.duration_seconds,
                    "maximumAudioShortfallSeconds": (1.0 / profile.fps) + 1e-6,
                },
                "media": {
                    "relativePath": str(media.relative_to(output)),
                    "sizeBytes": media.stat().st_size,
                    "sha256": media_hash,
                },
            }
        ),
        encoding="utf-8",
    )
    probe = {
        "formatName": "mov,mp4",
        "formatDurationSeconds": profile.duration_seconds,
        "sizeBytes": media.stat().st_size,
        "videoPresent": True,
        "audioPresent": True,
        "videoCodec": "h264",
        "videoProfile": "High",
        "audioCodec": "aac",
        "width": profile.width,
        "height": profile.height,
        "fps": profile.fps,
        "videoDurationSeconds": profile.duration_seconds,
        "audioDurationSeconds": profile.duration_seconds,
        "frameCount": profile.frame_count,
        "pixelFormat": "yuv420p",
        "colorRange": "tv",
        "colorPrimaries": "bt709",
        "colorTransfer": "bt709",
        "colorSpace": "bt709",
        "sampleRate": 48000,
        "channels": 2,
    }
    monkeypatch.setattr(tooling, "probe_media", lambda _ffprobe, _media: probe)
    report = tooling.verify_final(
        profile,
        scene,
        output,
        media,
        audio,
        scene,
        encode_manifest,
        kind="delivery",
    )
    assert report["ok"] is True
    assert report["verdict"] == "PASS"
    assert report["visualQa"]["status"] == "pending-human-review"
    assert {item["frame"] for item in report["visualQa"]["extractionFrames"]}.issuperset({1, 2, 3, 4, 6})
    manifest_payload = json.loads(encode_manifest.read_text(encoding="utf-8"))
    manifest_payload["approvedAudio"]["sha256"] = "B" * 64
    encode_manifest.write_text(json.dumps(manifest_payload), encoding="utf-8")
    with pytest.raises(tooling.ToolingError, match="approved audio sha256"):
        tooling.verify_final(profile, scene, output, media, audio, scene, encode_manifest, kind="delivery")
    manifest_payload["approvedAudio"]["sha256"] = approved_audio["sha256"]
    manifest_payload["clockPolicy"]["shortestAllowed"] = True
    encode_manifest.write_text(json.dumps(manifest_payload), encoding="utf-8")
    with pytest.raises(tooling.ToolingError, match="clock policy"):
        tooling.verify_final(profile, scene, output, media, audio, scene, encode_manifest, kind="delivery")


def test_encode_finalization_renames_only_after_probe_and_writes_manifest(
    render_inputs: tuple[tooling.RenderProfile, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    profile, scene, _profile_path = render_inputs
    output = scene.parent / "finalize-output"
    tooling.render_plan(
        profile,
        scene,
        output,
        initialize=True,
        authorization_token=profile.authorization_token,
        require_authorization=True,
        chunk_size_override=None,
        chunk_rationale_override=None,
        workers=1,
    )
    _write_range(output / "frames", profile, list(range(1, 7)))
    tooling.render_plan(
        profile,
        scene,
        output,
        initialize=True,
        authorization_token=profile.authorization_token,
        require_authorization=True,
        chunk_size_override=None,
        chunk_rationale_override=None,
        workers=1,
    )
    temporary = output / "delivery" / ".tiny.partial-test.mp4"
    destination = output / "delivery" / "tiny.mp4"
    audio = scene.parent / "approved-audio.wav"
    temporary.write_bytes(b"bounded synthetic encode")
    probe = {
        "formatName": "mov,mp4",
        "formatDurationSeconds": profile.duration_seconds,
        "sizeBytes": temporary.stat().st_size,
        "videoPresent": True,
        "audioPresent": True,
        "videoCodec": "h264",
        "videoProfile": "High",
        "audioCodec": "aac",
        "width": profile.width,
        "height": profile.height,
        "fps": profile.fps,
        "videoDurationSeconds": profile.duration_seconds,
        "audioDurationSeconds": profile.duration_seconds,
        "frameCount": profile.frame_count,
        "pixelFormat": "yuv420p",
        "colorRange": "tv",
        "colorPrimaries": "bt709",
        "colorTransfer": "bt709",
        "colorSpace": "bt709",
        "sampleRate": 48000,
        "channels": 2,
    }
    monkeypatch.setattr(tooling, "probe_media", lambda _ffprobe, _media: probe)
    result = tooling.finalize_encode(
        profile,
        scene,
        output,
        temporary,
        destination,
        audio,
        scene,
        kind="delivery",
        workers=1,
    )
    assert result["ok"] is True
    assert not temporary.exists()
    assert destination.read_bytes() == b"bounded synthetic encode"
    manifest = Path(result["encodeManifest"])
    assert manifest.is_file()
    manifest_payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert manifest_payload["media"]["sha256"] == _sha(destination)
    assert manifest_payload["approvedAudio"]["sha256"] == profile.audio["sha256"]
    assert manifest_payload["clockPolicy"]["shortestAllowed"] is False


def test_root_scripts_keep_hard_gates_and_separate_operations() -> None:
    repository = Path(__file__).resolve().parents[2]
    render_script = (repository / "render-trackprompt-final.ps1").read_text(encoding="utf-8")
    encode_script = (repository / "encode-trackprompt-final.ps1").read_text(encoding="utf-8")
    verify_script = (repository / "verify-trackprompt-final.ps1").read_text(encoding="utf-8")
    assert "--require-authorization" in render_script
    assert "acceptedTokenSha256" in (repository / "blender" / "render_final_chunk.py").read_text(encoding="utf-8")
    assert "--python-exit-code" in render_script and '"1"' in render_script
    assert "--background" in render_script and "render_final_chunk.py" in render_script
    assert "--shortest" not in encode_script
    assert "encode-preflight" in encode_script and "finalize-encode" in encode_script
    assert "verify-final" in verify_script and "AudioPath" in verify_script
    assert "[IO.DriveInfo]" in render_script and "Assert-AvailableStorage" in render_script
    assert render_script.index("Pre-initialization render inspection") < render_script.index('"--initialize"')
    chunk_loop = render_script.index("foreach ($chunk")
    chunk_storage_gate = render_script.index("Assert-AvailableStorage", chunk_loop)
    inflight_creation = render_script.index("New-Item -ItemType Directory", chunk_loop)
    blender_launch = render_script.index("& $resolvedBlender @blenderArguments", chunk_loop)
    assert chunk_storage_gate < inflight_creation < blender_launch
    forbidden = ("docker compose down --volumes", "git reset --hard", "git clean -fd")
    assert all(value not in (render_script + encode_script + verify_script) for value in forbidden)


def test_render_powershell_dry_run_checks_token_without_creating_output(
    render_inputs: tuple[tooling.RenderProfile, Path, Path]
) -> None:
    powershell = shutil.which("powershell.exe")
    if powershell is None:
        pytest.skip("Windows PowerShell is unavailable")
    profile, scene, profile_path = render_inputs
    repository = Path(__file__).resolve().parents[2]
    script = repository / "render-trackprompt-final.ps1"
    output = scene.parent / "powershell-dry-run"
    common = [
        powershell,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
        "-ApprovedScenePath",
        str(scene),
        "-RenderProfilePath",
        str(profile_path),
        "-OutputDirectory",
        str(output),
        "-PythonExecutable",
        sys.executable,
        "-DryRun",
        "-AuthorizationToken",
    ]
    rejected = subprocess.run(
        [*common, profile.authorization_token + " "],
        shell=False,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert rejected.returncode != 0
    assert not output.exists()
    accepted = subprocess.run(
        [*common, profile.authorization_token],
        shell=False,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert accepted.returncode == 0, accepted.stderr
    assert json.loads(accepted.stdout)["mode"] == "inspection-only"
    assert not output.exists()


def test_render_powershell_dry_run_accepts_saved_builder_profile_without_rendering(tmp_path: Path) -> None:
    powershell = shutil.which("powershell.exe")
    if powershell is None:
        pytest.skip("Windows PowerShell is unavailable")
    scene = tmp_path / "builder-candidate.blend"
    scene.write_bytes(b"synthetic builder scene")
    profile_path = tmp_path / "builder-profile.json"
    profile_path.write_text(json.dumps(_builder_profile_payload(scene)), encoding="utf-8")
    profile = tooling.load_render_profile(profile_path)
    output = tmp_path / "builder-dry-run-output"
    repository = Path(__file__).resolve().parents[2]
    completed = subprocess.run(
        [
            powershell,
            "-NoLogo",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(repository / "render-trackprompt-final.ps1"),
            "-ApprovedScenePath",
            str(scene),
            "-RenderProfilePath",
            str(profile_path),
            "-OutputDirectory",
            str(output),
            "-PythonExecutable",
            sys.executable,
            "-DryRun",
            "-AuthorizationToken",
            profile.authorization_token,
        ],
        shell=False,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["mode"] == "inspection-only"
    assert payload["renderProfile"]["schemaVersion"] == "1.1.0"
    assert payload["renderProfile"]["sha256"] == _sha(profile_path)
    assert not output.exists()
