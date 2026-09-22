"""Offline Hayale V2 planning; importing or preparing this module cannot call GCP."""

from __future__ import annotations

import json
import math
import re
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parent
REPO = PROJECT.parents[2]
sys.path.insert(0, str(REPO / "backend"))
from app.video_generation.contracts import CompiledPlan, CompiledReferenceImage  # noqa: E402
from app.video_generation.gcp_veo import build_request_payload  # noqa: E402
from app.video_generation.jsonio import atomic_write_json, sha256_file, sha256_json  # noqa: E402
from app.video_generation.planning import compile_project_plan  # noqa: E402

PREPARATION = (
    REPO / ".trackprompt-data/video-generation/hayale-flowers-promo/v2-preparation"
)
DEFAULT_AUDIO = Path(
    "D:/HayaleFlowers/promo-2026-09-15/audio/hayale-remaster-17-09-2026.wav"
)
FPS = 24
TOTAL_FRAMES = 60 * FPS


def ensure_runtime_path(path: Path) -> None:
    if not path.resolve().is_relative_to(REPO.resolve()):
        raise ValueError("Runtime path escapes this repository")
    cursor = path.absolute()
    while cursor != REPO:
        if cursor.is_symlink() or cursor.is_junction():
            raise ValueError("Redirected runtime path refused")
        if cursor.parent == cursor:
            raise ValueError("Runtime path is outside the repository")
        cursor = cursor.parent


def edit_plan(onsets: list[float]) -> dict[str, Any]:
    """Use local onset cues, never song duration, to choose seven internal cuts."""
    evidence = [float(x) for x in onsets if math.isfinite(float(x)) and 0 < x < 60]
    boundaries = [0]
    for target in (6, 14, 22, 30, 38, 46, 54):
        nearby = [x for x in evidence if abs(x - target) <= 0.35]
        boundary = min(nearby, key=lambda x: (abs(x - target), x)) if nearby else target
        boundaries.append(round(boundary * FPS))
    boundaries.append(TOTAL_FRAMES)
    ids = ["shot-007", *[f"shot-{n:03d}" for n in range(1, 7)], "shot-007"]
    roles = [
        "logo-intro",
        "storefront",
        "store-tour-entrance",
        "store-tour-interior",
        "orchid",
        "bouquet",
        "rose",
        "logo-outro",
    ]
    events = []
    for index, (start, end, shot_id, role) in enumerate(
        zip(boundaries, boundaries[1:], ids, roles, strict=False), 1
    ):
        duration = end - start
        # No repetition or fabricated motion to fill the time. A maximum ~9%
        # gentle slowdown fits an eight-second source around adjacent onset cuts.
        rate = min(1.0, 192 / duration)
        events.append(
            {
                "segmentId": f"event-{index:03d}",
                "shotId": shot_id,
                "chapterId": role,
                "timelineStartFrames": start,
                "durationFrames": duration,
                "sourceInFrames": 0,
                "playbackRate": rate,
                "transitionIn": "cut",
                "transitionOut": "cut",
                "sourceFraming": "center",
                "cropScale": 1.0,
                "editorialNote": f"{role}: onset-aligned edit; original logo scene reused"
                if shot_id == "shot-007"
                else f"{role}: restrained single-shot camera movement",
            }
        )
    return {
        "schemaVersion": "1.1.0",
        "projectId": "hayale-flowers-promo",
        "title": "Hayale Flowers V2 - 60 seconds",
        "timeline": {
            "durationFrames": TOTAL_FRAMES,
            "fps": FPS,
            "width": 1920,
            "height": 1080,
            "audioPath": None,
            "normalizeSdr": True,
            "frameExact": True,
        },
        "segments": events,
        "markers": [
            {
                "title": role,
                "chapterId": role,
                "startFrames": start,
                "durationFrames": end - start,
            }
            for start, end, role in zip(boundaries, boundaries[1:], roles, strict=False)
        ],
        "syncMethod": "nearest-detected-onset-within-0.35s; fixed-60s-clock",
        "syncCaveat": "Onsets and tempo are local DSP estimates; no downbeat or generated-motion synchronization is asserted.",
        "audioPolicy": "optional local mux; pad or trim to 60 seconds without time-stretch; never uploaded",
        "editorial": {
            "exportFiles": {
                "fcpxml": "hayale-promo-v2.fcpxml",
                "fcp7": "hayale-promo-v2.xml",
                "edl": "hayale-promo-v2.edl",
                "preview1080p": "hayale-promo-v2-60s.mp4",
            },
            "coverage": {
                "requiredShotIds": [f"shot-{n:03d}" for n in range(1, 8)],
                "eventCountRange": [8, 8],
            },
            "handoff": {
                "title": "Hayale Flowers V2",
                "summary": "Seven unique Veo scenes; one logo scene reused at both ends. Sixty seconds regardless of audio. Artist review remains required.",
            },
        },
    }


def reference_bindings(preparation: Path = PREPARATION) -> list[dict[str, Any]]:
    ensure_runtime_path(preparation)
    root = preparation.resolve()
    manifest = json.loads((root / "references-manifest.json").read_text())
    references = manifest["references"]
    if [r["shotId"] for r in references] != [f"shot-{n:03d}" for n in range(1, 8)]:
        raise ValueError("Exactly seven ordered prepared references are required")
    for reference in references:
        path = (REPO / reference["preparedPath"]).resolve()
        if not path.is_relative_to(root / "references") or not path.is_file():
            raise ValueError("Reference path escapes the prepared reference directory")
        if path.stat().st_size > 20_000_000 or sha256_file(path) != reference["sha256"]:
            raise ValueError(
                "A prepared reference changed or exceeds the image input limit"
            )
        if not re.fullmatch(r"hayale-v2-reference-[0-9]{3}", reference["assetId"]):
            raise ValueError("Invalid reference identity")
    return references


def provider_plan(
    gcp_project_id: str, bucket: str, *, preparation: Path = PREPARATION
) -> tuple[CompiledPlan, dict[str, Path]]:
    """Compile existing project contracts and bind one distinct first frame per shot."""
    bucket = bucket.removeprefix("gs://").strip("/")
    if not re.fullmatch(r"[a-z][a-z0-9-]{4,61}[a-z0-9]", gcp_project_id):
        raise ValueError("A valid GCP project ID is required")
    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", bucket):
        raise ValueError("A valid private GCS bucket name is required")
    references = reference_bindings(preparation)
    compiled = compile_project_plan(
        project_config_path=PROJECT / "project-config.json",
        creative_bible_path=PROJECT / "creative-bible.json",
        shot_bank_path=PROJECT / "shot-bank.json",
        continuity_profile_path=PROJECT / "continuity-profile.json",
        gcs_bucket=bucket,
    )
    material = {
        **compiled.source_artifacts,
        **{r["assetId"]: r["sha256"] for r in references},
        "gcpTargetSha256": sha256_json(
            {"project": gcp_project_id, "bucket": bucket, "region": "us-central1"}
        ),
    }
    prefix = f"gs://{bucket}/hayale-promo-v2/{sha256_json(material)[:24]}"
    paths = {}
    shots = []
    for shot, reference in zip(compiled.shots, references, strict=True):
        binding = CompiledReferenceImage(
            reference["assetId"],
            f"{prefix}/references/{reference['assetId']}-{reference['sha256'][:16]}.png",
            "image/png",
            reference["sha256"],
            reference["kind"],
        )
        shots.append(
            replace(
                shot,
                first_frame_reference=binding,
                continuation_mode="first-frame-reference",
                storage_uri=f"{prefix}/outputs/{shot.shot_id}/",
            )
        )
        paths[binding.asset_id] = (REPO / reference["preparedPath"]).resolve()
    # Audio path, duration, waveform, beats and lyrics are deliberately absent.
    continuity = {
        **compiled.continuity,
        "executionPolicy": "foreground-prepared-single-owner",
    }
    return replace(
        compiled, shots=tuple(shots), source_artifacts=material, continuity=continuity
    ).with_digest(), paths


def music_cues(audio_path: Path | None) -> dict[str, Any]:
    if audio_path is None:
        return {
            "audioPath": None,
            "onsets": [],
            "durationSeconds": None,
            "method": "silent fixed-duration edit",
        }
    from app.analysis.core import analyze_rhythm, load_audio, spectral_features
    import soundfile as sf

    info = sf.info(str(audio_path))
    if info.duration > 900:
        raise ValueError("Use a promo audio excerpt of no more than 15 minutes")
    before = sha256_file(audio_path)
    audio = load_audio(str(audio_path))
    rhythm = analyze_rhythm(audio, spectral_features(audio))
    if sha256_file(audio_path) != before:
        raise ValueError("Source audio changed during inspection")
    return {
        "audioPath": str(audio_path.resolve()),
        "sourceSha256": before,
        "durationSeconds": audio.duration,
        "sampleRate": audio.sample_rate,
        "channels": audio.samples.shape[1],
        "bpm": rhythm.bpm.model_dump(mode="json"),
        "onsets": rhythm.onset_timestamps.value or [],
        "beatTimestamps": rhythm.beat_timestamps.value or [],
        "downbeats": "unknown",
        "paddingSeconds": max(0.0, 60 - audio.duration),
        "trimmedSeconds": max(0.0, audio.duration - 60),
        "method": "existing TrackPrompt local rhythm analyzer; no speech/lyric analysis",
    }


def prepare(
    audio_path: Path | None = DEFAULT_AUDIO, *, preparation: Path = PREPARATION
) -> dict[str, Any]:
    ensure_runtime_path(preparation)
    compiled, _ = provider_plan(
        "offline-preparation", "hayale-v2-configure-at-launch", preparation=preparation
    )
    cues = music_cues(audio_path)
    edit = edit_plan(cues["onsets"])
    atomic_write_json(preparation / "music-cues.json", cues)
    atomic_write_json(preparation / "edit-plan.json", edit)
    atomic_write_json(preparation / "provider-plan.preview.json", compiled.to_dict())
    for shot in compiled.shots:
        atomic_write_json(
            preparation / "request-preview" / f"{shot.shot_id}.json",
            build_request_payload(shot),
        )
    report = {
        "status": "prepared-not-generated",
        "gcpApiCalls": 0,
        "veoSubmissions": 0,
        "referenceCount": 7,
        "uniqueGenerationCount": 7,
        "editEventCount": 8,
        "durationFrames": 1440,
        "durationSeconds": 60,
        "fps": 24,
        "aspectRatio": "16:9",
        "audioSeconds": cues["durationSeconds"],
        "audioPaddingSeconds": cues.get("paddingSeconds", 0),
        "baseVeoEstimateUsd": compiled.base_estimated_cost_usd,
        "generationReservationCapUsd": compiled.max_spend_usd,
        "cloudReadiness": "not contacted; enter project/bucket and authorize exact plan when running manually",
        "finalVisualReview": "not-run; no generated video exists",
    }
    atomic_write_json(preparation / "preparation-report.json", report)
    return report
