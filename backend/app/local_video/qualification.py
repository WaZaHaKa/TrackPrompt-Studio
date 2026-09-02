from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from .models import LocalVideoError, QualificationCandidate, QualificationView


@dataclass(frozen=True, slots=True)
class HardwareIdentity:
    gpu_name: str
    vram_bytes: int
    driver_version: str
    comfyui_revision: str
    custom_node_revisions: dict[str, str]
    model_sha256: dict[str, str]

    def cache_key(self) -> str:
        payload = {
            "gpuName": self.gpu_name,
            "vramBytes": self.vram_bytes,
            "driverVersion": self.driver_version,
            "comfyuiRevision": self.comfyui_revision,
            "customNodeRevisions": dict(sorted(self.custom_node_revisions.items())),
            "modelSha256": dict(sorted(self.model_sha256.items())),
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(
            "utf-8"
        )
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class QualificationProbe:
    tier: str
    width: int
    height: int
    length_frames: int
    steps: int
    timeout_seconds: float


QUALIFICATION_PROBES = (
    QualificationProbe("A14B-Q5_K_M", 512, 288, 33, 4, 45 * 60),
    QualificationProbe("A14B-Q4_K_M", 512, 288, 33, 4, 45 * 60),
    QualificationProbe("TI2V-5B", 512, 288, 33, 4, 30 * 60),
)


@dataclass(frozen=True, slots=True)
class QualificationSample:
    valid_output: bool
    elapsed_seconds: float
    peak_vram_bytes: int | None = None
    peak_system_memory_bytes: int | None = None
    cuda_oom: bool = False
    process_crashed: bool = False
    stalled: bool = False
    memory_pressure: bool = False
    timed_out: bool = False
    failure_code: str | None = None

    @property
    def passed(self) -> bool:
        return self.valid_output and not any(
            (
                self.cuda_oom,
                self.process_crashed,
                self.stalled,
                self.memory_pressure,
                self.timed_out,
            )
        )

    @property
    def reason(self) -> str | None:
        if self.passed:
            return None
        if self.cuda_oom:
            return "cuda_oom"
        if self.process_crashed:
            return "worker_crashed"
        if self.stalled:
            return "progress_stalled"
        if self.memory_pressure:
            return "unsafe_system_memory_pressure"
        if self.timed_out:
            return "bounded_timeout"
        if not self.valid_output:
            return self.failure_code or "output_invalid"
        return self.failure_code or "qualification_failed"


class QualificationRunner(Protocol):
    async def run_probe(self, probe: QualificationProbe) -> QualificationSample: ...


class QualificationCache:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, cache_key: str) -> Path:
        if len(cache_key) != 64 or any(character not in "0123456789abcdef" for character in cache_key):
            raise ValueError("invalid qualification cache key")
        return self.root / f"{cache_key}.json"

    def load(self, cache_key: str) -> QualificationView | None:
        path = self._path(cache_key)
        if not path.is_file() or path.stat().st_size > 1_000_000:
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            view = QualificationView.model_validate(value)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
            return None
        if view.cache_key != cache_key or view.selected_tier is None:
            return None
        return view.model_copy(update={"cached": True})

    def store(self, view: QualificationView) -> None:
        path = self._path(view.cache_key)
        temporary = path.with_name(f".{path.name}.partial-{uuid4().hex}")
        try:
            temporary.write_text(
                json.dumps(
                    view.model_dump(mode="json", by_alias=True),
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


async def qualify_hardware(
    identity: HardwareIdentity,
    runner: QualificationRunner,
    cache: QualificationCache,
) -> QualificationView:
    cache_key = identity.cache_key()
    cached = cache.load(cache_key)
    if cached is not None:
        return cached
    candidates: list[QualificationCandidate] = []
    selected: str | None = None
    for probe in QUALIFICATION_PROBES:
        if selected is not None:
            candidates.append(QualificationCandidate(tier=probe.tier, state="skipped"))
            continue
        candidates.append(QualificationCandidate(tier=probe.tier, state="running"))
        try:
            sample = await runner.run_probe(probe)
        except Exception:
            sample = QualificationSample(
                valid_output=False,
                elapsed_seconds=0,
                process_crashed=True,
                failure_code="probe_runner_failed",
            )
        candidates[-1] = QualificationCandidate(
            tier=probe.tier,
            state="passed" if sample.passed else "failed",
            reason=sample.reason,
            peak_vram_bytes=sample.peak_vram_bytes,
            peak_system_memory_bytes=sample.peak_system_memory_bytes,
            elapsed_seconds=sample.elapsed_seconds,
        )
        if sample.passed:
            selected = probe.tier
    completed_at = datetime.now(UTC)
    view = QualificationView(
        cache_key=cache_key,
        selected_tier=selected,
        completed_at=completed_at,
        candidates=candidates,
        error=(
            None
            if selected is not None
            else LocalVideoError(
                code="qualification_all_tiers_failed",
                summary="Every bounded local model tier failed hardware qualification.",
                action="Inspect the candidate reasons and validate the 5B workflow and local memory headroom.",
                retryable=True,
            )
        ),
    )
    if selected is not None:
        cache.store(view)
    return view
