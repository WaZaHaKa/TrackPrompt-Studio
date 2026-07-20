"""Provider-neutral contracts and fail-closed provisioning guards.

Provider implementations are deliberately small adapters.  Scheduling, cost
ranking, and storage live outside this package; the provider layer only
discovers offers, reports instances, and executes explicitly authorized
lifecycle operations.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from cloud_render.models import BudgetLimits, GpuOffer, IdentityBundle

LOCK_CLOUD_PLAN_CONFIRMATION = "LOCK CLOUD PLAN"
PROVISION_BILLABLE_WORKERS_CONFIRMATION = "PROVISION BILLABLE GPU WORKERS"
_MONEY_QUANTUM = Decimal("0.01")


class ProviderError(RuntimeError):
    """Base class for safe provider-facing errors."""


class ProviderUnavailableError(ProviderError):
    """Raised when a provider client is absent or cannot be inspected."""


class ProviderCapabilityError(ProviderError):
    """Raised when the installed client does not document a required command."""


class ProviderCommandError(ProviderError):
    """Raised when a provider command exits unsuccessfully."""


class ProvisioningDeniedError(ProviderError):
    """Raised when any provisioning authorization gate is not satisfied."""


class BudgetGuardError(ProvisioningDeniedError):
    """Raised when a plan exceeds an operator-owned budget limit."""


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Bounded result returned by an injected, argument-array command runner."""

    args: tuple[str, ...]
    returncode: int
    stdout: str = ""
    stderr: str = ""


class CommandRunner(Protocol):
    """Minimal injectable command runner used by provider CLI adapters."""

    def run(self, args: Sequence[str], *, timeout_seconds: float) -> CommandResult:
        """Run an argument array without a shell and return captured output."""


@dataclass(frozen=True, slots=True)
class CommandCapability:
    """A command and the flags observed in the installed CLI's own help."""

    name: str
    flags: frozenset[str]

    def supports(self, *required_flags: str) -> bool:
        return all(flag in self.flags for flag in required_flags)


@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    """Capabilities captured from one locally inspected provider client."""

    provider: str
    cli_version: str
    executable: tuple[str, ...]
    commands: tuple[CommandCapability, ...]

    def command(self, name: str) -> CommandCapability | None:
        return next((item for item in self.commands if item.name == name), None)

    def supports(self, name: str, *required_flags: str) -> bool:
        command = self.command(name)
        return command is not None and command.supports(*required_flags)


@dataclass(frozen=True, slots=True)
class ProviderReadiness:
    """Non-throwing readiness summary suitable for an offline UI."""

    provider: str
    available: bool
    cli_version: str | None
    detail: str
    capabilities: ProviderCapabilities | None = None


@dataclass(frozen=True, slots=True)
class ProviderInstance:
    """Provider-neutral view of a provisioned VM."""

    provider: str
    instance_id: str
    name: str
    status: str
    offer_id: str | None = None
    gpu_name: str | None = None
    region: str | None = None
    hourly_price: Decimal | None = None


@dataclass(frozen=True, slots=True)
class BenchmarkProvisioningPlan:
    """Frozen, single-worker benchmark plan presented to the operator."""

    instance_name: str
    offer: GpuOffer
    identities: IdentityBundle
    budget_ceiling: Decimal
    worker_count: int = 1

    def __post_init__(self) -> None:
        if not self.instance_name or self.instance_name.startswith("-"):
            raise ValueError("instance_name must be a non-option provider name")
        if self.worker_count < 1:
            raise ValueError("worker_count must be positive")
        if not self.budget_ceiling.is_finite() or self.budget_ceiling <= Decimal("0"):
            raise ValueError("budget_ceiling must be positive")
        if self.budget_ceiling != self.budget_ceiling.quantize(_MONEY_QUANTUM):
            raise ValueError("budget_ceiling must use whole-cent precision")


@dataclass(frozen=True, slots=True)
class ProvisioningAuthorization:
    """Independent lock, final-confirmation, and exact-token gates."""

    token: str
    plan_locked: bool
    final_confirmed: bool


def _format_money(value: Decimal) -> str:
    return format(value.quantize(_MONEY_QUANTUM), "f")


def benchmark_authorization_token(plan: BenchmarkProvisioningPlan) -> str:
    """Return the exact live benchmark token bound to hashes and budget."""

    return (
        "AUTHORIZE BREV BENCHMARK: "
        f"{plan.identities.package_sha256[:12]} | "
        f"{plan.identities.profile_sha256[:12]} | "
        f"MAX ${_format_money(plan.budget_ceiling)}"
    )


def validate_benchmark_provisioning(
    plan: BenchmarkProvisioningPlan,
    limits: BudgetLimits,
    authorization: ProvisioningAuthorization,
) -> None:
    """Validate every billable benchmark gate or raise without side effects."""

    if plan.worker_count != 1:
        raise BudgetGuardError("a Brev benchmark must provision exactly one worker")
    if plan.worker_count > limits.max_worker_count:
        raise BudgetGuardError("worker count exceeds the locked operator limit")
    if plan.offer.hourly_price > limits.max_hourly_price_per_worker:
        raise BudgetGuardError("offer hourly price exceeds the per-worker limit")
    if plan.budget_ceiling > limits.total_budget_ceiling:
        raise BudgetGuardError("benchmark budget exceeds the total budget ceiling")
    if not plan.offer.available:
        raise ProvisioningDeniedError("the selected GPU offer is not currently available")
    if not authorization.plan_locked:
        raise ProvisioningDeniedError(
            f"first confirmation is required: {LOCK_CLOUD_PLAN_CONFIRMATION}"
        )
    if not authorization.final_confirmed:
        raise ProvisioningDeniedError(
            "final confirmation is required: "
            f"{PROVISION_BILLABLE_WORKERS_CONFIRMATION}"
        )
    expected = benchmark_authorization_token(plan)
    if authorization.token != expected:
        raise ProvisioningDeniedError("benchmark authorization token does not match the locked plan")


class CloudProvider(ABC):
    """Provider-neutral discovery and lifecycle interface."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Stable provider identifier."""

    @abstractmethod
    def readiness(self) -> ProviderReadiness:
        """Inspect the local provider client without provisioning resources."""

    @abstractmethod
    def inspect_capabilities(self, *, force: bool = False) -> ProviderCapabilities:
        """Capture locally documented commands and flags."""

    @abstractmethod
    def discover_gpu_offers(self) -> tuple[GpuOffer, ...]:
        """Return currently discoverable GPU VM offers."""

    @abstractmethod
    def list_instances(self) -> tuple[ProviderInstance, ...]:
        """Return instances visible to the current provider context."""

    @abstractmethod
    def provision_benchmark(
        self,
        plan: BenchmarkProvisioningPlan,
        limits: BudgetLimits,
        authorization: ProvisioningAuthorization,
    ) -> ProviderInstance:
        """Provision one explicitly authorized billable benchmark worker."""

    @abstractmethod
    def stop_instance(self, instance_ref: str) -> CommandResult:
        """Stop one instance, preserving provider storage when supported."""

    @abstractmethod
    def delete_instance(self, instance_ref: str) -> CommandResult:
        """Permanently delete one instance and its provider-owned storage."""

    def teardown_instance(self, instance_ref: str) -> tuple[CommandResult, CommandResult]:
        """Stop then delete one known worker; never expands to an all-instances command."""

        stopped = self.stop_instance(instance_ref)
        deleted = self.delete_instance(instance_ref)
        return stopped, deleted
