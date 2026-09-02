from __future__ import annotations

import hashlib
import json
import math
import re
import struct
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import UUID

import numpy as np
from numpy.typing import NDArray
from pydantic import ValidationError

from ....privacy import secure_private_directory, secure_private_file
from ....subprocess_utils import ProcessTimedOut, run_process_bounded
from ..capture import _artifact, _atomic_json
from ..preflight import sha256_file
from ..production import (
    SpectrumArtifact,
    SpectrumArtifactType,
    SpectrumProductionError,
    SpectrumProductionState,
    review_frame_timestamps,
)
from .composition import COMPOSITION_MASTER_DURATION_SECONDS, GeometryComposition

REVIEW_WIDTH = 960
REVIEW_HEIGHT = 540
COMPARISON_DIRECTORY = "output/comparison-3.7"
MANIFEST_RELATIVE_PATH = f"{COMPARISON_DIRECTORY}/manifest.json"
SANITY_RELATIVE_PATH = f"{COMPARISON_DIRECTORY}/visual-sanity-report.json"
_HASH = re.compile(r"^[0-9a-f]{64}$")
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_EXTRACTION_POLICY: dict[str, Any] = {
    "timestampPrecisionSeconds": 0.001,
    "filters": "scale=960:540:flags=lanczos",
    "canonicalPngCompressionLevel": 6,
    "lineageComparison": "decoded-rgb24-sha256-against-source-video",
    "comparisonLayout": "milestone-3.6-left-milestone-3.7-right",
}
_EVIDENCE_LIMIT = (
    "Luminance and occupancy are rendered-frame sanity checks, not proof that bars "
    "are absent or that the design is aesthetically approved. Production-element "
    "removal also requires the typed composition contract, runtime implementation "
    "checks, and rendered-frame or playback inspection. User aesthetic approval is pending."
)


@dataclass(frozen=True, slots=True)
class ReviewPixels:
    width: int
    height: int
    rgb: bytes

    def __post_init__(self) -> None:
        if (
            self.width not in {REVIEW_WIDTH, REVIEW_WIDTH * 2}
            or self.height != REVIEW_HEIGHT
            or len(self.rgb) != self.width * self.height * 3
        ):
            raise SpectrumProductionError("Review-frame pixel dimensions are invalid.")

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.rgb).hexdigest()


@dataclass(frozen=True, slots=True)
class _ReviewSource:
    root: Path
    manifest: dict[str, Any]
    job_id: str
    video_path: Path
    video_relative_path: str
    video_sha256: str
    master_duration_seconds: float

    def record(self) -> dict[str, Any]:
        return {
            "jobId": self.job_id,
            "recordedWorkspaceHash": self.manifest["generatedWorkspaceHash"],
            "designSha256": self.manifest["designHash"],
            "masterDurationSeconds": self.master_duration_seconds,
            "video": _file_record(self.root, self.video_path),
        }


def _object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise SpectrumProductionError("A composition-review record is unreadable.") from exc
    if not isinstance(value, dict):
        raise SpectrumProductionError("A composition-review record must be an object.")
    return value


def _owned_path(root: Path, relative: str, *, must_exist: bool = True) -> Path:
    logical = PurePosixPath(relative)
    if (
        not relative
        or "\\" in relative
        or ":" in relative
        or logical.is_absolute()
        or logical.as_posix() != relative
        or any(part in {".", ".."} for part in logical.parts)
    ):
        raise SpectrumProductionError("A composition-review artifact path is invalid.")
    resolved_root = root.resolve(strict=True)
    candidate = resolved_root.joinpath(*logical.parts)
    for part in (root, *candidate.parents, candidate):
        if part == resolved_root.parent:
            continue
        is_junction = getattr(part, "is_junction", None)
        if part.is_symlink() or (callable(is_junction) and is_junction()):
            raise SpectrumProductionError("Linked composition-review paths are not accepted.")
    try:
        candidate.resolve(strict=must_exist).relative_to(resolved_root)
    except (OSError, ValueError) as exc:
        raise SpectrumProductionError("A composition-review artifact is missing or outside its job.") from exc
    if must_exist and not candidate.is_file():
        raise SpectrumProductionError("A composition-review artifact is missing.")
    return candidate


def _file_record(root: Path, path: Path) -> dict[str, Any]:
    return {
        "relativePath": path.relative_to(root).as_posix(),
        "sha256": sha256_file(path),
        "sizeBytes": path.stat().st_size,
    }


def _hash(value: object) -> str:
    if not isinstance(value, str) or _HASH.fullmatch(value) is None:
        raise SpectrumProductionError("A composition-review source hash is invalid.")
    return value


def _source(job_root: Path, *, expected_video_sha256: str | None = None) -> _ReviewSource:
    is_junction = getattr(job_root, "is_junction", None)
    if job_root.is_symlink() or (callable(is_junction) and is_junction()):
        raise SpectrumProductionError("Linked composition-review paths are not accepted.")
    try:
        root = job_root.resolve(strict=True)
        job_id = str(UUID(root.name))
    except (OSError, ValueError) as exc:
        raise SpectrumProductionError("The composition-review job identity is invalid.") from exc
    if root.name != job_id or UUID(job_id).version != 4:
        raise SpectrumProductionError("The composition-review job identity is invalid.")
    manifest = _object(_owned_path(root, "manifest.json"))
    if (
        manifest.get("jobId") != job_id
        or manifest.get("mode") != "production"
        or manifest.get("backgroundMode") != "generative-geometry"
        or manifest.get("state") != "COMPLETE"
        or not isinstance(manifest.get("validationReport"), dict)
        or manifest["validationReport"].get("valid") is not True
    ):
        raise SpectrumProductionError("Composition review requires a complete validated production job.")
    _hash(manifest.get("generatedWorkspaceHash"))
    expected_design = _hash(manifest.get("designHash"))
    if sha256_file(_owned_path(root, "design.json")) != expected_design:
        raise SpectrumProductionError("The composition-review source design has changed.")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        raise SpectrumProductionError("The composition-review source artifacts are invalid.")
    finals = [item for item in artifacts if isinstance(item, dict) and item.get("artifactType") == "final-video"]
    if len(finals) != 1 or not isinstance(finals[0].get("relativePath"), str):
        raise SpectrumProductionError("A unique recorded final video is required for composition review.")
    final = finals[0]
    video = _owned_path(root, final["relativePath"])
    digest = sha256_file(video)
    if (
        digest != _hash(final.get("sha256"))
        or video.stat().st_size != final.get("sizeBytes")
        or video.stat().st_size <= 0
        or video.suffix != ".mp4"
        or (expected_video_sha256 is not None and digest != _hash(expected_video_sha256))
    ):
        raise SpectrumProductionError("The composition-review source video hash or size does not match.")
    timing = manifest.get("masterTiming")
    duration = timing.get("masterDurationSeconds") if isinstance(timing, dict) else None
    if (
        isinstance(duration, bool)
        or not isinstance(duration, (int, float))
        or not math.isfinite(duration)
        or abs(duration - COMPOSITION_MASTER_DURATION_SECONDS) > 0.000001
    ):
        raise SpectrumProductionError("Composition review requires the complete intentional Scattered master.")
    return _ReviewSource(root, manifest, job_id, video, final["relativePath"], digest, float(duration))


def _png_dimensions(path: Path) -> tuple[int, int]:
    try:
        with path.open("rb") as stream:
            header = stream.read(24)
    except OSError as exc:
        raise SpectrumProductionError("A composition-review PNG is unavailable.") from exc
    if len(header) != 24 or header[:8] != _PNG_SIGNATURE or header[12:16] != b"IHDR":
        raise SpectrumProductionError("A composition-review frame is not a PNG.")
    return struct.unpack(">II", header[16:24])


def _ffmpeg(args: list[str], *, stdout_limit: int = 64_000) -> bytes:
    try:
        result = run_process_bounded(
            args,
            timeout_seconds=60,
            stdout_limit=stdout_limit,
            stderr_limit=64_000,
        )
    except (OSError, ProcessTimedOut) as exc:
        raise SpectrumProductionError("FFmpeg could not inspect a composition-review frame.") from exc
    if result.returncode != 0 or result.stdout_exceeded or result.stderr_exceeded:
        raise SpectrumProductionError("FFmpeg rejected a composition-review frame.")
    return result.stdout


def _video_pixels(ffmpeg_path: str, video: Path, timestamp: float) -> ReviewPixels:
    rgb = _ffmpeg(
        [
            ffmpeg_path, "-hide_banner", "-loglevel", "error", "-nostdin",
            "-ss", f"{timestamp:.3f}", "-i", str(video), "-frames:v", "1",
            "-vf", "scale=960:540:flags=lanczos", "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1",
        ],
        stdout_limit=REVIEW_WIDTH * REVIEW_HEIGHT * 3,
    )
    return ReviewPixels(REVIEW_WIDTH, REVIEW_HEIGHT, rgb)


def _png_pixels(ffmpeg_path: str, frame: Path) -> ReviewPixels:
    width, height = _png_dimensions(frame)
    if width not in {REVIEW_WIDTH, REVIEW_WIDTH * 2} or height != REVIEW_HEIGHT:
        raise SpectrumProductionError("Composition-review frame dimensions are invalid.")
    rgb = _ffmpeg(
        [
            ffmpeg_path, "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(frame),
            "-frames:v", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1",
        ],
        stdout_limit=width * height * 3,
    )
    return ReviewPixels(width, height, rgb)


def _review_frames(
    ffmpeg_path: str, source: _ReviewSource,
) -> dict[str, tuple[dict[str, Any], ReviewPixels]]:
    review_artifacts = [
        item for item in source.manifest["artifacts"]
        if isinstance(item, dict) and item.get("artifactType") == "review-frame"
    ]
    result: dict[str, tuple[dict[str, Any], ReviewPixels]] = {}
    for label, timestamp in review_frame_timestamps(source.master_duration_seconds):
        relative = f"output/review-frames/{label}.png"
        matches = [item for item in review_artifacts if item.get("relativePath") == relative]
        if len(matches) != 1:
            raise SpectrumProductionError("A unique complete timestamped review-frame set is required.")
        frame = _owned_path(source.root, relative)
        record = _file_record(source.root, frame)
        recorded_time = matches[0].get("timestampSeconds")
        if (
            record["sha256"] != _hash(matches[0].get("sha256"))
            or record["sizeBytes"] != matches[0].get("sizeBytes")
            or isinstance(recorded_time, bool)
            or not isinstance(recorded_time, (int, float))
            or not math.isfinite(recorded_time)
            or abs(recorded_time - timestamp) > 0.000001
        ):
            raise SpectrumProductionError("A review-frame source hash, size, or timestamp does not match.")
        pixels = _png_pixels(ffmpeg_path, frame)
        if pixels.width != REVIEW_WIDTH or pixels.sha256 != _video_pixels(ffmpeg_path, source.video_path, timestamp).sha256:
            raise SpectrumProductionError("A review frame is stale or does not match its recorded source video.")
        record.update(
            sourceVideoSha256=source.video_sha256,
            decodedRgbSha256=pixels.sha256,
            width=pixels.width,
            height=pixels.height,
        )
        result[label] = (record, pixels)
    return result


def _composition(source: _ReviewSource) -> tuple[dict[str, Any], GeometryComposition, str]:
    runtime_path = _owned_path(source.root, "geometry/config/runtime-config.json")
    config = _object(runtime_path)
    try:
        composition = GeometryComposition.model_validate(config.get("composition"))
    except ValidationError as exc:
        raise SpectrumProductionError("The Milestone 3.7 composition contract is invalid.") from exc
    branding = config.get("branding")
    lab = config.get("developerLab")
    if (
        config.get("mode") != "production"
        or config.get("logoUrl") != "/assets/logo"
        or not isinstance(branding, dict)
        or branding.get("enabled") is not True
        or branding.get("artist") != "DJ WaZaHaKa"
        or branding.get("title") != "SCATTERED"
        or branding.get("meta") not in (None, "")
        or not isinstance(lab, dict)
        or lab.get("enabled") is not False
        or lab.get("previewOverride") is not None
    ):
        raise SpectrumProductionError("The production identity or preview/debug separation is invalid.")
    design = _object(_owned_path(source.root, "design.json"))
    preset = design.get("resolvedPreset")
    if not isinstance(preset, dict) or preset.get("composition") != config.get("composition"):
        raise SpectrumProductionError("The runtime composition does not match the recorded design.")
    return config, composition, sha256_file(runtime_path)


def _luminance(pixels: ReviewPixels) -> NDArray[np.float64]:
    rgb = np.frombuffer(pixels.rgb, dtype=np.uint8).reshape(pixels.height, pixels.width, 3)
    return np.asarray(
        np.sum(rgb * np.array([0.2126, 0.7152, 0.0722], dtype=np.float64), axis=2),
        dtype=np.float64,
    )


def build_composition_visual_sanity(
    runtime_config: dict[str, Any],
    frames: Mapping[str, ReviewPixels],
    frame_sha256: Mapping[str, str],
) -> dict[str, Any]:
    """Measure geometry-first sanity; never infer bar absence from image statistics."""
    try:
        composition = GeometryComposition.model_validate(runtime_config.get("composition"))
    except ValidationError as exc:
        raise SpectrumProductionError("The Milestone 3.7 composition contract is invalid.") from exc
    names = [label for label, _ in review_frame_timestamps(COMPOSITION_MASTER_DURATION_SECONDS)]
    if set(frames) != set(names) or set(frame_sha256) != set(names):
        raise SpectrumProductionError("The Milestone 3.7 visual review-frame set is incomplete.")
    if any(frame.width != REVIEW_WIDTH or frame.height != REVIEW_HEIGHT for frame in frames.values()):
        raise SpectrumProductionError("The Milestone 3.7 visual review-frame dimensions are invalid.")
    luma = {name: _luminance(frames[name]) for name in names}
    y, x = np.mgrid[0:REVIEW_HEIGHT, 0:REVIEW_WIDTH]
    nx = (x + 0.5) / REVIEW_WIDTH
    ny = (y + 0.5) / REVIEW_HEIGHT
    identity = np.zeros((REVIEW_HEIGHT, REVIEW_WIDTH), dtype=np.bool_)
    identity_regions: dict[str, NDArray[np.bool_]] = {}
    for zone in composition.readability.zones:
        distance = ((nx - zone.center[0]) / zone.radius[0]) ** 2 + ((ny - zone.center[1]) / zone.radius[1]) ** 2
        identity_regions[zone.id] = distance <= 1
        identity |= distance <= 4
    bounds = {
        "left-outside-identity": (0.02, 0.02, 0.33, 0.98),
        "lower": (0.28, 0.66, 0.98, 0.98),
        "center": (0.35, 0.22, 0.70, 0.78),
        "right": (0.70, 0.08, 0.98, 0.92),
    }
    active_names = names[:-1]
    region_results: dict[str, Any] = {}
    for name, (left, top, right, bottom) in bounds.items():
        region = (nx >= left) & (nx < right) & (ny >= top) & (ny < bottom) & ~identity
        if not np.any(region):
            raise SpectrumProductionError("The readability masks leave no reviewable geometry region.")
        # RGB luminance > 28 distinguishes luminous nodes from the canonical dark field.
        samples = {
            frame: {
                "averageLuminance": round(float(np.mean(luma[frame][region])), 6),
                "occupiedFraction": round(float(np.mean(luma[frame][region] > 28)), 8),
            }
            for frame in active_names
        }
        occupied_frames = sum(sample["occupiedFraction"] >= 0.0005 for sample in samples.values())
        region_results[name] = {
            "bounds": [left, top, right, bottom],
            "identityEllipseExclusionRadiusMultiple": 2,
            "pixelCount": int(np.count_nonzero(region)),
            "samples": samples,
            "framesWithOccupancy": occupied_frames,
            "passed": occupied_frames >= 2,
        }
    outside_identity = ~identity
    tail_mean = float(np.mean(luma["post-grid-tail-0313"][outside_identity]))
    eof_mean = float(np.mean(luma["near-eof"][outside_identity]))
    checks: list[dict[str, Any]] = [
        {"id": "production-clean-contract", "passed": True, "measured": composition.production.model_dump(mode="json", by_alias=True)},
        {"id": "full-frame-geometry-contract", "passed": composition.geometry_coverage == "full-frame"},
        {"id": "bounded-soft-readability-masks", "passed": True, "measured": composition.readability.model_dump(mode="json", by_alias=True)},
        {
            "id": "frames-not-black",
            "passed": all(float(np.max(value)) > 120 and float(np.mean(value)) > 2 for value in luma.values()),
            "measured": {name: round(float(np.mean(value)), 6) for name, value in luma.items()},
        },
        {
            "id": "identity-regions-present",
            "passed": all(float(np.max(luma[name][region])) > 120 for name in names for region in identity_regions.values()),
            "note": "Bright content in logo/identity regions is not OCR or aesthetic approval.",
        },
        {
            "id": "geometry-spatial-occupancy",
            "passed": all(region["passed"] for region in region_results.values()),
            "expected": "Each non-identity region has >=0.05% luminous pixels in at least two active review frames.",
            "measured": region_results,
        },
        {"id": "timeline-changes", "passed": len(set(frame_sha256.values())) == len(names), "measured": len(set(frame_sha256.values()))},
        {
            "id": "tail-decays",
            "passed": eof_mean < tail_mean,
            "measured": {"tailLuminance": round(tail_mean, 6), "nearEofLuminance": round(eof_mean, 6)},
        },
    ]
    return {
        "schemaVersion": "1.0.0",
        "compositionRevision": composition.revision,
        "valid": all(check["passed"] for check in checks),
        "checks": checks,
        "frameSha256": dict(frame_sha256),
        "userAestheticApproval": "pending",
        "evidenceLimits": _EVIDENCE_LIMIT,
    }


def _write_new(path: Path, content: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(content)
    secure_private_file(path)


def _write_json_new(path: Path, value: dict[str, Any]) -> None:
    _write_new(path, (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8"))


def _comparison_pixels(left: ReviewPixels, right: ReviewPixels) -> ReviewPixels:
    lhs = np.frombuffer(left.rgb, dtype=np.uint8).reshape(REVIEW_HEIGHT, REVIEW_WIDTH, 3)
    rhs = np.frombuffer(right.rgb, dtype=np.uint8).reshape(REVIEW_HEIGHT, REVIEW_WIDTH, 3)
    return ReviewPixels(REVIEW_WIDTH * 2, REVIEW_HEIGHT, np.concatenate((lhs, rhs), axis=1).tobytes())


def _create_comparison(ffmpeg_path: str, baseline: Path, current: Path, output: Path) -> None:
    _ffmpeg([
        ffmpeg_path, "-hide_banner", "-loglevel", "error", "-nostdin", "-n",
        "-i", str(baseline), "-i", str(current), "-filter_complex", "[0:v][1:v]hstack=inputs=2[out]",
        "-map", "[out]", "-frames:v", "1", "-compression_level", "6", str(output),
    ])
    secure_private_file(output)


def _recheck_frame_files(
    source: _ReviewSource,
    frames: Mapping[str, tuple[dict[str, Any], ReviewPixels]],
) -> None:
    for record, _pixels in frames.values():
        path = _owned_path(source.root, record["relativePath"])
        if sha256_file(path) != record["sha256"] or path.stat().st_size != record["sizeBytes"]:
            raise SpectrumProductionError("A source review frame changed during evidence inspection.")


def create_composition_review(
    ffmpeg_path: str,
    job_root: Path,
    baseline_root: Path,
    *,
    expected_baseline_sha256: str,
) -> dict[str, Any]:
    """Create new-job-only comparisons after genuine production has completed.

    Existing frame files are checked against decoded pixels from their actual
    source videos. PNG compression differences therefore do not affect lineage.
    A partially created evidence directory is preserved, never overwritten.
    """
    current = _source(job_root)
    baseline = _source(baseline_root, expected_video_sha256=expected_baseline_sha256)
    if current.root == baseline.root or current.video_sha256 == baseline.video_sha256:
        raise SpectrumProductionError("Composition review requires a distinct newly rendered source video.")
    config, composition, config_sha = _composition(current)
    evidence_root = _owned_path(current.root, COMPARISON_DIRECTORY, must_exist=False)
    if evidence_root.exists():
        raise SpectrumProductionError("The composition-review destination already exists; existing evidence was preserved.")
    current_frames = _review_frames(ffmpeg_path, current)
    baseline_frames = _review_frames(ffmpeg_path, baseline)
    sanity = build_composition_visual_sanity(
        config,
        {label: value[1] for label, value in current_frames.items()},
        {label: value[0]["sha256"] for label, value in current_frames.items()},
    )
    evidence_root.mkdir()
    secure_private_directory(evidence_root)
    for child in ("milestone-3.6", "side-by-side"):
        directory = evidence_root / child
        directory.mkdir()
        secure_private_directory(directory)
    entries: list[dict[str, Any]] = []
    for label, timestamp in review_frame_timestamps(current.master_duration_seconds):
        baseline_record, baseline_pixels = baseline_frames[label]
        current_record, current_pixels = current_frames[label]
        baseline_copy = evidence_root / "milestone-3.6" / f"{label}.png"
        _write_new(baseline_copy, _owned_path(baseline.root, baseline_record["relativePath"]).read_bytes())
        comparison = evidence_root / "side-by-side" / f"{label}.png"
        _create_comparison(ffmpeg_path, baseline_copy, _owned_path(current.root, current_record["relativePath"]), comparison)
        expected_pixels = _comparison_pixels(baseline_pixels, current_pixels)
        if _png_pixels(ffmpeg_path, comparison).sha256 != expected_pixels.sha256:
            raise SpectrumProductionError("The side-by-side comparison does not match its left and right sources.")
        entries.append({
            "label": label,
            "timestampSeconds": timestamp,
            "current": current_record,
            "baseline": {**baseline_record, **_file_record(current.root, baseline_copy)},
            "comparison": {
                **_file_record(current.root, comparison),
                "width": REVIEW_WIDTH * 2,
                "height": REVIEW_HEIGHT,
                "decodedRgbSha256": expected_pixels.sha256,
                "leftSourceVideoSha256": baseline.video_sha256,
                "rightSourceVideoSha256": current.video_sha256,
            },
        })
    _write_json_new(current.root / SANITY_RELATIVE_PATH, sanity)
    payload = {
        "schemaVersion": "1.0.0",
        "milestone": "3.7",
        "compositionRevision": composition.revision,
        "manifestRelativePath": MANIFEST_RELATIVE_PATH,
        "current": current.record(),
        "baseline": {"milestone": "3.6", **baseline.record()},
        "runtimeConfig": {
            "relativePath": "geometry/config/runtime-config.json",
            "sha256": config_sha,
        },
        "extraction": dict(_EXTRACTION_POLICY),
        "frames": entries,
        "visualSanity": {**_file_record(current.root, current.root / SANITY_RELATIVE_PATH), "valid": sanity["valid"]},
        "userAestheticApproval": "pending",
        "evidenceLimits": _EVIDENCE_LIMIT,
    }
    # Recheck sources after extraction so a concurrently replaced video is never
    # published as the source of already extracted frames.
    _source(current.root, expected_video_sha256=current.video_sha256)
    _source(baseline.root, expected_video_sha256=baseline.video_sha256)
    _recheck_frame_files(current, current_frames)
    _recheck_frame_files(baseline, baseline_frames)
    if sha256_file(current.root / "geometry/config/runtime-config.json") != config_sha:
        raise SpectrumProductionError("The composition changed while its review evidence was built.")
    _write_json_new(current.root / MANIFEST_RELATIVE_PATH, payload)
    return payload


def validate_composition_review(
    ffmpeg_path: str,
    job_root: Path,
    baseline_root: Path,
    manifest_payload: dict[str, Any],
) -> dict[str, Any]:
    """Read-only validation of source, PNG, decoded-pixel, and comparison lineage."""
    payload = manifest_payload
    try:
        current_record = payload["current"]
        baseline_record = payload["baseline"]
        current = _source(job_root, expected_video_sha256=_hash(current_record["video"]["sha256"]))
        baseline = _source(baseline_root, expected_video_sha256=_hash(baseline_record["video"]["sha256"]))
        if (
            current.root == baseline.root
            or current.video_sha256 == baseline.video_sha256
            or payload["schemaVersion"] != "1.0.0"
            or payload["milestone"] != "3.7"
            or payload["manifestRelativePath"] != MANIFEST_RELATIVE_PATH
            or current_record != current.record()
            or baseline_record != {"milestone": "3.6", **baseline.record()}
            or payload["userAestheticApproval"] != "pending"
            or payload["evidenceLimits"] != _EVIDENCE_LIMIT
            or payload["extraction"] != _EXTRACTION_POLICY
        ):
            raise SpectrumProductionError("The composition-review source lineage is invalid.")
        config, composition, config_sha = _composition(current)
        if payload["compositionRevision"] != composition.revision or payload["runtimeConfig"] != {
            "relativePath": "geometry/config/runtime-config.json", "sha256": config_sha,
        }:
            raise SpectrumProductionError("The composition-review runtime source hash does not match.")
        current_frames = _review_frames(ffmpeg_path, current)
        baseline_frames = _review_frames(ffmpeg_path, baseline)
        entries = payload["frames"]
        expected = review_frame_timestamps(current.master_duration_seconds)
        if not isinstance(entries, list) or len(entries) != len(expected):
            raise SpectrumProductionError("The composition-review timestamp set is incomplete.")
        for entry, (label, timestamp) in zip(entries, expected, strict=True):
            if entry["label"] != label or entry["timestampSeconds"] != timestamp or entry["current"] != current_frames[label][0]:
                raise SpectrumProductionError("A composition-review timestamp or current frame does not match.")
            baseline_path = _owned_path(current.root, f"{COMPARISON_DIRECTORY}/milestone-3.6/{label}.png")
            if entry["baseline"] != {**baseline_frames[label][0], **_file_record(current.root, baseline_path)}:
                raise SpectrumProductionError("A copied baseline review frame has changed.")
            if sha256_file(baseline_path) != baseline_frames[label][0]["sha256"]:
                raise SpectrumProductionError("A copied baseline frame does not match its source.")
            comparison = _owned_path(current.root, f"{COMPARISON_DIRECTORY}/side-by-side/{label}.png")
            comparison_pixels = _comparison_pixels(baseline_frames[label][1], current_frames[label][1])
            if entry["comparison"] != {
                **_file_record(current.root, comparison),
                "width": REVIEW_WIDTH * 2,
                "height": REVIEW_HEIGHT,
                "decodedRgbSha256": comparison_pixels.sha256,
                "leftSourceVideoSha256": baseline.video_sha256,
                "rightSourceVideoSha256": current.video_sha256,
            } or _png_pixels(ffmpeg_path, comparison).sha256 != comparison_pixels.sha256:
                raise SpectrumProductionError("A side-by-side frame does not match its recorded sources.")
        sanity = build_composition_visual_sanity(
            config,
            {label: value[1] for label, value in current_frames.items()},
            {label: value[0]["sha256"] for label, value in current_frames.items()},
        )
        sanity_path = _owned_path(current.root, SANITY_RELATIVE_PATH)
        if payload["visualSanity"] != {**_file_record(current.root, sanity_path), "valid": sanity["valid"]} or _object(sanity_path) != sanity:
            raise SpectrumProductionError("The visual-sanity evidence has changed.")
        _source(current.root, expected_video_sha256=current.video_sha256)
        _source(baseline.root, expected_video_sha256=baseline.video_sha256)
        _recheck_frame_files(current, current_frames)
        _recheck_frame_files(baseline, baseline_frames)
        if sha256_file(current.root / "geometry/config/runtime-config.json") != config_sha:
            raise SpectrumProductionError("The composition changed while its review evidence was inspected.")
        return {
            "valid": sanity["valid"],
            "sourceLineageValid": True,
            "matchedFrames": len(entries),
            "currentVideoSha256": current.video_sha256,
            "baselineVideoSha256": baseline.video_sha256,
            "userAestheticApproval": "pending",
            "evidenceLimits": _EVIDENCE_LIMIT,
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise SpectrumProductionError("The composition-review manifest is malformed.") from exc


def register_composition_review(
    ffmpeg_path: str,
    job_root: Path,
    baseline_root: Path,
) -> dict[str, Any]:
    """Append validated comparison artifacts to this completed 3.7 job only.

    The comparison manifest and baseline job are read-only. Exact registration
    replays perform validation again but do not rewrite the current job manifest.
    Existing records are never replaced, including on a same-path disagreement.
    """
    current = _source(job_root)
    manifest = current.manifest
    if (
        manifest.get("compositionRevision") != "scattered-geometry-first-3.7"
        or manifest.get("visualQaRequired") is not True
    ):
        raise SpectrumProductionError("Review registration requires a complete 3.7 job with visual approval pending.")
    review_path = _owned_path(current.root, MANIFEST_RELATIVE_PATH)
    review_sha256 = sha256_file(review_path)
    payload = _object(review_path)
    validation = validate_composition_review(ffmpeg_path, current.root, baseline_root, payload)
    if validation.get("valid") is not True or validation.get("sourceLineageValid") is not True:
        raise SpectrumProductionError("Composition-review registration requires valid visual and source-lineage evidence.")

    artifacts: list[SpectrumArtifact] = [
        _artifact(
            current.root,
            review_path,
            SpectrumArtifactType.COMPARISON_MANIFEST,
            SpectrumProductionState.COMPLETE,
            "Validated Milestone 3.6/3.7 source-video and decoded review-frame lineage; aesthetic approval pending",
        ),
        _artifact(
            current.root,
            _owned_path(current.root, SANITY_RELATIVE_PATH),
            SpectrumArtifactType.VISUAL_SANITY_REPORT,
            SpectrumProductionState.COMPLETE,
            "Geometry-first rendered-frame sanity and production contract checks; not human aesthetic approval",
        ),
    ]
    expected_records = [
        {"sha256": review_sha256, "sizeBytes": review_path.stat().st_size},
        payload["visualSanity"],
    ]
    for entry in payload["frames"]:
        label = entry["label"]
        timestamp = entry["timestampSeconds"]
        for directory, record, provenance in (
            (
                "milestone-3.6", entry["baseline"],
                "Verified Milestone 3.6 baseline frame copied into this job for matched comparison",
            ),
            (
                "side-by-side", entry["comparison"],
                "Verified matched comparison: Milestone 3.6 left and Milestone 3.7 right",
            ),
        ):
            artifacts.append(_artifact(
                current.root,
                _owned_path(current.root, f"{COMPARISON_DIRECTORY}/{directory}/{label}.png"),
                SpectrumArtifactType.COMPARISON_FRAME,
                SpectrumProductionState.COMPLETE,
                provenance,
                timestamp_seconds=timestamp,
            ))
            expected_records.append(record)
    for artifact, expected in zip(artifacts, expected_records, strict=True):
        if artifact.sha256 != expected["sha256"] or artifact.size_bytes != expected["sizeBytes"]:
            raise SpectrumProductionError("Composition-review evidence changed before artifact registration.")

    existing = manifest["artifacts"]
    by_path: dict[str, dict[str, Any]] = {}
    for record in existing:
        if not isinstance(record, dict) or not isinstance(record.get("relativePath"), str):
            raise SpectrumProductionError("Existing production artifact records are malformed.")
        relative = record["relativePath"]
        if relative in by_path:
            raise SpectrumProductionError("Existing production artifact paths are ambiguous.")
        by_path[relative] = record
    additions: list[dict[str, Any]] = []
    for artifact in artifacts:
        encoded = artifact.model_dump(mode="json", by_alias=True)
        existing_record = by_path.get(artifact.relative_path)
        if existing_record is not None:
            if existing_record != encoded:
                raise SpectrumProductionError("A same-path composition-review artifact disagrees with the validated evidence.")
        else:
            additions.append(encoded)

    manifest_path = _owned_path(current.root, "manifest.json")
    if _object(manifest_path) != manifest or sha256_file(review_path) != review_sha256:
        raise SpectrumProductionError("The job or comparison manifest changed during review registration.")
    for artifact in artifacts:
        path = _owned_path(current.root, artifact.relative_path)
        if sha256_file(path) != artifact.sha256 or path.stat().st_size != artifact.size_bytes:
            raise SpectrumProductionError("Composition-review evidence changed during artifact registration.")
    if not additions:
        return manifest
    updated = {**manifest, "artifacts": [*existing, *additions]}
    _atomic_json(manifest_path, updated)
    if _object(manifest_path) != updated:
        raise SpectrumProductionError("The composition-review artifact registration could not be confirmed.")
    return updated
