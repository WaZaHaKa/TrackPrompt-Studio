from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from cloud_render.fleet import FleetController
from cloud_render.models import BudgetLimits
from cloud_render.providers import MockProvider, ProviderInstance


@dataclass
class Status:
    complete: bool


class Scheduler:
    def __init__(self, complete: bool) -> None:
        self.complete = complete

    def status(self, _job_id: str) -> Status:
        return Status(self.complete)


def _limits() -> BudgetLimits:
    return BudgetLimits(Decimal("5"), 4, Decimal("100"))


def _instances() -> list[ProviderInstance]:
    return [
        ProviderInstance("mock", "instance-1", "worker-1", "running"),
        ProviderInstance("mock", "instance-2", "worker-2", "running"),
    ]


def test_completed_job_automatically_stops_every_worker_without_deleting() -> None:
    provider = MockProvider(instances=_instances())
    result = FleetController(provider, Scheduler(True)).reconcile(
        job_id="job-1",
        instances=_instances(),
        limits=_limits(),
        current_spend=Decimal("10"),
        forecast_final_spend=Decimal("10"),
        hourly_price_per_worker=Decimal("2"),
    )
    assert result.reason == "job-complete"
    assert result.terminated_instances == ("instance-1", "instance-2")
    assert provider.calls == (
        ("stop", "instance-1"),
        ("stop", "instance-2"),
    )


def test_budget_forecast_stops_fleet_before_ceiling() -> None:
    provider = MockProvider(instances=_instances())
    result = FleetController(provider, Scheduler(False)).reconcile(
        job_id="job-1",
        instances=_instances(),
        limits=_limits(),
        current_spend=Decimal("75"),
        forecast_final_spend=Decimal("101"),
        hourly_price_per_worker=Decimal("2"),
    )
    assert result.reason == "budget-safety-stop"
    assert result.budget.stop_required is True
    assert len(result.terminated_instances) == 2


def test_only_explicitly_idle_worker_is_torn_down() -> None:
    provider = MockProvider(instances=_instances())
    result = FleetController(provider, Scheduler(False)).reconcile(
        job_id="job-1",
        instances=_instances(),
        limits=_limits(),
        current_spend=Decimal("1"),
        forecast_final_spend=Decimal("10"),
        hourly_price_per_worker=Decimal("2"),
        idle_instance_refs=("worker-2",),
    )
    assert result.terminated_instances == ("instance-2",)
    assert result.retained_instances == ("instance-1",)
