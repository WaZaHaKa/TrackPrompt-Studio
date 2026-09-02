# ruff: noqa: E402
from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parents[3]
if str(PACKAGE_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT / "backend"))

from app.video_generation import timeline as timeline_module
from app.video_generation.contracts import (
    ContractError,
    load_project_config,
    load_shot_bank,
)
from app.video_generation.exporter import export_davinci_package
from app.video_generation.gcp_veo import build_request_payload
from app.video_generation.jsonio import atomic_write_json, read_json
from app.video_generation.mission_models import VideoAudioSelection
from app.video_generation.planning import compile_project_plan
from app.video_generation.timeline import load_edit_blueprint, resolve_timeline

PROJECT = PACKAGE_ROOT / "video-projects" / "static-into-signal"
GLITCH = PACKAGE_ROOT / "video-projects" / "the-glitch-is-me"


def _compile(*, config: str = "project-config.json", shot_bank: Path | None = None):
    return compile_project_plan(
        project_config_path=PROJECT / config,
        creative_bible_path=PROJECT / "creative-bible.json",
        shot_bank_path=shot_bank or PROJECT / "shot-bank.json",
        continuity_profile_path=PROJECT / "continuity-profile.json",
        gcs_bucket="gs://reviewed-static-signal-output",
        analysis_job_id="11111111-1111-1111-1111-111111111111",
        master_seed=314159265,
        seed_locked=True,
    )


def test_static_package_configs_and_shots_are_complete() -> None:
    configs = {
        name: load_project_config(PROJECT / name)
        for name in (
            "project-config.json",
            "project-config.quality-1080p.json",
            "project-config.smoke.json",
            "project-config.4k-optional.json",
        )
    }
    shots = load_shot_bank(PROJECT / "shot-bank.json")
    assert [shot.shot_id for shot in shots] == [f"shot-{index:03d}" for index in range(1, 17)]
    assert len({shot.title for shot in shots}) == 16
    assert configs["project-config.json"].required_shot_ids == tuple(
        f"shot-{index:03d}" for index in range(1, 17)
    )
    fast = configs["project-config.json"].selected_profile()
    assert (
        fast.model_id,
        fast.resolution,
        fast.duration_seconds,
        fast.fps,
        fast.aspect_ratio,
        fast.sample_count,
        fast.generate_audio,
    ) == ("veo-3.1-fast-generate-001", "1080p", 8, 24, "16:9", 1, False)
    assert configs["project-config.quality-1080p.json"].required_shot_ids == (
        "shot-007",
        "shot-012",
        "shot-016",
    )
    assert configs["project-config.smoke.json"].required_shot_ids == ("shot-001",)


def test_static_fast_plan_is_deterministic_private_and_cost_bounded() -> None:
    first = _compile()
    second = _compile()
    assert first.to_dict() == second.to_dict()
    assert first.plan_digest == second.plan_digest
    assert len(first.shots) == 16
    assert first.base_estimated_cost_usd == 12.8
    assert first.conservative_estimated_cost_usd == 19.2
    assert first.max_spend_usd == 24.0
    assert first.pricing_snapshot_date
    assert len({shot.seed for shot in first.shots}) == 16

    payloads = [build_request_payload(shot) for shot in first.shots]
    assert all(payload["parameters"]["generateAudio"] is False for payload in payloads)
    serialized = json.dumps(payloads, sort_keys=True).lower()
    assert "transcript" not in serialized
    assert "lyrics" not in serialized
    assert ".mp3" not in serialized and ".wav" not in serialized and "source.bin" not in serialized
    assert "c:\\\\users" not in serialized and "file://" not in serialized


def test_static_prompt_and_profile_changes_change_digest(tmp_path: Path) -> None:
    fast = _compile()
    value = read_json(PROJECT / "shot-bank.json")
    assert isinstance(value, dict)
    value["shots"][0]["prompt"] += " Preserve one additional quiet beat before the window lights."
    revised_path = tmp_path / "shot-bank.json"
    atomic_write_json(revised_path, value)
    revised = _compile(shot_bank=revised_path)
    quality = _compile(config="project-config.quality-1080p.json")
    assert revised.plan_digest != fast.plan_digest
    assert quality.plan_digest != fast.plan_digest
    assert [shot.shot_id for shot in quality.shots] == ["shot-007", "shot-012", "shot-016"]


def test_static_optional_4k_fails_closed() -> None:
    with pytest.raises(ContractError, match="not 4k"):
        _compile(config="project-config.4k-optional.json")


def test_static_audio_selection_serializes_selected_as_boolean() -> None:
    value = VideoAudioSelection(selected=True, verified=True).model_dump(
        mode="json",
        by_alias=True,
    )
    assert value["selected"] is True
    assert isinstance(value["selected"], bool)


def test_static_218_second_timeline_is_deterministic_and_exports(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(timeline_module, "audio_duration_seconds", lambda *_args, **_kwargs: 218.0)
    audio = tmp_path / "private-master.wav"
    audio.write_bytes(b"local-only-test-placeholder")
    clips = {f"shot-{index:03d}": tmp_path / f"shot-{index:03d}.mp4" for index in range(1, 17)}
    arguments = {
        "project_id": "static-into-signal",
        "title": "Static Into Signal",
        "audio_path": audio,
        "chapter_map_path": PROJECT / "chapter-map.json",
        "edit_blueprint_path": PROJECT / "edit-blueprint.json",
        "clips_root": tmp_path,
        "clip_paths": clips,
        "output_width": 1920,
        "output_height": 1080,
        "fps": 24,
        "generated_clip_duration_seconds": 8,
    }
    first = resolve_timeline(**arguments)
    second = resolve_timeline(**arguments)
    assert first == second
    segments = first["segments"]
    assert first["timeline"]["durationFrames"] == 218 * 24
    assert segments[0]["timelineStartFrames"] == 0
    assert all(
        left["timelineStartFrames"] + left["durationFrames"]
        == right["timelineStartFrames"]
        for left, right in zip(segments, segments[1:], strict=False)
    )
    assert segments[-1]["timelineStartFrames"] + segments[-1]["durationFrames"] == 218 * 24
    assert {segment["shotId"] for segment in segments} == {
        f"shot-{index:03d}" for index in range(1, 17)
    }
    chapter_six = [item for item in segments if item["chapterId"] == "chapter-06"]
    chapter_seven = [item for item in segments if item["chapterId"] == "chapter-07"]
    chapter_eight = [item for item in segments if item["chapterId"] == "chapter-08"]
    assert chapter_six[-1]["shotId"] == "shot-012"
    assert {"shot-007", "shot-008", "shot-013", "shot-014"}.issubset(
        {item["shotId"] for item in chapter_seven}
    )
    assert chapter_eight[0]["shotId"] == "shot-015"
    assert chapter_eight[-1]["shotId"] == "shot-016"
    assert not any(item["reverse"] for item in segments)
    assert all(0.85 <= float(item["playbackRate"]) <= 1.15 for item in segments)
    repeated_inpoints: dict[str, list[int]] = {}
    for segment in segments:
        repeated_inpoints.setdefault(segment["shotId"], []).append(segment["sourceInFrames"])
    assert all(
        len(set(values)) > 1
        for values in repeated_inpoints.values()
        if len(values) > 1
    )

    outputs = export_davinci_package(
        first,
        output_root=tmp_path / "davinci",
        ffmpeg="ffmpeg.exe",
    )
    assert Path(outputs["fcpxml"]).name == "trackprompt-timeline.fcpxml"
    assert Path(outputs["fcp7Xml"]).name == "trackprompt-timeline.xml"
    assert Path(outputs["edl"]).name == "trackprompt-timeline.edl"
    assert Path(outputs["previewOutput"]).name == "autonomous-preview-1080p.mp4"
    ET.parse(outputs["fcpxml"])
    ET.parse(outputs["fcp7Xml"])
    assert "FCM: NON-DROP FRAME" in Path(outputs["edl"]).read_text(encoding="utf-8")
    coverage = read_json(Path(outputs["coverageReport"]))
    assert isinstance(coverage, dict)
    assert coverage["continuous"] is True
    assert coverage["allSixteenShotsUsed"] is True
    assert coverage["editorialChecksPassed"] is True
    assert Path(outputs["editSheet"]).read_text(encoding="utf-8-sig")
    assert Path(outputs["markers"]).read_text(encoding="utf-8-sig")


def test_glitch_package_remains_valid_with_its_data_driven_blueprint() -> None:
    config = load_project_config(GLITCH / "project-config.json")
    shots = load_shot_bank(GLITCH / "shot-bank.json")
    blueprint = load_edit_blueprint(
        GLITCH / "edit-blueprint.json",
        project_id="the-glitch-is-me",
        title="The Glitch Is Me",
    )
    assert len(config.required_shot_ids) == len(shots) == 16
    assert blueprint["timelineTreatment"]["version"] == "the-glitch-is-me-rough-cut-1.0.0"
    assert blueprint["exports"]["fcpxml"] == "the-glitch-is-me-rough-cut.fcpxml"
