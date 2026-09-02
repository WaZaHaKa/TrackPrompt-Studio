from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from statistics import median
from typing import Self

from pydantic import Field, model_validator

from .render_contracts import (
    Identifier,
    ImmutableRenderContractModel,
    MediaRenderJob,
    OutputVariant,
    RenderContractModel,
    RenderStage,
    Sha256Digest,
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class EstimateState(StrEnum):
    CALIBRATING = "calibrating"
    STABLE = "stable"
    DEGRADED = "degraded"


class EtaConfidence(StrEnum):
    UNKNOWN = "unknown"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class EtaSample(ImmutableRenderContractModel):
    output_variant_id: Identifier
    stage: RenderStage
    complexity_class: Identifier = "default"
    task_id: Identifier
    worker_id: Identifier
    attempt: int = Field(default=1, ge=1)
    succeeded: bool = True
    retried: bool = False
    duration_seconds: float = Field(gt=0)
    completed_units: float = Field(gt=0)
    recorded_at: datetime

    @property
    def seconds_per_unit(self) -> float:
        return self.duration_seconds / self.completed_units


class WorkerAllocation(ImmutableRenderContractModel):
    output_variant_id: Identifier
    stage: RenderStage
    worker_count: int = Field(ge=0)
    observed_at: datetime


class EtaPersistentState(RenderContractModel):
    revision: int = Field(default=0, ge=0)
    samples: tuple[EtaSample, ...] = ()
    worker_allocations: tuple[WorkerAllocation, ...] = ()
    updated_at: datetime


class StageWorkload(ImmutableRenderContractModel):
    output_variant_id: Identifier
    stage: RenderStage
    complexity_class: Identifier = "default"
    remaining_units: float = Field(ge=0)
    parallelizable: bool = True
    worker_count_override: int | None = Field(default=None, ge=0)
    measured_fallback_p50_seconds_per_unit: float | None = Field(default=None, gt=0)
    measured_fallback_p90_seconds_per_unit: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_fallback_bounds(self) -> Self:
        if (
            self.measured_fallback_p90_seconds_per_unit is not None
            and self.measured_fallback_p50_seconds_per_unit is None
        ):
            raise ValueError("fallback P50 is required when fallback P90 is supplied")
        if (
            self.measured_fallback_p50_seconds_per_unit is not None
            and self.measured_fallback_p90_seconds_per_unit is not None
            and self.measured_fallback_p90_seconds_per_unit
            < self.measured_fallback_p50_seconds_per_unit
        ):
            raise ValueError("fallback P90 cannot be lower than fallback P50")
        return self


class EtaEstimate(ImmutableRenderContractModel):
    output_variant_id: Identifier
    stage: RenderStage
    complexity_class: Identifier
    state: EstimateState
    confidence: EtaConfidence
    remaining_units: float = Field(ge=0)
    effective_worker_count: int = Field(ge=0)
    retained_sample_count: int = Field(ge=0)
    excluded_retry_count: int = Field(ge=0)
    excluded_outlier_count: int = Field(ge=0)
    p50_seconds_per_unit: float | None = Field(default=None, gt=0)
    p90_seconds_per_unit: float | None = Field(default=None, gt=0)
    p50_remaining_seconds: float | None = Field(default=None, ge=0)
    p90_remaining_seconds: float | None = Field(default=None, ge=0)
    last_estimate_at: datetime

    @model_validator(mode="after")
    def validate_bounds(self) -> Self:
        if (
            self.p50_seconds_per_unit is not None
            and self.p90_seconds_per_unit is not None
            and self.p90_seconds_per_unit < self.p50_seconds_per_unit
        ):
            raise ValueError("P90 unit duration cannot be lower than P50")
        if (
            self.p50_remaining_seconds is not None
            and self.p90_remaining_seconds is not None
            and self.p90_remaining_seconds < self.p50_remaining_seconds
        ):
            raise ValueError("P90 remaining duration cannot be lower than P50")
        return self


class VariantEtaForecast(ImmutableRenderContractModel):
    output_variant_id: Identifier
    enabled: bool
    estimates: tuple[EtaEstimate, ...]
    state: EstimateState
    confidence: EtaConfidence
    p50_remaining_seconds: float | None = Field(default=None, ge=0)
    p90_remaining_seconds: float | None = Field(default=None, ge=0)
    last_estimate_at: datetime


class MatrixEtaForecast(ImmutableRenderContractModel):
    job_id: Identifier
    matrix_id: Identifier
    matrix_sha256: Sha256Digest
    enabled_variant_ids: tuple[Identifier, ...]
    variants_run_in_parallel: bool
    variant_forecasts: tuple[VariantEtaForecast, ...]
    state: EstimateState
    confidence: EtaConfidence
    p50_remaining_seconds: float | None = Field(default=None, ge=0)
    p90_remaining_seconds: float | None = Field(default=None, ge=0)
    last_estimate_at: datetime


def _quantile(values: Sequence[float], probability: float) -> float:
    if not values:
        raise ValueError("cannot calculate a quantile without values")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * probability
    lower_index = int(position)
    upper_index = min(lower_index + 1, len(ordered) - 1)
    fraction = position - lower_index
    return ordered[lower_index] + (ordered[upper_index] - ordered[lower_index]) * fraction


def _filter_outliers(values: Sequence[float]) -> tuple[list[float], int]:
    """Apply a median-absolute-deviation filter to positive unit durations."""
    if len(values) < 5:
        return list(values), 0
    center = median(values)
    deviations = [abs(value - center) for value in values]
    absolute_deviation = median(deviations)
    if absolute_deviation == 0:
        lower_bound = center / 3
        upper_bound = center * 3
        retained = [value for value in values if lower_bound <= value <= upper_bound]
    else:
        retained = [
            value
            for value in values
            if 0.6745 * abs(value - center) / absolute_deviation <= 3.5
        ]
    if not retained:
        return list(values), 0
    return retained, len(values) - len(retained)


def _confidence_for_samples(sample_count: int, degraded: bool) -> EtaConfidence:
    if sample_count == 0:
        return EtaConfidence.UNKNOWN
    if sample_count < 5:
        confidence = EtaConfidence.LOW
    elif sample_count < 12:
        confidence = EtaConfidence.MEDIUM
    else:
        confidence = EtaConfidence.HIGH
    if degraded and confidence is EtaConfidence.HIGH:
        return EtaConfidence.MEDIUM
    if degraded and confidence is EtaConfidence.MEDIUM:
        return EtaConfidence.LOW
    return confidence


def _aggregate_state(states: Sequence[EstimateState]) -> EstimateState:
    if any(state is EstimateState.DEGRADED for state in states):
        return EstimateState.DEGRADED
    if any(state is EstimateState.CALIBRATING for state in states):
        return EstimateState.CALIBRATING
    return EstimateState.STABLE


def _aggregate_confidence(confidences: Sequence[EtaConfidence]) -> EtaConfidence:
    if not confidences:
        return EtaConfidence.HIGH
    rank = {
        EtaConfidence.UNKNOWN: 0,
        EtaConfidence.LOW: 1,
        EtaConfidence.MEDIUM: 2,
        EtaConfidence.HIGH: 3,
    }
    return min(confidences, key=rank.__getitem__)


class EtaService:
    """Persistent robust ETA estimator scoped by output variant, stage, and complexity."""

    def __init__(
        self,
        state: EtaPersistentState | None = None,
        *,
        clock: Callable[[], datetime] = _utc_now,
        minimum_stable_samples: int = 5,
        max_samples: int = 20_000,
    ) -> None:
        if minimum_stable_samples < 1:
            raise ValueError("minimum_stable_samples must be positive")
        if max_samples < 1:
            raise ValueError("max_samples must be positive")
        self._clock = clock
        self._minimum_stable_samples = minimum_stable_samples
        self._max_samples = max_samples
        self._state = state or EtaPersistentState(updated_at=clock())

    @classmethod
    def from_state(
        cls,
        state: EtaPersistentState,
        *,
        clock: Callable[[], datetime] = _utc_now,
        minimum_stable_samples: int = 5,
        max_samples: int = 20_000,
    ) -> EtaService:
        return cls(
            state.model_copy(deep=True),
            clock=clock,
            minimum_stable_samples=minimum_stable_samples,
            max_samples=max_samples,
        )

    def snapshot(self) -> EtaPersistentState:
        return self._state.model_copy(deep=True)

    def record_sample(self, sample: EtaSample) -> None:
        samples = (*self._state.samples, sample)[-self._max_samples :]
        self._state = self._state.model_copy(
            update={
                "revision": self._state.revision + 1,
                "samples": samples,
                "updated_at": sample.recorded_at,
            }
        )

    def set_worker_count(
        self,
        output_variant_id: str,
        stage: RenderStage,
        worker_count: int,
        *,
        observed_at: datetime | None = None,
    ) -> None:
        allocation = WorkerAllocation(
            output_variant_id=output_variant_id,
            stage=stage,
            worker_count=worker_count,
            observed_at=observed_at or self._clock(),
        )
        allocations = (*self._state.worker_allocations, allocation)
        self._state = self._state.model_copy(
            update={
                "revision": self._state.revision + 1,
                "worker_allocations": allocations,
                "updated_at": allocation.observed_at,
            }
        )

    def estimate(self, workload: StageWorkload) -> EtaEstimate:
        return self._estimate(workload, self._clock())

    def _estimate(self, workload: StageWorkload, estimated_at: datetime) -> EtaEstimate:
        worker_count = self._effective_worker_count(workload)
        if workload.remaining_units == 0:
            return EtaEstimate(
                output_variant_id=workload.output_variant_id,
                stage=workload.stage,
                complexity_class=workload.complexity_class,
                state=EstimateState.STABLE,
                confidence=EtaConfidence.HIGH,
                remaining_units=0,
                effective_worker_count=worker_count,
                retained_sample_count=0,
                excluded_retry_count=0,
                excluded_outlier_count=0,
                p50_remaining_seconds=0,
                p90_remaining_seconds=0,
                last_estimate_at=estimated_at,
            )

        rates, retry_count, outlier_count, candidate_count = self._sample_rates(workload)
        fallback_p50 = workload.measured_fallback_p50_seconds_per_unit
        fallback_p90 = workload.measured_fallback_p90_seconds_per_unit or fallback_p50
        using_fallback = not rates and fallback_p50 is not None

        p50_rate: float | None
        p90_rate: float | None
        if rates:
            p50_rate = _quantile(rates, 0.5)
            p90_rate = max(p50_rate, _quantile(rates, 0.9))
        elif using_fallback:
            p50_rate = fallback_p50
            p90_rate = fallback_p90
        else:
            p50_rate = None
            p90_rate = None

        exclusion_ratio = (retry_count + outlier_count) / max(candidate_count, 1)
        degraded = worker_count == 0 or exclusion_ratio > 0.4
        if degraded:
            estimate_state = EstimateState.DEGRADED
        elif using_fallback or len(rates) < self._minimum_stable_samples:
            estimate_state = EstimateState.CALIBRATING
        else:
            estimate_state = EstimateState.STABLE

        confidence = (
            EtaConfidence.LOW
            if using_fallback
            else _confidence_for_samples(len(rates), degraded)
        )
        if worker_count > 0 and p50_rate is not None and p90_rate is not None:
            p50_remaining = p50_rate * workload.remaining_units / worker_count
            p90_remaining = p90_rate * workload.remaining_units / worker_count
        else:
            p50_remaining = None
            p90_remaining = None
        return EtaEstimate(
            output_variant_id=workload.output_variant_id,
            stage=workload.stage,
            complexity_class=workload.complexity_class,
            state=estimate_state,
            confidence=confidence,
            remaining_units=workload.remaining_units,
            effective_worker_count=worker_count,
            retained_sample_count=len(rates),
            excluded_retry_count=retry_count,
            excluded_outlier_count=outlier_count,
            p50_seconds_per_unit=p50_rate,
            p90_seconds_per_unit=p90_rate,
            p50_remaining_seconds=p50_remaining,
            p90_remaining_seconds=p90_remaining,
            last_estimate_at=estimated_at,
        )

    def _effective_worker_count(self, workload: StageWorkload) -> int:
        if workload.worker_count_override is not None:
            configured_count = workload.worker_count_override
        else:
            matching = [
                allocation
                for allocation in self._state.worker_allocations
                if allocation.output_variant_id == workload.output_variant_id
                and allocation.stage is workload.stage
            ]
            configured_count = matching[-1].worker_count if matching else 1
        if not workload.parallelizable and configured_count > 0:
            return 1
        return configured_count

    def _sample_rates(self, workload: StageWorkload) -> tuple[list[float], int, int, int]:
        candidates = [
            sample
            for sample in self._state.samples
            if sample.output_variant_id == workload.output_variant_id
            and sample.stage is workload.stage
            and sample.complexity_class == workload.complexity_class
        ]
        retry_count = sum(
            1
            for sample in candidates
            if not sample.succeeded or sample.retried or sample.attempt > 1
        )
        usable = [
            sample
            for sample in candidates
            if sample.succeeded and not sample.retried and sample.attempt == 1
        ]
        latest_by_task: dict[str, EtaSample] = {}
        for sample in usable:
            previous = latest_by_task.get(sample.task_id)
            if previous is None or sample.recorded_at >= previous.recorded_at:
                latest_by_task[sample.task_id] = sample
        rates = [sample.seconds_per_unit for sample in latest_by_task.values()]
        filtered, outlier_count = _filter_outliers(rates)
        return filtered, retry_count, outlier_count, len(candidates)

    def forecast_variant(
        self,
        output_variant: OutputVariant,
        workloads: Iterable[StageWorkload],
        *,
        estimated_at: datetime | None = None,
    ) -> VariantEtaForecast:
        forecast_at = estimated_at or self._clock()
        if not output_variant.enabled:
            return VariantEtaForecast(
                output_variant_id=output_variant.id,
                enabled=False,
                estimates=(),
                state=EstimateState.STABLE,
                confidence=EtaConfidence.HIGH,
                p50_remaining_seconds=0,
                p90_remaining_seconds=0,
                last_estimate_at=forecast_at,
            )
        matching_workloads = [
            workload
            for workload in workloads
            if workload.output_variant_id == output_variant.id
        ]
        estimates = tuple(self._estimate(workload, forecast_at) for workload in matching_workloads)
        p50_total = self._sum_known(
            estimate.p50_remaining_seconds for estimate in estimates
        )
        p90_total = self._sum_known(
            estimate.p90_remaining_seconds for estimate in estimates
        )
        return VariantEtaForecast(
            output_variant_id=output_variant.id,
            enabled=True,
            estimates=estimates,
            state=_aggregate_state([estimate.state for estimate in estimates]),
            confidence=_aggregate_confidence([estimate.confidence for estimate in estimates]),
            p50_remaining_seconds=p50_total,
            p90_remaining_seconds=p90_total,
            last_estimate_at=forecast_at,
        )

    def forecast_matrix(
        self,
        job: MediaRenderJob,
        workloads: Iterable[StageWorkload],
        *,
        variants_run_in_parallel: bool = False,
    ) -> MatrixEtaForecast:
        enabled_variants = tuple(variant for variant in job.output_variants if variant.enabled)
        enabled_ids = tuple(variant.id for variant in enabled_variants)
        if enabled_ids != job.output_matrix.enabled_variant_ids:
            raise ValueError("the enabled output set no longer matches the bound matrix identity")
        workload_list = tuple(workloads)
        forecast_at = self._clock()
        variant_forecasts = tuple(
            self.forecast_variant(variant, workload_list, estimated_at=forecast_at)
            for variant in enabled_variants
        )
        p50_total = self._aggregate_variants(
            [forecast.p50_remaining_seconds for forecast in variant_forecasts],
            variants_run_in_parallel,
        )
        p90_total = self._aggregate_variants(
            [forecast.p90_remaining_seconds for forecast in variant_forecasts],
            variants_run_in_parallel,
        )
        return MatrixEtaForecast(
            job_id=job.id,
            matrix_id=job.output_matrix.matrix_id,
            matrix_sha256=job.output_matrix.matrix_sha256,
            enabled_variant_ids=enabled_ids,
            variants_run_in_parallel=variants_run_in_parallel,
            variant_forecasts=variant_forecasts,
            state=_aggregate_state(
                [forecast.state for forecast in variant_forecasts]
            ),
            confidence=_aggregate_confidence(
                [forecast.confidence for forecast in variant_forecasts]
            ),
            p50_remaining_seconds=p50_total,
            p90_remaining_seconds=p90_total,
            last_estimate_at=forecast_at,
        )

    @staticmethod
    def _sum_known(values: Iterable[float | None]) -> float | None:
        materialized = tuple(values)
        if any(value is None for value in materialized):
            return None
        return sum(value for value in materialized if value is not None)

    @staticmethod
    def _aggregate_variants(
        values: Sequence[float | None],
        variants_run_in_parallel: bool,
    ) -> float | None:
        if any(value is None for value in values):
            return None
        known = [value for value in values if value is not None]
        if not known:
            return 0
        return max(known) if variants_run_in_parallel else sum(known)
