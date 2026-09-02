from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class FinalMediaEvidence:
    width: int
    height: int
    fps: float
    duration_seconds: float
    video_codec: str
    audio_codec: str | None
    frame_count: int | None


@dataclass(frozen=True, slots=True)
class FinalQcResult:
    passed: bool
    checks: dict[str, bool]
    evidence: FinalMediaEvidence

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": "1.0.0",
            "passed": self.passed,
            "checks": self.checks,
            "media": {
                "width": self.evidence.width,
                "height": self.evidence.height,
                "fps": self.evidence.fps,
                "durationSeconds": self.evidence.duration_seconds,
                "videoCodec": self.evidence.video_codec,
                "audioCodec": self.evidence.audio_codec,
                "frameCount": self.evidence.frame_count,
            },
        }


def _ratio(value: object) -> float:
    if not isinstance(value, str) or "/" not in value:
        return 0
    numerator, denominator = value.split("/", 1)
    try:
        return float(numerator) / float(denominator)
    except (ValueError, ZeroDivisionError):
        return 0


def _float_value(value: object, field: str) -> float:
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        raise ValueError(f"ffprobe {field} is invalid")
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"ffprobe {field} is invalid") from exc


def _optional_int(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def parse_ffprobe_contract(value: dict[str, Any]) -> FinalMediaEvidence:
    streams = value.get("streams")
    media_format = value.get("format")
    if not isinstance(streams, list) or not isinstance(media_format, dict):
        raise ValueError("ffprobe result is invalid")
    video = next(
        (item for item in streams if isinstance(item, dict) and item.get("codec_type") == "video"),
        None,
    )
    audio = next(
        (item for item in streams if isinstance(item, dict) and item.get("codec_type") == "audio"),
        None,
    )
    if not isinstance(video, dict):
        raise ValueError("The final output has no video stream")
    duration_raw = media_format.get("duration") or video.get("duration")
    frame_count_raw = video.get("nb_read_frames") or video.get("nb_frames")
    return FinalMediaEvidence(
        width=int(video.get("width") or 0),
        height=int(video.get("height") or 0),
        fps=_ratio(video.get("avg_frame_rate")),
        duration_seconds=_float_value(duration_raw, "duration"),
        video_codec=str(video.get("codec_name") or "unknown"),
        audio_codec=str(audio.get("codec_name")) if isinstance(audio, dict) else None,
        frame_count=_optional_int(frame_count_raw),
    )


def validate_final_contract(
    evidence: FinalMediaEvidence,
    *,
    source_duration_seconds: float,
    required_scene_count: int,
    represented_scene_ids: list[str],
    edit_transition_count: int,
    expected_manifest_hashes_match: bool,
) -> FinalQcResult:
    checks = {
        "width": evidence.width == 1920,
        "height": evidence.height == 1080,
        "fps": abs(evidence.fps - 24.0) < 0.001,
        "audioPresent": evidence.audio_codec is not None,
        "durationWithinOneFrame": abs(evidence.duration_seconds - source_duration_seconds) <= 1 / 24,
        "requiredScenes": len(set(represented_scene_ids)) == required_scene_count,
        "transitions": edit_transition_count == required_scene_count - 1,
        "freshness": expected_manifest_hashes_match,
    }
    return FinalQcResult(passed=all(checks.values()), checks=checks, evidence=evidence)


def freshness_manifest(paths: dict[str, Path]) -> dict[str, Any]:
    hashes: dict[str, str] = {}
    for role, path in sorted(paths.items()):
        if not path.is_file():
            raise ValueError(f"Required freshness input is unavailable: {role}")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(4 * 1024 * 1024):
                digest.update(chunk)
        hashes[role] = digest.hexdigest()
    identity = hashlib.sha256(
        json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {"schemaVersion": "1.0.0", "identity": identity, "hashes": hashes}
