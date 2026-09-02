from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, ROUND_CEILING
from typing import Iterable

from .models import BenchmarkResult, BudgetLimits

SECONDS_PER_HOUR = Decimal("3600")


class BudgetError(ValueError):
    """A fleet or live-spend plan violates an operator-locked hard limit."""


def cost_per_validated_frame(hourly_price: Decimal, seconds_per_frame: Decimal) -> Decimal:
    if hourly_price < 0 or seconds_per_frame <= 0:
        raise ValueError("hourly price must be nonnegative and frame timing positive")
    return hourly_price * seconds_per_frame / SECONDS_PER_HOUR


def cost_per_thousand_frames(hourly_price: Decimal, seconds_per_frame: Decimal) -> Decimal:
    return cost_per_validated_frame(hourly_price, seconds_per_frame) * Decimal(1000)


@dataclass(frozen=True, slots=True)
class RankedBenchmark:
    rank: int
    benchmark: BenchmarkResult
    cost_per_frame: Decimal
    cost_per_1000_frames: Decimal


def rank_benchmarks(results: Iterable[BenchmarkResult]) -> list[RankedBenchmark]:
    eligible = [item for item in results if item.eligible]
    eligible.sort(
        key=lambda item: (
            cost_per_validated_frame(item.offer.hourly_price, item.seconds_per_frame),
            item.p90_seconds_per_frame,
            item.seconds_per_frame,
            item.offer.provider.casefold(),
            item.offer.offer_id.casefold(),
        )
    )
    return [
        RankedBenchmark(
            rank=index,
            benchmark=item,
            cost_per_frame=cost_per_validated_frame(item.offer.hourly_price, item.seconds_per_frame),
            cost_per_1000_frames=cost_per_thousand_frames(
                item.offer.hourly_price,
                item.seconds_per_frame,
            ),
        )
        for index, item in enumerate(eligible, start=1)
    ]


@dataclass(frozen=True, slots=True)
class FleetPlan:
    worker_count: int
    frame_count: int
    expected_wall_seconds: Decimal
    conservative_wall_seconds: Decimal
    expected_compute_cost: Decimal
    conservative_compute_cost: Decimal
    storage_cost: Decimal
    transfer_cost: Decimal
    safety_reserve: Decimal
    expected_total_cost: Decimal
    conservative_total_cost: Decimal
    deadline_utc: datetime | None


def plan_fleet(
    benchmark: BenchmarkResult,
    *,
    frame_count: int,
    worker_count: int | None = None,
    target_completion_hours: Decimal | None = None,
    limits: BudgetLimits,
    storage_cost: Decimal = Decimal("0"),
    transfer_cost: Decimal = Decimal("0"),
    safety_fraction: Decimal = Decimal("0.15"),
    now: datetime | None = None,
) -> FleetPlan:
    if not benchmark.eligible:
        raise BudgetError("fleet planning requires a visually and technically valid benchmark")
    if frame_count < 1:
        raise ValueError("frame_count must be positive")
    if benchmark.offer.hourly_price > limits.max_hourly_price_per_worker:
        raise BudgetError("GPU hourly price exceeds the locked per-worker maximum")
    if any(value < 0 for value in (storage_cost, transfer_cost, safety_fraction)):
        raise ValueError("costs and safety fraction cannot be negative")
    if worker_count is None:
        if target_completion_hours is None:
            worker_count = min(4, limits.max_worker_count)
        else:
            if target_completion_hours <= 0:
                raise ValueError("target_completion_hours must be positive")
            serial_hours = benchmark.seconds_per_frame * frame_count / SECONDS_PER_HOUR
            worker_count = int(
                (serial_hours / target_completion_hours).to_integral_value(rounding=ROUND_CEILING)
            )
            worker_count = max(1, worker_count)
    if worker_count < 1 or worker_count > limits.max_worker_count:
        raise BudgetError("worker count exceeds the locked maximum")
    expected_gpu_hours = benchmark.seconds_per_frame * frame_count / SECONDS_PER_HOUR
    conservative_gpu_hours = benchmark.p90_seconds_per_frame * frame_count / SECONDS_PER_HOUR
    expected_compute = expected_gpu_hours * benchmark.offer.hourly_price
    conservative_compute = conservative_gpu_hours * benchmark.offer.hourly_price
    boot_overhead = benchmark.boot_seconds * worker_count
    transfer_overhead = benchmark.upload_seconds
    expected_wall = expected_gpu_hours * SECONDS_PER_HOUR / worker_count + boot_overhead + transfer_overhead
    conservative_wall = (
        conservative_gpu_hours * SECONDS_PER_HOUR / worker_count
        + boot_overhead
        + transfer_overhead
    )
    base_expected = expected_compute + storage_cost + transfer_cost
    base_conservative = conservative_compute + storage_cost + transfer_cost
    reserve = base_conservative * safety_fraction
    expected_total = base_expected + reserve
    conservative_total = base_conservative + reserve
    if conservative_total > limits.total_budget_ceiling:
        raise BudgetError("conservative fleet cost exceeds the locked total budget")
    current = now or datetime.now(UTC)
    deadline = limits.deadline_utc
    if deadline is not None and current + timedelta(seconds=float(conservative_wall)) > deadline:
        raise BudgetError("conservative fleet completion would miss the locked deadline")
    return FleetPlan(
        worker_count=worker_count,
        frame_count=frame_count,
        expected_wall_seconds=expected_wall,
        conservative_wall_seconds=conservative_wall,
        expected_compute_cost=expected_compute,
        conservative_compute_cost=conservative_compute,
        storage_cost=storage_cost,
        transfer_cost=transfer_cost,
        safety_reserve=reserve,
        expected_total_cost=expected_total,
        conservative_total_cost=conservative_total,
        deadline_utc=deadline,
    )


@dataclass(frozen=True, slots=True)
class BudgetStatus:
    allowed: bool
    stop_required: bool
    warning: bool
    current_spend: Decimal
    forecast_final_spend: Decimal
    remaining: Decimal
    reason: str


def budget_status(
    limits: BudgetLimits,
    *,
    worker_count: int,
    hourly_price_per_worker: Decimal,
    current_spend: Decimal,
    forecast_final_spend: Decimal,
) -> BudgetStatus:
    if current_spend < 0 or forecast_final_spend < current_spend:
        raise ValueError("spend values are invalid")
    violations: list[str] = []
    if worker_count > limits.max_worker_count:
        violations.append("worker-count-limit")
    if hourly_price_per_worker > limits.max_hourly_price_per_worker:
        violations.append("hourly-price-limit")
    if current_spend >= limits.total_budget_ceiling:
        violations.append("budget-exhausted")
    if forecast_final_spend > limits.total_budget_ceiling:
        violations.append("forecast-over-budget")
    warning_at = limits.total_budget_ceiling * limits.warning_fraction
    warning = current_spend >= warning_at or forecast_final_spend >= warning_at
    remaining = max(Decimal("0"), limits.total_budget_ceiling - current_spend)
    return BudgetStatus(
        allowed=not violations,
        stop_required=bool(violations),
        warning=warning,
        current_spend=current_spend,
        forecast_final_spend=forecast_final_spend,
        remaining=remaining,
        reason=",".join(violations) if violations else "within-locked-budget",
    )
