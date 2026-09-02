from __future__ import annotations

import copy
import json
import os
import shutil
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import create_app
from app.renderers.schemas import (
    RendererAvailabilityState,
    SpectrumWorkspacePrepareRequest,
)
from app.renderers.wzhk_spectrum.contracts import SpectrumRenderRequest
from app.renderers.wzhk_spectrum.design import (
    SpectrumDesignError,
    SpectrumDesignPreset,
    SpectrumVisualOverrides,
    load_design_preset,
    resolve_design_preset,
    resolve_state_at_milliseconds,
)
from app.renderers.wzhk_spectrum.generative.composition import (
    GeometryComposition,
    readability_at,
    resolve_composition_envelope,
)
from app.renderers.wzhk_spectrum.generative.contracts import GeometryPreviewOverride
from app.renderers.wzhk_spectrum.generative.workspace import build_runtime_config
from app.renderers.wzhk_spectrum.preflight import (
    SpectrumInspection,
    SpectrumPathError,
    SpectrumPaths,
    ensure_within,
    hash_tree,
    inspect_wzhk_spectrum,
    sha256_file,
)
from app.renderers.wzhk_spectrum.production import (
    GeometryCapabilityEvidence,
    resolve_master_timing,
)
from app.renderers.wzhk_spectrum.workspace import (
    SpectrumWorkspaceError,
    _publish_directory,
    load_workspace_job,
    prepare_workspace,
)

from .helpers import settings_for

EXPECTED_COMMIT = "553aa755ef0cc394259fb1a55560f1b31864d2e0"
CANONICAL_DESIGN_PATH = (
    Path(__file__).resolve().parents[2]
    / "tools"
    / "wzhk-spectrum"
    / "config"
    / "scattered.visual-preset.json"
)
CANONICAL_RUNTIME_PATH = CANONICAL_DESIGN_PATH.parents[1] / "runtime"


def _contract_payload() -> dict[str, Any]:
    return {
        "schemaVersion": "1.0.0",
        "rendererId": "wzhk-spectrum",
        "project": {
            "slug": "dj-wazahaka-scattered",
            "artist": "DJ WaZaHaKa",
            "title": "Scattered",
        },
        "track": {
            "bpm": 120,
            "timeSignature": {"numerator": 4, "denominator": 4},
            "totalBars": 96,
            "gridDurationSeconds": 192,
            "audioAssetDirectory": ".trackprompt-data/wzhk-spectrum/assets/track",
            "preferredMasterExtensions": [".wav", ".flac", ".aiff", ".aif"],
        },
        "sections": [
            {
                "id": "intro",
                "label": "Intro",
                "startBarInclusive": 1,
                "endBarExclusive": 33,
                "startSeconds": 0,
                "endSeconds": 64,
                "visualCue": "restrained",
            },
            {
                "id": "main",
                "label": "Main",
                "startBarInclusive": 33,
                "endBarExclusive": 89,
                "startSeconds": 64,
                "endSeconds": 176,
                "visualCue": "full-energy",
            },
            {
                "id": "outro",
                "label": "Outro",
                "startBarInclusive": 89,
                "endBarExclusive": 97,
                "startSeconds": 176,
                "endSeconds": 192,
                "visualCue": "deconstruct",
            },
        ],
        "branding": {
            "brand": "DJ WaZaHaKa",
            "logoAssetDirectory": ".trackprompt-data/wzhk-spectrum/assets/logo",
            "removeMonstercatIdentityFromGeneratedWorkspace": True,
        },
        "renderer": {
            "platform": "windows",
            "engine": "rainmeter-audio-spectrum",
            "vendorSourceDirectory": "vendor/wzhk-spectrum-visualizer",
            "workspaceRoot": ".trackprompt-data/wzhk-spectrum/jobs",
            "capture": {"width": 1920, "height": 1080, "fps": 60},
            "audioPolicy": {
                "visualizeSystemOutput": True,
                "replaceCapturedAudioWithOriginalMaster": True,
            },
        },
    }


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def _build_fixture(root: Path) -> tuple[Path, Path, SpectrumInspection]:
    repository_root = root / "repo"
    data_root = root / "data"
    vendor = repository_root / "vendor" / "wzhk-spectrum-visualizer"
    resources = vendor / "@Resources"
    song_information = vendor / "Song Information"
    cover = song_information / "Cover"
    for directory in (resources, cover):
        directory.mkdir(parents=True, exist_ok=True)

    (vendor / "visualizer.ini").write_text(
        "[Rainmeter]\nGroup=MonstercatVisualizer\n[Metadata]\nName=Monstercat Visualizer\n",
        encoding="utf-8",
    )
    (resources / "variables.ini").write_text(
        "[Variables]\n"
        "ScaleVisualizer=0.8\n"
        "ScaleSongInformation=0.8\n"
        "PlayerName=AIMP\n"
        "ShowProgressBar=0\n"
        "ShowMonstercatCover=0\n"
        "CoverSize=200\n"
        "BarCount=63\n"
        "BarWidth=18\n"
        "BarHeight=350\n"
        "BarGap=7\n"
        "Sensitivity=35\n"
        "FFTSize=4096\n"
        "FFTAttack=0\n"
        "FFTDecay=50\n"
        "FontSize1=72\n"
        "FontSize2=40\n"
        "Color=255,255,255,255\n"
        "TextColor=255,255,255\n"
        "EnableDynamicColors=0\n"
        "EnableDynamicFontColors=1\n"
        "EnableDropShadow=1\n"
        "DropShadowColor=0,0,0,75\n"
        "Config=monstercat-visualizer\n"
        "SkinWidth=1255\n",
        encoding="utf-8",
    )
    for name in ("Left.ini", "Right.ini"):
        (song_information / name).write_text(
            "[Rainmeter]\nGroup=MonstercatVisualizer\n"
            "[MeterArtist]\nMeter=String\nMeasureName=MeasureArtist\n"
            "[MeterTrack]\nMeter=String\nMeasureName=MeasureTrack\n",
            encoding="utf-8",
        )
    (cover / "Cover.ini").write_text(
        "[Rainmeter]\nGroup=MonstercatVisualizer\n"
        "[MeterMonstercatCover]\nMeter=Image\nImageName=#@#images\\nocover.png\n"
        "[MeterCover]\nMeter=Image\nMeasureName=MeasureCover\n"
        "Hidden=#ShowMonstercatCover#\n",
        encoding="utf-8",
    )
    (vendor / "LICENSE").write_text(
        "Synthetic MIT fixture preserving upstream legal text.",
        encoding="utf-8",
    )
    _write_json(
        vendor / "UPSTREAM-SOURCE.json",
        {
            "repository": "https://example.invalid/upstream.git",
            "commit": EXPECTED_COMMIT,
            "vendoredWithoutGitMetadata": True,
        },
    )
    _write_json(
        repository_root
        / "tools"
        / "wzhk-spectrum"
        / "config"
        / "scattered.wzhk-spectrum.json",
        _contract_payload(),
    )
    _write_json(
        repository_root
        / "tools"
        / "wzhk-spectrum"
        / "config"
        / "scattered.visual-preset.json",
        json.loads(CANONICAL_DESIGN_PATH.read_text(encoding="utf-8")),
    )
    shutil.copytree(
        CANONICAL_RUNTIME_PATH,
        repository_root / "tools" / "wzhk-spectrum" / "runtime",
    )

    logo_directory = data_root / "wzhk-spectrum" / "assets" / "logo"
    track_directory = data_root / "wzhk-spectrum" / "assets" / "track"
    logo_directory.mkdir(parents=True)
    track_directory.mkdir(parents=True)
    (logo_directory / "synthetic-logo.png").write_bytes(b"synthetic-logo")
    (track_directory / "synthetic-master.wav").write_bytes(b"synthetic-master")
    rainmeter = root / "tools" / "Rainmeter.exe"
    rainmeter.parent.mkdir(parents=True)
    rainmeter.write_bytes(b"synthetic-rainmeter")
    ffplay = root / "tools" / "ffplay.exe"
    ffplay.write_bytes(b"synthetic-ffplay")
    inspection = SpectrumInspection(
        platform_name="win32",
        rainmeter_available=True,
        rainmeter_path=rainmeter,
        ffmpeg_available=True,
        ffmpeg_version="ffmpeg synthetic",
        ffprobe_available=True,
        ffplay_available=True,
        ffplay_path=ffplay,
        capture_provider_available=True,
        capture_encoder="h264_nvenc",
        nvenc_available=True,
        alpha_composition_available=True,
        monitor_capture_available=True,
        chroma_composition_available=True,
        geometry_capability=GeometryCapabilityEvidence(
            state="READY",
            webgl2=True,
            gpu_renderer="Synthetic D3D11 GPU",
            shader_compiled=True,
            performance_measured=True,
            performance_sufficient=True,
            renderer_fps=60,
            average_frame_time_ms=16.2,
            point_count=4096,
            detail="Synthetic WebGL2 capability evidence for isolated backend tests.",
        ),
        duration_reader=lambda _path: 196.61979591836734,
    )
    return repository_root, data_root, inspection


def _inspect(
    repository_root: Path,
    data_root: Path,
    inspection: SpectrumInspection,
):  # type: ignore[no-untyped-def]
    return inspect_wzhk_spectrum(
        settings_for(data_root),
        SpectrumPaths(repository_root, data_root),
        inspection,
    )


def test_scattered_contract_rejects_gaps_overlaps_and_wrong_meter() -> None:
    valid = _contract_payload()
    parsed = SpectrumRenderRequest.model_validate(valid)
    assert parsed.track.bpm == 120
    assert parsed.track.time_signature.numerator == 4
    assert parsed.track.time_signature.denominator == 4
    assert parsed.track.total_bars == 96
    assert parsed.track.grid_duration_seconds == 192
    assert sum(
        section.end_bar_exclusive - section.start_bar_inclusive
        for section in parsed.sections
    ) == 96

    gap = copy.deepcopy(valid)
    gap["sections"][1]["startBarInclusive"] = 34
    overlap = copy.deepcopy(valid)
    overlap["sections"][1]["startBarInclusive"] = 32
    wrong_meter = copy.deepcopy(valid)
    wrong_meter["track"]["timeSignature"]["numerator"] = 3
    for invalid in (gap, overlap, wrong_meter):
        with pytest.raises(ValidationError):
            SpectrumRenderRequest.model_validate(invalid)


def test_scattered_design_timeline_boundaries_and_fixed_previews() -> None:
    preset = load_design_preset(CANONICAL_DESIGN_PATH)
    expected = (
        (0, "intro", False),
        (63_999, "intro", False),
        (64_000, "main", False),
        (175_999, "main", False),
        (176_000, "outro", False),
        (191_999, "outro", False),
        (192_000, "post-grid-tail", False),
        (196_619, "post-grid-tail", False),
        (196_620, "end", True),
    )
    assert tuple(
        (
            milliseconds,
            resolve_state_at_milliseconds(preset, milliseconds, 196.62).section_id,
            resolve_state_at_milliseconds(preset, milliseconds, 196.62).section_complete,
        )
        for milliseconds, _section, _complete in expected
    ) == expected

    main_preview = resolve_state_at_milliseconds(
        preset,
        0,
        196.62,
        preview_section="main",
    )
    canonical_intro = resolve_state_at_milliseconds(preset, 0)
    assert main_preview.section_id == "main"
    assert main_preview.state == preset.sections[1].state
    assert canonical_intro.section_id == "intro"
    assert main_preview.state.fragment_density > canonical_intro.state.fragment_density
    assert preset.sections[2].state.fragment_density < main_preview.state.fragment_density
    assert preset.post_grid_tail.state.fragment_density < preset.sections[2].state.fragment_density
    assert preset.post_grid_tail.end_state.fragment_motion == 0
    assert [(section.start_seconds, section.end_seconds) for section in preset.sections] == [
        (0, 64),
        (64, 176),
        (176, 192),
    ]

    with pytest.raises(SpectrumDesignError):
        resolve_state_at_milliseconds(preset, -1)


def test_scattered_fragment_field_preset_is_typed_bounded_and_section_aware() -> None:
    canonical = json.loads(CANONICAL_DESIGN_PATH.read_text(encoding="utf-8"))
    parsed = SpectrumDesignPreset.model_validate(canonical)
    assert parsed.background.depth_layers == 3
    assert parsed.background.fragment_count == 24
    assert parsed.background.fragment_seed == 351705

    for path, invalid_value in (
        (("background", "fragmentCount"), 80),
        (("background", "maxMotionPixels"), -1),
        (("sections", 1, "state", "fragmentDensity"), 1.5),
        (("sections", 0, "state", "lineIntensity"), float("nan")),
    ):
        payload = copy.deepcopy(canonical)
        target: Any = payload
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = invalid_value
        with pytest.raises(ValidationError):
            SpectrumDesignPreset.model_validate(payload)

    wrong_energy = copy.deepcopy(canonical)
    wrong_energy["sections"][0]["state"]["fragmentDensity"] = 1
    with pytest.raises(ValidationError):
        SpectrumDesignPreset.model_validate(wrong_energy)


def test_canonical_geometry_first_composition_is_full_frame_localized_and_section_aware() -> None:
    preset = load_design_preset(CANONICAL_DESIGN_PATH)
    assert preset.schema_version == "3.1.0"
    composition = preset.composition
    assert composition.revision == "scattered-geometry-first-3.7"
    assert composition.geometry_coverage == "full-frame"
    assert composition.production.model_dump(mode="json", by_alias=True) == {
        "logoVisible": True,
        "artistVisible": True,
        "titleVisible": True,
        "spectrumBarsVisible": False,
        "spectralRibbonVisible": False,
        "technicalMetadataVisible": False,
        "sectionLabelsVisible": False,
    }
    for zone in composition.readability.zones:
        attenuation = readability_at(composition, *zone.center)
        assert composition.readability.minimum_brightness <= attenuation.intensity < 1
        assert 0 < attenuation.halo <= attenuation.intensity
    assert readability_at(composition, 0.02, 0.88).intensity > 0.99
    assert readability_at(composition, 0.95, 0.75).intensity > 0.99
    intro = resolve_composition_envelope(composition, 10)
    main = resolve_composition_envelope(composition, 120)
    outro = resolve_composition_envelope(composition, 177)
    tail = resolve_composition_envelope(composition, 193)
    eof = resolve_composition_envelope(composition, 196.619796)
    assert main.density > intro.density
    assert main.brightness > intro.brightness
    assert main.density > outro.density > tail.density > eof.density
    assert main.brightness > outro.brightness > tail.brightness > eof.brightness
    assert eof.density == eof.brightness == 0
    assert composition.envelope[-1].time_seconds == pytest.approx(
        preset.generative_geometry.choreography.master_duration_seconds,
        abs=1e-6,
    )


def test_visual_overrides_are_typed_and_bounded() -> None:
    valid = SpectrumWorkspacePrepareRequest.model_validate(
        {
            "contractId": "scattered",
            "presetId": "scattered",
            "previewSection": "main",
            "visualOverrides": {
                "spectrumScale": 1.1,
                "sensitivity": 42,
                "logoScale": 0.9,
                "accentColor": "#45D7FF",
                "backgroundIntensity": 0.5,
            },
        }
    )
    assert valid.preview_section == "main"
    assert valid.visual_overrides.sensitivity == 42
    preset = load_design_preset(CANONICAL_DESIGN_PATH)
    resolved = resolve_design_preset(preset, valid.visual_overrides)
    assert resolved.sections[1].state.sensitivity > resolved.sections[0].state.sensitivity
    assert resolved.sections[1].state.sensitivity > resolved.sections[2].state.sensitivity
    for invalid in (
        {"spectrumScale": 4},
        {"sensitivity": 120},
        {"logoScale": -1},
        {"accentColor": "red;[Rainmeter]"},
        {"backgroundIntensity": float("nan")},
    ):
        with pytest.raises(ValidationError):
            SpectrumVisualOverrides.model_validate(invalid)


def test_renderer_availability_states_are_explicit_and_isolated(tmp_path: Path) -> None:
    repository_root, data_root, inspection = _build_fixture(tmp_path / "ready")
    ready = _inspect(repository_root, data_root, inspection)
    assert ready.descriptor.availability is RendererAvailabilityState.READY_FOR_CAPTURE
    assert ready.descriptor.available is True
    assert ready.descriptor.preparation_available is True
    assert ready.descriptor.design_preset is not None
    assert [section.id for section in ready.descriptor.design_preset.sections] == [
        "intro",
        "main",
        "outro",
        "post-grid-tail",
    ]

    unsupported = _inspect(
        repository_root,
        data_root,
        SpectrumInspection(
            platform_name="linux",
            ffmpeg_available=True,
            duration_reader=lambda _path: 192.0,
        ),
    )
    assert unsupported.descriptor.availability is RendererAvailabilityState.UNSUPPORTED_PLATFORM

    missing_rainmeter = _inspect(
        repository_root,
        data_root,
        SpectrumInspection(
            platform_name="win32",
            rainmeter_available=False,
            ffmpeg_available=True,
            duration_reader=lambda _path: 192.0,
        ),
    )
    assert missing_rainmeter.descriptor.availability is RendererAvailabilityState.MISSING_RAINMETER
    assert missing_rainmeter.descriptor.preparation_available is True

    missing_ffmpeg = _inspect(
        repository_root,
        data_root,
        SpectrumInspection(
            platform_name="win32",
            rainmeter_available=True,
            rainmeter_path=inspection.rainmeter_path,
            ffmpeg_available=False,
            duration_reader=lambda _path: 192.0,
        ),
    )
    assert missing_ffmpeg.descriptor.availability is RendererAvailabilityState.MISSING_FFMPEG
    assert missing_ffmpeg.descriptor.preparation_available is True

    logo_root, logo_data, logo_inspection = _build_fixture(tmp_path / "missing-logo")
    (logo_data / "wzhk-spectrum" / "assets" / "logo" / "synthetic-logo.png").unlink()
    assert _inspect(logo_root, logo_data, logo_inspection).descriptor.availability is RendererAvailabilityState.MISSING_ASSETS

    track_root, track_data, track_inspection = _build_fixture(tmp_path / "missing-track")
    (track_data / "wzhk-spectrum" / "assets" / "track" / "synthetic-master.wav").unlink()
    assert _inspect(track_root, track_data, track_inspection).descriptor.availability is RendererAvailabilityState.MISSING_MASTER

    vendor_root, vendor_data, vendor_inspection = _build_fixture(tmp_path / "invalid-vendor")
    (vendor_root / "vendor" / "wzhk-spectrum-visualizer" / ".git").mkdir()
    invalid_vendor = _inspect(vendor_root, vendor_data, vendor_inspection)
    assert invalid_vendor.descriptor.availability is RendererAvailabilityState.INVALID_VENDOR_SNAPSHOT
    assert invalid_vendor.descriptor.preparation_available is False


def test_master_tail_is_intentional_and_short_master_is_rejected(
    tmp_path: Path,
) -> None:
    repository_root, data_root, inspection = _build_fixture(tmp_path)
    tail_inspection = SpectrumInspection(
        platform_name="win32",
        rainmeter_available=True,
        rainmeter_path=inspection.rainmeter_path,
        ffmpeg_available=True,
        ffprobe_available=True,
        ffplay_available=True,
        ffplay_path=inspection.ffplay_path,
        capture_provider_available=True,
        capture_encoder="h264_nvenc",
        nvenc_available=True,
        duration_reader=lambda _path: 196.61979591836734,
    )
    outcome = _inspect(repository_root, data_root, tail_inspection)
    assert outcome.descriptor.availability is RendererAvailabilityState.READY_FOR_CAPTURE
    assert outcome.descriptor.preparation_available is True
    assert outcome.master_timing is not None
    assert outcome.master_timing.grid_duration_seconds == 192
    assert outcome.master_timing.master_duration_seconds == pytest.approx(196.61979591836734)
    assert outcome.master_timing.tail_duration_seconds == pytest.approx(4.61979591836734)
    assert not any("mismatch" in warning.casefold() for warning in outcome.descriptor.warnings)

    short = _inspect(
        repository_root,
        data_root,
        SpectrumInspection(
            platform_name="win32",
            rainmeter_available=True,
            rainmeter_path=inspection.rainmeter_path,
            ffmpeg_available=True,
            ffprobe_available=True,
            ffplay_available=True,
            ffplay_path=inspection.ffplay_path,
            capture_provider_available=True,
            capture_encoder="h264_nvenc",
            duration_reader=lambda _path: 191.999,
        ),
    )
    assert short.descriptor.availability is RendererAvailabilityState.INVALID_MASTER_DURATION
    assert short.descriptor.preparation_available is False


def test_workspace_is_private_deterministic_branded_and_vendor_immutable(
    tmp_path: Path,
) -> None:
    repository_root, data_root, inspection = _build_fixture(tmp_path)
    paths = SpectrumPaths(repository_root, data_root)
    outcome = _inspect(repository_root, data_root, inspection)
    vendor_hash_before = hash_tree(
        paths.vendor_root,
        prefix="vendor/wzhk-spectrum-visualizer",
    )
    logo_hash_before = sha256_file(outcome.logo_path) if outcome.logo_path else ""
    master_hash_before = (
        sha256_file(outcome.master_audio_path) if outcome.master_audio_path else ""
    )

    request = SpectrumWorkspacePrepareRequest(
        preview_section="main",
        visual_overrides=SpectrumVisualOverrides(
            spectrum_scale=1.05,
            sensitivity=40,
            logo_scale=0.9,
            accent_color="#45D7FF",
            background_intensity=0.5,
        ),
    )
    first = prepare_workspace(paths, outcome, request)
    second = prepare_workspace(paths, outcome, request)
    fallback = prepare_workspace(
        paths,
        outcome,
        SpectrumWorkspacePrepareRequest(
            mode="preview",
            background_mode="static-structured",
            preview_section="main",
        ),
    )
    first_root = data_root / first.workspace_relative_path
    second_root = data_root / second.workspace_relative_path
    fallback_root = data_root / fallback.workspace_relative_path

    assert first.job_id != second.job_id
    assert first.generated_workspace_hash == second.generated_workspace_hash
    assert (first_root / "generation.json").read_bytes() == (
        second_root / "generation.json"
    ).read_bytes()
    assert (first_root / "contract.json").read_bytes() == (
        second_root / "contract.json"
    ).read_bytes()
    assert (first_root / "design.json").read_bytes() == (
        second_root / "design.json"
    ).read_bytes()
    assert (first_root / "skin" / "WZHK Presentation" / "Scattered.ini").read_bytes() == (
        second_root / "skin" / "WZHK Presentation" / "Scattered.ini"
    ).read_bytes()
    assert (first_root / "skin" / "@Resources" / "scripts" / "WZHKSectionController.lua").read_bytes() == (
        second_root / "skin" / "@Resources" / "scripts" / "WZHKSectionController.lua"
    ).read_bytes()
    assert (first_root / "skin" / "LICENSE").is_file()
    assert (first_root / "skin" / "UPSTREAM-SOURCE.json").is_file()
    assert first.contract_valid is True
    assert first.branding_applied is True
    assert first.vendor_unchanged is True
    assert first.schema_version == "4.0.0"
    assert first.background_mode == "generative-geometry"
    assert first.preset_id == "scattered"
    assert first.preview_section == "main"
    assert first.design_hash is not None
    assert first.timing_source == "external-media-player-position"
    assert first.timing_accuracy == "preview-level"
    assert first.visual_qa_required is True
    assert first.state.value == "PREVIEW_READY"
    assert first.master_timing is not None
    assert first.master_timing.tail_duration_seconds == pytest.approx(4.61979591836734)

    generated_ini = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (first_root / "skin").rglob("*.ini")
    )
    assert "DJ WaZaHaKa" in generated_ini
    assert "SCATTERED" in generated_ini
    assert "wzhk-logo.png" in generated_ini
    assert "Monstercat" not in generated_ini
    assert "MeasureName=MeasureArtist" not in generated_ini
    assert "MeasureName=MeasureTrack" not in generated_ini
    assert "WZHKPresentation" in generated_ini
    assert "MeasureWZHKPosition" in generated_ini
    assert "WZHKPreviewSection=main" in generated_ini
    assert "Text=120 BPM  /  4/4  /  #WZHKSection#" in generated_ini
    assert "ShowProgressBar=0" in generated_ini
    assert "Group=WZHKLogo" in generated_ini
    assert "MeterWZHKFieldFar" not in generated_ini
    assert (first_root / "geometry" / "index.html").is_file()
    assert (first_root / "geometry" / "runtime.js").is_file()
    assert (first_root / "geometry" / "shaders" / "neopixel.vert.glsl").is_file()
    runtime_config = json.loads(
        (first_root / "geometry" / "config" / "runtime-config.json").read_text(
            encoding="utf-8"
        )
    )
    assert runtime_config["pointCount"] == 1600
    assert runtime_config["seed"] == 84291
    assert len(runtime_config["trustedShapes"]) >= 6
    assert runtime_config["choreography"][-1]["section"] == "post-grid-tail"
    assert runtime_config["composition"]["revision"] == "scattered-geometry-first-3.7"
    assert "safeRect" not in runtime_config["branding"]
    assert runtime_config["branding"]["meta"] == "120 BPM  /  4/4  /  96 BARS"
    assert runtime_config["developerLab"] == {
        "enabled": False,
        "spectrumDiagnostics": False,
        "technicalMetadata": False,
        "previewOverride": None,
    }

    fallback_ini = (
        fallback_root / "skin" / "WZHK Presentation" / "Scattered.ini"
    ).read_text(encoding="utf-8")
    assert fallback.background_mode == "static-structured"
    assert not (fallback_root / "geometry").exists()
    assert "MeterWZHKFieldFar" in fallback_ini
    assert "MeterWZHKFieldMid" in fallback_ini
    assert "MeterWZHKFieldNear" in fallback_ini
    assert "MeterWZHKFieldLines" in fallback_ini
    assert "ClosePath 1" in fallback_ini

    generated_lua = (
        first_root / "skin" / "@Resources" / "scripts" / "WZHKSectionController.lua"
    ).read_text(encoding="utf-8")
    assert "seconds < 64.000000" in generated_lua
    assert "seconds < 176.000000" in generated_lua
    assert "seconds < 192.000000" in generated_lua
    assert "seconds - 192.619796" in generated_lua
    assert "return 'post-grid-tail'" in generated_lua
    assert "positionMeasure:GetValue()" in generated_lua
    assert "if logoAlpha ~= lastLogoAlpha then" in generated_lua
    assert "SKIN:Bang('!UpdateMeter', 'MeterWZHKLogo')" in generated_lua
    assert "local function applyMotion(seconds, state)" in generated_lua
    assert "state.fragmentDensity" in generated_lua
    assert "WZHKBackgroundMotion" in generated_lua

    assert hash_tree(
        paths.vendor_root,
        prefix="vendor/wzhk-spectrum-visualizer",
    ) == vendor_hash_before
    assert outcome.logo_path is not None
    assert outcome.master_audio_path is not None
    assert sha256_file(outcome.logo_path) == logo_hash_before
    assert sha256_file(outcome.master_audio_path) == master_hash_before
    assert load_workspace_job(paths, first.job_id) == first


@pytest.mark.parametrize("background_mode", ["generative-geometry", "static-structured"])
def test_production_omits_legacy_meters_and_metadata_while_preview_keeps_diagnostics(
    tmp_path: Path,
    background_mode: str,
) -> None:
    repository_root, data_root, inspection = _build_fixture(tmp_path)
    paths = SpectrumPaths(repository_root, data_root)
    outcome = _inspect(repository_root, data_root, inspection)
    preview = prepare_workspace(
        paths,
        outcome,
        SpectrumWorkspacePrepareRequest.model_validate(
            {"mode": "preview", "previewSection": "intro", "backgroundMode": background_mode}
        ),
    )
    production = prepare_workspace(
        paths,
        outcome,
        SpectrumWorkspacePrepareRequest.model_validate(
            {"mode": "production", "backgroundMode": background_mode}
        ),
    )
    preview_ini = (
        data_root
        / preview.workspace_relative_path
        / "skin"
        / "WZHK Presentation"
        / "Scattered.ini"
    ).read_text(encoding="utf-8")
    production_ini = (
        data_root
        / production.workspace_relative_path
        / "skin"
        / "WZHK Presentation"
        / "Scattered.ini"
    ).read_text(encoding="utf-8")
    preview_text = "\n".join(line for line in preview_ini.splitlines() if line.startswith("Text="))
    production_text = "\n".join(
        line for line in production_ini.splitlines() if line.startswith("Text=")
    )
    assert "#WZHKSection#" in preview_text
    assert "Text=120 BPM  /  4/4  /  #WZHKSection#" in preview_text
    assert "[MeterWZHKMeta]" in preview_ini
    assert "[MeterWZHKSpectrumBase]" in preview_ini
    assert "[MeterWZHKBar0]" in preview_ini
    assert set(production_text.splitlines()) == {"Text=DJ WaZaHaKa", "Text=SCATTERED"}
    assert "#WZHKSection#" not in production_text
    assert not any(
        label in production_text
        for label in ("BPM", "4/4", "96 BARS", "INTRO", "MAIN", "OUTRO", "POST_GRID_TAIL", "POST-GRID-TAIL")
    )
    production_meters = {
        line.strip()[1:-1]
        for line in production_ini.splitlines()
        if line.startswith("[") and line.endswith("]")
    }
    assert {"MeterWZHKLogo", "MeterWZHKArtist", "MeterWZHKTitle"}.issubset(production_meters)
    assert not production_meters.intersection({"MeterWZHKMeta", "MeterWZHKSpectrumBase", "MeterWZHKProgress"})
    assert not any(meter.startswith(("MeterWZHKBar", "MeterWZHKGlow", "MeterWZHKRibbon")) for meter in production_meters)


def test_geometry_first_production_workspaces_are_deterministic_and_keep_source_assets_immutable(
    tmp_path: Path,
) -> None:
    repository_root, data_root, inspection = _build_fixture(tmp_path)
    paths = SpectrumPaths(repository_root, data_root)
    outcome = _inspect(repository_root, data_root, inspection)
    vendor_before = hash_tree(paths.vendor_root, prefix="vendor/wzhk-spectrum-visualizer")
    assert outcome.logo_path is not None
    assert outcome.master_audio_path is not None
    source_hashes = (sha256_file(outcome.logo_path), sha256_file(outcome.master_audio_path))
    request = SpectrumWorkspacePrepareRequest(mode="production", background_mode="generative-geometry")
    first = prepare_workspace(paths, outcome, request)
    second = prepare_workspace(paths, outcome, request)
    first_root = data_root / first.workspace_relative_path
    second_root = data_root / second.workspace_relative_path
    generation = json.loads((first_root / "generation.json").read_text(encoding="utf-8"))
    assert first.job_id != second.job_id
    assert first.generated_workspace_hash == second.generated_workspace_hash
    assert first.design_hash == second.design_hash
    assert (first_root / "generation.json").read_bytes() == (second_root / "generation.json").read_bytes()
    assert "geometry/config/runtime-config.json" in generation["filesGenerated"]
    for relative_path in generation["filesGenerated"]:
        assert (first_root / relative_path).read_bytes() == (second_root / relative_path).read_bytes()
    assert generation["compositionRevision"] == "scattered-geometry-first-3.7"
    for workspace_root in (first_root, second_root):
        manifest = json.loads((workspace_root / "manifest.json").read_text(encoding="utf-8"))
        design = json.loads((workspace_root / "design.json").read_text(encoding="utf-8"))
        config = json.loads((workspace_root / "geometry" / "config" / "runtime-config.json").read_text(encoding="utf-8"))
        assert manifest["compositionRevision"] == generation["compositionRevision"]
        assert design["resolvedPreset"]["schemaVersion"] == "3.1.0"
        assert config["composition"] == design["resolvedPreset"]["composition"]
        assert GeometryComposition.model_validate(config["composition"]).revision == manifest["compositionRevision"]
        assert config["mode"] == "production"
        assert config["pointCount"] == 4096
        assert config["seed"] == 84291
        assert config["branding"] == {"enabled": True, "artist": "DJ WaZaHaKa", "title": "SCATTERED"}
        assert config["developerLab"] == {
            "enabled": False,
            "spectrumDiagnostics": False,
            "technicalMetadata": False,
            "previewOverride": None,
        }
        assert config["masterDurationSeconds"] == pytest.approx(196.61979591836734)
        assert config["composition"]["envelope"][-1]["timeSeconds"] == pytest.approx(config["masterDurationSeconds"])
        assert manifest["state"] == "WORKSPACE_READY"
        assert manifest["validationReport"] is None
        assert manifest["geometryTelemetry"] is None
        assert manifest["vendorUnchanged"] is True
    assert hash_tree(paths.vendor_root, prefix="vendor/wzhk-spectrum-visualizer") == vendor_before
    assert (sha256_file(outcome.logo_path), sha256_file(outcome.master_audio_path)) == source_hashes


def test_composition_change_changes_production_hashes_without_changing_master_or_seed(tmp_path: Path) -> None:
    repository_root, data_root, inspection = _build_fixture(tmp_path)
    paths = SpectrumPaths(repository_root, data_root)
    outcome = _inspect(repository_root, data_root, inspection)
    request = SpectrumWorkspacePrepareRequest(mode="production")
    first = prepare_workspace(paths, outcome, request)
    changed_preset = json.loads(paths.design_preset_path.read_text(encoding="utf-8"))
    changed_preset["composition"]["readability"]["minimumBrightness"] = 0.5
    _write_json(paths.design_preset_path, changed_preset)
    changed_outcome = _inspect(repository_root, data_root, inspection)
    second = prepare_workspace(paths, changed_outcome, request)
    assert first.design_hash != second.design_hash
    assert first.generated_workspace_hash != second.generated_workspace_hash
    assert first.vendor_source_hash == second.vendor_source_hash
    first_root = data_root / first.workspace_relative_path
    second_root = data_root / second.workspace_relative_path
    original_config = json.loads((first_root / "geometry" / "config" / "runtime-config.json").read_text(encoding="utf-8"))
    changed_config = json.loads((second_root / "geometry" / "config" / "runtime-config.json").read_text(encoding="utf-8"))
    assert original_config["seed"] == changed_config["seed"] == 84291
    assert original_config["composition"]["readability"]["minimumBrightness"] == 0.42
    assert changed_config["composition"]["readability"]["minimumBrightness"] == 0.5
    assert first.master_timing == second.master_timing


@pytest.mark.parametrize(
    ("override_payload", "lab_enabled"),
    [
        (None, False),
        ({"mode": "section", "section": "main"}, False),
        ({"mode": "shape", "shapeA": {"shapeId": "torus"}}, True),
        ({"mode": "morph", "shapeA": {"shapeId": "torus"}, "shapeB": {"shapeId": "trefoil-knot"}, "morphProgress": 0.5}, True),
        ({"mode": "lab", "shapeA": {"shapeId": "torus"}}, True),
    ],
)
def test_geometry_runtime_developer_lab_requires_an_explicit_shape_morph_or_lab_preview(
    override_payload: dict[str, Any] | None,
    lab_enabled: bool,
) -> None:
    preset = load_design_preset(CANONICAL_DESIGN_PATH)
    override = GeometryPreviewOverride.model_validate(override_payload) if override_payload is not None else None
    config = build_runtime_config(
        mode="preview",
        design=preset,
        timing=resolve_master_timing(196.61979591836734),
        preview_override=override,
    )
    assert config["developerLab"]["enabled"] is lab_enabled
    assert config["developerLab"]["spectrumDiagnostics"] is False
    assert config["developerLab"]["technicalMetadata"] is False
    assert config["developerLab"]["previewOverride"] == (
        override.model_dump(mode="json", by_alias=True) if override is not None else None
    )
    assert config["composition"] == preset.composition.model_dump(mode="json", by_alias=True)
    assert "safeRect" not in config["branding"]


def test_workspace_paths_reject_traversal_and_symlink_escape(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir()
    with pytest.raises(SpectrumPathError):
        ensure_within(data_root, data_root / ".." / "outside")
    with pytest.raises(SpectrumWorkspaceError):
        load_workspace_job(SpectrumPaths(tmp_path / "repo", data_root), "../../outside")

    repository_root, linked_data, inspection = _build_fixture(tmp_path / "linked")
    outside = tmp_path / "outside-jobs"
    outside.mkdir()
    jobs_root = linked_data / "wzhk-spectrum" / "jobs"
    try:
        jobs_root.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Directory symlinks are not available in this Windows test environment")
    outcome = _inspect(repository_root, linked_data, inspection)
    workspace_requirement = next(
        item for item in outcome.descriptor.requirements if item.id == "runtime-workspace"
    )
    assert workspace_requirement.available is False
    assert outcome.descriptor.preparation_available is False


def test_workspace_publication_retries_a_transient_windows_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    staging = tmp_path / ".job.staging"
    target = tmp_path / "job"
    staging.mkdir()
    (staging / "manifest.json").write_text("{}", encoding="utf-8")
    actual_replace = os.replace
    attempts = 0

    def transient_replace(source: Path, destination: Path) -> None:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise PermissionError("synthetic scanner lock")
        actual_replace(source, destination)

    monkeypatch.setattr(
        "app.renderers.wzhk_spectrum.workspace.os.replace",
        transient_replace,
    )
    _publish_directory(staging, target, retry_permission_errors=True)
    assert attempts == 3
    assert (target / "manifest.json").is_file()


def test_renderer_api_lists_prepares_and_reloads_spectrum_workspace(
    tmp_path: Path,
) -> None:
    repository_root, data_root, inspection = _build_fixture(tmp_path)
    application = create_app(
        settings_for(data_root),
        renderer_repository_root=repository_root,
        spectrum_inspection=inspection,
    )
    with TestClient(application) as client:
        listed = client.get("/api/renderers")
        descriptor = client.get("/api/renderers/wzhk-spectrum")
        prepared = client.post(
            "/api/renderers/wzhk-spectrum/jobs",
            json={
                "contractId": "scattered",
                "presetId": "scattered",
                "previewSection": "outro",
                "visualOverrides": {"accentColor": "#45D7FF"},
            },
        )
        job_id = prepared.json()["jobId"]
        reloaded = client.get(f"/api/renderers/wzhk-spectrum/jobs/{job_id}")
        preview_capture_preflight = client.post(
            f"/api/renderers/wzhk-spectrum/jobs/{job_id}/capture-preflight",
            json={"refresh": True},
        )
        production = client.post(
            "/api/renderers/wzhk-spectrum/jobs",
            json={"contractId": "scattered", "presetId": "scattered", "mode": "production"},
        )
        production_id = production.json()["jobId"]
        production_preflight = client.post(
            f"/api/renderers/wzhk-spectrum/jobs/{production_id}/capture-preflight",
            json={"refresh": True},
        )
        unconfirmed_start = client.post(
            f"/api/renderers/wzhk-spectrum/jobs/{production_id}/production",
            json={"operatorConfirmed": False, "confirmationPhrase": "no"},
        )
        health = client.get("/api/health")

    assert listed.status_code == 200
    assert [item["rendererId"] for item in listed.json()["renderers"]] == [
        "blender",
        "wzhk-spectrum",
    ]
    assert descriptor.status_code == 200
    assert descriptor.json()["availability"] == "READY_FOR_CAPTURE"
    assert descriptor.json()["designPreset"]["presetId"] == "scattered"
    assert descriptor.json()["designPreset"]["previewTimingSource"] == "external-media-player-position"
    assert descriptor.json()["designPreset"]["productionTimingSource"] == "trackprompt-production-clock"
    assert descriptor.json()["contractSummary"] == {
        "artist": "DJ WaZaHaKa",
        "title": "Scattered",
        "bpm": 120.0,
        "meter": "4/4",
        "totalBars": 96,
        "expectedDurationSeconds": 192.0,
        "gridDurationSeconds": 192.0,
        "masterDurationSeconds": pytest.approx(196.61979591836734),
        "tailDurationSeconds": pytest.approx(4.61979591836734),
        "width": 1920,
        "height": 1080,
        "fps": 60,
    }
    assert prepared.status_code == 201
    assert prepared.json()["state"] == "PREVIEW_READY"
    assert prepared.json()["schemaVersion"] == "4.0.0"
    assert prepared.json()["previewSection"] == "outro"
    assert prepared.json()["presetId"] == "scattered"
    assert prepared.json()["workspaceRelativePath"].startswith("wzhk-spectrum/jobs/")
    assert reloaded.status_code == 200
    assert reloaded.json() == prepared.json()
    assert preview_capture_preflight.status_code == 200
    assert preview_capture_preflight.json()["productionAvailability"] == "INVALID_WORKSPACE"
    assert production.status_code == 201
    assert production.json()["state"] == "WORKSPACE_READY"
    assert production.json()["previewSection"] is None
    assert production_preflight.status_code == 200
    assert production_preflight.json()["state"] == "CAPTURE_READY"
    assert production_preflight.json()["capturePreflight"]["ready"] is True
    assert unconfirmed_start.status_code == 422
    assert health.status_code == 200


def test_missing_rainmeter_does_not_break_trackprompt_health(tmp_path: Path) -> None:
    repository_root, data_root, _inspection = _build_fixture(tmp_path)
    application = create_app(
        settings_for(data_root),
        renderer_repository_root=repository_root,
        spectrum_inspection=SpectrumInspection(
            platform_name="win32",
            rainmeter_available=False,
            ffmpeg_available=True,
            duration_reader=lambda _path: 192.0,
        ),
    )
    with TestClient(application) as client:
        health = client.get("/api/health")
        renderers = client.get("/api/renderers")
    spectrum = next(
        item
        for item in renderers.json()["renderers"]
        if item["rendererId"] == "wzhk-spectrum"
    )
    assert health.status_code == 200
    assert spectrum["availability"] == "MISSING_RAINMETER"
    assert spectrum["available"] is False
    assert spectrum["preparationAvailable"] is True


def test_geometry_failure_keeps_static_fallback_capture_available(tmp_path: Path) -> None:
    repository_root, data_root, inspection = _build_fixture(tmp_path)
    unavailable = replace(
        inspection,
        geometry_capability=GeometryCapabilityEvidence(
            state="PERFORMANCE_INSUFFICIENT",
            webgl2=True,
            gpu_renderer="Synthetic D3D11 GPU",
            shader_compiled=True,
            performance_measured=True,
            performance_sufficient=False,
            renderer_fps=42,
            average_frame_time_ms=24,
            point_count=4096,
            detail="Synthetic insufficient renderer cadence.",
        ),
    )
    application = create_app(
        settings_for(data_root),
        renderer_repository_root=repository_root,
        spectrum_inspection=unavailable,
    )
    with TestClient(application) as client:
        geometry = client.post(
            "/api/renderers/wzhk-spectrum/jobs",
            json={
                "contractId": "scattered",
                "presetId": "scattered",
                "mode": "production",
                "backgroundMode": "generative-geometry",
            },
        ).json()
        geometry_preflight = client.post(
            f"/api/renderers/wzhk-spectrum/jobs/{geometry['jobId']}/capture-preflight",
            json={"refresh": True},
        )
        fallback = client.post(
            "/api/renderers/wzhk-spectrum/jobs",
            json={
                "contractId": "scattered",
                "presetId": "scattered",
                "mode": "production",
                "backgroundMode": "static-structured",
            },
        ).json()
        fallback_preflight = client.post(
            f"/api/renderers/wzhk-spectrum/jobs/{fallback['jobId']}/capture-preflight",
            json={"refresh": True},
        )

    assert geometry_preflight.status_code == 200
    assert geometry_preflight.json()["state"] == "CAPTURE_PREFLIGHT"
    assert geometry_preflight.json()["geometryCapability"]["state"] == "PERFORMANCE_INSUFFICIENT"
    assert geometry_preflight.json()["capturePreflight"]["staticFallbackAvailable"] is True
    assert fallback_preflight.status_code == 200
    assert fallback_preflight.json()["state"] == "CAPTURE_READY"
    assert fallback_preflight.json()["backgroundMode"] == "static-structured"
