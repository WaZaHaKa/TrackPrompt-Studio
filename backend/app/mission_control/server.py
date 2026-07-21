from __future__ import annotations

import argparse
import ipaddress
import json
import os
import sys
from collections.abc import MutableMapping
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import uvicorn
from fastapi import FastAPI
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException

from ..config import Settings
from ..main import create_app
from .models import RuntimeIdentity
from .processes import process_is_alive
from .router import install_mission_control


class SPAStaticFiles(StaticFiles):
    async def get_response(
        self,
        path: str,
        scope: MutableMapping[str, Any],
    ) -> Response:
        request_path = str(scope.get("path", ""))
        is_api_path = request_path.startswith("/api/") or path.lstrip("/").startswith(
            "api/"
        )
        try:
            response = await super().get_response(path, scope)
        except HTTPException as exc:
            if (
                exc.status_code != 404
                or scope.get("method") not in {"GET", "HEAD"}
                or is_api_path
            ):
                raise
        else:
            if (
                response.status_code != 404
                or scope.get("method") not in {"GET", "HEAD"}
                or is_api_path
            ):
                return response
        return await super().get_response("index.html", cast(Any, scope))


def _loopback_host(value: str) -> str:
    normalized = value.strip().lower()
    if normalized == "localhost":
        return "127.0.0.1"
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Mission Control host must be a loopback IP address.") from exc
    if not address.is_loopback:
        raise argparse.ArgumentTypeError("Mission Control refuses non-loopback bind addresses.")
    if address.version != 4:
        raise argparse.ArgumentTypeError("Mission Control currently supports IPv4 loopback only.")
    return str(address)


def create_server_app(
    *,
    repository_root: Path,
    static_dir: Path | None,
    runtime: RuntimeIdentity,
) -> FastAPI:
    settings = Settings.from_env()
    runtime_origin = f"http://{runtime.host}:{runtime.port}"
    settings = replace(
        settings,
        cors_origins=tuple(dict.fromkeys((*settings.cors_origins, runtime_origin))),
    )
    application = create_app(settings)
    application.state.mission_control_runtime = runtime
    install_mission_control(application)
    if static_dir is not None:
        resolved_static = static_dir.resolve(strict=True)
        if not resolved_static.is_dir() or not (resolved_static / "index.html").is_file():
            raise RuntimeError("Mission Control static directory must contain index.html.")
        application.mount(
            "/",
            SPAStaticFiles(directory=resolved_static, html=True),
            name="mission-control-ui",
        )
    _ = repository_root
    return application


def _pid_alive(pid: int) -> bool:
    return process_is_alive(pid)


class InstanceDescriptorLease:
    def __init__(self, path: Path, runtime: RuntimeIdentity) -> None:
        self.path = path.resolve()
        self.lock_path = self.path.with_name(f"{self.path.name}.lock")
        self.runtime = runtime
        self._lock_owned = False

    def _claim_lock(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(2):
            try:
                descriptor = os.open(
                    self.lock_path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                )
            except FileExistsError as exc:
                try:
                    existing = json.loads(self.lock_path.read_text(encoding="utf-8-sig"))
                except (OSError, UnicodeError, json.JSONDecodeError):
                    existing = {}
                existing_pid = existing.get("pid") if isinstance(existing, dict) else None
                existing_instance = (
                    existing.get("instanceId") if isinstance(existing, dict) else None
                )
                if (
                    isinstance(existing_pid, int)
                    and _pid_alive(existing_pid)
                    and existing_instance != self.runtime.instance_id
                ):
                    raise RuntimeError(
                        "A live Mission Control instance already owns the startup lease."
                    ) from exc
                if attempt == 0:
                    try:
                        self.lock_path.unlink()
                    except OSError as unlink_exc:
                        raise RuntimeError(
                            "The stale Mission Control startup lease could not be reclaimed."
                        ) from unlink_exc
                    continue
                raise RuntimeError(
                    "The Mission Control startup lease could not be claimed."
                ) from exc
            else:
                payload = {
                    "schemaVersion": "1.0.0",
                    "kind": "trackprompt-mission-control-instance-lock",
                    "instanceId": self.runtime.instance_id,
                    "pid": self.runtime.pid,
                }
                with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                    json.dump(payload, stream, sort_keys=True)
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                self._lock_owned = True
                return
        raise AssertionError("unreachable")

    def _release_lock(self) -> None:
        if not self._lock_owned or not self.lock_path.is_file():
            return
        try:
            payload = json.loads(self.lock_path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return
        if isinstance(payload, dict) and payload.get("instanceId") == self.runtime.instance_id:
            self.lock_path.unlink(missing_ok=True)
            self._lock_owned = False

    def claim(self) -> None:
        if self.path.is_file():
            try:
                existing = json.loads(self.path.read_text(encoding="utf-8-sig"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                existing = {}
            if isinstance(existing, dict):
                existing_pid = existing.get("pid")
                existing_instance = existing.get("instanceId")
                if (
                    isinstance(existing_pid, int)
                    and _pid_alive(existing_pid)
                    and existing_instance != self.runtime.instance_id
                ):
                    raise RuntimeError("A live Mission Control instance already owns the descriptor.")
        self._claim_lock()
        temporary = self.path.parent / f".{self.path.name}.{self.runtime.instance_id}.tmp"
        payload = {
            "schemaVersion": "1.0.0",
            "kind": "trackprompt-mission-control-instance",
            "instanceId": self.runtime.instance_id,
            "pid": self.runtime.pid,
            "host": self.runtime.host,
            "port": self.runtime.port,
            "url": f"http://{self.runtime.host}:{self.runtime.port}",
            "healthUrl": f"http://{self.runtime.host}:{self.runtime.port}/api/mission-control/health",
            "startedAt": self.runtime.started_at.isoformat(),
        }
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as stream:
                json.dump(payload, stream, indent=2, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        except BaseException:
            self._release_lock()
            raise
        finally:
            temporary.unlink(missing_ok=True)

    def release(self) -> None:
        if self.path.is_file():
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8-sig"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                payload = None
            if isinstance(payload, dict) and payload.get("instanceId") == self.runtime.instance_id:
                self.path.unlink(missing_ok=True)
        self._release_lock()


def _parser(repository_root: Path) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="WZHK Media Mission Control local server")
    parser.add_argument("--host", type=_loopback_host, default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument(
        "--static-dir",
        type=Path,
        default=repository_root / "frontend" / "dist",
    )
    parser.add_argument(
        "--instance-descriptor",
        type=Path,
        required=True,
    )
    parser.add_argument("--allow-fake-renderer", action="store_true")
    parser.add_argument("--disable-native-dialog", action="store_true")
    parser.add_argument("--log-level", choices=("critical", "error", "warning", "info"), default="warning")
    return parser


def main(argv: list[str] | None = None) -> int:
    repository_root = Path(__file__).resolve().parents[3]
    parser = _parser(repository_root)
    args = parser.parse_args(argv)
    port = int(args.port)
    if not 1 <= port <= 65_535:
        parser.error("--port must be between 1 and 65535")
    if args.allow_fake_renderer:
        os.environ["TRACKPROMPT_MC_ALLOW_FAKE_RENDERER"] = "1"
    if args.disable_native_dialog:
        os.environ["TRACKPROMPT_MC_DISABLE_NATIVE_DIALOG"] = "1"
    runtime = RuntimeIdentity(
        instance_id=str(uuid4()),
        pid=os.getpid(),
        host=str(args.host),
        port=port,
        started_at=datetime.now(UTC),
    )
    lease = InstanceDescriptorLease(Path(args.instance_descriptor), runtime)
    try:
        lease.claim()
        application = create_server_app(
            repository_root=repository_root,
            static_dir=Path(args.static_dir),
            runtime=runtime,
        )
        uvicorn.run(
            application,
            host=runtime.host,
            port=runtime.port,
            log_level=str(args.log_level),
            access_log=False,
        )
    except (OSError, RuntimeError) as exc:
        print(f"Mission Control failed to start: {exc}", file=sys.stderr)
        return 1
    finally:
        lease.release()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
