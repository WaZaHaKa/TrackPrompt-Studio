from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from cloud_render.costs import (
    BudgetError,
    budget_status,
    cost_per_thousand_frames,
    cost_per_validated_frame,
    plan_fleet,
    rank_benchmarks,
)
from cloud_render.models import BenchmarkResult, BudgetLimits, GpuOffer


def _result(
    name: str,
    hourly: str,
    seconds: str,
    *,
    eligible: bool = True,
    software: bool = False,
) -> BenchmarkResult:
    return BenchmarkResult(
        GpuOffer("brev", name, name, "region", Decimal(hourly)),
        Decimal(seconds),
        Decimal(seconds) * Decimal("1.20"),
        60,
        visual_passed=eligible,
        technical_passed=eligible,
        software_rendering=software,
    )


def _limits(**updates: object) -> BudgetLimits:
    values = {
        "max_hourly_price_per_worker": Decimal("10"),
        "max_worker_count": 8,
        "total_budget_ceiling": Decimal("1000"),
    }
    values.update(updates)
    return BudgetLimits(**values)


def test_cost_formulas_are_per_validated_frame() -> None:
    assert cost_per_validated_frame(Decimal("2"), Decimal("1.5")) == Decimal("3") / 3600
    assert cost_per_thousand_frames(Decimal("2"), Decimal("1.5")) == Decimal("3000") / 3600


def test_tournament_ranks_measured_cost_not_gpu_tier() -> None:
    h200 = _result("H200", "8", "1")
    l40s = _result("L40S", "2", "2")
    ranked = rank_benchmarks([h200, l40s])
    assert [item.benchmark.offer.gpu_name for item in ranked] == ["L40S", "H200"]


def test_tournament_rejects_failed_or_software_candidates() -> None:
    assert rank_benchmarks([_result("bad", "1", "1", eligible=False)]) == []
    assert rank_benchmarks([_result("cpu", "1", "1", software=True)]) == []


def test_fleet_plan_defaults_to_four_and_stays_under_budget() -> None:
    plan = plan_fleet(_result("L40S", "2", "2"), frame_count=1000, limits=_limits())
    assert plan.worker_count == 4
    assert plan.expected_wall_seconds < Decimal("600")
    assert plan.conservative_total_cost <= Decimal("1000")


def test_fleet_plan_can_size_to_target_time() -> None:
    plan = plan_fleet(
        _result("L40S", "2", "3"),
        frame_count=3600,
        target_completion_hours=Decimal("1"),
        limits=_limits(),
    )
    assert plan.worker_count == 3


def test_fleet_plan_enforces_worker_price_budget_and_deadline() -> None:
    with pytest.raises(BudgetError, match="hourly"):
        plan_fleet(_result("costly", "11", "1"), frame_count=10, limits=_limits())
    with pytest.raises(BudgetError, match="worker"):
        plan_fleet(
            _result("gpu", "2", "1"),
            frame_count=10,
            worker_count=9,
            limits=_limits(),
        )
    now = datetime(2026, 7, 20, tzinfo=UTC)
    with pytest.raises(BudgetError, match="deadline"):
        plan_fleet(
            _result("gpu", "2", "100"),
            frame_count=100,
            worker_count=1,
            limits=_limits(deadline_utc=now + timedelta(seconds=5)),
            now=now,
        )


def test_budget_status_requires_stop_before_forecast_exceeds_ceiling() -> None:
    status = budget_status(
        _limits(total_budget_ceiling=Decimal("100")),
        worker_count=4,
        hourly_price_per_worker=Decimal("2"),
        current_spend=Decimal("70"),
        forecast_final_spend=Decimal("101"),
    )
    assert status.stop_required is True
    assert status.allowed is False
    assert status.warning is True
