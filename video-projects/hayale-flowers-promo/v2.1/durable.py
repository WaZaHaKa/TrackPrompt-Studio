"""Hayale's existing JSON authority with WNP-kit reconciliation semantics.

The kit SQLite ledger is a reference: this adapter uses the existing per-shot
JSON state and native production.lock, not a second execution database.
"""

from __future__ import annotations

import importlib
import copy
import os
import queue
import re
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "backend"))
for path in (
    REPO / "tools/wnp-trackprompt-kit/master/src",
    REPO / "tools/wnp-trackprompt-kit/trackprompt/src",
):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
from app.mission_control.processes import process_started_at  # noqa: E402
from app.video_generation.jsonio import (  # noqa: E402
    atomic_write_json,
    read_json,
    sha256_file,
    sha256_json,
)

_policy = importlib.import_module("recovery_policy")
_fs = importlib.import_module("wnp_host.fs")
_frames = importlib.import_module("frame_check")
CURRENT_OWNER: Any = None
_messages: queue.Queue[Any] = queue.Queue(maxsize=16)
_logger_started = False
_logger_lock = threading.Lock()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_output_subfolder(value: str) -> str:
    normalized = value.replace('\\', '/')
    if (PurePosixPath(normalized).is_absolute() or PureWindowsPath(value).drive
        or '..' in normalized.split('/') or '\x00' in value or ':' in value):
        raise ValueError('Provider subfolder must remain a safe relative path')
    return normalized


def report(*args: Any, **kwargs: Any) -> None:
    """Console backpressure cannot stop provider polling or durable commits."""
    global _logger_started
    with _logger_lock:
        if not _logger_started:

            def writer() -> None:
                while True:
                    printer, message, options = _messages.get()
                    try:
                        printer(*message, **options)
                    except (OSError, ValueError):
                        pass

            threading.Thread(
                target=writer, name="hayale-console-observer", daemon=True
            ).start()
            _logger_started = True
    try:
        _messages.put_nowait((print, args, kwargs))
    except queue.Full:
        pass  # Status/checkpoints remain on disk; text display is expendable.


class ControllerLease:
    def __init__(self, root: Path, run_id: str) -> None:
        self.root, self.run_id = root, run_id
        self.lock = _fs.FileLock(root / "production.lock", timeout=0.1)
        self.held = False
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None
        self.activity: dict[str, Any] = {
            "stage": "reconciling",
            "last_material_progress_at": None,
        }

    def __enter__(self) -> ControllerLease:
        global CURRENT_OWNER
        self.lock.__enter__()
        self.held = True
        try:
            started = process_started_at(os.getpid())
            if os.name == "nt" and started is None:
                raise ValueError("Cannot bind controller PID to its process start time")
            self.identity = {
                "pid": os.getpid(),
                "started_at": started.isoformat() if started else now(),
                "run": self.run_id,
            }
            CURRENT_OWNER = self
            self.thread = threading.Thread(
                target=self._heartbeat, name="hayale-heartbeat", daemon=True
            )
            self.thread.start()
            return self
        except BaseException:
            self.held = False
            self.lock.__exit__(None, None, None)
            raise

    def _heartbeat(self) -> None:
        while not self.stop.is_set():
            try:
                atomic_write_json(
                    self.root / "runs" / self.run_id / "worker.json",
                    {
                        **self.identity,
                        **self.activity,
                        "heartbeat_at": now(),
                        "state": "active",
                        "owner_held": True,
                    },
                )
            except (OSError, ValueError):
                pass  # Observer failure is not execution failure or retry authority.
            self.stop.wait(2)

    def progress(self, stage: str, shot: str, *, material: bool = False) -> None:
        self.activity = {
            **self.activity,
            "stage": stage,
            "shot": shot,
            **({"last_material_progress_at": now()} if material else {}),
        }

    def __exit__(self, *exc: Any) -> None:
        global CURRENT_OWNER
        self.stop.set()
        if self.thread:
            self.thread.join(3)
        try:
            atomic_write_json(
                self.root / "runs" / self.run_id / "worker.json",
                {
                    **self.identity,
                    **self.activity,
                    "heartbeat_at": now(),
                    "state": "stopped",
                    "owner_held": False,
                },
            )
        except (OSError, ValueError):
            pass
        CURRENT_OWNER = None
        self.held = False
        self.lock.__exit__(*exc)


class Attempts:
    """Atomic per-shot prepare -> sending -> accepted; never rewind sending."""

    def __init__(self, scene_root: Path, owner: Any) -> None:
        self.path, self.owner = scene_root / "attempt.json", owner

    def owned(self) -> None:
        if not getattr(self.owner, "held", False):
            raise ValueError("Exclusive ownership is required for attempt mutation")

    def get(self) -> dict[str, Any] | None:
        value = read_json(self.path) if self.path.exists() else None
        if value is not None and (
            value.get("identity_sha256") != sha256_json(value.get("identity"))
            or value.get("state") not in {"prepared", "sending", "accepted", "rejected"}
        ):
            raise ValueError("Durable attempt identity/state corrupted")
        return value  # type: ignore[no-any-return]

    def prepare(self, identity: dict[str, Any]) -> dict[str, Any]:
        self.owned()
        if getattr(self.owner, "run_id", identity.get("run")) != identity.get("run"):
            raise ValueError("Exclusive ownership belongs to a different run")
        if hasattr(self.owner, "root"):
            expected = self.owner.root / "runs" / identity["run"] / identity["shot"]
            if self.path.parent.resolve() != expected.resolve():
                raise ValueError("Attempt path differs from its owned run/shot")
        required = {
            "run",
            "shot",
            "attempt",
            "workflow_sha256",
            "input_sha256",
            "seed",
            "settings",
            "output_prefix",
            "provider_instance",
        }
        if set(identity) != required or not all(
            re.fullmatch(r"[0-9a-f]{64}", str(identity[k]))
            for k in ("workflow_sha256", "input_sha256")
        ):
            raise ValueError("Incomplete durable attempt identity")
        provider = identity["provider_instance"]
        if (
            not isinstance(provider.get("pid"), int)
            or provider["pid"] <= 0
            or not provider.get("started_at")
            or not provider.get("endpoint")
        ):
            raise ValueError("Provider instance needs PID, start time and endpoint")
        previous = self.get()
        digest = sha256_json(identity)
        if previous:
            if previous["identity_sha256"] != digest:
                raise ValueError("Attempt identity changed")
            return previous
        value = {
            "identity": identity,
            "identity_sha256": digest,
            "state": "prepared",
            "prompt_id": None,
            "prepared_at": now(),
        }
        atomic_write_json(self.path, value)
        return value

    def begin_send(self) -> None:
        self.owned()
        value = self.get()
        if value is None or value["state"] != "prepared":
            raise ValueError("Submission already started or no prepared intent exists")
        atomic_write_json(self.path, {**value, "state": "sending", "sending_at": now()})

    def accept(self, prompt_id: str) -> None:
        self.owned()
        value = self.get()
        if (
            value is None
            or value["state"] not in {"sending", "accepted"}
            or value.get("prompt_id") not in {None, prompt_id}
        ):
            raise ValueError("Attempt/provider identity conflict")
        if not isinstance(prompt_id, str) or not 1 <= len(prompt_id) <= 256:
            raise ValueError("Invalid provider identity")
        if value.get("prompt_id") == prompt_id:
            return
        atomic_write_json(
            self.path,
            {
                **value,
                "state": "accepted",
                "prompt_id": prompt_id,
                "accepted_at": now(),
            },
        )

    def import_accepted(
        self, identity: dict[str, Any], prompt_id: str, legacy_receipt: Path
    ) -> None:
        self.owned()
        existing = self.get()
        if existing:
            if (
                existing["identity"]["workflow_sha256"] != identity["workflow_sha256"]
                or existing["identity"]["input_sha256"] != identity["input_sha256"]
            ):
                raise ValueError("Imported attempt identity conflicts")
            self.accept(prompt_id)
            return
        self.prepare(identity)
        value = self.get()
        assert value is not None
        atomic_write_json(
            self.path,
            {
                **value,
                "state": "accepted",
                "prompt_id": prompt_id,
                "legacy_receipt_sha256": sha256_file(legacy_receipt),
                "imported_at": now(),
            },
        )


def effective_receipt(scene_root: Path) -> dict[str, Any]:
    receipt = read_json(scene_root / "submission.json")
    attempt = (
        read_json(scene_root / "attempt.json")
        if (scene_root / "attempt.json").exists()
        else None
    )
    if attempt:
        if (
            attempt["identity_sha256"] != sha256_json(attempt["identity"])
            or attempt["identity"]["workflow_sha256"] != receipt["workflowDigest"]
        ):
            raise ValueError("Canonical attempt conflicts with original receipt")
        if attempt.get("prompt_id"):
            receipt = {**receipt, "promptId": attempt["prompt_id"]}
    return receipt  # type: ignore[no-any-return]


def adopt_existing(
    run_root: Path,
    shot: str,
    manifest: dict[str, Any],
    provider_instance: dict[str, Any],
    prompt_id: str,
) -> None:
    graph, reference, _ = scene_binding(run_root, shot, manifest)
    scene = run_root / shot
    ledger = Attempts(scene, CURRENT_OWNER)
    current = ledger.get()
    identity = {
        "run": run_root.name,
        "shot": shot,
        "attempt": current["identity"]["attempt"] if current else "legacy-001",
        "workflow_sha256": sha256_json(graph),
        "input_sha256": sha256_file(reference),
        "seed": graph["high_sampler"]["inputs"]["noise_seed"],
        "settings": {
            "width": graph["latent"]["inputs"]["width"],
            "height": graph["latent"]["inputs"]["height"],
            "frames": 129,
            "steps": 40,
        },
        "output_prefix": graph["save_frames"]["inputs"]["filename_prefix"],
        "provider_instance": provider_instance,
    }
    ledger.import_accepted(identity, prompt_id, scene / "submission.json")


class CompletedSnapshot:
    """Positive retained completion evidence, never an assertion of absence."""

    def __init__(self, run_root: Path) -> None:
        self.snapshot = read_json(
            run_root / "recovery/completed-provider-snapshot.json"
        )
        self.provider_instance = self.snapshot["provider_instance"]
        if (
            self.snapshot["run"] != run_root.name
            or self.provider_instance["run"] != run_root.name
        ):
            raise ValueError("Retained provider snapshot belongs to another run")
        records = [
            run_root / "comfy/server-process.json",
            *sorted((run_root / "comfy").glob("server-process-previous-*.json")),
        ]
        matching = next((p for p in records if p.is_file() and sha256_file(p) == self.provider_instance["server_record_sha256"]), None)
        if matching is None:
            raise ValueError(
                "Retained provider instance no longer matches preserved metadata"
            )
        metadata = read_json(matching)
        if metadata["pid"] != self.provider_instance["pid"]:
            raise ValueError("Retained provider PID conflicts with its bound metadata")
        self.provider_instance = {**self.provider_instance, "endpoint": metadata["endpoint"]}

    def history(self, prompt_id: str) -> dict[str, Any]:
        value = self.snapshot["history"].get(prompt_id)
        return {prompt_id: value} if value else {}

    def queue_snapshot(self) -> dict[str, Any]:
        return {"queue_running": [], "queue_pending": []}

    def _request_json(self, method: str, path: str) -> dict[str, Any]:
        if (method, path) != ("GET", "/history"):
            raise ValueError("Retained evidence cannot execute provider operations")
        return self.snapshot["history"]  # type: ignore[no-any-return]


def restore_completed(
    run_root: Path, manifest: dict[str, Any], runtime: Any, recovery: Any
) -> None:
    """Recover only positive completed evidence under the current native owner."""
    snapshot_path = run_root / "recovery/completed-provider-snapshot.json"
    if not snapshot_path.exists():
        return
    source = CompletedSnapshot(run_root)
    for scene in sorted(run_root.glob("shot-*")):
        if not (scene / "submission.json").exists() or (scene / "raw").exists():
            continue
        current = recovery.inspect_submission(scene, source)
        if current["state"] != "frames_complete":
            continue  # Missing historical data never authorizes a retry.
        graph, _, _ = scene_binding(run_root, scene.name, manifest)
        adopt_existing(
            run_root,
            scene.name,
            manifest,
            source.provider_instance,
            current["promptId"],
        )
        history_path = scene / "completed-history.json"
        if history_path.exists() and read_json(history_path) != current["history"]:
            raise ValueError(
                "Existing history differs; preserve both records for reconciliation"
            )
        if not history_path.exists():
            atomic_write_json(history_path, current["history"])
        runtime.collect_frames(
            runtime.RetainedOutputs(run_root / "comfy/output"),
            current["history"],
            current["promptId"],
            scene,
            (graph["latent"]["inputs"]["width"], graph["latent"]["inputs"]["height"]),
        )


def decide_verified(**proven_facts: Any) -> Any:
    if "identity_match" not in proven_facts:
        raise ValueError("Identity evidence must be explicit")
    return _policy.decide(_policy.Observation(**proven_facts))


def verify_raw(
    scene_root: Path,
    size: tuple[int, int],
    graph: dict[str, Any],
    reference: Path,
    provider_input: Path,
    *,
    history: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Full kit decoder plus application provenance; marker presence proves nothing."""
    receipt = effective_receipt(scene_root)
    workflow_digest = sha256_json(graph)
    if receipt["workflowDigest"] != workflow_digest or sha256_file(
        reference
    ) != sha256_file(provider_input):
        raise ValueError("Workflow/input provenance mismatch")
    source_history = (
        history
        if history is not None
        else read_json(scene_root / "completed-history.json")
    )
    record = source_history.get(receipt["promptId"], {})
    prompt = record.get("prompt", [])
    if (
        len(prompt) < 4
        or prompt[1] != receipt["promptId"]
        or sha256_json(prompt[2]) != workflow_digest
        or prompt[3].get("client_id") != receipt["identity"]
    ):
        raise ValueError("Provider provenance does not match the submitted attempt")
    if record.get("status", {}).get("status_str") != "success" or not record.get(
        "status", {}
    ).get("completed"):
        raise ValueError("Provider did not prove successful completion")
    report_value = _frames.verify_frames(scene_root / "raw", 129, *size)
    marker = (
        read_json(scene_root / "raw-verified.json")
        if (scene_root / "raw-verified.json").exists()
        else None
    )
    if marker:
        expected = [(r["name"], r["sha256"]) for r in marker["frames"]]
        if (
            marker["promptId"] != receipt["promptId"]
            or marker["dimensions"] != list(size)
            or expected != [(r["name"], r["sha256"]) for r in report_value["frames"]]
        ):
            raise ValueError("Existing raw checkpoint conflicts with decoded bytes")
    for index, frame in enumerate(report_value["frames"], 1):
        download = read_json(scene_root / "downloads" / f"{index:03d}.json")
        if download["sha256"] != frame["sha256"]:
            raise ValueError("Frame/download receipt hash mismatch")
        expected_output = {
            "nodeId": "save_frames",
            **record["outputs"]["save_frames"]["images"][index - 1],
        }
        actual_output = dict(download["output"])

        # ComfyUI history can retain Windows backslashes while retained-output
        # recovery serializes the same relative subfolder with forward slashes.
        # Canonicalize separators only; every other provenance field remains strict.
        for output_record in (actual_output, expected_output):
            subfolder = output_record.get("subfolder")
            if isinstance(subfolder, str):
                output_record["subfolder"] = normalize_output_subfolder(subfolder)

        if actual_output != expected_output:
            raise ValueError("Frame download/provider output identity mismatch")
    return {
        **report_value,
        "workflow_binding": "verified",
        "workflow_sha256": workflow_digest,
        "input_sha256": sha256_file(reference),
        "prompt_id": receipt["promptId"],
        "seed": graph["high_sampler"]["inputs"]["noise_seed"],
        "raw_checkpoint": "raw_verified",
    }


def scene_binding(
    run_root: Path, shot_id: str, manifest: dict[str, Any]
) -> tuple[dict[str, Any], Path, Path]:
    if not re.fullmatch(r"shot-00[1-7]", shot_id):
        raise ValueError("Invalid scene identity")
    run = read_json(run_root / "run.json")
    unsigned = {k: v for k, v in manifest.items() if k != "digest"}
    if (
        sha256_json(unsigned) != manifest["digest"]
        or run["preparation"] != manifest["digest"]
    ):
        raise ValueError("Preparation identity does not match this run")
    scene = run_root / shot_id
    graph = read_json(scene / "submitted-workflow.json")
    prepared = run_root.parents[1] / "prepared"
    expected = read_json(prepared / "workflows" / run["profile"] / f"{shot_id}.json")
    reference_record = next(
        row for row in manifest["references"] if row["shotId"] == shot_id
    )
    reference = Path(reference_record["path"])
    if (
        not reference.resolve().is_relative_to((prepared / "references").resolve())
        or sha256_file(reference) != reference_record["sha256"]
    ):
        raise ValueError("Prepared reference identity mismatch")
    image = graph["start_image"]["inputs"]["image"]
    prefix = graph["save_frames"]["inputs"]["filename_prefix"]
    if not re.fullmatch(r"[A-Za-z0-9_.-]+\.png", image) or not re.fullmatch(
        shot_id + r"/(?:attempt-[a-z0-9-]+/)?frame", prefix
    ):
        raise ValueError("Input/output identity escapes this scene")
    expected = copy.deepcopy(expected)
    expected["start_image"]["inputs"]["image"] = image
    expected["save_frames"]["inputs"]["filename_prefix"] = prefix
    if graph != expected:
        raise ValueError(
            "Submitted workflow/settings/seed differ from the approved prepared scene"
        )
    provider_input = run_root / "comfy/input" / image
    if sha256_file(provider_input) != reference_record["sha256"]:
        raise ValueError("Provider input bytes differ from the approved reference")
    return graph, reference, provider_input


def verify_checkpoint(
    run_root: Path,
    shot_id: str,
    manifest: dict[str, Any],
    *,
    source_history: dict[str, Any] | None = None,
    write_proof: bool = False,
) -> dict[str, Any]:
    graph, reference, provider_input = scene_binding(run_root, shot_id, manifest)
    size = (graph["latent"]["inputs"]["width"], graph["latent"]["inputs"]["height"])
    proof = verify_raw(
        run_root / shot_id,
        size,
        graph,
        reference,
        provider_input,
        history=source_history,
    )
    if write_proof:
        if (
            CURRENT_OWNER is None
            or not CURRENT_OWNER.held
            or CURRENT_OWNER.run_id != run_root.name
        ):
            raise ValueError(
                "Exclusive run ownership required to commit checkpoint proof"
            )
        proof_path = run_root / shot_id / "verified-provenance.json"
        marker_path = run_root / shot_id / "raw-verified.json"
        if not marker_path.exists():
            atomic_write_json(
                marker_path,
                {
                    "promptId": proof["prompt_id"],
                    "dimensions": [proof["width"], proof["height"]],
                    "frames": [
                        {"name": row["name"], "sha256": row["sha256"]}
                        for row in proof["frames"]
                    ],
                },
            )
        if proof_path.exists():
            previous = read_json(proof_path)
            if any(
                previous.get(key) != proof[key]
                for key in (
                    "manifest_sha256",
                    "workflow_sha256",
                    "input_sha256",
                    "prompt_id",
                )
            ):
                raise ValueError("Existing verification proof conflicts; preserve it")
            return previous  # type: ignore[no-any-return]
        atomic_write_json(proof_path, proof)
    return proof
