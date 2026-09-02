"""In-memory provider used by scheduler and orchestration tests."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from cloud_render.models import BudgetLimits, GpuOffer
from cloud_render.providers.base import (
    BenchmarkProvisioningPlan,
    CloudProvider,
    CommandCapability,
    CommandResult,
    ProviderCapabilities,
    ProviderInstance,
    ProviderReadiness,
    ProvisioningAuthorization,
    ProvisioningDeniedError,
    validate_benchmark_provisioning,
)


class MockProvider(CloudProvider):
    """Deterministic provider with no subprocess, network, or cloud access."""

    def __init__(
        self,
        *,
        offers: Sequence[GpuOffer] = (),
        instances: Sequence[ProviderInstance] = (),
        allow_provisioning: bool = False,
    ) -> None:
        self._offers = list(offers)
        self._instances = list(instances)
        self._allow_provisioning = allow_provisioning
        self._calls: list[tuple[str, ...]] = []

    @property
    def provider_name(self) -> str:
        return "mock"

    @property
    def calls(self) -> tuple[tuple[str, ...], ...]:
        return tuple(self._calls)

    def readiness(self) -> ProviderReadiness:
        capabilities = self.inspect_capabilities()
        return ProviderReadiness(
            provider=self.provider_name,
            available=True,
            cli_version="in-memory",
            detail="deterministic in-memory provider",
            capabilities=capabilities,
        )

    def inspect_capabilities(self, *, force: bool = False) -> ProviderCapabilities:
        del force
        return ProviderCapabilities(
            provider=self.provider_name,
            cli_version="in-memory",
            executable=("mock",),
            commands=(
                CommandCapability("search", frozenset({"--json"})),
                CommandCapability("ls", frozenset({"--json"})),
                CommandCapability(
                    "create", frozenset({"--type", "--count", "--detached"})
                ),
                CommandCapability("stop", frozenset()),
                CommandCapability("delete", frozenset()),
            ),
        )

    def discover_gpu_offers(self) -> tuple[GpuOffer, ...]:
        self._calls.append(("search",))
        return tuple(self._offers)

    def list_instances(self) -> tuple[ProviderInstance, ...]:
        self._calls.append(("ls",))
        return tuple(self._instances)

    def provision_benchmark(
        self,
        plan: BenchmarkProvisioningPlan,
        limits: BudgetLimits,
        authorization: ProvisioningAuthorization,
    ) -> ProviderInstance:
        if not self._allow_provisioning:
            raise ProvisioningDeniedError("mock provisioning is disabled by default")
        if plan.offer.provider != self.provider_name:
            raise ProvisioningDeniedError("selected offer belongs to another provider")
        validate_benchmark_provisioning(plan, limits, authorization)
        self._calls.append(("create", plan.instance_name, plan.offer.offer_id, "1"))
        instance = ProviderInstance(
            provider=self.provider_name,
            instance_id=f"mock-{len(self._instances) + 1:04d}",
            name=plan.instance_name,
            status="provisioning",
            offer_id=plan.offer.offer_id,
            gpu_name=plan.offer.gpu_name,
            region=plan.offer.region,
            hourly_price=plan.offer.hourly_price,
        )
        self._instances.append(instance)
        return instance

    def stop_instance(self, instance_ref: str) -> CommandResult:
        self._require_instance_ref(instance_ref)
        self._calls.append(("stop", instance_ref))
        self._instances = [
            replace(instance, status="stopped")
            if instance_ref in {instance.instance_id, instance.name}
            else instance
            for instance in self._instances
        ]
        return CommandResult(("mock", "stop", instance_ref), 0)

    def delete_instance(self, instance_ref: str) -> CommandResult:
        self._require_instance_ref(instance_ref)
        self._calls.append(("delete", instance_ref))
        self._instances = [
            instance
            for instance in self._instances
            if instance_ref not in {instance.instance_id, instance.name}
        ]
        return CommandResult(("mock", "delete", instance_ref), 0)

    @staticmethod
    def _require_instance_ref(instance_ref: str) -> None:
        if not instance_ref or instance_ref.startswith("-"):
            raise ValueError("instance_ref must identify one instance, not an option")
