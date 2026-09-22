"""Read verified Hayale facts for WNP; never control a renderer or repair output."""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

PROJECT = Path(__file__).resolve().parent
REPO = PROJECT.parents[2]
sys.path.insert(0, str(REPO / "backend"))
from app.local_video.comfyui import ComfyUIClient, ComfyUIProviderError  # noqa: E402
from app.local_video.wnp_projection import sanitize_observation  # noqa: E402
from app.mission_control.processes import process_started_at  # noqa: E402
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


def now() -> datetime:
    return datetime.now(UTC)


def identity(path: Path) -> tuple[int, int, int, int, int] | None:
    try:
        if path.is_symlink() or path.is_junction():
            raise ValueError("Redirected observation source")
        stat = path.stat()
        return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns
    except FileNotFoundError:
        return None


def read(path: Path) -> dict[str, Any]:
    before = identity(path)
    if before is None or before[2] > 8 * 1024 * 1024:
        raise ValueError("Missing or oversized observation source")
    raw = path.read_bytes()
    if identity(path) != before:
        raise ValueError("Observation source changed during read")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Observation source must be an object")
    return value


def source_time(path: Path) -> str | None:
    value = identity(path)
    return datetime.fromtimestamp(value[3] / 1e9, UTC).isoformat() if value else None


def code_files() -> list[Path]:
    return [*PROJECT.glob("*.py"), REPO / "tools/wnp-trackprompt-kit/trackprompt/src/frame_check.py",
            REPO / "backend/app/video_generation/jsonio.py", REPO / "backend/app/local_video/wnp_projection.py"]


class ReadOnlyClient(ComfyUIClient):
    observation_identity: dict[str, Any]

    def _request_bytes(self, method: str, path: str, **kwargs: Any) -> bytes:
        if method != "GET" or not (
            path in {"/queue", "/system_stats", "/history"} or path.startswith("/history/")
        ):
            raise ValueError("Observer permits only bounded provider status GETs")
        return super()._request_bytes(method, path, **kwargs)


@contextmanager
def provider_connection(run_root: Path) -> Iterator[tuple[ReadOnlyClient, dict[str, Any]]]:
    metadata = run_root / "comfy/server-process.json"
    record = read(metadata)
    metadata_sha = sha256_file(metadata)
    endpoint = record.get("endpoint")
    parsed = urlsplit(endpoint) if isinstance(endpoint, str) else None
    if parsed is None or parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path:
        raise ValueError("Unverified local provider endpoint")
    runtime = sibling("runtime")
    # This existing ownership verifier is specific to the recorded 8189 instance.
    if endpoint != runtime.ENDPOINT:
        raise ValueError("Recorded provider is outside the existing ownership verifier")
    pid = record.get("pid")
    if type(pid) is not int or pid <= 0:
        raise ValueError("Provider PID identity unavailable")
    expected_start = record.get("started_at") or record.get("startedAt")
    if not expected_start:
        audited = read(run_root / "recovery/provider-instance.json")
        if audited.get("pid") != pid or audited.get("run") != run_root.name or audited.get("server_record_sha256") != metadata_sha:
            raise ValueError("Legacy provider has no audited process start binding")
        expected_start = audited.get("started_at")
    if not isinstance(expected_start, str):
        raise ValueError("Provider process start timestamp unavailable")
    bound_start = datetime.fromisoformat(expected_start.replace("Z", "+00:00"))
    actual_start = process_started_at(pid)
    if actual_start is None or bound_start.tzinfo is None or abs((actual_start - bound_start).total_seconds()) > 0.01:
        raise ValueError("Provider process start identity does not match")
    runtime.require_listener_owner(pid)
    client = ReadOnlyClient(endpoint, request_timeout_seconds=4, connect_timeout_seconds=2)
    stats = client._request_json("GET", "/system_stats")
    arguments = stats.get("system", {}).get("argv", [])
    for flag, expected in (("--output-directory", run_root / "comfy/output"),
                           ("--input-directory", run_root / "comfy/input")):
        if flag not in arguments or arguments.index(flag) + 1 >= len(arguments):
            raise ValueError("Provider source run identity unavailable")
        if Path(arguments[arguments.index(flag) + 1]).resolve() != expected.resolve():
            raise ValueError("Provider belongs to a different run")
    if process_started_at(pid) != actual_start or sha256_file(metadata) != metadata_sha:
        raise ValueError("Provider identity changed during observation")
    client.observation_identity = {"pid": pid, "started_at": actual_start.isoformat(),
                                   "run": run_root.name, "server_record_sha256": metadata_sha}
    yield client, stats


class HayaleObservationReader:
    def __init__(self, run_root: Path, manifest_path: Path, *,
                 checkpoint_verifier: Callable[..., dict[str, Any]] | None = None,
                 provider_factory: Callable[..., Any] | None = None,
                 process_started: Callable[[int], datetime | None] = process_started_at,
                 clock: Callable[[], datetime] = now, cache_path: Path | None = None) -> None:
        self.run_root = run_root.resolve()
        if not re.fullmatch(r"[0-9a-f]{12}", self.run_root.name):
            raise ValueError("Existing Hayale run identity required")
        self.manifest_path = manifest_path.resolve()
        self.verifier = checkpoint_verifier
        self.provider_factory = provider_factory or provider_connection
        self.process_started = process_started
        self.clock = clock
        self._code_fingerprint = self._fingerprint(code_files())
        self.cache_path = cache_path
        self._cache: dict[str, tuple[str, dict[str, Any], str | None]] = {}
        self._final_cache: tuple[str, dict[str, Any]] | None = None
        self._transition: dict[str, str] | None = None
        self._provider_observed_at: str | None = None
        self._provider_instance: dict[str, Any] | None = None
        if cache_path is not None and cache_path.is_file():
            try:
                cached = read(cache_path)
                if cached.get("schema") == 1 and cached.get("run") == self.run_root.name:
                    self._provider_observed_at = cached.get("provider_observed_at")
                    self._provider_instance = cached.get("provider_instance")
                    transition = cached.get("transition")
                    if isinstance(transition, dict) and re.fullmatch(r"[0-9a-f]{64}", str(transition.get("semantic", ""))) and re.fullmatch(r"hayale:[0-9a-f]{64}", str(transition.get("source_id", ""))):
                        self._transition = transition
                    for shot, row in cached.get("checkpoints", {}).items():
                        if re.fullmatch(r"shot-00[1-7]", shot):
                            proof = self._proof(row[1])
                            self._cache[shot] = str(row[0]), proof, row[2]
            except (OSError, ValueError, KeyError, TypeError, IndexError):
                self._cache.clear()

    @staticmethod
    def _proof(report: dict[str, Any]) -> dict[str, Any]:
        if (report.get("workflow_binding"), report.get("count"), report.get("width"), report.get("height")) != ("verified", 129, 832, 480):
            raise ValueError("Checkpoint verification is incomplete")
        if not re.fullmatch(r"[0-9a-f]{64}", str(report.get("manifest_sha256", ""))):
            raise ValueError("Checkpoint has no bound frame manifest")
        stamp = datetime.fromisoformat(report["observed_at"].replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError("Checkpoint source timestamp unavailable")
        return {key: report[key] for key in (
            "manifest_sha256", "count", "width", "height", "observed_at", "workflow_binding",
        )}

    def _save_cache(self, *, required: bool = False) -> None:
        if self.cache_path is not None:
            try:
                if self.cache_path.is_symlink() or self.cache_path.is_junction():
                    raise ValueError("Redirected observer cache")
                atomic_write_json(self.cache_path, {"schema": 1, "run": self.run_root.name,
                                  "checkpoints": self._cache, "transition": self._transition,
                                  "provider_observed_at": self._provider_observed_at,
                                  "provider_instance": self._provider_instance})
            except (OSError, ValueError):
                if required:
                    raise
                pass  # Failure loses an optimization, never verification or execution authority.

    def _source_id(self, durable: dict[str, Any]) -> str:
        semantic = sha256_json(durable)
        previous = self._transition
        if previous and previous["semantic"] == semantic:
            return previous["source_id"]
        # Bind each distinct transition to its predecessor, preserving real state
        # revisits while keeping retries and ordinary restarts on the same source ID.
        source_id = "hayale:" + sha256_json({"semantic": semantic, "previous": previous["source_id"] if previous else None})
        self._transition = {"semantic": semantic, "source_id": source_id}
        try:
            self._save_cache(required=True)
        except Exception:
            self._transition = previous
            raise
        return source_id

    def _tracked(self, shot: str, manifest: dict[str, Any]) -> list[Path]:
        scene = self.run_root / shot
        paths = [self.run_root / "run.json", self.manifest_path]
        paths += code_files()
        paths += [scene / name for name in (
            "submission.json", "attempt.json", "submitted-workflow.json", "completed-history.json",
            "raw-verified.json", "provenance-verified.json",
        )]
        for directory in (scene / "raw", scene / "downloads", self.run_root / "comfy/input"):
            paths.append(directory)
            if directory.is_dir():
                paths += list(directory.rglob("*"))
        for group in ("references", "sources", "files", "installed"):
            for record in manifest.get(group, []):
                if isinstance(record, dict) and isinstance(record.get("path"), str):
                    paths.append(Path(record["path"]))
        return sorted(set(paths), key=str)

    @staticmethod
    def _fingerprint(paths: list[Path], extra: Any = None) -> str:
        return sha256_json({"files": [(str(path), identity(path)) for path in paths], "binding": extra})

    def _checkpoint(self, shot: str, manifest: dict[str, Any], history: dict[str, Any] | None = None) -> dict[str, Any]:
        paths = self._tracked(shot, manifest)
        digest = self._fingerprint(paths)
        history_digest = sha256_json(history) if history is not None else None
        cached = self._cache.get(shot)
        if cached and cached[0] == digest and (history_digest is None or history_digest == cached[2]):
            return cached[1]
        self._cache.pop(shot, None)
        verifier = self.verifier or sibling("durable").verify_checkpoint
        report = self._proof(verifier(self.run_root, shot, manifest, source_history=history, write_proof=False))
        if self._fingerprint(self._tracked(shot, manifest)) != digest:
            raise ValueError("Checkpoint inputs changed during verification")
        self._cache[shot] = digest, report, history_digest
        self._save_cache()
        return report

    def _worker(self) -> dict[str, Any]:
        result: dict[str, Any] = {"id": "hayale-controller", "state": "unknown", "heartbeat_at": None}
        try:
            record = read(self.run_root / "worker.json")
            if record.get("run") != self.run_root.name:
                return result
            heartbeat = record.get("heartbeat_at")
            if isinstance(heartbeat, str):
                result["heartbeat_at"] = heartbeat
            pid = record.get("pid")
            if type(pid) is not int or pid <= 0:
                return result
            result["pid"] = pid
            actual_start = self.process_started(pid)
            expected_start = datetime.fromisoformat(record["started_at"].replace("Z", "+00:00"))
            if actual_start is None or expected_start.tzinfo is None or abs((actual_start - expected_start).total_seconds()) > 0.01:
                return result
            if not isinstance(heartbeat, str):
                return result
            stamp = datetime.fromisoformat(heartbeat.replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                return result
            age = (self.clock() - stamp).total_seconds()
            if age < -5:
                return result
            result["state"] = "stale" if age > 90 else "active"
            if record.get("state") == "stopped":
                result["state"] = "stopped"
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            pass
        return result

    @staticmethod
    def _metrics(stats: dict[str, Any]) -> dict[str, Any]:
        metrics: dict[str, Any] = {}
        system = stats.get("system", {})
        if all(type(system.get(key)) in {int, float} for key in ("ram_total", "ram_free")):
            used = system["ram_total"] - system["ram_free"]
            if used >= 0:
                metrics["ram_used_mib"] = used / 1024**2
        gpu = []
        for index, device in enumerate(stats.get("devices", [])):
            if all(type(device.get(key)) in {int, float} for key in ("vram_total", "vram_free")):
                used = device["vram_total"] - device["vram_free"]
                if used >= 0:
                    gpu.append({"index": index, "vram_total_mib": device["vram_total"] / 1024**2,
                                "vram_used_mib": used / 1024**2})
        if gpu:
            metrics["gpu"] = gpu
        return metrics

    def _final(self, binding: dict[str, Any]) -> dict[str, Any] | None:
        path = self.run_root / "delivery.json"
        if not path.is_file():
            return None
        delivery = read(path)
        report, files = delivery.get("report", {}), delivery.get("files", [])
        if not isinstance(files, list) or not files:
            raise ValueError("Delivery files are unbound")
        paths = [path, self.run_root / "run.json", *PROJECT.glob("*.py")]
        references: dict[Path, dict[str, Any]] = {}
        for record in files:
            asset = Path(record["path"]).resolve()
            if not asset.is_relative_to(self.run_root) or asset in references:
                raise ValueError("Invalid delivery asset binding")
            references[asset] = record
            paths.append(asset)
        digest = self._fingerprint(paths)
        if self._final_cache and self._final_cache[0] == digest:
            return self._final_cache[1]
        self._final_cache = None
        for asset, record in references.items():
            if sha256_file(asset) != record["sha256"] or ("bytes" in record and asset.stat().st_size != record["bytes"]):
                raise ValueError("Delivery file digest mismatch")
        expected: dict[str, Any] = {"frames": 1440, "fps": 24, "durationSeconds": 60, "size": [1920, 1080],
                    "uniqueGenerations": 7, "logoUses": 2, "rawClipsUnchanged": True, "sourceAudioUnchanged": True,
                    "audioPresent": not binding.get("silent", False)}
        if any(report.get(key) != value for key, value in expected.items()):
            raise ValueError("Final delivery contract is incomplete")
        preview = Path(report["preview"]).resolve()
        proof = preview.parent / "verification-report.json"
        probe = preview.parent / "ffprobe-evidence.json"
        if any(asset not in references for asset in (preview, proof, probe)) or read(proof) != report:
            raise ValueError("Final verification report is not bound to delivery")
        if references[preview]["sha256"] != report.get("previewSha256"):
            raise ValueError("Final preview digest does not match report")
        evidence = read(probe)
        video = [row for row in evidence["streams"] if row.get("codec_type") == "video"]
        audio = [row for row in evidence["streams"] if row.get("codec_type") == "audio"]
        if len(video) != 1 or (video[0].get("width"), video[0].get("height"),
                              int(video[0].get("nb_read_frames", 0)), video[0].get("avg_frame_rate")) != (1920, 1080, 1440, "24/1"):
            raise ValueError("Final frame verification is incomplete")
        if abs(float(evidence["format"]["duration"]) - 60) > 0.1 or len(audio) != int(expected["audioPresent"]):
            raise ValueError("Final duration/audio verification is incomplete")
        if audio and (audio[0].get("sample_rate") != "48000" or audio[0].get("channels") != 2 or
                      abs(float(audio[0].get("duration", 0)) - 60) > 0.1):
            raise ValueError("Final audio format/sync verification is incomplete")
        if self._fingerprint(paths) != digest:
            raise ValueError("Delivery changed during verification")
        result = {"final_verified": True, "final_manifest_sha256": sha256_file(path),
                  "review_complete": report.get("creativeReview") == "approved"}
        self._final_cache = digest, result
        return result

    def read(self) -> tuple[str, dict[str, Any]]:
        if self._fingerprint(code_files()) != self._code_fingerprint:
            raise ValueError("Observer code changed; restart to load the new verifier")
        manifest = read(self.manifest_path)
        if sha256_json({key: value for key, value in manifest.items() if key != "digest"}) != manifest.get("digest"):
            raise ValueError("Preparation manifest changed")
        binding = read(self.run_root / "run.json")
        if binding.get("preparation") != manifest["digest"] or binding.get("profile") != "quality":
            raise ValueError("Run preparation/quality binding changed")
        tasks: list[dict[str, Any]] = []
        pending: list[str] = []
        for index in range(1, 8):
            shot = f"shot-{index:03d}"
            scene = self.run_root / shot
            receipt_path = scene / "submission.json"
            task: dict[str, Any] = {"task": shot, "phase": "prepared", "expected_frames": 129,
                                    "updated_at": source_time(receipt_path) or source_time(self.run_root / "run.json")}
            if receipt_path.is_file():
                task.update(phase="reconciling", attempt="legacy-001")
            attempt = scene / "attempt.json"
            if attempt.is_file():
                task["attempt"] = read(attempt).get("identity", {}).get("attempt", "legacy-001")
            if (scene / "raw").is_dir() and any((scene / "raw").glob("*.png")):
                try:
                    proof = self._checkpoint(shot, manifest)
                    task.update(phase="raw_verified", frames_verified=proof["count"],
                                raw_manifest_sha256=proof["manifest_sha256"], width=proof["width"], height=proof["height"],
                                updated_at=proof["observed_at"])
                except Exception:
                    task["phase"] = "reconciling"
            if task["phase"] != "raw_verified" and receipt_path.is_file():
                pending.append(shot)
            tasks.append(task)
        provider: dict[str, Any] = {"name": "comfyui", "state": "unknown", "observed_at": None}
        metrics: dict[str, Any] = {}
        if pending:
            provider["job"] = read(self.run_root / pending[0] / "submission.json").get("promptId")
            try:
                with self.provider_factory(self.run_root) as (client, stats):
                    instance = getattr(client, "observation_identity", None)
                    if isinstance(instance, dict):
                        if self._provider_instance is not None and instance != self._provider_instance:
                            raise ValueError("Provider instance changed; reconciliation required")
                        self._provider_instance = instance
                        self._save_cache(required=True)
                    metrics = self._metrics(stats)
                    for shot in pending:
                        observed = sibling("recovery").inspect_submission(self.run_root / shot, client)
                        task = tasks[int(shot[-3:]) - 1]
                        phase = {"missing": "ambiguous", "queued": "submitted"}.get(observed["state"], observed["state"])
                        task["phase"] = phase
                        if (self.run_root / shot / "raw").is_dir() and observed.get("history"):
                            try:
                                proof = self._checkpoint(shot, manifest, observed["history"])
                                task.update(phase="raw_verified", frames_verified=proof["count"],
                                            raw_manifest_sha256=proof["manifest_sha256"], width=proof["width"],
                                            height=proof["height"], updated_at=proof["observed_at"])
                            except Exception:
                                pass
                        if shot == pending[0]:
                            self._provider_observed_at = self.clock().isoformat()
                            provider.update(state={"frames_complete": "completed", "generating": "running", "missing": "unknown"}.get(observed["state"], observed["state"]),
                                            job=observed.get("promptId"), observed_at=self._provider_observed_at)
            except Exception as exc:
                unavailable = isinstance(exc, ComfyUIProviderError) and exc.code in {"provider_unavailable", "provider_http_error"}
                provider.update(state="unreachable" if unavailable else "unknown", observed_at=self._provider_observed_at)
                for shot in pending:
                    tasks[int(shot[-3:]) - 1]["phase"] = "waiting_provider" if unavailable else "reconciling"
        verified = sum(task["phase"] == "raw_verified" for task in tasks)
        active = next((task for task in tasks if task["phase"] != "raw_verified"), tasks[-1])
        phase = active["phase"] if verified != 7 else "finalizing"
        workload = "running" if phase == "generating" else "waiting"
        health = "degraded" if phase in {"waiting_provider", "reconciling", "ambiguous", "failed"} else "ok"
        observation = {"run": self.run_root.name, "task": active["task"], "attempt": active.get("attempt"),
                       "phase": phase, "workload_state": workload, "health": health, "tasks": tasks,
                       "worker": self._worker(), "provider": provider, "metrics": metrics,
                       "progress": {"current": verified, "total": 7, "unit": "shots"}}
        try:
            final = self._final(binding) if verified == 7 else None
            if final:
                observation.update(phase="final_verified", final_verified=True,
                                   final_manifest_sha256=final["final_manifest_sha256"],
                                   workload_state="complete" if final["review_complete"] else "waiting")
        except Exception:
            observation.update(phase="reconciling", workload_state="waiting", health="degraded")
        normalized = sanitize_observation(observation)
        durable = {key: value for key, value in normalized.items() if key not in {"worker", "provider", "metrics", "progress"}}
        return self._source_id(durable), normalized
