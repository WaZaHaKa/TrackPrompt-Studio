from __future__ import annotations

import math
import os
import shutil
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from .contracts import ContractError
from .jsonio import read_json

TIMELINE_SCHEMA_VERSION = "1.1.0"
DEFAULT_TREATMENT_VERSION = "trackprompt-straight-cut-1.0.0"
DEFAULT_EXPORT_FILES = {
    "fcpxml": "trackprompt-timeline.fcpxml",
    "fcp7": "trackprompt-timeline.xml",
    "edl": "trackprompt-timeline.edl",
    "preview1080p": "autonomous-preview-1080p.mp4",
    "preview4k": "autonomous-preview-4k.mp4",
}


@dataclass(frozen=True)
class ChapterRange:
    chapter_id: str
    title: str
    start_fraction: float
    end_fraction: float
    shot_ids: tuple[str, ...]
    transition_in: str
    transition_out: str


@dataclass(frozen=True)
class TimelineSegment:
    segment_id: str
    chapter_id: str
    shot_id: str
    clip_path: str
    timeline_start_frames: int
    duration_frames: int
    source_in_frames: int
    source_duration_frames: int
    transition_in: str
    transition_out: str
    editorial_note: str
    playback_rate: float
    reverse: bool
    ping_pong: bool
    freeze_frames: int
    crop_scale: float
    crop_x: float
    crop_y: float
    overlay_shot_id: str | None
    overlay_opacity: float
    fade_in_frames: int
    fade_out_frames: int
    treatment: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "segmentId": self.segment_id,
            "chapterId": self.chapter_id,
            "shotId": self.shot_id,
            "clipPath": self.clip_path,
            "timelineStartFrames": self.timeline_start_frames,
            "durationFrames": self.duration_frames,
            "sourceInFrames": self.source_in_frames,
            "sourceDurationFrames": self.source_duration_frames,
            "transitionIn": self.transition_in,
            "transitionOut": self.transition_out,
            "editorialNote": self.editorial_note,
            "playbackRate": self.playback_rate,
            "reverse": self.reverse,
            "pingPong": self.ping_pong,
            "freezeFrames": self.freeze_frames,
            "cropScale": self.crop_scale,
            "cropX": self.crop_x,
            "cropY": self.crop_y,
            "overlayShotId": self.overlay_shot_id,
            "overlayOpacity": self.overlay_opacity,
            "fadeInFrames": self.fade_in_frames,
            "fadeOutFrames": self.fade_out_frames,
            "treatment": self.treatment,
        }


def audio_duration_seconds(path: Path, *, ffprobe: str | None = None) -> float:
    executable = ffprobe or os.getenv("TRACKPROMPT_MC_FFPROBE_PATH") or shutil.which("ffprobe")
    if not executable:
        raise ContractError("ffprobe is required to resolve the song timeline")
    result = subprocess.run(
        [
            executable,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise ContractError(f"ffprobe failed: {result.stderr.strip()}")
    try:
        duration = float(result.stdout.strip())
    except ValueError as exc:
        raise ContractError("ffprobe returned an invalid duration") from exc
    if duration <= 0:
        raise ContractError("audio duration must be positive")
    return duration


def load_chapter_map(path: Path) -> tuple[ChapterRange, ...]:
    value = read_json(path)
    if not isinstance(value, dict) or value.get("schemaVersion") != "1.0.0":
        raise ContractError("chapter map has an unsupported schemaVersion")
    chapters: list[ChapterRange] = []
    for item in value.get("chapters", []):
        chapter = ChapterRange(
            chapter_id=str(item["chapterId"]),
            title=str(item["title"]),
            start_fraction=float(item["startFraction"]),
            end_fraction=float(item["endFraction"]),
            shot_ids=tuple(str(value) for value in item["shotIds"]),
            transition_in=str(item.get("transitionIn", "cut")),
            transition_out=str(item.get("transitionOut", "cut")),
        )
        if not 0 <= chapter.start_fraction < chapter.end_fraction <= 1:
            raise ContractError(f"invalid normalized range for {chapter.chapter_id}")
        if not chapter.shot_ids:
            raise ContractError(f"{chapter.chapter_id} has no shot IDs")
        chapters.append(chapter)
    if not chapters:
        raise ContractError("chapter map contains no chapters")
    chapters.sort(key=lambda item: item.start_fraction)
    if abs(chapters[0].start_fraction) > 1e-9 or abs(chapters[-1].end_fraction - 1) > 1e-9:
        raise ContractError("chapter map must cover normalized range 0..1")
    for previous, current in zip(chapters, chapters[1:], strict=False):
        if abs(previous.end_fraction - current.start_fraction) > 1e-6:
            raise ContractError("chapter map must be contiguous")
    return tuple(chapters)


def _safe_export_filename(value: Any, *, field: str) -> str:
    name = str(value).strip()
    if not name or Path(name).name != name or name in {".", ".."}:
        raise ContractError(f"edit blueprint {field} must be a safe filename")
    return name


def load_edit_blueprint(
    path: Path | None,
    *,
    project_id: str,
    title: str,
) -> dict[str, Any]:
    """Load project-owned editorial decisions without teaching the engine song names."""
    default: dict[str, Any] = {
        "schemaVersion": "1.1.0",
        "projectId": project_id,
        "timelineTreatment": {
            "version": DEFAULT_TREATMENT_VERSION,
            "targetEditSeconds": 6.0,
            "default": {
                "playbackRates": [1.0],
                "playbackRateIndex": "timeline",
                "cropScales": [1.0],
                "cropScaleIndex": "timeline",
            },
            "chapters": {},
        },
        "exports": dict(DEFAULT_EXPORT_FILES),
        "coverage": {
            "requiredShotIds": [f"shot-{index:03d}" for index in range(1, 17)],
            "eventCountRange": [1, 10_000],
            "openingShotId": None,
            "closingShotId": None,
            "chapterRequirements": [],
        },
        "handoff": {
            "title": title,
            "summary": "Conservative local assembly for final artistic finishing in DaVinci Resolve.",
        },
    }
    if path is None:
        return default
    value = read_json(path)
    if not isinstance(value, dict) or value.get("schemaVersion") not in {"1.0.0", "1.1.0"}:
        raise ContractError("edit blueprint has an unsupported schemaVersion")
    if str(value.get("projectId", "")) != project_id:
        raise ContractError("edit blueprint and requested project IDs differ")

    timeline_treatment = value.get("timelineTreatment", default["timelineTreatment"])
    if not isinstance(timeline_treatment, dict):
        raise ContractError("edit blueprint timelineTreatment must be an object")
    version = str(timeline_treatment.get("version", DEFAULT_TREATMENT_VERSION)).strip()
    target = _number(timeline_treatment.get("targetEditSeconds"))
    if not version or target is None or target <= 0:
        raise ContractError("edit blueprint timeline treatment is invalid")
    default_rule = timeline_treatment.get("default", {})
    chapter_rules = timeline_treatment.get("chapters", {})
    if not isinstance(default_rule, dict) or not isinstance(chapter_rules, dict):
        raise ContractError("edit blueprint treatment rules must be objects")

    exports = dict(DEFAULT_EXPORT_FILES)
    supplied_exports = value.get("exports", {})
    if not isinstance(supplied_exports, dict):
        raise ContractError("edit blueprint exports must be an object")
    for key in exports:
        if key in supplied_exports:
            exports[key] = _safe_export_filename(supplied_exports[key], field=f"exports.{key}")

    coverage = value.get("coverage", default["coverage"])
    if not isinstance(coverage, dict):
        raise ContractError("edit blueprint coverage must be an object")
    required_ids = coverage.get("requiredShotIds", default["coverage"]["requiredShotIds"])
    event_range = coverage.get("eventCountRange", default["coverage"]["eventCountRange"])
    requirements = coverage.get("chapterRequirements", [])
    if (
        not isinstance(required_ids, list)
        or not all(isinstance(item, str) and item for item in required_ids)
        or len(required_ids) != len(set(required_ids))
    ):
        raise ContractError("edit blueprint requiredShotIds must be unique strings")
    if (
        not isinstance(event_range, list)
        or len(event_range) != 2
        or not all(isinstance(item, int) and not isinstance(item, bool) for item in event_range)
        or event_range[0] < 1
        or event_range[1] < event_range[0]
    ):
        raise ContractError("edit blueprint eventCountRange is invalid")
    if not isinstance(requirements, list) or not all(isinstance(item, dict) for item in requirements):
        raise ContractError("edit blueprint chapterRequirements must be objects")
    handoff = value.get("handoff", default["handoff"])
    if not isinstance(handoff, dict):
        raise ContractError("edit blueprint handoff must be an object")

    return {
        "schemaVersion": str(value["schemaVersion"]),
        "projectId": project_id,
        "timelineTreatment": {
            "version": version,
            "targetEditSeconds": target,
            "default": default_rule,
            "chapters": chapter_rules,
        },
        "exports": exports,
        "coverage": {
            "requiredShotIds": list(required_ids),
            "eventCountRange": list(event_range),
            "openingShotId": coverage.get("openingShotId"),
            "closingShotId": coverage.get("closingShotId"),
            "chapterRequirements": requirements,
        },
        "handoff": {
            "title": str(handoff.get("title", title)),
            "summary": str(handoff.get("summary", default["handoff"]["summary"])),
        },
    }


def editorial_export_files(value: dict[str, Any]) -> dict[str, str]:
    editorial = value.get("editorial", {})
    supplied = editorial.get("exportFiles", {}) if isinstance(editorial, dict) else {}
    if not isinstance(supplied, dict):
        supplied = {}
    result = dict(DEFAULT_EXPORT_FILES)
    for key in result:
        if key in supplied:
            result[key] = _safe_export_filename(supplied[key], field=f"exportFiles.{key}")
    return result


def _number(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _extract_analysis_boundaries(value: Any, inherited_fps: float | None = None) -> list[float]:
    boundaries: list[float] = []
    if isinstance(value, dict):
        fps = _number(value.get("fps")) or _number(value.get("frameRate")) or inherited_fps
        start = None
        end = None
        for key in (
            "startSeconds",
            "timelineStartSeconds",
            "sourceStartSeconds",
            "startTimeSeconds",
        ):
            start = _number(value.get(key))
            if start is not None:
                break
        for key in (
            "endSeconds",
            "timelineEndSeconds",
            "sourceEndSeconds",
            "endTimeSeconds",
        ):
            end = _number(value.get(key))
            if end is not None:
                break
        if fps:
            if start is None:
                frame = _number(value.get("startFrame"))
                if frame is not None:
                    start = frame / fps
            if end is None:
                frame = _number(value.get("endFrame"))
                if frame is not None:
                    # Existing TrackPrompt contracts commonly use inclusive end frames.
                    end = (frame + 1) / fps
        for boundary in (start, end):
            if boundary is not None and boundary >= 0:
                boundaries.append(boundary)
        for child in value.values():
            boundaries.extend(_extract_analysis_boundaries(child, fps))
    elif isinstance(value, list):
        for child in value:
            boundaries.extend(_extract_analysis_boundaries(child, inherited_fps))
    return boundaries


def analysis_boundaries(path: Path | None, duration_seconds: float) -> tuple[float, ...]:
    if not path:
        return ()
    value = read_json(path)
    all_boundaries = _extract_analysis_boundaries(value)
    # A retained ShotPlan can outlive the media it was authored against. Never
    # snap a new finishing master to an unrelated clock.
    if all_boundaries:
        inferred_duration = max(all_boundaries)
        if abs(inferred_duration - duration_seconds) > max(3.0, duration_seconds * 0.05):
            return ()
    candidates = sorted(
        {
            round(boundary, 6)
            for boundary in all_boundaries
            if 0 < boundary < duration_seconds
        }
    )
    return tuple(candidates)


def _snap(value: float, candidates: Iterable[float], tolerance: float) -> float:
    nearest = min(candidates, key=lambda item: abs(item - value), default=value)
    return nearest if abs(nearest - value) <= tolerance else value


def _cycle_value(
    rules: dict[str, Any],
    *,
    key: str,
    index_key: str,
    offset_key: str,
    local_index: int,
    global_index: int,
    fallback: float,
) -> float:
    values = rules.get(key, [fallback])
    if not isinstance(values, list) or not values:
        raise ContractError(f"edit blueprint {key} must be a non-empty list")
    normalized = [_number(value) for value in values]
    if any(value is None for value in normalized):
        raise ContractError(f"edit blueprint {key} must contain only numbers")
    index = local_index if rules.get(index_key, "timeline") == "chapter" else global_index
    offset = int(rules.get(offset_key, 0))
    return cast(float, normalized[(index + offset) % len(normalized)])


def _matches_pattern(
    value: Any,
    *,
    shot_id: str,
    local_index: int,
    global_index: int,
) -> bool:
    if not isinstance(value, dict):
        return False
    shot_ids = value.get("shotIds", [])
    if not isinstance(shot_ids, list) or (shot_ids and shot_id not in shot_ids):
        return False
    index = local_index if value.get("index", "chapter") == "chapter" else global_index
    modulo = int(value.get("modulo", 1))
    remainders = value.get("remainders", [0])
    if modulo < 1 or not isinstance(remainders, list):
        raise ContractError("edit blueprint conditional treatment is invalid")
    return index % modulo in {int(item) for item in remainders}


def resolve_timeline(
    *,
    project_id: str,
    title: str,
    audio_path: Path,
    chapter_map_path: Path,
    clips_root: Path,
    edit_blueprint_path: Path | None = None,
    clip_paths: dict[str, Path] | None = None,
    output_width: int = 1920,
    output_height: int = 1080,
    fps: int = 24,
    generated_clip_duration_seconds: int = 8,
    target_edit_seconds: float | None = None,
    analysis_shot_plan_path: Path | None = None,
    local_edit_digest: str | None = None,
    ffprobe: str | None = None,
) -> dict[str, Any]:
    if fps != 24:
        raise ContractError("The Veo fast lane uses a 24 FPS delivery timeline")
    duration_seconds = audio_duration_seconds(audio_path, ffprobe=ffprobe)
    total_frames = max(1, round(duration_seconds * fps))
    chapters = load_chapter_map(chapter_map_path)
    blueprint = load_edit_blueprint(
        edit_blueprint_path,
        project_id=project_id,
        title=title,
    )
    treatment = dict(blueprint["timelineTreatment"])
    treatment_version = str(treatment["version"])
    effective_target_edit_seconds = (
        target_edit_seconds
        if target_edit_seconds is not None
        else float(treatment["targetEditSeconds"])
    )
    if effective_target_edit_seconds <= 0:
        raise ContractError("target edit duration must be positive")
    default_treatment = dict(treatment.get("default", {}))
    chapter_treatments = treatment.get("chapters", {})
    if not isinstance(chapter_treatments, dict):
        raise ContractError("edit blueprint chapter treatments must be an object")
    snap_candidates = analysis_boundaries(analysis_shot_plan_path, duration_seconds)

    raw_boundaries = [0.0]
    for chapter in chapters[:-1]:
        target = chapter.end_fraction * duration_seconds
        raw_boundaries.append(_snap(target, snap_candidates, tolerance=3.0))
    raw_boundaries.append(duration_seconds)
    # Quantize once to timeline frames and enforce strict monotonicity.
    chapter_frame_boundaries = [0]
    for value in raw_boundaries[1:-1]:
        frame = round(value * fps)
        frame = max(chapter_frame_boundaries[-1] + 1, min(frame, total_frames - 1))
        chapter_frame_boundaries.append(frame)
    chapter_frame_boundaries.append(total_frames)

    generated_clip_frames = generated_clip_duration_seconds * fps
    target_frames = max(1, round(effective_target_edit_seconds * fps))
    segments: list[TimelineSegment] = []
    markers: list[dict[str, Any]] = []
    global_index = 0
    shot_use_counts: dict[str, int] = {}

    for chapter_index, chapter in enumerate(chapters):
        chapter_start = chapter_frame_boundaries[chapter_index]
        chapter_end = chapter_frame_boundaries[chapter_index + 1]
        chapter_frames = chapter_end - chapter_start
        segment_count = max(1, math.ceil(chapter_frames / target_frames))
        base = chapter_frames // segment_count
        remainder = chapter_frames % segment_count
        chapter_cursor = chapter_start
        markers.append(
            {
                "markerId": f"chapter-{chapter_index + 1:02d}",
                "chapterId": chapter.chapter_id,
                "title": chapter.title,
                "startFrames": chapter_start,
                "durationFrames": chapter_frames,
            }
        )
        chapter_rule_value = chapter_treatments.get(chapter.chapter_id, {})
        if not isinstance(chapter_rule_value, dict):
            raise ContractError(f"edit blueprint rule for {chapter.chapter_id} must be an object")
        rules = {**default_treatment, **chapter_rule_value}
        supplied_sequence = rules.get("shotSequence", list(chapter.shot_ids))
        if not isinstance(supplied_sequence, list) or not supplied_sequence:
            raise ContractError(f"edit blueprint shot sequence for {chapter.chapter_id} is invalid")
        shot_sequence = tuple(str(item) for item in supplied_sequence)

        for local_index in range(segment_count):
            duration_frames = base + (1 if local_index < remainder else 0)
            shot_id = shot_sequence[local_index % len(shot_sequence)]
            if local_index == segment_count - 1 and rules.get("closingShotId"):
                shot_id = str(rules["closingShotId"])
            max_in = max(0, generated_clip_frames - min(duration_frames, generated_clip_frames))
            shot_use_index = shot_use_counts.get(shot_id, 0)
            source_in = 0 if max_in == 0 or shot_use_index % 2 == 0 else max_in
            shot_use_counts[shot_id] = shot_use_index + 1
            playback_rate = _cycle_value(
                rules,
                key="playbackRates",
                index_key="playbackRateIndex",
                offset_key="playbackRateIndexOffset",
                local_index=local_index,
                global_index=global_index,
                fallback=1.0,
            )
            crop_scale = _cycle_value(
                rules,
                key="cropScales",
                index_key="cropScaleIndex",
                offset_key="cropScaleIndexOffset",
                local_index=local_index,
                global_index=global_index,
                fallback=1.0,
            )
            reverse = _matches_pattern(
                rules.get("reverse"),
                shot_id=shot_id,
                local_index=local_index,
                global_index=global_index,
            )
            ping_pong = _matches_pattern(
                rules.get("pingPong"),
                shot_id=shot_id,
                local_index=local_index,
                global_index=global_index,
            )
            freeze = rules.get("freeze")
            freeze_frames = (
                int(freeze.get("frames", 0))
                if _matches_pattern(
                    freeze,
                    shot_id=shot_id,
                    local_index=local_index,
                    global_index=global_index,
                )
                and isinstance(freeze, dict)
                else 0
            )
            overlay_shot_id = None
            overlay_opacity = 0.0
            overlay = rules.get("overlay")
            if _matches_pattern(
                overlay,
                shot_id=shot_id,
                local_index=local_index,
                global_index=global_index,
            ) and isinstance(overlay, dict):
                preferred = str(overlay.get("preferredShotId", ""))
                fallback = str(overlay.get("fallbackShotId", ""))
                overlay_shot_id = preferred if preferred and preferred != shot_id else fallback or None
                opacities = overlay.get("opacities", [0.2])
                if not isinstance(opacities, list) or not opacities:
                    raise ContractError("edit blueprint overlay opacities are invalid")
                overlay_opacity = float(opacities[local_index % len(opacities)])
            transition_in = chapter.transition_in if local_index == 0 else "cut"
            transition_out = chapter.transition_out if local_index == segment_count - 1 else "cut"
            fade_in_frames = 18 if chapter_index == 0 and local_index == 0 else (
                8 if "dissolve" in transition_in else 0
            )
            final_fade_out_frames = int(rules.get("finalFadeOutFrames", 0))
            fade_out_frames = final_fade_out_frames if (
                final_fade_out_frames and local_index == segment_count - 1
            ) else (
                8 if "dissolve" in transition_out else 0
            )
            global_index += 1
            segments.append(
                TimelineSegment(
                    segment_id=f"seg-{global_index:04d}",
                    chapter_id=chapter.chapter_id,
                    shot_id=shot_id,
                    clip_path=str(
                        (
                            clip_paths[shot_id]
                            if clip_paths is not None and shot_id in clip_paths
                            else clips_root / f"{shot_id}.mp4"
                        ).resolve()
                    ),
                    timeline_start_frames=chapter_cursor,
                    duration_frames=duration_frames,
                    source_in_frames=source_in,
                    source_duration_frames=min(duration_frames, generated_clip_frames),
                    transition_in=transition_in,
                    transition_out=transition_out,
                    editorial_note=(
                        "Deterministic local-only rough-cut treatment; original generated clip remains immutable."
                    ),
                    playback_rate=playback_rate,
                    reverse=reverse,
                    ping_pong=ping_pong,
                    freeze_frames=freeze_frames,
                    crop_scale=crop_scale,
                    crop_x=((global_index * 37) % 100) / 100,
                    crop_y=((global_index * 53) % 100) / 100,
                    overlay_shot_id=overlay_shot_id,
                    overlay_opacity=overlay_opacity,
                    fade_in_frames=fade_in_frames,
                    fade_out_frames=fade_out_frames,
                    treatment=f"{treatment_version}:{chapter.chapter_id}:{global_index:04d}",
                )
            )
            chapter_cursor += duration_frames

    if not segments or segments[-1].timeline_start_frames + segments[-1].duration_frames != total_frames:
        raise ContractError("resolved timeline did not cover the complete audio clock")

    return {
        "schemaVersion": TIMELINE_SCHEMA_VERSION,
        "projectId": project_id,
        "title": title,
        "localEditDigest": local_edit_digest,
        "treatmentVersion": treatment_version,
        "editorial": {
            "blueprintSchemaVersion": blueprint["schemaVersion"],
            "treatmentVersion": treatment_version,
            "exportFiles": blueprint["exports"],
            "coverage": blueprint["coverage"],
            "handoff": blueprint["handoff"],
        },
        "timeline": {
            "fps": fps,
            "width": output_width,
            "height": output_height,
            "durationFrames": total_frames,
            "durationSeconds": total_frames / fps,
            "audioPath": str(audio_path.resolve()),
            "generatedClipDurationSeconds": generated_clip_duration_seconds,
            "targetEditSeconds": effective_target_edit_seconds,
        },
        "markers": markers,
        "segments": [segment.to_dict() for segment in segments],
    }
