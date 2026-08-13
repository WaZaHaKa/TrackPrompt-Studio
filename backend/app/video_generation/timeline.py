from __future__ import annotations

import math
import os
import shutil
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .contracts import ContractError
from .jsonio import read_json

TIMELINE_SCHEMA_VERSION = "1.0.0"


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
    if not isinstance(value, dict) or value.get("schemaVersion") != TIMELINE_SCHEMA_VERSION:
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
    candidates = sorted(
        {
            round(boundary, 6)
            for boundary in _extract_analysis_boundaries(value)
            if 0 < boundary < duration_seconds
        }
    )
    return tuple(candidates)


def _snap(value: float, candidates: Iterable[float], tolerance: float) -> float:
    nearest = min(candidates, key=lambda item: abs(item - value), default=value)
    return nearest if abs(nearest - value) <= tolerance else value


def resolve_timeline(
    *,
    project_id: str,
    title: str,
    audio_path: Path,
    chapter_map_path: Path,
    clips_root: Path,
    clip_paths: dict[str, Path] | None = None,
    output_width: int = 1920,
    output_height: int = 1080,
    fps: int = 24,
    generated_clip_duration_seconds: int = 8,
    target_edit_seconds: float = 6.0,
    analysis_shot_plan_path: Path | None = None,
    ffprobe: str | None = None,
) -> dict[str, Any]:
    if fps != 24:
        raise ContractError("The Veo fast lane uses a 24 FPS delivery timeline")
    duration_seconds = audio_duration_seconds(audio_path, ffprobe=ffprobe)
    total_frames = max(1, round(duration_seconds * fps))
    chapters = load_chapter_map(chapter_map_path)
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
    target_frames = max(1, round(target_edit_seconds * fps))
    segments: list[TimelineSegment] = []
    markers: list[dict[str, Any]] = []
    global_index = 0

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
        for local_index in range(segment_count):
            duration_frames = base + (1 if local_index < remainder else 0)
            shot_id = chapter.shot_ids[local_index % len(chapter.shot_ids)]
            max_in = max(0, generated_clip_frames - duration_frames)
            source_in = 0
            if max_in:
                phase = (local_index // len(chapter.shot_ids)) % 3
                source_in = min(max_in, round(max_in * (phase / 2)))
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
                    transition_in=(chapter.transition_in if local_index == 0 else "cut"),
                    transition_out=(chapter.transition_out if local_index == segment_count - 1 else "cut"),
                    editorial_note=(
                        "Primary generated shot; alternate in-point chosen "
                        "deterministically when the clip is reused."
                    ),
                )
            )
            chapter_cursor += duration_frames

    if not segments or segments[-1].timeline_start_frames + segments[-1].duration_frames != total_frames:
        raise ContractError("resolved timeline did not cover the complete audio clock")

    return {
        "schemaVersion": TIMELINE_SCHEMA_VERSION,
        "projectId": project_id,
        "title": title,
        "timeline": {
            "fps": fps,
            "width": output_width,
            "height": output_height,
            "durationFrames": total_frames,
            "durationSeconds": total_frames / fps,
            "audioPath": str(audio_path.resolve()),
            "generatedClipDurationSeconds": generated_clip_duration_seconds,
            "targetEditSeconds": target_edit_seconds,
        },
        "markers": markers,
        "segments": [segment.to_dict() for segment in segments],
    }
