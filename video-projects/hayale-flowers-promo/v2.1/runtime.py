"""The manual local server lifecycle and receipt-backed scene submission."""

from __future__ import annotations

import ctypes
import importlib.util
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "backend"))
from app.local_video.comfyui import ComfyUIClient, ComfyUIProviderError  # noqa: E402
from app.mission_control.processes import process_is_alive, process_started_at  # noqa: E402
from app.subprocess_utils import _kill_process_tree, run_process_bounded  # noqa: E402
from app.video_generation.jsonio import atomic_write_json, sha256_file, sha256_json  # noqa: E402

ENDPOINT = "http://127.0.0.1:8189"


def durable_module() -> Any:
    key = "hayale_v21_durable"
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            key, Path(__file__).with_name("durable.py")
        )
        assert spec and spec.loader
        value = importlib.util.module_from_spec(spec)
        sys.modules[key] = value
        spec.loader.exec_module(value)
    return sys.modules[key]


def report(*args: Any, **kwargs: Any) -> None:
    durable_module().report(*args, **kwargs)


def drain_output(source: Any, log_path: Path) -> None:
    """A failed/filled log sink must never apply backpressure to ComfyUI."""
    sink = None
    written = 0
    try:
        try:
            sink = log_path.open("xb")
        except OSError:
            pass
        while True:
            try:
                chunk = source.read1(4096)
            except (OSError, ValueError):
                break
            if not chunk:
                break
            if sink is not None and written < 8_000_000:
                try:
                    sink.write(chunk[: 8_000_000 - written])
                    sink.flush()
                    written += len(chunk)
                except OSError:
                    try:
                        sink.close()
                    except OSError:
                        pass
                    sink = None
    finally:
        if sink is not None:
            try:
                sink.close()
            except OSError:
                pass


def require_listener_owner(pid: int) -> None:
    """Check the actual Windows listening socket, not just a successful HTTP response."""
    executable = (
        Path(os.environ.get("SystemRoot", "C:/Windows"))
        / "System32/WindowsPowerShell/v1.0/powershell.exe"
    )
    if not isinstance(pid, int) or pid <= 0:
        raise ValueError("Invalid owned process identity")
    # Windows venv launchers may host the interpreter in a child process.
    # Follow only the observed listener's parent chain back to this Popen PID.
    script = (
        "$ErrorActionPreference='Stop'; "
        "$owners=@(Get-NetTCPConnection -LocalPort 8189 -State Listen | Select-Object -ExpandProperty OwningProcess -Unique); "
        "if ($owners.Count -ne 1) { exit 1 }; $owner=[int]$owners[0]; "
        f"for ($depth=0; $depth -lt 16; $depth++) {{ if ($owner -eq {pid}) {{ Write-Output 'owned'; exit 0 }}; "
        "$item=Get-CimInstance Win32_Process -Filter ('ProcessId=' + $owner); "
        "if (-not $item -or $item.ParentProcessId -eq 0) { exit 1 }; $owner=[int]$item.ParentProcessId }; exit 1"
    )
    result = run_process_bounded(
        [str(executable), "-NoProfile", "-NonInteractive", "-Command", script],
        timeout_seconds=20,
        stdout_limit=4096,
        stderr_limit=4096,
        hide_window=True,
    )
    if result.returncode or result.stdout.strip() != b"owned":
        raise ValueError(
            "The local ComfyUI listening socket is not owned by this launch; no submission allowed"
        )


class OwnedClient(ComfyUIClient):
    owner_pid: int
    provider_instance: dict[str, Any]

    def _request_bytes(self, method: str, path: str, **kwargs: Any) -> bytes:
        if method != "GET":
            require_listener_owner(self.owner_pid)
            if hasattr(self, "provider_instance"):
                started = process_started_at(self.owner_pid)
                if started is None or started.isoformat() != self.provider_instance.get(
                    "started_at"
                ):
                    raise ValueError(
                        "Provider PID/start-time binding changed; no mutation allowed"
                    )
        return super()._request_bytes(method, path, **kwargs)


def read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def recorded_scene_state(
    scene_root: Path,
    observation: dict[str, Any] | None = None,
    observed_at: str | None = None,
) -> dict[str, Any]:
    receipt = (
        read(scene_root / "submission.json")
        if (scene_root / "submission.json").exists()
        else {}
    )
    stage = (
        read(scene_root / "stage.json") if (scene_root / "stage.json").exists() else {}
    )
    raw = (scene_root / "raw-verified.json").exists()
    source = "saved-records"
    if (
        not stage
        and observation
        and observation.get("promptId") == receipt.get("promptId")
        and observation.get("workflowDigest") == receipt.get("workflowDigest")
    ):
        stage = {**observation, "updatedAt": observed_at}
        source = "recovery-observation"
    if stage.get("promptId") not in {None, receipt.get("promptId")}:
        stage = {}
    state = stage.get("state", receipt.get("state", "not_submitted"))
    if raw and state not in {"finalizing", "complete", "failed"}:
        state = "raw_verified"
    return {
        "state": state,
        "rawCheckpoint": "raw_verified" if raw else None,
        "submissionReceiptState": receipt.get("state"),
        "promptId": receipt.get("promptId"),
        "waitingFor": stage.get("waitingFor") if state != "raw_verified" else None,
        "stageObservedAt": stage.get("updatedAt"),
        "source": source,
    }


def existing_server_available(run_root: Path) -> bool:
    metadata = run_root / "comfy/server-process.json"
    if not metadata.exists():
        return False
    with socket.socket() as probe:
        probe.settimeout(1)
        if probe.connect_ex(("127.0.0.1", 8189)) == 0:
            return True  # Borrowing separately verifies listener ownership + run paths.
    if process_is_alive(read(metadata)["pid"]):
        raise ValueError(
            "Recorded ComfyUI process is alive but not listening; reconcile its health before any restart"
        )
    return False


def handoff_gpu(client: ComfyUIClient, *, timeout: float = 60) -> None:
    queue = client.queue_snapshot()
    if queue.get("queue_running") or queue.get("queue_pending"):
        raise ValueError(
            "Provider queue is busy; defer GPU finishing without interrupting it"
        )
    client._request_bytes(
        "POST",
        "/free",
        body=b'{"unload_models":true,"free_memory":true}',
        content_type="application/json",
    )
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        queue = client.queue_snapshot()
        if queue.get("queue_running") or queue.get("queue_pending"):
            raise ValueError(
                "Provider work appeared during GPU handoff; finishing deferred"
            )
        stats = client._request_json("GET", "/system_stats")
        if stats.get("devices") and all(
            int(d.get("torch_vram_total", 2**63)) < 2**30 for d in stats["devices"]
        ):
            return
        time.sleep(1)
    raise TimeoutError(
        "ComfyUI has not released its model allocation; finishing deferred without stopping the server"
    )


def publish_stage(
    scene_root: Path,
    state: str,
    waiting_for: str | None = None,
    *,
    prompt_id: str | None = None,
) -> None:
    """A supervisor heartbeat describes activity; it cannot revoke raw completion."""
    raw_complete = (scene_root / "raw-verified.json").exists()
    if raw_complete and state in {
        "submitted",
        "queued",
        "generating",
        "frames_complete",
        "downloading",
    }:
        state, waiting_for = "raw_verified", None
    value = {
        "state": state,
        "waitingFor": waiting_for,
        "promptId": prompt_id,
        "rawCheckpoint": "raw_verified" if raw_complete else None,
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "supervisorPid": os.getpid(),
    }
    owner = durable_module().CURRENT_OWNER
    if owner is not None:
        owner.progress(
            state,
            scene_root.name,
            material=state in {"frames_complete", "raw_verified", "complete"},
        )
    try:
        atomic_write_json(scene_root / "stage.json", value)
        atomic_write_json(
            scene_root.parent / "supervisor.json",
            {**value, "activeShot": scene_root.name},
        )
    except (OSError, ValueError):
        pass  # Display/telemetry loss cannot abort accepted provider work.


def submit_once(
    client: ComfyUIClient,
    graph: dict[str, Any],
    receipt: Path,
    identity: str,
    *,
    binding: dict[str, Any] | None = None,
) -> str:
    digest = sha256_json(graph)
    if receipt.exists():
        previous = read(receipt)
        if previous["workflowDigest"] != digest or previous["identity"] != identity:
            raise ValueError(
                "Scene graph or identity changed; retain the previous attempt"
            )
        if previous.get("promptId"):
            return str(previous["promptId"])
        raise ValueError(
            "Submission is ambiguous; no automatic retry. Inspect the retained receipt and server log."
        )
    lifecycle = durable_module()
    if binding is None or binding.get("workflow_sha256") != digest:
        raise ValueError(
            "A complete bound attempt is required before provider submission"
        )
    ledger = lifecycle.Attempts(receipt.parent, lifecycle.CURRENT_OWNER)
    ledger.prepare(binding)
    ledger.begin_send()  # Commit sending BEFORE the one external POST.
    record = {
        "state": "submission-intent",
        "workflowDigest": digest,
        "identity": identity,
    }
    atomic_write_json(receipt, record)
    prompt_id = client.queue_prompt(graph, client_id=identity)
    ledger.accept(
        prompt_id
    )  # Canonical durable ID wins if compatibility receipt write fails.
    atomic_write_json(receipt, {**record, "state": "submitted", "promptId": prompt_id})
    publish_stage(receipt.parent, "submitted", "provider_queue", prompt_id=prompt_id)
    return prompt_id


def server_command(comfy_root: Path, work: Path) -> list[str]:
    return [
        str(comfy_root / ".venv/Scripts/python.exe"),
        "-B",
        str(comfy_root / "main.py"),
        "--listen",
        "127.0.0.1",
        "--port",
        "8189",
        "--cuda-device",
        "0",
        "--disable-auto-launch",
        "--disable-manager-ui",
        "--disable-api-nodes",
        "--disable-all-custom-nodes",
        "--whitelist-custom-nodes",
        "ComfyUI-GGUF",
        "--disable-metadata",
        "--preview-method",
        "none",
        "--cache-none",
        "--enable-dynamic-vram",
        "--async-offload",
        "2",
        "--reserve-vram",
        "1.5",
        "--input-directory",
        str(work / "input"),
        "--output-directory",
        str(work / "output"),
        "--temp-directory",
        str(work / "temp"),
        "--user-directory",
        str(work / "user"),
        "--database-url",
        f"sqlite:///{(work / 'user/comfyui.db').as_posix()}",
    ]


def resource_check(work: Path) -> dict[str, Any]:
    """Only invoked by the operator's Create command, never by Prepare."""
    result = run_process_bounded(
        [
            str(
                Path(os.environ.get("SystemRoot", "C:/Windows"))
                / "System32/nvidia-smi.exe"
            ),
            "--query-gpu=name,memory.total,memory.free",
            "--format=csv,noheader,nounits",
            "--id=0",
        ],
        timeout_seconds=15,
        stdout_limit=4096,
        stderr_limit=4096,
    )
    if result.returncode:
        raise ValueError(
            "Cannot read GPU memory. Stop other GPU work and check the installed driver."
        )
    name, total, free = result.stdout.decode().strip().split(",")

    class MemoryStatus(ctypes.Structure):
        _fields_ = [
            ("length", ctypes.c_ulong),
            ("load", ctypes.c_ulong),
            *[
                (key, ctypes.c_ulonglong)
                for key in (
                    "totalPhysical",
                    "availablePhysical",
                    "totalPage",
                    "availablePage",
                    "totalVirtual",
                    "availableVirtual",
                    "availableExtended",
                )
            ],
        ]

    status = MemoryStatus()
    status.length = ctypes.sizeof(status)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        raise ValueError("Could not inspect available RAM")
    evidence = {
        "gpu": name.strip(),
        "totalVramMiB": int(total),
        "freeVramMiB": int(free),
        "freeRamGiB": round(status.availablePhysical / 2**30, 2),
        "freeDiskGiB": round(shutil.disk_usage(work).free / 2**30, 2),
    }
    print(json.dumps(evidence), flush=True)
    if int(total) < 11000 or int(free) < 10000 or status.availablePhysical < 18 * 2**30:
        raise ValueError(
            "Local generation needs at least 10000 MiB free VRAM and 18 GiB free RAM. Close other GPU/model workloads yourself, then rerun. Nothing was submitted."
        )
    if evidence["freeDiskGiB"] < 80:
        raise ValueError(
            "Keep at least 80 GiB free on the output drive for retained lossless frames. Nothing was submitted."
        )
    return evidence


@contextmanager
def local_server(comfy_root: Path, work: Path) -> Iterator[ComfyUIClient]:
    # Never take over an existing process, even one with a healthy local API.
    with socket.socket() as probe:
        probe.settimeout(1)
        if probe.connect_ex(("127.0.0.1", 8189)) == 0:
            raise ValueError(
                "Port 8189 is already occupied. Close that server yourself before starting V2.1."
            )
    for name in ("input", "output", "temp", "user", "cache"):
        (work / name).mkdir(parents=True, exist_ok=True)
    environment = {
        **os.environ,
        "PYTHONDONTWRITEBYTECODE": "1",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "HF_HOME": str(work / "cache/huggingface"),
        "TORCH_HOME": str(work / "cache/torch"),
        "CUDA_CACHE_PATH": str(work / "cache/cuda"),
        "TRITON_CACHE_DIR": str(work / "cache/triton"),
        "TORCHINDUCTOR_CACHE_DIR": str(work / "cache/inductor"),
        "TEMP": str(work / "temp"),
        "TMP": str(work / "temp"),
    }
    command = server_command(comfy_root, work)
    log_path = work / f"server-{uuid4().hex[:8]}.log"
    client = OwnedClient(ENDPOINT, request_timeout_seconds=10, exclusive_control=True)
    process = subprocess.Popen(
        command,
        cwd=work,
        env=environment,
        shell=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    client.owner_pid = process.pid
    process_start = process_started_at(process.pid)
    client.provider_instance = {
        "pid": process.pid,
        "started_at": process_start.isoformat() if process_start else None,
        "endpoint": ENDPOINT,
        "run": work.parent.name,
    }

    def drain() -> None:
        assert process.stdout is not None
        drain_output(process.stdout, log_path)

    thread = threading.Thread(target=drain, daemon=True)
    try:
        thread.start()
        previous_server = work / "server-process.json"
        if previous_server.exists():
            atomic_write_json(
                work / f"server-process-previous-{uuid4().hex[:8]}.json",
                read(previous_server),
            )
        atomic_write_json(
            work / "server-process.json",
            {
                "pid": process.pid,
                "started_at": client.provider_instance["started_at"],
                "endpoint": ENDPOINT,
                "log": str(log_path),
                "command": command,
            },
        )
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise ValueError(
                    f"Local ComfyUI startup failed; inspect {log_path.name}"
                )
            try:
                client.health()
                break
            except ComfyUIProviderError:
                time.sleep(2)
        else:
            raise TimeoutError("Local ComfyUI startup exceeded four minutes")
        require_listener_owner(process.pid)
        yield client
    finally:
        # Popen identity binds shutdown to this launch; never terminate by port or name.
        if process.poll() is None:
            _kill_process_tree(process)
            process.wait(timeout=15)
        if thread.ident is not None:
            thread.join(timeout=5)
        if process.stdout:
            process.stdout.close()


def wait_history(
    client: ComfyUIClient, prompt_id: str, receipt: Path, *, timeout: int = 12 * 3600
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    next_message = 0.0
    while time.monotonic() < deadline:
        try:
            history = client.history(prompt_id)
        except ComfyUIProviderError:
            publish_stage(
                receipt.parent,
                "waiting_provider",
                "provider_reconnect",
                prompt_id=prompt_id,
            )
            time.sleep(5)
            continue
        record = history.get(prompt_id, {})
        status = record.get("status", {})
        if status.get("status_str") == "error":
            atomic_write_json(receipt.parent / "provider-error.json", history)
            publish_stage(
                receipt.parent, "failed", "operator_review", prompt_id=prompt_id
            )
            raise ValueError(
                "ComfyUI reported a scene error. Inspect provider-error.json and server log; no automatic reroll."
            )
        if status.get("completed"):
            atomic_write_json(receipt.parent / "completed-history.json", history)
            publish_stage(
                receipt.parent, "frames_complete", "frame_download", prompt_id=prompt_id
            )
            return history
        try:
            queue = client.queue_snapshot()
        except ComfyUIProviderError:
            publish_stage(
                receipt.parent,
                "waiting_provider",
                "provider_reconnect",
                prompt_id=prompt_id,
            )
            time.sleep(5)
            continue
        if not client._queue_contains(queue, prompt_id):
            # Completion can occur between the history and queue reads.
            history = client.history(prompt_id)
            terminal = history.get(prompt_id, {}).get("status", {})
            if terminal.get("status_str") == "error":
                atomic_write_json(receipt.parent / "provider-error.json", history)
                publish_stage(
                    receipt.parent, "failed", "operator_review", prompt_id=prompt_id
                )
                raise ValueError("ComfyUI reported a scene error; no automatic reroll.")
            if terminal.get("completed"):
                atomic_write_json(receipt.parent / "completed-history.json", history)
                publish_stage(
                    receipt.parent,
                    "frames_complete",
                    "frame_download",
                    prompt_id=prompt_id,
                )
                return history
            raise ValueError(
                "Saved prompt is absent from ComfyUI history and queue. Its outcome is unknown; no resubmission."
            )
        running = client._queue_contains(queue.get("queue_running"), prompt_id)
        stage = "generating" if running else "queued"
        waiting = "provider_history" if running else "provider_queue"
        publish_stage(receipt.parent, stage, waiting, prompt_id=prompt_id)
        if time.monotonic() >= next_message:
            report(
                f"{receipt.parent.name}: ComfyUI reports {stage}; waiting for {waiting}. GPU activity is not inferred from queue state.",
                flush=True,
            )
            next_message = time.monotonic() + 60
        time.sleep(5)
    raise TimeoutError(
        "Scene wait exceeded 12 hours; receipt preserved and no resubmission attempted"
    )


class RetainedOutputs:
    """Read completed ComfyUI frames from the owned run after its server stopped."""

    history_outputs = staticmethod(ComfyUIClient.history_outputs)

    def __init__(self, output_root: Path) -> None:
        self.output_root = output_root

    def retrieve_output(
        self,
        output: dict[str, str],
        *,
        attempt_root: Path,
        destination_name: str,
        maximum_bytes: int,
    ) -> tuple[Path, str]:
        source = self.output_root / output["subfolder"] / output["filename"]
        if output["type"] != "output" or not source.resolve().is_relative_to(
            self.output_root.resolve()
        ):
            raise ValueError("Retained provider output escapes this owned run")
        cursor = source
        while cursor != self.output_root.parent:
            if cursor.is_symlink() or cursor.is_junction():
                raise ValueError("Redirected retained provider output refused")
            cursor = cursor.parent
        if source.stat().st_size > maximum_bytes:
            raise ValueError("Retained provider output exceeds the image limit")
        digest = sha256_file(source)
        attempt_root.mkdir(parents=True, exist_ok=True)
        destination = attempt_root / destination_name
        if destination.exists():
            raise ValueError("Retained frame copy would overwrite an existing file")
        temporary = attempt_root / f"copy-{uuid4().hex}.partial"
        with source.open("rb") as reader, temporary.open("xb") as writer:
            shutil.copyfileobj(reader, writer, length=1024 * 1024)
            writer.flush()
            os.fsync(writer.fileno())
        if sha256_file(temporary) != digest or sha256_file(source) != digest:
            raise ValueError("Retained provider output changed while copying")
        os.replace(temporary, destination)
        return destination, digest


def recover_completed(
    scene_root: Path, server_output: Path, size: tuple[int, int]
) -> bool:
    history_path = scene_root / "completed-history.json"
    if not history_path.exists():
        return False
    receipt = read(scene_root / "submission.json")
    prompt_id = receipt["promptId"]
    history = read(history_path)
    status = history.get(prompt_id, {}).get("status", {})
    if not status.get("completed") or status.get("status_str") != "success":
        raise ValueError(
            "Saved history does not prove successful completion of this prompt"
        )
    collect_frames(RetainedOutputs(server_output), history, prompt_id, scene_root, size)  # type: ignore[arg-type]
    return True


def verified_frames(scene_root: Path, size: tuple[int, int]) -> list[Path] | None:
    marker = scene_root / "raw-verified.json"
    if not marker.exists():
        return None
    record = read(marker)
    expected = [f"frame_{n:08d}.png" for n in range(1, 130)]
    if [r["name"] for r in record["frames"]] != expected or record[
        "dimensions"
    ] != list(size):
        raise ValueError("Saved raw frame manifest is invalid")
    result = []
    for row in record["frames"]:
        path = scene_root / "raw" / row["name"]
        if sha256_file(path) != row["sha256"]:
            raise ValueError("A retained raw frame changed; refusing to overwrite it")
        result.append(path)
    return result


def _collect_frames_impl(
    client: ComfyUIClient,
    history: dict[str, Any],
    prompt_id: str,
    scene_root: Path,
    size: tuple[int, int],
    *,
    publish: bool = True,
) -> list[Path]:
    if publish:
        publish_stage(scene_root, "downloading", "frame_download", prompt_id=prompt_id)
    outputs = [
        row
        for row in client.history_outputs(history, prompt_id)
        if row["nodeId"] == "save_frames" and row["filename"].lower().endswith(".png")
    ]
    if (
        len(outputs) != 129
        or len({(r["subfolder"], r["filename"]) for r in outputs}) != 129
    ):
        raise ValueError("Expected exactly 129 distinct lossless Wan frames")
    # Preserve the provider's batch order (lexical filenames break at large counters).
    records = []
    for index, output in enumerate(outputs, 1):
        name = f"frame_{index:08d}.png"
        target = scene_root / "raw" / name
        if target.exists():
            # Resume partial retrieval against the exact persisted completed history.
            checksum = scene_root / "downloads" / f"{index:03d}.json"
            if (
                not checksum.exists()
                or sha256_file(target) != read(checksum)["sha256"]
                or read(checksum)["output"] != output
            ):
                raise ValueError(
                    "An unverified partial frame already exists; preserve it for inspection"
                )
            digest = read(checksum)["sha256"]
        else:
            target, digest = client.retrieve_output(
                output,
                attempt_root=scene_root / "raw",
                destination_name=name,
                maximum_bytes=20_000_000,
            )
            atomic_write_json(
                scene_root / "downloads" / f"{index:03d}.json",
                {"sha256": digest, "output": output},
            )
        with target.open("rb") as stream:
            header = stream.read(24)
        if (
            len(header) != 24
            or header[:8] != b"\x89PNG\r\n\x1a\n"
            or struct.unpack(">II", header[16:24]) != size
        ):
            raise ValueError("Wan frame dimensions differ from the selected profile")
        records.append({"name": name, "sha256": digest})
    atomic_write_json(
        scene_root / "raw-verified.json",
        {"promptId": prompt_id, "dimensions": size, "frames": records},
    )
    result = verified_frames(scene_root, size)
    assert result is not None
    if publish:
        publish_stage(scene_root, "raw_verified", prompt_id=prompt_id)
    return result


def collect_frames(
    client: ComfyUIClient,
    history: dict[str, Any],
    prompt_id: str,
    scene_root: Path,
    size: tuple[int, int],
) -> list[Path]:
    lifecycle = durable_module()
    owner = lifecycle.CURRENT_OWNER
    if owner is None:
        # Low-level fixture/legacy reader compatibility; production callers hold
        # ControllerLease and always take the verified atomic path below.
        return _collect_frames_impl(client, history, prompt_id, scene_root, size)
    if not owner.held or owner.run_id != scene_root.parent.name:
        raise ValueError("Exclusive ownership does not match frame retrieval")
    if (scene_root / "raw").exists():
        manifest = read(scene_root.parent.parents[1] / "prepared/manifest.json")
        lifecycle.verify_checkpoint(
            scene_root.parent,
            scene_root.name,
            manifest,
            source_history=history,
            write_proof=True,
        )
        verified = verified_frames(scene_root, size)
        assert verified is not None
        return verified
    stage = scene_root / "retrieval-attempts" / uuid4().hex[:12]
    stage.mkdir(parents=True)
    for name in ("submission.json", "submitted-workflow.json"):
        atomic_write_json(
            stage / name,
            lifecycle.effective_receipt(scene_root)
            if name == "submission.json"
            else read(scene_root / name),
        )
    atomic_write_json(stage / "completed-history.json", history)
    publish_stage(scene_root, "downloading", "frame_download", prompt_id=prompt_id)
    _collect_frames_impl(client, history, prompt_id, stage, size, publish=False)
    run_root = scene_root.parent
    manifest = read(run_root.parents[1] / "prepared/manifest.json")
    graph, reference, provider_input = lifecycle.scene_binding(
        run_root, scene_root.name, manifest
    )
    proof = lifecycle.verify_raw(
        stage, size, graph, reference, provider_input, history=history
    )
    atomic_write_json(stage / "verified-provenance.json", proof)
    # Receipts arrive before the atomic raw-directory promotion. A crash after
    # promotion and before the marker is recovered by full verification only.
    destination = scene_root / "downloads"
    if destination.exists():
        for source in (stage / "downloads").glob("*.json"):
            if not (destination / source.name).exists() or read(
                destination / source.name
            ) != read(source):
                raise ValueError(
                    "Existing download receipts conflict; preserve both attempts"
                )
    else:
        os.replace(stage / "downloads", destination)
    if (scene_root / "raw").exists():
        raise ValueError(
            "Raw destination appeared during promotion; nothing overwritten"
        )
    os.replace(stage / "raw", scene_root / "raw")
    atomic_write_json(
        scene_root / "raw-verified.json", read(stage / "raw-verified.json")
    )
    atomic_write_json(scene_root / "verified-provenance.json", proof)
    publish_stage(scene_root, "raw_verified", prompt_id=prompt_id)
    result = verified_frames(scene_root, size)
    assert result is not None
    return result
