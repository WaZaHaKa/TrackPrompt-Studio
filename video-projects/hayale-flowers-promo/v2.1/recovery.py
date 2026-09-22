"""Reconcile durable checkpoints with an existing ComfyUI run; never start a server."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

PROJECT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT.parents[2] / "backend"))
from app.local_video.comfyui import ComfyUIClient  # noqa: E402
from app.video_generation.jsonio import atomic_write_json, sha256_file, sha256_json  # noqa: E402


def sibling(name: str) -> Any:
    key = f"hayale_v21_{name}"
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, PROJECT / f"{name}.py")
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[key] = module
        spec.loader.exec_module(module)
    return sys.modules[key]


def verified_frames(path: Path, size: tuple[int, int]) -> list[Path] | None:
    return sibling("runtime").verified_frames(path, size)  # type: ignore[no-any-return]


def read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def inspect_submission(scene_root: Path, client: ComfyUIClient) -> dict[str, Any]:
    receipt = read(scene_root / "submission.json")
    graph = read(scene_root / "submitted-workflow.json")
    digest = sha256_json(graph)
    if digest != receipt["workflowDigest"]:
        raise ValueError("Saved workflow and submission digest disagree")
    attempt_path = scene_root / "attempt.json"
    attempt = read(attempt_path) if attempt_path.exists() else {}
    if attempt and (
        attempt["identity_sha256"] != sha256_json(attempt["identity"])
        or attempt["identity"]["workflow_sha256"] != digest
    ):
        raise ValueError("Durable attempt identity conflicts with the saved workflow")
    prompt_id = attempt.get("prompt_id") or receipt.get("promptId")
    all_history = (
        client._request_json("GET", "/history")
        if hasattr(client, "_request_json")
        else {}
    )
    queue = client.queue_snapshot()
    correlated = []
    for row in [
        *queue.get("queue_running", []),
        *queue.get("queue_pending", []),
        *[entry.get("prompt", []) for entry in all_history.values()],
    ]:
        if len(row) >= 4 and row[3].get("client_id") == receipt["identity"]:
            if sha256_json(row[2]) != digest:
                raise ValueError("Correlated provider job has a different workflow")
            correlated.append(row[1])
    if len(set(correlated)) > 1:
        raise ValueError(
            "Found multiple correlated provider jobs; preserve all attempts"
        )
    if not prompt_id and correlated:
        prompt_id = correlated[0]
    if not prompt_id:
        return {"state": "ambiguous", "waitingFor": "reconciliation", "promptId": None}
    history = (
        {prompt_id: all_history[prompt_id]}
        if prompt_id in all_history
        else client.history(prompt_id)
    )
    # A completion can land between either network read.
    if not history.get(prompt_id):
        history = client.history(prompt_id)
    candidates = []
    matching_queue = []
    for key, state in (("queue_running", "generating"), ("queue_pending", "queued")):
        for row in queue.get(key, []):
            if len(row) < 4:
                raise ValueError("Invalid ComfyUI queue row")
            if row[1] == prompt_id or row[3].get("client_id") == receipt["identity"]:
                matching_queue.append(row[1])
                candidates.append((row, state))
    if len(set(matching_queue)) > 1 or len(matching_queue) > 1:
        raise ValueError(
            "Found multiple jobs for this scene; do not adopt or duplicate them"
        )
    completed = history.get(prompt_id)
    if completed:
        row = completed.get("prompt")
        if not isinstance(row, list) or len(row) < 4 or not isinstance(row[3], dict):
            raise ValueError(
                "Provider history is missing its matching prompt/workflow identity"
            )
        candidates.append((row, "history"))
    for row, _ in candidates:
        if (
            row[1] != prompt_id
            or sha256_json(row[2]) != digest
            or row[3].get("client_id") != receipt["identity"]
        ):
            raise ValueError(
                "Provider workflow, prompt or client identity differs from its receipt"
            )
    result = {
        "promptId": prompt_id,
        "workflowDigest": digest,
        "queueCopies": len(matching_queue),
        "historyPresent": bool(completed),
    }
    if completed:
        status = completed.get("status", {})
        if status.get("status_str") == "error":
            return {
                **result,
                "state": "failed",
                "waitingFor": "operator_review",
                "history": history,
            }
        if status.get("completed") and status.get("status_str") == "success":
            return {
                **result,
                "state": "frames_complete",
                "waitingFor": "frame_download",
                "history": history,
            }
    if matching_queue:
        state = candidates[0][1]
        return {
            **result,
            "state": state,
            "waitingFor": "provider_history"
            if state == "generating"
            else "provider_queue",
        }
    # Missing from two reads is not proof against evicted history or a restarted
    # provider. Preserve evidence rather than converting 'missing' to 'failed'.
    return {**result, "state": "missing", "waitingFor": "reconciliation"}


def inspect_boundary(
    run_root: Path,
    client: ComfyUIClient,
    size: tuple[int, int],
    *,
    manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    scenes: dict[str, Any] = {}
    active = None
    for index in range(1, 8):
        shot = f"shot-{index:03d}"
        scene_root = run_root / shot
        proof = None
        if manifest is not None and (scene_root / "raw").exists():
            proof = (
                sibling("runtime")
                .durable_module()
                .verify_checkpoint(run_root, shot, manifest)
            )
        frames = (
            [None] * proof["count"]
            if proof is not None
            else verified_frames(scene_root, size)
        )
        if frames is not None:
            scenes[shot] = {
                "state": "raw_verified",
                "frames": len(frames),
                "waitingFor": None,
                **({"raw_manifest_sha256": proof["manifest_sha256"]} if proof else {}),
            }
            continue
        active = shot
        if (scene_root / "submission.json").exists():
            scenes[shot] = inspect_submission(scene_root, client)
            if manifest is not None:
                lifecycle = sibling("runtime").durable_module()
                lifecycle.scene_binding(run_root, shot, manifest)
                provider_state = {
                    "generating": "running",
                    "frames_complete": "completed",
                    "missing": "absent",
                    "ambiguous": "unknown",
                }.get(scenes[shot]["state"], scenes[shot]["state"])
                attempt = (
                    read(scene_root / "attempt.json")
                    if (scene_root / "attempt.json").exists()
                    else {}
                )
                submission = attempt.get("state") or (
                    "accepted"
                    if read(scene_root / "submission.json").get("promptId")
                    else "sending"
                )
                decision = lifecycle.decide_verified(
                    raw="absent",
                    provider=provider_state,
                    submission=submission,
                    owner_held=bool(
                        lifecycle.CURRENT_OWNER and lifecycle.CURRENT_OWNER.held
                    ),
                    identity_match=True,
                    matches=scenes[shot].get("queueCopies", 0)
                    or int(scenes[shot].get("historyPresent", False)),
                )
                scenes[shot]["reconciliationAction"] = decision.action
                scenes[shot]["maySubmit"] = decision.may_submit
        else:
            scenes[shot] = {"state": "not_submitted", "waitingFor": "operator_resume"}
        break
    return {
        "runId": run_root.name,
        "activeShot": active,
        "scenes": scenes,
        "submissionAllowed": False,
        "observedAt": datetime.now(timezone.utc).isoformat(),
        "authority": "verified checkpoints, then matching provider queue/history; never receipt age",
    }


def lease_is_held(root: Path) -> bool:
    """Read-only probe of the existing single-supervisor lease, not its mtime."""
    path = root / "production.lock"
    if not path.exists():
        return False
    with path.open("r+b") as stream:
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)  # type: ignore[attr-defined]
        except OSError:
            return True
        if os.name == "nt":
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)  # type: ignore[attr-defined]
    return False


@contextmanager
def existing_server(run_root: Path) -> Iterator[ComfyUIClient]:
    runtime = sibling("runtime")
    server = read(run_root / "comfy/server-process.json")
    if server["endpoint"] != runtime.ENDPOINT:
        raise ValueError("Unexpected provider endpoint")
    runtime.require_listener_owner(server["pid"])
    client = runtime.OwnedClient(runtime.ENDPOINT, exclusive_control=True)
    client.owner_pid = server["pid"]
    started = runtime.process_started_at(server["pid"])
    if started is None:
        raise ValueError("Cannot bind existing provider PID to a process start time")
    if server.get("started_at") and server["started_at"] != started.isoformat():
        raise ValueError("Provider PID was reused; explicit reconciliation required")
    client.provider_instance = {
        "pid": server["pid"],
        "started_at": started.isoformat(),
        "run": run_root.name,
        "endpoint": server["endpoint"],
    }
    stats = client._request_json("GET", "/system_stats")
    argv = stats["system"].get("argv", [])
    for flag, expected in (
        ("--output-directory", run_root / "comfy/output"),
        ("--input-directory", run_root / "comfy/input"),
    ):
        if (
            flag not in argv
            or Path(argv[argv.index(flag) + 1]).resolve() != expected.resolve()
        ):
            raise ValueError("The listening server belongs to a different run")
    # This is a borrowed live server: never terminate it on exit or exception.
    yield client


def recover(run_id: str, *, resume: bool = False) -> dict[str, Any]:
    prep, runtime = sibling("prepare"), sibling("runtime")
    if not re.fullmatch(r"[a-f0-9]{12}", run_id):
        raise ValueError("An existing twelve-character V2.1 run ID is required")
    run_root = prep.contained(prep.ROOT / "runs" / run_id)
    binding = read(run_root / "run.json")
    manifest = prep.verify_prepared()
    if binding["preparation"] != manifest["digest"]:
        raise ValueError("Existing run does not match the prepared assets")
    size = prep.PROFILES[binding["profile"]]
    with existing_server(run_root) as client:
        view = inspect_boundary(run_root, client, size, manifest=manifest)
        held = lease_is_held(prep.ROOT)
        view["supervisorLeaseHeld"] = held
        view["action"] = (
            "existing-supervisor-retained" if held else "inspected-existing-prompt"
        )
        # Separate observer snapshot: do not pretend to heartbeat for a legacy
        # supervisor that has not loaded this patched code.
        snapshot = {
            **view,
            "scenes": {
                k: {a: b for a, b in row.items() if a != "history"}
                for k, row in view["scenes"].items()
            },
        }
        atomic_write_json(run_root / "recovery/latest-observation.json", snapshot)
        # Explicit reconciliation authorizes only this orchestration upgrade.
        # Preserve the original run binding and reject changed media/finishing.
        for name in ("prepare.py", "finish.py"):
            if sha256_file(PROJECT / name) != binding["code"][name]:
                raise ValueError(
                    "Preparation or finishing code changed; this orchestration-only recovery cannot adopt it"
                )
        for receipt_path in sorted(run_root.glob("shot-*/submission.json")):
            receipt = read(receipt_path)
            if (
                sha256_json(read(receipt_path.parent / "submitted-workflow.json"))
                != receipt["workflowDigest"]
            ):
                raise ValueError("A saved workflow changed; recovery is not authorized")
        identity = {
            "preparation": binding["preparation"],
            "profile": binding["profile"],
            "silent": binding["silent"],
            "code": {p.name: sha256_file(p) for p in sorted(PROJECT.glob("*.py"))},
        }
        atomic_write_json(
            run_root / "recovery/compatible-code.json",
            {
                "originalRunDigest": binding["digest"],
                "approvedIdentityDigest": sha256_json(identity),
                "scope": "orchestration-only; prepared assets and submitted workflows unchanged",
                "observedAt": snapshot["observedAt"],
            },
        )
        if not resume or held:
            return snapshot
        # Reacquire before any media write/continuation. A raced supervisor wins.
        with runtime.durable_module().ControllerLease(prep.ROOT, run_id):
            completed_recovery = run_root / "recovery/recovered-checkpoint.json"
            if completed_recovery.exists():
                previous = read(completed_recovery)
                if verified_frames(run_root / previous["shotId"], size) is not None:
                    return {
                        **snapshot,
                        "action": "checkpoint-already-recovered",
                        "recoveredShot": previous["shotId"],
                        "laterShotsSubmitted": 0,
                    }
            active = view["activeShot"]
            if active is None:
                return {**snapshot, "action": "all-raw-checkpoints-preserved"}
            scene_root = run_root / active
            if not (scene_root / "submission.json").exists():
                raise ValueError(
                    "The first incomplete scene has never been submitted; use normal Create after explicit run-code reconciliation"
                )
            current = inspect_submission(scene_root, client)
            if current["state"] in {"failed", "missing", "ambiguous"}:
                atomic_write_json(
                    run_root / "recovery/blocked-submission.json", current
                )
                raise ValueError(
                    "Existing submission requires reconciliation; no automatic generation retry"
                )
            atomic_write_json(
                run_root / "recovery/adoption.json",
                {
                    "runDigest": binding["digest"],
                    "shotId": active,
                    "promptId": current["promptId"],
                    "workflowDigest": current["workflowDigest"],
                    "codeSha256": {
                        p.name: sha256_file(p) for p in PROJECT.glob("*.py")
                    },
                },
            )
            history = current.get("history") or runtime.wait_history(
                client, current["promptId"], scene_root / "submission.json"
            )
            atomic_write_json(scene_root / "completed-history.json", history)
            runtime.collect_frames(
                client, history, current["promptId"], scene_root, size
            )
            atomic_write_json(
                completed_recovery,
                {
                    "shotId": active,
                    "promptId": current["promptId"],
                    "state": "raw_verified",
                },
            )
            # A bounded recovery stops at the repaired checkpoint, so later shots
            # cannot start before the caller verifies this boundary.
            return {
                **snapshot,
                "action": "existing-prompt-recovered",
                "recoveredShot": active,
                "recoveredState": "raw_verified",
                "laterShotsSubmitted": 0,
            }
