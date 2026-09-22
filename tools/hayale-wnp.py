"""Independent, read-only Hayale observer and authenticated loopback WNP API."""
from __future__ import annotations

import argparse
import base64
import ctypes
import importlib
import importlib.util
import json
import os
import re
import runpy
import secrets
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

REPO = Path(__file__).resolve().parents[1]


def stable_python() -> Path:
    if os.name != "nt":
        raise RuntimeError("Detached observer launch requires Windows")
    executable = Path(os.environ["LOCALAPPDATA"]) / "Programs/Python/Python312/python.exe"
    if not executable.is_file():
        raise RuntimeError("Existing stable Python 3.12 is required")
    return executable.resolve()


def _native_detached_python(arguments: list[str], stdout: Path, stderr: Path, *, cwd: Path) -> subprocess.Popen[bytes]:
    """One native breakaway attempt; never fall back to an inherited agent job.

    Only the private standard log handles are inherited. The CLI supplies fixed
    observer arguments; the same helper is exercised with a bounded test child.
    """
    executable = stable_python()
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0  # SW_HIDE; no interactive window.
    flags = subprocess.CREATE_BREAKAWAY_FROM_JOB | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    with stdout.open("xb") as output, stderr.open("xb") as errors:
        return subprocess.Popen(
            [str(executable), "-B", *arguments], cwd=cwd, stdin=subprocess.DEVNULL,
            stdout=output, stderr=errors, close_fds=True, creationflags=flags, startupinfo=startup,
        )


def process_in_job() -> bool:
    if os.name != "nt":
        raise RuntimeError("Windows process ownership is required")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
    kernel.IsProcessInJob.restype = wintypes.BOOL
    result = wintypes.BOOL()
    if not kernel.IsProcessInJob(kernel.GetCurrentProcess(), None, ctypes.byref(result)):
        raise OSError("Cannot verify detached process ownership")
    return bool(result.value)


class DetachedPython:
    def __init__(self, pid: int, started_at: str) -> None:
        self.pid = pid
        self.started_at = started_at


def _write_private_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_launch_ticket(path: Path) -> dict[str, Any]:
    if path.is_symlink() or path.is_junction() or path.parent.is_symlink() or path.parent.is_junction() or path.stat().st_size > 65536:
        raise ValueError("Invalid private launch ticket")
    ticket = json.loads(path.read_text(encoding="utf-8"))
    age = time.time() - ticket["created_at"]
    if ticket.get("version") != 1 or not -5 <= age <= 60 or not re.fullmatch(r"[a-f0-9]{64}", str(ticket.get("nonce", ""))):
        raise ValueError("Expired or invalid private launch ticket")
    args = ticket.get("arguments")
    if not isinstance(args, list) or not 1 <= len(args) <= 128 or not all(isinstance(arg, str) and "\0" not in arg for arg in args):
        raise ValueError("Invalid script launch arguments")
    script = Path(args[0])
    if not script.is_absolute() or script.suffix != ".py" or not script.is_file():
        raise ValueError("A local Python script is required")
    if Path(ticket["stdout"]).parent != path.parent or Path(ticket["stderr"]).parent != path.parent:
        raise ValueError("Private log location does not match the launch ticket")
    if not Path(ticket["cwd"]).is_absolute() or not Path(ticket["cwd"]).is_dir():
        raise ValueError("Invalid script working directory")
    return ticket  # type: ignore[no-any-return]


def _broker_entry(mode: str, path: Path) -> int:
    """Local-only process bootstrap; never exposed by the read-only HTTP API."""
    ready = path.with_suffix(".ready.json")
    try:
        ticket = _read_launch_ticket(path)
        if Path(sys.executable).resolve() != stable_python() or process_in_job():
            raise RuntimeError("Process still belongs to an inherited job")
        if mode == "--broker-guardian":
            # One guardian may claim the ticket. Ambiguous WMI submission is never retried.
            with path.with_suffix(".claimed").open("x", encoding="ascii") as claim:
                claim.write(str(os.getpid()))
            _native_detached_python(
                [str(Path(__file__).resolve()), "--broker-child", str(path)],
                Path(ticket["stdout"]), Path(ticket["stderr"]), cwd=Path(ticket["cwd"]),
            )
            return 0
        bootstrap()
        from app.mission_control.processes import process_started_at

        started = process_started_at(os.getpid())
        if started is None:
            raise RuntimeError("Detached child start time is unavailable")
        _write_private_json(ready, {"ok": True, "nonce": ticket["nonce"], "pid": os.getpid(),
                            "started_at": started.isoformat(), "in_job": False})
    except Exception:
        if not ready.exists():
            _write_private_json(ready, {"ok": False, "error": "detached_bootstrap_refused"})
        return 2
    # Identity/no-job proof precedes all target application imports and API loading.
    sys.argv = ticket["arguments"]
    sys.stdout.reconfigure(line_buffering=True)  # type: ignore[union-attr]
    sys.stderr.reconfigure(line_buffering=True)  # type: ignore[union-attr]
    runpy.run_path(sys.argv[0], run_name="__main__")
    return 0


def spawn_detached_python(arguments: list[str], stdout: Path, stderr: Path, *, cwd: Path) -> DetachedPython:
    """Launch via existing local WMI, then native breakaway, with explicit no-job proof.

    No service/task registration, remote WMI, elevation, or inherited-job fallback.
    A private single-use ticket preserves an uncertain broker launch for inspection.
    """
    executable = stable_python()
    stdout, stderr, cwd = stdout.resolve(), stderr.resolve(), cwd.resolve()
    if stdout.parent != stderr.parent or not stdout.parent.is_dir():
        raise ValueError("Existing private log directory is required")
    ticket_path = stdout.parent / f"launch-{uuid4().hex}.ticket.json"
    ticket = {"version": 1, "created_at": time.time(), "nonce": secrets.token_hex(32),
              "arguments": arguments, "cwd": str(cwd), "stdout": str(stdout), "stderr": str(stderr)}
    _write_private_json(ticket_path, ticket)
    _read_launch_ticket(ticket_path)
    command = subprocess.list2cmdline([str(executable), "-B", str(Path(__file__).resolve()), "--broker-guardian", str(ticket_path)])

    def literal(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    script = (
        "$ErrorActionPreference='Stop'; "
        "$startup=New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{ShowWindow=[uint16]0}; "
        "$result=Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine="
        + literal(command) + ";CurrentDirectory=" + literal(str(cwd)) + ";ProcessStartupInformation=$startup}; "
        "[pscustomobject]@{return_value=$result.ReturnValue;pid=$result.ProcessId}|ConvertTo-Json -Compress"
    )
    powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    broker_output = ticket_path.with_suffix(".broker.stdout")
    broker_error = ticket_path.with_suffix(".broker.stderr")
    with broker_output.open("xb") as output, broker_error.open("xb") as errors:
        broker = subprocess.run([str(powershell), "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                                cwd=cwd, stdin=subprocess.DEVNULL, stdout=output, stderr=errors,
                                timeout=15, check=False, creationflags=subprocess.CREATE_NO_WINDOW)
    if broker.returncode or broker_output.stat().st_size > 4096:
        raise RuntimeError("Local broker launch is unconfirmed; inspect the retained ticket")
    receipt = json.loads(broker_output.read_text(encoding="utf-8-sig"))
    if receipt.get("return_value") != 0:
        raise RuntimeError("Local broker refused the launch")
    ready = ticket_path.with_suffix(".ready.json")
    deadline = time.monotonic() + 15
    while not ready.exists() and time.monotonic() < deadline:
        time.sleep(.05)
    if not ready.exists():
        raise RuntimeError("Detached child readiness is unconfirmed; inspect the retained ticket")
    proof = json.loads(ready.read_text(encoding="utf-8"))
    if proof.get("ok") is not True or proof.get("in_job") is not False or proof.get("nonce") != ticket["nonce"]:
        raise RuntimeError("Detached child did not prove independence")
    return DetachedPython(int(proof["pid"]), str(proof["started_at"]))


def launch_observer(run_id: str, interval: float, port: int) -> dict[str, Any]:
    from app.mission_control.processes import process_is_alive, process_started_at
    from app.video_generation.jsonio import atomic_write_json

    if Path(sys.executable).resolve() != stable_python():
        raise RuntimeError("Launch mode must run on the stable Python 3.12 installation")
    runtime = REPO / ".trackprompt-data/hayale-v21/wnp-observer"
    runtime.mkdir(parents=True, exist_ok=True)
    worker = runtime / "worker.json"
    if worker.is_file():
        prior = json.loads(worker.read_text(encoding="utf-8"))
        current = process_started_at(prior.get("pid"))
        expected = datetime.fromisoformat(prior["started_at"].replace("Z", "+00:00"))
        if process_is_alive(prior.get("pid")) and current is not None and abs((current - expected).total_seconds()) < .01:
            return {"ok": False, "error": "observer_already_running", "pid": prior["pid"], "run": prior.get("run")}
    label = datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:12]
    receipt = runtime / f"{label}.launch.json"
    intent: dict[str, Any] = {"run": run_id, "role": "observation-only", "status": "launch-intent",
                              "creation_flags": "BREAKAWAY_FROM_JOB|NEW_PROCESS_GROUP|DETACHED_PROCESS"}
    atomic_write_json(receipt, intent)
    try:
        child = spawn_detached_python(
            [str(Path(__file__).resolve()), "--serve-forever", "--run-id", run_id,
             "--interval", str(interval), "--port", str(port)],
            runtime / f"{label}.stdout.log", runtime / f"{label}.stderr.log", cwd=REPO,
        )
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        outcome = {**intent, "ok": False, "status": "launch-unconfirmed", "error": "observer_detachment_unconfirmed"}
        atomic_write_json(receipt, outcome)
        return outcome
    started = process_started_at(child.pid)
    outcome = {**intent, "ok": started is not None, "pid": child.pid,
               "started_at": started.isoformat() if started else None,
               "status": "launched-not-yet-verified" if started else "launch-identity-unconfirmed"}
    atomic_write_json(receipt, outcome)
    # Closing this local Popen handle does not terminate the detached process.
    return outcome


def bootstrap() -> None:
    """Use already installed Python 3.12 libraries; never download dependencies."""
    for directory in (
        REPO / "backend/.venv/Lib/site-packages",
        Path("D:/TrackPrompt-ComfyUI/.venv/Lib/site-packages"),
    ):
        if directory.is_dir() and str(directory) not in sys.path:
            sys.path.append(str(directory))
    if str(REPO / "backend") not in sys.path:
        sys.path.insert(0, str(REPO / "backend"))


def load_reader() -> Any:
    key = "hayale_v21_observation"
    if key not in sys.modules:
        path = REPO / "video-projects/hayale-flowers-promo/v2.1/observation.py"
        spec = importlib.util.spec_from_file_location(key, path)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[key] = module
        spec.loader.exec_module(module)
    return sys.modules[key]


def poll_once(reader: Any, projection: Any) -> dict[str, Any]:
    try:
        source_id, observation = reader.read()
    except Exception:
        return {"ok": False, "error": "reader_unavailable"}
    result = projection.publish(source_id, observation)
    return {"ok": result.ok, "pending": result.pending, "error": result.error, "sequence": result.sequence}


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    modes = result.add_mutually_exclusive_group()
    modes.add_argument("--check-only", action="store_true", help="Check existing runtime and imports without network or publication")
    modes.add_argument("--once", action="store_true", help="Read and publish one observation; do not start an API")
    modes.add_argument("--serve-forever", action="store_true", help="Run an independent observer and authenticated loopback API")
    modes.add_argument("--launch", action="store_true", help="Explicitly start a hidden Windows breakaway observer using stable Python")
    result.add_argument("--run-id", default="344ab3d2444c")
    result.add_argument("--interval", type=float, default=10)
    result.add_argument("--port", type=int, default=9761)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    bootstrap()
    try:
        if not re.fullmatch(r"[a-f0-9]{12}", args.run_id):
            raise ValueError("Invalid existing Hayale run identity")
        if not 2 <= args.interval <= 300 or not 1024 <= args.port <= 65535:
            raise ValueError("Invalid bounded observer configuration")
        from app.local_video.wnp_projection import WNPProjection, load_wnp_kit
        from app.mission_control.processes import process_started_at
        from app.video_generation.jsonio import atomic_write_json

        reader_module = load_reader()
        run_root = REPO / ".trackprompt-data/hayale-v21/runs" / args.run_id
        manifest = REPO / ".trackprompt-data/hayale-v21/prepared/manifest.json"
        reader = reader_module.HayaleObservationReader(run_root, manifest, cache_path=REPO / ".wnp/hayale-reader-cache.json")
        store_module, _ = load_wnp_kit()
        store = store_module.Store(REPO)
        # Local checks only; no implicit node creation, token generation, or provider request.
        store.node()
        reader_module.read(run_root / "run.json")
        reader_module.read(manifest)
        importlib.import_module("PIL.Image")
        if not callable(getattr(reader_module.sibling("durable"), "verify_checkpoint", None)):
            raise ValueError("Verified checkpoint reader unavailable")
        if args.launch:
            result = launch_observer(args.run_id, args.interval, args.port)
            print(json.dumps(result))
            return 0 if result["ok"] else 2
        if not args.once and not args.serve_forever:
            print(json.dumps({"ok": True, "mode": "check-only", "run": args.run_id,
                              "python": sys.version.split()[0], "provider_requests": 0}))
            return 0
        with WNPProjection(REPO) as projection:
            if args.once:
                result = poll_once(reader, projection)
                print(json.dumps(result))
                return 0 if result["ok"] else 2
            token_path = store.runtime / "auth/read.token"
            if token_path.stat().st_size > 256:
                raise ValueError("Invalid local reader credential")
            token = token_path.read_text(encoding="utf-8").strip()
            server = importlib.import_module("wnp_host.api").make_server(store, token, host="127.0.0.1", port=args.port)
            started = process_started_at(os.getpid())
            if started is None:
                server.server_close()
                raise ValueError("Observer process start identity unavailable")
            supervisor = REPO / ".trackprompt-data/hayale-v21/wnp-observer"
            atomic_write_json(supervisor / "worker.json", {"pid": os.getpid(), "started_at": started.isoformat(),
                              "run": args.run_id, "role": "observation-only", "port": args.port})
            service = threading.Thread(target=server.serve_forever, name="hayale-wnp-read-api", daemon=True)
            service.start()
            stop = threading.Event()
            last_summary = None
            try:
                while not stop.is_set():
                    result = poll_once(reader, projection)
                    summary = json.dumps(result, sort_keys=True)
                    if summary != last_summary:
                        print(summary, flush=True)
                        last_summary = summary
                    stop.wait(args.interval)
            except KeyboardInterrupt:
                pass
            finally:
                server.shutdown()
                server.server_close()
                service.join(timeout=5)
        return 0
    except Exception:
        print(json.dumps({"ok": False, "error": "observer_startup_unavailable"}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] in {"--broker-guardian", "--broker-child"}:
        raise SystemExit(_broker_entry(sys.argv[1], Path(sys.argv[2])))
    raise SystemExit(main())
