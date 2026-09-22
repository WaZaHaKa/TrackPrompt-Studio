"""Allowlisted projections of committed native state, with explicit run selection.

Normal analysis does no network I/O. Even when enabled, hooks only update the
existing local WNP node; its separately supervised read-only host serves clients.
"""
from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .projection import ProjectionError, ProjectionResult, WNPProjection


def opaque(value: object, *, prefix: str) -> str:
    """Resource names and arbitrary native IDs never become public identifiers."""
    return prefix + "-" + hashlib.sha256(str(value).encode()).hexdigest()[:24]


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _publish(run: str, source_id: str, observation: Mapping[str, object]) -> ProjectionResult | None:
    root = os.environ.get("TRACKPROMPT_WNP_ROOT")
    selected = os.environ.get("TRACKPROMPT_WNP_RUN")
    if not root or not selected or run != selected:
        return None
    try:
        with WNPProjection(Path(root)) as projection:
            projection.require_selected(run)
            return projection.publish(source_id, observation)
    except Exception as exc:
        return ProjectionResult(False, None, exc.code if isinstance(exc, ProjectionError) else "projection_unavailable")


def observe_worker(*, run: str, heartbeat_at: str, state: str = "active", pid: int | None = None) -> ProjectionResult | None:
    """Accepted worker's original heartbeat, without altering tasks/provider/history."""
    root = os.environ.get("TRACKPROMPT_WNP_ROOT")
    if not root or os.environ.get("TRACKPROMPT_WNP_RUN") != run:
        return None
    try:
        with WNPProjection(Path(root)) as projection:
            projection.require_selected(run)
            snapshot = projection.store.snapshot()
            if snapshot.get("run") != run:
                return ProjectionResult(False, None, "job_observation_required")
            live = {key: value for key, value in snapshot.get("observations", {}).items()
                    if key in {"provider", "metrics", "progress"}}
            live["worker"] = {"state": state, "heartbeat_at": heartbeat_at, "pid": pid}
            return projection.observe(live)
    except Exception:
        return ProjectionResult(False, None, "projection_unavailable")


def emit_lifecycle(
    *, run: str, source_id: str, stage: str, state: str = "waiting", profile: str | None = None,
    technical_verified: bool = False, final_manifest_sha256: str | None = None,
    creative_approval: str = "pending", tasks: list[dict[str, object]] | None = None,
    pipeline: Mapping[str, object] | None = None, worker: Mapping[str, object] | None = None,
    provider: Mapping[str, object] | None = None,
    progress_unit: str = "scenes", progress: Mapping[str, object] | None = None,
    live_progress: Mapping[str, object] | None = None,
) -> ProjectionResult | None:
    """Publish already proven native facts; no hashes here imply media inspection.

    source_id identifies a committed transition, not a poll timestamp. Technical
    completion requires a verified final manifest; creative approval stays separate.
    """
    try:
        if technical_verified and not final_manifest_sha256:
            return ProjectionResult(False, None, "verification_manifest_required")
        phase = {"preparation": "prepared", "planning": "prepared", "generation": "generating",
                 "analysis": "generating", "transfer": "frames_complete", "verification": "frames_complete",
                 "finishing": "finalizing", "music-mux": "finalizing", "export": "finalizing",
                 "recovery": "reconciling"}.get(stage, "prepared")
        if state == "failed":
            phase = "failed"
        if technical_verified and final_manifest_sha256:
            phase = "final_verified"
        elif state == "complete":
            state = "waiting"  # native 'done' alone is not technical media verification
        details: dict[str, object] = {**(pipeline or {}), "stage": stage,
                                      "technical_verified": technical_verified,
                                      "creative_approval": creative_approval}
        if profile:
            details["profile"] = profile
        observation: dict[str, object] = {"run": run, "phase": phase, "workload_state": state,
                                          "health": "error" if state == "failed" else "ok", "pipeline": details}
        if technical_verified and final_manifest_sha256:
            observation.update(final_verified=True, final_manifest_sha256=final_manifest_sha256)
        if tasks is not None:
            observation["tasks"] = tasks
            complete = sum(item.get("phase") in {"raw_verified", "final_verified", "complete"} for item in tasks)
            details["scene_total"] = len(tasks)
            details["scenes_verified" if progress_unit == "scenes" else "source_clips_verified"] = complete
            observation["durable_progress"] = {"current": complete, "total": len(tasks), "unit": progress_unit}
        if progress is not None:
            observation["durable_progress"] = dict(progress)
        if live_progress is not None:
            observation["progress"] = dict(live_progress)
        for key, value in (("worker", worker), ("provider", provider)):
            if value is not None:
                observation[key] = dict(value)
        return _publish(run, source_id, observation)
    except Exception:
        return ProjectionResult(False, None, "invalid_observation")


def emit_analysis(record: Any) -> ProjectionResult | None:
    """JobStore create/update boundary; no filename, lyrics, message or audio data."""
    try:
        status = record.status.value
        state = {"queued": "queued", "completed": "idle", "failed": "failed",
                 "cancelled": "paused", "expired": "idle"}.get(status, "running")
        stage = "preparation" if record.stage in {"queued", "uploading", "validating", "decoding"} else "analysis"
        return emit_lifecycle(run=record.job_id, source_id=f"analysis:{record.job_id}:{record.updated_at.isoformat()}",
                              stage=stage, state=state, profile=record.effective_mode.value,
                              creative_approval="unknown")
    except Exception:
        return ProjectionResult(False, None, "invalid_observation")


def emit_local_run(manifest: Mapping[str, Any]) -> ProjectionResult | None:
    """Local manifest commit: unit hashes/QC are native facts, not file existence."""
    try:
        tasks: list[dict[str, object]] = []
        units = manifest.get("units", {})
        for shot_id, unit in sorted(units.get("video", {}).items()):
            verified = unit.get("status") == "complete" and bool(unit.get("outputSha256"))
            task: dict[str, object] = {"task": shot_id, "phase": "raw_verified" if verified else
                                       "failed" if unit.get("status") == "failed" else "generating" if unit.get("status") == "running" else "queued",
                                       "attempt": f"attempt-{int(unit.get('attempt', 0)):03d}"}
            if verified:
                task["raw_manifest_sha256"] = unit["outputSha256"]
            tasks.append(task)
        status = str(manifest.get("status"))
        state = {"running": "running", "failed": "failed", "cancelled": "paused", "planned": "queued"}.get(status, "waiting")
        stage = {"references": "preparation", "keyframes": "preparation", "video": "generation",
                 "post": "finishing", "edit": "finishing", "qc": "verification", "complete": "export"}.get(str(manifest.get("stage")), "recovery")
        verified = status == "complete" and manifest.get("finalQcPassed") is True
        return emit_lifecycle(run=str(manifest["revisionId"]), source_id="local:" + _digest(manifest), stage=stage,
                              state="waiting" if verified else state, technical_verified=verified,
                              final_manifest_sha256=_digest(manifest) if verified else None, tasks=tasks,
                              progress_unit="source-clips",
                              provider={"name": "comfyui", "state": "unknown", "observed_at": None})
    except Exception:
        return ProjectionResult(False, None, "invalid_observation")



def video_observation(job: Mapping[str, Any]) -> dict[str, Any]:
    """Pure mapper for Mission Control's alias-form VideoJobRecord."""
    tasks: list[dict[str, object]] = []
    paid = 0
    active: Mapping[str, Any] | None = None
    last_operation: Mapping[str, Any] | None = None
    for index, shot in enumerate(job.get("shots", []), 1):
        attempts = shot.get("attempts", [])
        attempt = attempts[-1] if attempts else {}
        paid += sum(bool(item.get("operationName")) or item.get("state") in {"reserved", "submitted", "running"} for item in attempts)
        native = attempt.get("state", "planned")
        phase = {"planned": "queued", "reserved": "prepared", "submitted": "submitted", "running": "generating",
                 "succeeded": "frames_complete", "downloaded": "frames_complete", "verified": "raw_verified",
                 "failed": "failed", "filtered": "failed"}.get(native, "ambiguous")
        task: dict[str, object] = {"task": f"scene-{index:02d}", "phase": phase}
        if attempt:
            task["attempt"] = f"attempt-{int(attempt.get('attempt', 1)):03d}"
        if phase == "raw_verified":
            if not attempt.get("clipSha256") or not attempt.get("probeEvidence"):
                task["phase"] = "frames_complete"
            else:
                task["raw_manifest_sha256"] = _digest({"clip": attempt["clipSha256"], "probe": attempt["probeEvidence"]})
        tasks.append(task)
        if attempt.get("operationName"):
            last_operation = attempt
        if native in {"reserved", "submitted", "running", "succeeded", "downloaded"}:
            active = attempt
    native_state = str(job.get("state", "planned"))
    stage = {"planned": "planning", "authorized": "planning", "smoke_submitted": "generation", "generating": "generation",
             "review_ready": "verification", "timeline_ready": "finishing", "assembling": "finishing", "exported": "export", "complete": "export"}.get(native_state, "recovery")
    if active and active.get("state") == "succeeded":
        stage = "transfer"
    elif active and active.get("state") == "downloaded":
        stage = "verification"
    state = "running" if native_state in {"generating", "smoke_submitted", "assembling"} else "failed" if native_state == "failed" else "paused" if native_state == "cancelled" else "waiting"
    error = job.get("error") or (active or {}).get("error") or {}
    error_code = str(error.get("code", ""))
    if error_code in {"provider_submission_outcome_unknown", "provider_operation_missing", "auth_identity_mismatch", "local_clip_conflict"} or any(item in error_code for item in ("ambiguous", "uncertain", "identity_mismatch")):
        action = "reconcile"
    elif "auth" in error_code:
        action = "reauthenticate"
    elif any(item in error_code for item in ("permission", "access", "billing", "disabled")):
        action = "repair-access"
    elif any(item in error_code for item in ("artifact", "corrupt", "disk", "contract")):
        action = "repair-artifact"
    elif native_state == "blocked_budget":
        action = "authorize"
    else:
        action = "retry-safe-read" if error.get("retryable") else "review" if error else "none"
    pipeline: dict[str, object] = {"provider_attempts": paid, "action": action, "retryable": error.get("retryable") is True,
                                  "provider_cancel_confirmed": job.get("providerCancellationConfirmed") is True,
                                  "created_at": job.get("createdAt"), "reserved_cost_usd": job.get("reservedCostUsd", 0)}
    if action != "none":
        pipeline["remedy"] = {"reconcile": "reconcile-private-receipts", "reauthenticate": "reauth-gcp",
                               "repair-access": "review-bound-access", "repair-artifact": "verify-retained-artifact",
                               "authorize": "approve-bounded-batch", "retry-safe-read": "resume-retained-operation",
                               "review": "review-native-error"}[action]
    if error_code:
        # The native code is enum-like but remains untrusted; never export free text.
        pipeline["error_code"] = error_code if error_code.isascii() and error_code.replace("_", "").isalnum() else "native_error"
    if active and active.get("nextRetryAt"):
        pipeline["next_retry_at"] = active["nextRetryAt"]
    heartbeat = job.get("workerHeartbeatAt")
    worker_state = "unknown"
    if heartbeat:
        age = (datetime.now(UTC) - datetime.fromisoformat(heartbeat.replace("Z", "+00:00"))).total_seconds()
        worker_state = "active" if 0 <= age <= 90 else "stale"
    observed_operation = active or last_operation or {}
    provider_state = {"submitted": "queued", "running": "running", "succeeded": "completed", "downloaded": "completed", "verified": "completed"}.get(str(observed_operation.get("state")), "unknown")
    provider: dict[str, object] = {"name": "vertex-veo", "state": provider_state,
                                  "observed_at": observed_operation.get("lastProviderObservationAt")}
    if observed_operation.get("operationName"):
        provider["job"] = opaque(observed_operation["operationName"], prefix="operation")
    # Do not infer verification from COMPLETE/path presence. The finishing caller
    # emits a separate manifest-bound technical verification after media checks.
    return {"run": str(job["id"]), "source_id": f"video:{job['id']}:{job.get('updatedAt')}:{_digest([native_state, tasks, pipeline])}",
            "stage": stage, "state": state, "tasks": tasks, "pipeline": pipeline,
            "profile": job.get("plan", {}).get("profile", {}).get("profileId"),
            "worker": {"state": worker_state, "heartbeat_at": heartbeat}, "provider": provider,
            "creative_approval": "pending", "progress_unit": "source-clips"}


def emit_video_job(job: Mapping[str, Any]) -> ProjectionResult | None:
    try:
        return emit_lifecycle(**video_observation(job))
    except Exception:
        return ProjectionResult(False, None, "invalid_observation")
