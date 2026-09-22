"""Redacted WNP projection of verified observations, never an execution controller.

The caller supplies source identity and normalized, proven application facts. This
module reads no render receipts, media, provider endpoints, or process state.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import math
import re
import sqlite3
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import ModuleType, TracebackType
from typing import Any, cast

_IDENT = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}\Z")
_OPAQUE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_HASH = re.compile(r"[a-f0-9]{64}\Z")
_PHASES = {
    "queued", "prepared", "submitted", "generating", "frames_complete", "raw_verified",
    "finalizing", "final_verified", "complete", "failed", "waiting_provider", "ambiguous", "reconciling",
}
_STATES = {"idle", "queued", "running", "waiting", "paused", "complete", "failed", "degraded", "unknown"}
_HEALTH = {"ok", "degraded", "error", "unknown"}
_LIVE_KEYS = {"worker", "provider", "metrics", "progress"}
_MAX_NORMALIZED_BYTES = 32 * 1024
STAGES = {"preparation", "analysis", "planning", "generation", "transfer", "verification",
          "finishing", "music-mux", "export", "recovery"}


def _pipeline(source: Mapping[str, object]) -> dict[str, object]:
    output: dict[str, object] = {"stage": _choice(source.get("stage"), STAGES)}
    for key in ("profile", "error_code", "remedy"):
        if source.get(key) is not None:
            output[key] = _identifier(source[key])
    for key in ("scene_total", "scenes_verified", "source_clips_verified", "provider_attempts"):
        if key in source:
            output[key] = _integer(source[key])
    for key in ("technical_verified", "retryable", "provider_cancel_confirmed"):
        if key in source:
            _require(type(source[key]) is bool)
            output[key] = source[key]
    for key in ("created_at", "next_retry_at"):
        if key in source:
            output[key] = _timestamp(source[key])
    for key in ("reserved_cost_usd", "estimated_cost_usd"):
        if key in source:
            output[key] = _number(source[key])
    if "creative_approval" in source:
        output["creative_approval"] = _choice(source["creative_approval"], {"pending", "accepted", "rejected", "unknown"})
    if "action" in source:
        output["action"] = _choice(source["action"], {"none", "retry-safe-read", "reauthenticate", "reconcile", "review", "repair-access", "repair-artifact", "authorize"})
    if "scenes_verified" in output and "scene_total" in output:
        _require(cast(int, output["scenes_verified"]) <= cast(int, output["scene_total"]))
    return output


def load_wnp_kit() -> tuple[ModuleType, ModuleType]:
    """Load the supplied master and adapter without installing or editing either."""
    kit = Path(__file__).resolve().parents[3] / "tools" / "wnp-trackprompt-kit"
    roots = (kit / "master" / "src", kit / "trackprompt" / "src")
    for root in roots:
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
    modules = (importlib.import_module("wnp_host.store"), importlib.import_module("wnp_trackprompt"))
    for module, root in zip(modules, roots, strict=True):
        if not module.__file__ or not Path(module.__file__).resolve().is_relative_to(root.resolve()):
            raise RuntimeError("Bundled WNP module identity conflict")
    return modules


class ProjectionError(ValueError):
    """Local safe error code; no input bytes or filesystem paths in the message."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _require(condition: bool) -> None:
    if not condition:
        raise ProjectionError("invalid_observation")


def _identifier(value: object) -> str:
    _require(isinstance(value, str) and bool(_IDENT.fullmatch(value)))
    return cast(str, value)


def _choice(value: object, choices: set[str]) -> str:
    _require(isinstance(value, str) and value in choices)
    return cast(str, value)


def _timestamp(value: object) -> str | None:
    if value is None:
        return None
    _require(isinstance(value, str) and len(value) <= 40 and "T" in value)
    try:
        parsed = datetime.fromisoformat(cast(str, value).replace("Z", "+00:00"))
    except ValueError:
        raise ProjectionError("invalid_observation") from None
    _require(parsed.tzinfo is not None)
    return cast(str, value)


def _number(value: object, *, maximum: float | None = None) -> int | float | None:
    if value is None:
        return None
    _require(type(value) in (int, float))
    number = cast(int | float, value)
    _require(math.isfinite(number) and number >= 0 and (maximum is None or number <= maximum))
    return number


def _integer(value: object) -> int:
    _require(type(value) is int and value >= 0)
    return cast(int, value)


def _mapping(value: object) -> Mapping[str, object]:
    _require(isinstance(value, Mapping))
    return cast(Mapping[str, object], value)


def _stage_fields(source: Mapping[str, object]) -> dict[str, object]:
    output: dict[str, object] = {"phase": _choice(source.get("phase"), _PHASES)}
    for key in ("task", "attempt"):
        if key in source:
            output[key] = _identifier(source[key]) if source[key] is not None else None
    for key in ("raw_manifest_sha256", "final_manifest_sha256"):
        if key in source:
            value = source[key]
            _require(isinstance(value, str) and bool(_HASH.fullmatch(value)))
            output[key] = value
    if "final_verified" in source:
        _require(type(source["final_verified"]) is bool)
        output["final_verified"] = source["final_verified"]
    if output["phase"] == "raw_verified":
        _require("raw_manifest_sha256" in output)
    if output["phase"] in {"complete", "final_verified"}:
        _require(output.get("final_verified") is True and "final_manifest_sha256" in output)
    return output


def _live_observation(source: Mapping[str, object]) -> dict[str, object]:
    output: dict[str, object] = {}
    if "worker" in source:
        worker = _mapping(source["worker"])
        clean: dict[str, object] = {}
        if "state" in worker:
            clean["state"] = _choice(worker["state"], {"idle", "active", "stale", "stopped", "unknown"})
        if "id" in worker:
            clean["id"] = _identifier(worker["id"])
        if "pid" in worker:
            clean["pid"] = _integer(worker["pid"]) if worker["pid"] is not None else None
        for key in ("heartbeat_at", "lease_expires_at"):
            if key in worker:
                clean[key] = _timestamp(worker[key])
        output["worker"] = clean
    if "provider" in source:
        provider = _mapping(source["provider"])
        clean = {}
        if "name" in provider:
            clean["name"] = _identifier(provider["name"])
        if "state" in provider:
            clean["state"] = _choice(provider["state"], {
                "idle", "queued", "running", "completed", "complete", "failed", "unknown",
                "unreachable", "unavailable", "loading", "waiting", "ambiguous",
            })
        if "job" in provider:
            job = provider["job"]
            # Unsafe opaque IDs are omitted; never export a URL/path as a job ID.
            if job is None or (isinstance(job, str) and _OPAQUE.fullmatch(job)):
                clean["job"] = job
        if "observed_at" in provider:
            clean["observed_at"] = _timestamp(provider["observed_at"])
        output["provider"] = clean
    if "metrics" in source:
        metrics = _mapping(source["metrics"])
        clean = {}
        for key in ("cpu_percent", "ram_used_mib", "disk_free_mib"):
            if key in metrics:
                clean[key] = _number(metrics[key], maximum=100 if key == "cpu_percent" else None)
        if "gpu" in metrics:
            gpu = metrics["gpu"]
            _require(isinstance(gpu, list) and len(gpu) <= 32)
            readings: list[dict[str, object]] = []
            for raw in cast(list[object], gpu):
                sample = _mapping(raw)
                entry: dict[str, object] = {"index": _integer(sample.get("index"))}
                for key in ("utilization_percent", "vram_used_mib", "vram_total_mib", "temperature_c"):
                    if key in sample:
                        entry[key] = _number(sample[key], maximum=100 if key == "utilization_percent" else None)
                readings.append(entry)
            clean["gpu"] = readings
        output["metrics"] = clean
    if "progress" in source:
        progress = _mapping(source["progress"])
        current = _number(progress.get("current"))
        _require(current is not None)
        clean = {"current": current, "unit": _identifier(progress.get("unit"))}
        if "total" in progress:
            total = _number(progress["total"])
            _require(total is None or (current is not None and total >= current))
            clean["total"] = total
        output["progress"] = clean
    return output


def sanitize_observation(observation: Mapping[str, object]) -> dict[str, object]:
    """Copy only bounded protocol fields, preserving source timestamps verbatim.

    Arbitrary evidence, messages, prompts, bytes, paths, and free text are omitted.
    Digests are references to verification done by the reader, not proof performed here.
    """
    source = _mapping(observation)
    output = _stage_fields(source)
    output.update({"run": _identifier(source.get("run")),
                   "workload_state": _choice(source.get("workload_state"), _STATES),
                   "health": _choice(source.get("health"), _HEALTH)})
    if output["workload_state"] == "complete":
        _require(output["phase"] in {"complete", "final_verified"})
    if "tasks" in source:
        tasks = source["tasks"]
        _require(isinstance(tasks, list) and len(tasks) <= 128)
        summaries: list[dict[str, object]] = []
        seen: set[str] = set()
        for raw in cast(list[object], tasks):
            task = _mapping(raw)
            summary = _stage_fields(task)
            task_id = _identifier(task.get("task"))
            _require(task_id not in seen)
            seen.add(task_id)
            summary["task"] = task_id
            for key in ("frames_verified", "expected_frames", "width", "height"):
                if key in task:
                    summary[key] = _integer(task[key])
            if "frames_verified" in summary and "expected_frames" in summary:
                _require(cast(int, summary["frames_verified"]) <= cast(int, summary["expected_frames"]))
            if "updated_at" in task:
                summary["updated_at"] = _timestamp(task["updated_at"])
            summaries.append(summary)
        output["tasks"] = summaries
    output.update(_live_observation(source))
    if "pipeline" in source:
        output["pipeline"] = _pipeline(_mapping(source["pipeline"]))
        if cast(dict[str, object], output["pipeline"]).get("technical_verified") is True:
            _require(output.get("final_verified") is True and "final_manifest_sha256" in output)
    if "durable_progress" in source:
        output["durable_progress"] = _live_observation({"progress": source["durable_progress"]})["progress"]
    _require(len(_encode(output).encode("utf-8")) <= _MAX_NORMALIZED_BYTES)
    return output


def _encode(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


@dataclass(frozen=True)
class ProjectionResult:
    ok: bool
    pending: int | None
    error: str | None = None
    sequence: int | None = None


class _SourceStore:
    """Preserve the supplied mapping while binding append to the durable source ID."""

    def __init__(self, store: Any, source_key: str, observation: Mapping[str, object]) -> None:
        self.store = store
        self.source_key = source_key
        self.observation = observation

    def _patch(self, patch: dict[str, Any]) -> dict[str, Any]:
        if "pipeline" in self.observation:
            pipeline = _mapping(self.observation["pipeline"])
            patch["extensions"]["trackprompt"]["pipeline"] = dict(pipeline)
            patch["detail"] = "trackprompt." + str(pipeline["stage"])
            patch["progress"] = self.observation.get("durable_progress")
        return patch

    def snapshot(self, live: bool = True) -> dict[str, Any]:
        snapshot = cast(dict[str, Any], self.store.snapshot(live=live))
        # The frozen adapter compares its legacy patch before append. Force that
        # comparison to include shared extensions, without editing bundled bytes.
        if "pipeline" in self.observation:
            snapshot = dict(snapshot)
            snapshot["extensions"] = {"trackprompt": {"shared_mapping": True}}
        return snapshot

    def append(self, event_type: str, **kwargs: Any) -> dict[str, Any]:
        patch = self._patch(kwargs["patch"])
        prior = self.store.snapshot(live=False)
        if all(prior.get(key) == value for key, value in patch.items()):
            return cast(dict[str, Any], prior)
        kwargs["patch"] = patch
        kwargs["key"] = self.source_key
        return cast(dict[str, Any], self.store.append(event_type, **kwargs))


class WNPProjection:
    """Single-owner observation outbox for an already initialized local WNP node.

    Use as a context manager in an independent observer. `publish`, `drain`, and
    `observe` contain telemetry failures in safe results. Startup errors can raise;
    execution must not depend on launching or keeping this observer alive.
    """

    def __init__(self, project_root: Path) -> None:
        store_module, self._adapter = load_wnp_kit()
        self.store: Any = store_module.Store(project_root)
        self._fs = importlib.import_module("wnp_host.fs")
        self._lock: Any = None
        self._db: sqlite3.Connection | None = None
        self._stream: str | None = None

    def __enter__(self) -> WNPProjection:
        if self._db is not None:
            raise ProjectionError("adapter_already_open")
        self._lock = self._fs.FileLock(self.store.runtime / "trackprompt-adapter.lock", timeout=0)
        self._lock.__enter__()
        try:
            path = self._fs.safe_child(self.store.runtime, "trackprompt-outbox.sqlite3")
            self._db = sqlite3.connect(path, timeout=1)
            self._db.execute("PRAGMA synchronous=FULL")
            self._db.execute("PRAGMA journal_mode=DELETE")
            self._db.execute("CREATE TABLE IF NOT EXISTS metadata (singleton INTEGER PRIMARY KEY CHECK(singleton=1), stream TEXT NOT NULL)")
            self._db.execute("CREATE TABLE IF NOT EXISTS outbox (ordinal INTEGER PRIMARY KEY AUTOINCREMENT, source_key TEXT UNIQUE NOT NULL, observation TEXT NOT NULL, ack_sequence INTEGER)")
            self._db.execute("CREATE TABLE IF NOT EXISTS selection (singleton INTEGER PRIMARY KEY CHECK(singleton=1), run TEXT NOT NULL)")
            self._stream = str(self.store.node()["stream"])
            with self._db:
                self._db.execute("INSERT OR IGNORE INTO metadata VALUES (1, ?)", (self._stream,))
            if self._db.execute("SELECT stream FROM metadata WHERE singleton=1").fetchone()[0] != self._stream:
                raise ProjectionError("outbox_stream_conflict")
            self._check_history()
            return self
        except Exception:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None,
                 traceback: TracebackType | None) -> None:
        try:
            if self._db is not None:
                self._db.close()
                self._db = None
        finally:
            if self._lock is not None:
                self._lock.__exit__(exc_type, exc, traceback)
                self._lock = None

    def _connection(self) -> sqlite3.Connection:
        if self._db is None:
            raise ProjectionError("adapter_not_open")
        return self._db

    def select_run(self, run: str) -> None:
        """Explicit operator selection; retains the complete node journal/outbox."""
        _identifier(run)
        if self._result().pending:
            raise ProjectionError("pending_selection_conflict")
        with self._connection():
            self._connection().execute("INSERT OR REPLACE INTO selection VALUES (1, ?)", (run,))

    def require_selected(self, run: str) -> None:
        row = self._connection().execute("SELECT run FROM selection WHERE singleton=1").fetchone()
        if row is None or row[0] != run:
            raise ProjectionError("job_selection_required")

    def _enqueue(self, source_key: str, observation: dict[str, object]) -> None:
        connection = self._connection()
        encoded = _encode(observation)
        with connection:
            row = connection.execute("SELECT observation FROM outbox WHERE source_key=?", (source_key,)).fetchone()
            if row is not None and row[0] != encoded:
                raise ProjectionError("source_identity_conflict")
            connection.execute("INSERT OR IGNORE INTO outbox(source_key,observation) VALUES (?,?)", (source_key, encoded))

    def _check_history(self) -> None:
        if self.store.node()["stream"] != self._stream:
            raise ProjectionError("outbox_stream_conflict")
        acknowledged = self._connection().execute("SELECT max(ack_sequence) FROM outbox").fetchone()[0]
        if acknowledged is not None and acknowledged > self.store.snapshot(live=False)["sequence"]:
            raise ProjectionError("outbox_cursor_ahead")

    def _ack(self, source_key: str, sequence: int) -> None:
        connection = self._connection()
        with connection:
            connection.execute("UPDATE outbox SET ack_sequence=? WHERE source_key=?", (sequence, source_key))

    def _result(self, error: str | None = None, sequence: int | None = None) -> ProjectionResult:
        try:
            pending = int(self._connection().execute("SELECT count(*) FROM outbox WHERE ack_sequence IS NULL").fetchone()[0])
        except Exception:
            pending = None
        return ProjectionResult(ok=error is None, pending=pending, error=error, sequence=sequence)

    def _failure(self, exc: Exception) -> ProjectionResult:
        # Do not relay arbitrary exception messages, paths, SQL values, or secrets.
        code = exc.code if isinstance(exc, ProjectionError) else "projection_unavailable"
        return self._result(error=code)

    def publish(self, source_id: str, observation: Mapping[str, object]) -> ProjectionResult:
        """Persist the source transition before delivery; poll-only fields stay transient."""
        try:
            _require(isinstance(source_id, str) and 0 < len(source_id) <= 4096)
            normalized = sanitize_observation(observation)
            source_key = "trackprompt-source:" + hashlib.sha256(source_id.encode("utf-8")).hexdigest()
            durable = {key: value for key, value in normalized.items() if key not in _LIVE_KEYS}
            self._enqueue(source_key, durable)
            result = self.drain()
            live = {key: value for key, value in normalized.items() if key in _LIVE_KEYS}
            if live:
                try:
                    self.store.observe(live)
                except Exception:
                    if result.ok:
                        raise
            return result
        except Exception as exc:
            return self._failure(exc)

    def drain(self) -> ProjectionResult:
        """Replay oldest unacknowledged source records, stopping on the first failure."""
        try:
            connection = self._connection()
            self._check_history()
            while True:
                row = connection.execute("SELECT source_key,observation FROM outbox WHERE ack_sequence IS NULL ORDER BY ordinal LIMIT 1").fetchone()
                if row is None:
                    return self._result(sequence=int(self.store.snapshot(live=False)["sequence"]))
                source_key, encoded = row
                observation = sanitize_observation(json.loads(encoded))
                snapshot = self._adapter.publish(_SourceStore(self.store, source_key, observation), observation)
                self._ack(source_key, int(snapshot["sequence"]))
        except Exception as exc:
            return self._failure(exc)

    def observe(self, observation: Mapping[str, object]) -> ProjectionResult:
        """Refresh current metrics and original source timestamps, without a journal event."""
        try:
            self._connection()
            live = _live_observation(_mapping(observation))
            _require(len(_encode(live).encode("utf-8")) <= _MAX_NORMALIZED_BYTES)
            if live:
                self.store.observe(live)
            return self._result(sequence=int(self.store.snapshot(live=False)["sequence"]))
        except Exception as exc:
            return self._failure(exc)
