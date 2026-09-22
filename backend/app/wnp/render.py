"""Native Mission Control render/encode facts, without filesystem/provider probes."""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from .lifecycle import emit_lifecycle
from .projection import ProjectionResult

_PHASES = {
    "SCENE_LOAD": "preparation", "RENDER_FRAME": "generation", "WRITE_FRAME": "generation",
    "VALIDATE_FRAME": "verification", "VALIDATE_CHUNK": "verification", "PUBLISH_CHUNK": "transfer",
    "WAITING_FOR_STORAGE": "transfer", "ENCODE_MASTER": "finishing", "ENCODE_DELIVERY": "finishing",
    "MUX_AUDIO": "music-mux", "FINAL_VERIFY": "verification",
}
_STAGES = {
    "input-verification": "preparation", "story-compilation": "planning", "scene-build": "preparation",
    "asset-preparation": "preparation", "cache-bake": "preparation", "calibration": "preparation",
    "rendering": "generation", "frame-validation": "verification", "encoding": "finishing",
    "final-qa": "verification", "publication": "export",
}


def _verified(variant: Mapping[str, Any], job_id: str) -> bool:
    """Require bound native QA, completed encodes and matching artifact identities.

    A finished frame sequence, COMPLETE flag or output path is insufficient.
    The referenced native record remains the evidence authority, not WNP.
    """
    progress = variant.get("progress", {})
    results = progress.get("qa_results", [])
    encodes = progress.get("encode_tasks", [])
    artifacts = progress.get("artifacts", [])
    if (not results or not encodes or not artifacts or progress.get("total_frames", 0) <= 0
            or progress.get("validated_frames") != progress.get("total_frames")):
        return False
    latest = max(results, key=lambda item: datetime.fromisoformat(str(item["completed_at"]).replace("Z", "+00:00")))
    if (latest.get("job_id") != job_id or latest.get("output_variant_id") != variant.get("id")
            or latest.get("status") != "pass" or not latest.get("report_sha256")
            or not latest.get("checks") or any(check.get("status") != "pass" for check in latest["checks"])):
        return False
    if not any(stage.get("stage") == "final-qa" and stage.get("state") == "complete"
               for stage in progress.get("stages", [])):
        return False
    return all(
        task.get("job_id") == job_id and task.get("output_variant_id") == variant.get("id")
        and task.get("state") == "complete" and task.get("completed_units") == task.get("total_units")
        and any(artifact.get("job_id") == job_id and artifact.get("output_variant_id") == variant.get("id")
                and artifact.get("artifact_key") == task.get("output_artifact_key")
                and artifact.get("deliverable_role") == task.get("deliverable_role")
                and bool(artifact.get("sha256")) for artifact in artifacts)
        for task in encodes
    )


def emit_render_job(job: Mapping[str, Any], *, record_sha256: str,
                    primary: Mapping[str, Any] | None = None) -> ProjectionResult | None:
    """Accept snake-case native JobRecord/MediaRenderJob JSON after DB commit.

    When both contracts exist, primary supplies the precise native phase (including
    MUX_AUDIO) and the just-written media record supplies its variant evidence.
    record_sha256 is the SHA-256 of the exact persisted payload_json bytes.
    """
    try:
        record = primary or job
        run = str(job["id"])
        variants = [item for item in job.get("output_variants", []) if item.get("enabled") is True]
        stages = [stage for variant in variants for stage in variant.get("progress", {}).get("stages", [])]
        active = next((stage for stage in stages if stage.get("state") in {"running", "calibrating", "indeterminate"}), None)
        latest: Mapping[str, Any] = active or max(stages, key=lambda item: list(_STAGES).index(item["stage"]) if item.get("stage") in _STAGES else -1, default={})
        phase = _PHASES.get(str(record.get("phase"))) or _STAGES.get(str(latest.get("stage")), "preparation")
        native = str(record.get("state", latest.get("state", "pending"))).upper()
        state = {"IDLE": "idle", "VALIDATING": "running", "READY": "queued", "PENDING": "queued",
                 "STARTING": "running", "RUNNING": "running", "ENCODING": "running", "VERIFYING": "running",
                 "FINISHING_CURRENT_CHUNK": "running", "FAILED": "failed", "PAUSED": "paused",
                 "PAUSED_SAFELY": "paused", "CANCELLED": "paused"}.get(native, "waiting")
        verified = bool(variants) and all(_verified(variant, run) for variant in variants)
        if verified:
            phase, state = "export", "waiting"
        progress: dict[str, object] | None = None
        if active and active.get("completed_units") is not None:
            progress = {"current": active["completed_units"], "total": active.get("total_units"),
                        "unit": active.get("unit", "items")}
        elif "rendered_frame_count" in record:
            verifying = record.get("phase") in {"VALIDATE_FRAME", "VALIDATE_CHUNK", "FINAL_VERIFY"}
            progress = {"current": record.get("validated_frame_count" if verifying else "rendered_frame_count", 0),
                        "total": record.get("total_frame_count"), "unit": "validated-frames" if verifying else "rendered-frames"}
        elif variants:
            progress = {"current": sum(item["progress"].get("validated_frames", 0) for item in variants),
                        "total": sum(item["progress"].get("total_frames", 0) for item in variants), "unit": "validated-frames"}
        error = record.get("error") or {}
        return emit_lifecycle(
            run=run, source_id=f"render-native:{run}:{record_sha256}", stage=phase, state=state,
            profile=record.get("identity", {}).get("profile_id"), technical_verified=verified,
            final_manifest_sha256=record_sha256 if verified else None,
            pipeline={"created_at": job.get("created_at"), "action": "review" if error else "none",
                      **({"error_code": "native_render_error", "remedy": "review-native-error",
                          "retryable": error.get("retryable") is True} if error else {})},
            worker={"state": "unknown", "heartbeat_at": None},
            provider={"name": "blender", "state": "unknown", "observed_at": None}, live_progress=progress,
        )
    except Exception:
        return ProjectionResult(False, None, "invalid_observation")


def emit_encode_status(status: Mapping[str, Any], *, record_sha256: str) -> ProjectionResult | None:
    """Standalone FFmpeg encode setting: progress never proves verified delivery."""
    try:
        native = status.get("status")
        state = {"idle": "idle", "queued": "queued", "encoding": "running", "verifying": "running",
                 "failed": "failed", "complete": "waiting"}.get(str(native), "unknown")
        phase = "verification" if native == "verifying" else "export" if native == "complete" else "finishing"
        return emit_lifecycle(
            run=str(status["render_job_id"]), source_id="encode-native:" + record_sha256, stage=phase, state=state,
            pipeline={"action": "review" if status.get("error") else "none",
                      **({"error_code": "native_encode_error", "remedy": "verify-retained-artifact"}
                         if status.get("error") else {})},
            live_progress={"current": status.get("progress", 0), "total": 100, "unit": "percent"},
            worker={"state": "unknown", "heartbeat_at": None},
            provider={"name": "ffmpeg", "state": "unknown", "observed_at": None},
        )
    except Exception:
        return ProjectionResult(False, None, "invalid_observation")
