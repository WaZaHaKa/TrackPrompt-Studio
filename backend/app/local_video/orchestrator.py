from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4


class LocalVideoRunError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class UnitResult:
    output_sha256: str
    qc_passed: bool = True
    detail: str = "complete"


class StageExecutor(Protocol):
    async def execute(self, stage: str, unit_id: str) -> UnitResult: ...


EventCallback = Callable[[dict[str, Any]], Awaitable[None] | None]

STAGES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("references", ("architect", "listener", "origami-bird")),
    ("keyframes", tuple(f"shot-{index:03d}" for index in range(1, 17))),
    ("video", tuple(f"shot-{index:03d}" for index in range(1, 17))),
    ("post", tuple(f"shot-{index:03d}" for index in range(1, 17))),
    ("edit", ("timeline",)),
    ("qc", ("final-delivery",)),
)


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.partial-{uuid4().hex}")
    try:
        temporary.write_text(
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ),
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _manifest_identity(project_id: str, revision_id: str, package_digest: str) -> str:
    return hashlib.sha256(f"{project_id}\n{revision_id}\n{package_digest}".encode()).hexdigest()


class ResumableLocalVideoRun:
    """One-at-a-time, manifest-first execution that preserves successful units."""

    def __init__(
        self,
        manifest_path: Path,
        *,
        project_id: str,
        revision_id: str,
        package_digest: str,
        alternate_shot_ids: frozenset[str],
        event_callback: EventCallback | None = None,
    ) -> None:
        self.manifest_path = manifest_path.resolve()
        self.project_id = project_id
        self.revision_id = revision_id
        self.package_digest = package_digest
        self.alternate_shot_ids = alternate_shot_ids
        self.event_callback = event_callback
        self.cancel_requested = False

    def _new_manifest(self) -> dict[str, Any]:
        return {
            "schemaVersion": "1.0.0",
            "kind": "trackprompt-local-video-resumable-run",
            "identity": _manifest_identity(
                self.project_id,
                self.revision_id,
                self.package_digest,
            ),
            "projectId": self.project_id,
            "revisionId": self.revision_id,
            "packageDigest": self.package_digest,
            "status": "planned",
            "stage": "references",
            "createdAt": datetime.now(UTC).isoformat(),
            "updatedAt": datetime.now(UTC).isoformat(),
            "finalQcPassed": False,
            "units": {
                stage: {
                    unit_id: {
                        "status": "planned",
                        "attempt": 0,
                        "alternate": False,
                        "outputSha256": None,
                        "detail": None,
                    }
                    for unit_id in units
                }
                for stage, units in STAGES
            },
        }

    def load(self) -> dict[str, Any]:
        if not self.manifest_path.is_file():
            return self._new_manifest()
        value = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise LocalVideoRunError("The resumable run manifest is invalid")
        expected = _manifest_identity(self.project_id, self.revision_id, self.package_digest)
        if value.get("identity") != expected:
            raise LocalVideoRunError("The resumable run identity does not match this project revision")
        return value

    def request_cancel(self) -> None:
        self.cancel_requested = True

    async def _event(self, manifest: dict[str, Any], stage: str, unit_id: str, message: str) -> None:
        if self.event_callback is None:
            return
        units = manifest["units"]
        completed = sum(
            1
            for stage_units in units.values()
            for unit in stage_units.values()
            if unit["status"] == "complete"
        )
        total = sum(len(stage_units) for stage_units in units.values())
        value = self.event_callback(
            {
                "stage": stage,
                "shotId": unit_id if unit_id.startswith("shot-") else None,
                "completedUnits": completed,
                "totalUnits": total,
                "statusMessage": message,
            }
        )
        if value is not None:
            await value

    def retry_shot(self, shot_id: str, *, alternate: bool = False) -> None:
        if not re_full_shot_id(shot_id):
            raise LocalVideoRunError("The retry shot identity is invalid")
        if alternate and shot_id not in self.alternate_shot_ids:
            raise LocalVideoRunError("An alternate is not allowed for this shot")
        manifest = self.load()
        unit = manifest["units"]["video"][shot_id]
        if unit["status"] not in {"failed", "cancelled"}:
            raise LocalVideoRunError("Only a failed or cancelled shot can be retried")
        if int(unit["attempt"]) >= 2:
            raise LocalVideoRunError("The bounded retry limit has been reached")
        unit.update({"status": "planned", "alternate": alternate, "detail": None})
        manifest.update({"status": "resumable", "stage": "video", "updatedAt": datetime.now(UTC).isoformat()})
        _atomic_json(self.manifest_path, manifest)

    async def run(self, executor: StageExecutor) -> dict[str, Any]:
        manifest = self.load()
        if manifest.get("status") == "complete" and manifest.get("finalQcPassed") is True:
            return manifest
        manifest["status"] = "running"
        _atomic_json(self.manifest_path, manifest)
        for stage, ordered_units in STAGES:
            manifest["stage"] = stage
            for unit_id in ordered_units:
                unit = manifest["units"][stage][unit_id]
                if unit["status"] == "complete" and unit.get("outputSha256"):
                    continue
                if self.cancel_requested:
                    unit["status"] = "cancelled"
                    manifest.update(
                        {
                            "status": "cancelled",
                            "updatedAt": datetime.now(UTC).isoformat(),
                        }
                    )
                    _atomic_json(self.manifest_path, manifest)
                    await self._event(manifest, stage, unit_id, "Cancellation preserved completed units")
                    return manifest
                attempt = int(unit.get("attempt", 0)) + 1
                if attempt > 2:
                    manifest["status"] = "failed"
                    _atomic_json(self.manifest_path, manifest)
                    raise LocalVideoRunError("The bounded retry limit has been reached")
                unit.update({"status": "running", "attempt": attempt})
                manifest["updatedAt"] = datetime.now(UTC).isoformat()
                _atomic_json(self.manifest_path, manifest)
                await self._event(manifest, stage, unit_id, "Unit started")
                try:
                    result = await executor.execute(stage, unit_id)
                except Exception as exc:
                    unit.update({"status": "failed", "detail": type(exc).__name__})
                    manifest.update(
                        {"status": "failed", "updatedAt": datetime.now(UTC).isoformat()}
                    )
                    _atomic_json(self.manifest_path, manifest)
                    await self._event(manifest, stage, unit_id, "Unit failed safely")
                    return manifest
                if not re_full_sha256(result.output_sha256):
                    unit.update({"status": "failed", "detail": "output_hash_invalid"})
                    manifest["status"] = "failed"
                    _atomic_json(self.manifest_path, manifest)
                    return manifest
                if stage == "qc" and not result.qc_passed:
                    unit.update(
                        {
                            "status": "failed",
                            "outputSha256": result.output_sha256,
                            "detail": result.detail,
                        }
                    )
                    manifest.update(
                        {
                            "status": "failed",
                            "finalQcPassed": False,
                            "updatedAt": datetime.now(UTC).isoformat(),
                        }
                    )
                    _atomic_json(self.manifest_path, manifest)
                    return manifest
                unit.update(
                    {
                        "status": "complete",
                        "outputSha256": result.output_sha256,
                        "detail": result.detail,
                    }
                )
                manifest["updatedAt"] = datetime.now(UTC).isoformat()
                _atomic_json(self.manifest_path, manifest)
                await self._event(manifest, stage, unit_id, "Unit completed and hash-bound")
        manifest.update(
            {
                "status": "complete",
                "stage": "complete",
                "finalQcPassed": True,
                "updatedAt": datetime.now(UTC).isoformat(),
            }
        )
        _atomic_json(self.manifest_path, manifest)
        return manifest


def re_full_shot_id(value: str) -> bool:
    return len(value) == 8 and value.startswith("shot-") and value[5:].isdigit()


def re_full_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)
