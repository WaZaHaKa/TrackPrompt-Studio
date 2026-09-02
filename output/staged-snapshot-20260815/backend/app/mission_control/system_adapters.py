from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

from .config import MissionControlConfig
from .discovery import atomic_write_json, load_json_object
from .errors import MissionControlError
from .models import NativePickerResponse, PerformanceStatus
from .processes import process_is_alive


def _powershell() -> str | None:
    return shutil.which("powershell.exe") or shutil.which("pwsh")


def _last_json_object(text: str) -> dict[str, Any] | None:
    for line in reversed(text.splitlines()):
        try:
            value = json.loads(line.strip())
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return cast(dict[str, Any], value)
    return None


class NativePicker:
    def __init__(self, config: MissionControlConfig) -> None:
        self.config = config

    async def choose(
        self,
        kind: Literal["folder", "file"],
        *,
        initial_directory: str | None,
        title: str,
    ) -> NativePickerResponse:
        override = os.getenv("TRACKPROMPT_MC_PICKER_RESULT")
        if override is not None:
            if not override.strip():
                return NativePickerResponse(cancelled=True)
            selected = Path(override).expanduser()
            if not selected.is_absolute():
                raise MissionControlError(
                    500,
                    "picker_override_invalid",
                    "Test picker override is invalid",
                    "TRACKPROMPT_MC_PICKER_RESULT must contain an absolute path.",
                    "Set the test picker override to an absolute fixture path.",
                )
            resolved = selected.resolve(strict=True)
            if (kind == "folder" and not resolved.is_dir()) or (kind == "file" and not resolved.is_file()):
                raise MissionControlError(
                    500,
                    "picker_override_type_mismatch",
                    "Test picker override is invalid",
                    "The picker override does not match the requested selection type.",
                    "Use a directory for folder selection or a file for file selection.",
                )
            return NativePickerResponse(cancelled=False, path=str(resolved))
        if not self.config.native_dialog_enabled:
            raise MissionControlError(
                409,
                "native_dialog_disabled",
                "Native picker is disabled",
                "This Mission Control instance was started with native dialogs disabled.",
                "Use the configured test picker override or restart with native dialogs enabled.",
            )
        powershell = _powershell()
        if powershell is None or os.name != "nt":
            raise MissionControlError(
                409,
                "native_dialog_unavailable",
                "Native picker is unavailable",
                "The Windows native folder picker requires Windows PowerShell.",
                "Select the folder on a Windows Mission Control host.",
            )
        bridge = Path(__file__).with_name("native_picker_bridge.ps1")
        arguments = [
            powershell,
            "-NoLogo",
            "-NoProfile",
            "-STA",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(bridge),
            "-Kind",
            "Folder" if kind == "folder" else "File",
            "-Title",
            title,
        ]
        if initial_directory:
            initial = Path(initial_directory).expanduser()
            if not initial.is_absolute() or not initial.is_dir():
                raise MissionControlError(
                    422,
                    "invalid_picker_initial_directory",
                    "Initial folder is invalid",
                    "The native picker initial directory must be an existing absolute folder.",
                    "Choose an existing local folder and retry.",
                )
            arguments.extend(("-InitialDirectory", str(initial.resolve())))
        result = await asyncio.to_thread(
            subprocess.run,
            arguments,
            capture_output=True,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
            shell=False,
            creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
        )
        payload = _last_json_object(result.stdout)
        if result.returncode != 0 or payload is None:
            raise MissionControlError(
                409,
                "native_dialog_failed",
                "Native picker could not open",
                "Windows did not complete the native picker operation.",
                "Retry the picker or inspect advanced diagnostics.",
                retryable=True,
                technical_details=(result.stderr or result.stdout)[-2_000:],
            )
        return NativePickerResponse.model_validate(payload)


class PerformanceAdapter:
    def __init__(self, config: MissionControlConfig) -> None:
        self.config = config
        self.state_path = config.state_root / "exclusive-performance-state.json"
        self.helper_path = config.state_root / "exclusive-performance-helper.json"
        self.control_path = config.state_root / "exclusive-performance-control.json"
        self._helper_process: asyncio.subprocess.Process | None = None
        self._operation_lock = asyncio.Lock()

    async def status(self) -> PerformanceStatus:
        powershell = _powershell()
        if powershell is None or os.name != "nt":
            return PerformanceStatus(
                available=False,
                active=False,
                restore_required=False,
                detail="Exclusive Performance Mode requires Windows PowerShell.",
            )
        status = await self._invoke("Status")
        helper_alive = self._helper_alive()
        if not helper_alive and not status.restore_required:
            self.helper_path.unlink(missing_ok=True)
            self.control_path.unlink(missing_ok=True)
            if self._helper_process is not None and self._helper_process.returncode is not None:
                self._helper_process = None
        if status.restore_required and not helper_alive:
            return status.model_copy(
                update={
                    "active": False,
                    "sleep_inhibited": False,
                    "detail": "Performance settings require restoration, but the sleep-inhibition helper is no longer active.",
                }
            )
        return status.model_copy(update={"active": status.restore_required and helper_alive})

    async def enable(
        self,
        *,
        operator_confirmed: bool,
        use_high_performance_power_plan: bool,
        blender_process_id: int,
    ) -> PerformanceStatus:
        async with self._operation_lock:
            return await self._enable_locked(
                operator_confirmed=operator_confirmed,
                use_high_performance_power_plan=use_high_performance_power_plan,
                blender_process_id=blender_process_id,
            )

    async def _enable_locked(
        self,
        *,
        operator_confirmed: bool,
        use_high_performance_power_plan: bool,
        blender_process_id: int,
    ) -> PerformanceStatus:
        if not operator_confirmed:
            raise MissionControlError(
                422,
                "performance_confirmation_required",
                "Confirmation required",
                "Maximize local render performance changes the power plan and inhibits sleep.",
                "Review the changes and confirm before enabling performance mode.",
            )
        current = await self.status()
        if current.restore_required:
            raise MissionControlError(
                409,
                "performance_restore_required",
                "Performance mode must be restored first",
                "A prior performance-mode state still requires restoration.",
                "Restore the prior session before enabling performance mode again.",
            )
        powershell = _powershell()
        if powershell is None or os.name != "nt":
            raise MissionControlError(
                409,
                "performance_mode_unavailable",
                "Performance mode is unavailable",
                "Exclusive Performance Mode requires Windows PowerShell.",
                "Use the Windows Mission Control host.",
            )
        bridge = Path(__file__).with_name("performance_daemon.ps1")
        module = self.config.repository_root / "tools" / "wzhk-launcher" / "WZHK.Performance.psm1"
        self.control_path.unlink(missing_ok=True)
        arguments = [
            powershell,
            "-NoLogo",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(bridge),
            "-ModulePath",
            str(module),
            "-StatePath",
            str(self.state_path),
            "-ControlPath",
            str(self.control_path),
            "-BlenderProcessId",
            str(blender_process_id),
        ]
        if use_high_performance_power_plan:
            arguments.append("-UseHighPerformancePowerPlan")
        try:
            process = await asyncio.create_subprocess_exec(
                *arguments,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
                | int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
            )
            if process.stdout is None:
                raise RuntimeError("helper stdout unavailable")
            ready_line = await asyncio.wait_for(process.stdout.readline(), timeout=45.0)
        except (OSError, RuntimeError, TimeoutError) as exc:
            if "process" in locals() and process.returncode is None:
                await self._rollback_helper_start(process)
            raise MissionControlError(
                409,
                "performance_mode_failed",
                "Performance mode could not be enabled",
                "Windows did not start the dedicated sleep-inhibition helper.",
                "Review AC power and restoration status, then retry.",
                retryable=True,
                technical_details=type(exc).__name__,
            ) from exc
        payload = _last_json_object(ready_line.decode("utf-8", errors="replace"))
        if payload is None or payload.get("ready") is not True:
            stderr = b""
            if process.stderr is not None:
                try:
                    stderr = await asyncio.wait_for(process.stderr.read(16_384), timeout=2.0)
                except TimeoutError:
                    pass
            await self._rollback_helper_start(process)
            raise MissionControlError(
                409,
                "performance_mode_failed",
                "Performance mode could not be enabled",
                "The dedicated performance helper did not confirm a reversible active state.",
                "Review AC power and restoration status, then retry.",
                retryable=True,
                technical_details=stderr.decode("utf-8", errors="replace")[-2_000:],
            )
        self._helper_process = process
        try:
            atomic_write_json(
                self.helper_path,
                {
                    "schemaVersion": "1.0.0",
                    "kind": "trackprompt-performance-helper",
                    "processId": process.pid,
                    "startedAt": datetime.now(UTC).isoformat(),
                    "statePath": str(self.state_path),
                    "controlPath": str(self.control_path),
                },
            )
        except Exception:
            await self._rollback_helper_start(process)
            self._helper_process = None
            raise
        return await self.status()

    async def restore(self, *, operator_confirmed: bool) -> PerformanceStatus:
        async with self._operation_lock:
            return await self._restore_locked(operator_confirmed=operator_confirmed)

    async def _restore_locked(self, *, operator_confirmed: bool) -> PerformanceStatus:
        if not operator_confirmed:
            raise MissionControlError(
                422,
                "performance_restore_confirmation_required",
                "Confirmation required",
                "Restoring performance mode changes the active Windows power plan.",
                "Confirm restoration, then try again.",
            )
        status = await self.status()
        if not status.restore_required:
            return status
        if self._helper_alive():
            atomic_write_json(
                self.control_path,
                {
                    "schemaVersion": "1.0.0",
                    "kind": "trackprompt-performance-control",
                    "action": "restore",
                    "requestedAt": datetime.now(UTC).isoformat(),
                },
            )
            for _attempt in range(120):
                await asyncio.sleep(0.25)
                updated = await self._invoke("Status")
                if not updated.restore_required:
                    self.helper_path.unlink(missing_ok=True)
                    self.control_path.unlink(missing_ok=True)
                    if self._helper_process is not None:
                        try:
                            await asyncio.wait_for(self._helper_process.wait(), timeout=2.0)
                        except TimeoutError:
                            pass
                        self._helper_process = None
                    return updated
            raise MissionControlError(
                504,
                "performance_restore_timeout",
                "Performance mode restoration timed out",
                "The dedicated helper did not confirm restoration in time.",
                "Keep Mission Control open and retry restoration before rendering.",
                retryable=True,
            )
        restored = await self._invoke("Restore", operator_confirmed=True)
        self.helper_path.unlink(missing_ok=True)
        self.control_path.unlink(missing_ok=True)
        return restored

    async def _rollback_helper_start(
        self,
        process: asyncio.subprocess.Process,
    ) -> None:
        try:
            atomic_write_json(
                self.control_path,
                {
                    "schemaVersion": "1.0.0",
                    "kind": "trackprompt-performance-control",
                    "action": "restore",
                    "requestedAt": datetime.now(UTC).isoformat(),
                },
            )
        except MissionControlError:
            try:
                await self._invoke("Restore", operator_confirmed=True)
            except MissionControlError:
                pass
        if process.returncode is None:
            try:
                await asyncio.wait_for(process.wait(), timeout=15.0)
            except TimeoutError:
                try:
                    await self._invoke("Restore", operator_confirmed=True)
                finally:
                    if process.returncode is None:
                        process.kill()
                        await process.wait()
        self.control_path.unlink(missing_ok=True)

    def _helper_alive(self) -> bool:
        if not self.helper_path.is_file():
            return False
        try:
            payload = load_json_object(self.helper_path, "Performance helper descriptor")
            pid = int(payload.get("processId", 0))
        except (MissionControlError, TypeError, ValueError):
            return False
        return process_is_alive(pid)

    async def _invoke(
        self,
        action: Literal["Status", "Enable", "Restore"],
        *,
        operator_confirmed: bool = False,
        use_high_performance_power_plan: bool = False,
        blender_process_id: int = 0,
    ) -> PerformanceStatus:
        powershell = _powershell()
        if powershell is None or os.name != "nt":
            raise MissionControlError(
                409,
                "performance_mode_unavailable",
                "Performance mode is unavailable",
                "Exclusive Performance Mode requires Windows PowerShell.",
                "Use the Windows Mission Control host.",
            )
        bridge = Path(__file__).with_name("performance_bridge.ps1")
        module = self.config.repository_root / "tools" / "wzhk-launcher" / "WZHK.Performance.psm1"
        arguments = [
            powershell,
            "-NoLogo",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(bridge),
            "-Action",
            action,
            "-ModulePath",
            str(module),
            "-StatePath",
            str(self.state_path),
            "-BlenderProcessId",
            str(blender_process_id),
        ]
        if operator_confirmed:
            arguments.append("-OperatorConfirmed")
        if use_high_performance_power_plan:
            arguments.append("-UseHighPerformancePowerPlan")
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                arguments,
                capture_output=True,
                check=False,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
                shell=False,
                creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
            )
        except subprocess.TimeoutExpired as exc:
            raise MissionControlError(
                504,
                "performance_mode_timeout",
                "Performance mode timed out",
                "Windows did not complete the performance-mode operation in time.",
                "Inspect the current power plan before retrying.",
                retryable=True,
            ) from exc
        payload = _last_json_object(result.stdout)
        if result.returncode != 0 or payload is None:
            summary = (result.stderr or result.stdout).strip()[-2_000:]
            raise MissionControlError(
                409,
                "performance_mode_failed",
                "Performance mode could not be changed",
                "Windows rejected or could not complete the requested performance-mode operation.",
                "Review AC power and restoration status, then retry.",
                retryable=True,
                technical_details=summary,
            )
        normalized = {
            key: (None if value == "" and key not in {"detail"} else value)
            for key, value in payload.items()
        }
        if normalized.get("blenderProcessId") == 0:
            normalized["blenderProcessId"] = None
        return PerformanceStatus.model_validate(normalized)
