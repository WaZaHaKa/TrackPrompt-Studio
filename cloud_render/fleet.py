from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol, Sequence

from .costs import BudgetStatus, budget_status
from .models import BudgetLimits
from .providers.base import CloudProvider, ProviderInstance


class JobStatusLike(Protocol):
    complete: bool


class SchedulerStatusReader(Protocol):
    def status(self, job_id: str) -> JobStatusLike: ...


@dataclass(frozen=True, slots=True)
class FleetReconcileResult:
    reason: str
    budget: BudgetStatus
    terminated_instances: tuple[str, ...]
    retained_instances: tuple[str, ...]


class FleetController:
    """Idempotent worker stop controller; permanent deletion stays explicit."""

    def __init__(self, provider: CloudProvider, scheduler: SchedulerStatusReader) -> None:
        self.provider = provider
        self.scheduler = scheduler

    def reconcile(
        self,
        *,
        job_id: str,
        instances: Sequence[ProviderInstance],
        limits: BudgetLimits,
        current_spend: Decimal,
        forecast_final_spend: Decimal,
        hourly_price_per_worker: Decimal,
        idle_instance_refs: Sequence[str] = (),
    ) -> FleetReconcileResult:
        budget = budget_status(
            limits,
            worker_count=len(instances),
            hourly_price_per_worker=hourly_price_per_worker,
            current_spend=current_spend,
            forecast_final_spend=forecast_final_spend,
        )
        status = self.scheduler.status(job_id)
        idle = set(idle_instance_refs)
        if status.complete:
            reason = "job-complete"
            targets = {instance.instance_id for instance in instances}
        elif budget.stop_required:
            reason = "budget-safety-stop"
            targets = {instance.instance_id for instance in instances}
        elif idle:
            reason = "idle-worker-timeout"
            targets = {
                instance.instance_id
                for instance in instances
                if instance.instance_id in idle or instance.name in idle
            }
        else:
            reason = "fleet-retained"
            targets = set()
        terminated: list[str] = []
        retained: list[str] = []
        for instance in instances:
            if instance.instance_id in targets:
                # Stop completed/idle compute automatically to bound spend.
                # Permanent provider deletion remains a separately confirmed
                # operator action through the provider/CLI teardown path.
                self.provider.stop_instance(instance.instance_id)
                terminated.append(instance.instance_id)
            else:
                retained.append(instance.instance_id)
        return FleetReconcileResult(
            reason,
            budget,
            tuple(sorted(terminated)),
            tuple(sorted(retained)),
        )
