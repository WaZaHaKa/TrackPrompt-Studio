from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import replace
from decimal import Decimal

import pytest

from cloud_render.models import BudgetLimits, GpuOffer, IdentityBundle
from cloud_render.providers import (
    BenchmarkProvisioningPlan,
    BrevProvider,
    BudgetGuardError,
    CommandResult,
    MockProvider,
    ProviderCapabilityError,
    ProviderCommandError,
    ProviderInstance,
    ProvisioningAuthorization,
    ProvisioningDeniedError,
    benchmark_authorization_token,
    validate_benchmark_provisioning,
)

ROOT_HELP = """
Available Commands:
  search      Search GPU instance types
  ls          List instances
  create      Create an instance
  stop        Stop an instance
  delete      Delete an instance
"""


class FakeRunner:
    """Exact argument-array fake; it never starts a process or contacts Brev."""

    def __init__(self, responses: dict[tuple[str, ...], CommandResult]) -> None:
        self.responses = responses
        self.calls: list[tuple[tuple[str, ...], float]] = []

    def run(self, args: Sequence[str], *, timeout_seconds: float) -> CommandResult:
        key = tuple(args)
        self.calls.append((key, timeout_seconds))
        if key not in self.responses:
            raise AssertionError(f"unexpected command: {key!r}")
        return self.responses[key]


def result(args: tuple[str, ...], stdout: str = "", returncode: int = 0) -> CommandResult:
    return CommandResult(args=args, returncode=returncode, stdout=stdout)


def cli_responses(
    *, create_help: str = "--type --count --detached", search_help: str = "--json"
) -> dict[tuple[str, ...], CommandResult]:
    responses: dict[tuple[str, ...], CommandResult] = {
        ("brev", "--version"): result(("brev", "--version"), "brev version 0.11.0\n"),
        ("brev", "--help"): result(("brev", "--help"), ROOT_HELP),
        ("brev", "search", "--help"): result(
            ("brev", "search", "--help"), search_help
        ),
        ("brev", "ls", "--help"): result(("brev", "ls", "--help"), "--json"),
        ("brev", "create", "--help"): result(
            ("brev", "create", "--help"), create_help
        ),
        ("brev", "stop", "--help"): result(("brev", "stop", "--help"), "stop NAME"),
        ("brev", "delete", "--help"): result(
            ("brev", "delete", "--help"), "delete NAME"
        ),
    }
    return responses


def make_offer(
    *, price: str = "1.25", available: bool = True, provider: str = "brev"
) -> GpuOffer:
    return GpuOffer(
        provider=provider,
        offer_id="nebius.l40sx1.pcie",
        gpu_name="L40S",
        region="eu-west",
        hourly_price=Decimal(price),
        vram_gib=Decimal("48"),
        available=available,
    )


def make_plan(
    *,
    offer: GpuOffer | None = None,
    budget: str = "25",
    worker_count: int = 1,
) -> BenchmarkProvisioningPlan:
    return BenchmarkProvisioningPlan(
        instance_name="wzhk-benchmark-01",
        offer=offer or make_offer(),
        identities=IdentityBundle(
            scene_sha256="A" * 64,
            profile_sha256="B" * 64,
            package_sha256="C" * 64,
        ),
        budget_ceiling=Decimal(budget),
        worker_count=worker_count,
    )


def make_limits(
    *, hourly: str = "2", workers: int = 4, budget: str = "30"
) -> BudgetLimits:
    return BudgetLimits(
        max_hourly_price_per_worker=Decimal(hourly),
        max_worker_count=workers,
        total_budget_ceiling=Decimal(budget),
    )


def authorized(plan: BenchmarkProvisioningPlan) -> ProvisioningAuthorization:
    return ProvisioningAuthorization(
        token=benchmark_authorization_token(plan),
        plan_locked=True,
        final_confirmed=True,
    )


def test_inspection_gpu_discovery_and_instance_listing_use_only_json_arrays() -> None:
    responses = cli_responses()
    responses[("brev", "search", "--json")] = result(
        ("brev", "search", "--json"),
        "\x1b[36mnotice\x1b[0m\n"
        + json.dumps(
            {
                "results": [
                    {
                        "type": "aws.g6e.xlarge",
                        "gpu_name": "L40S",
                        "price": "$1.50/hr",
                        "provider": "aws",
                        "region": "us-east-1",
                        "vram": "48 GB",
                        "available": True,
                        "boot_time": "4m",
                    },
                    {
                        "instanceType": "nebius.l40sx1.pcie",
                        "gpu": {"model": "NVIDIA L40S", "memory": 48},
                        "pricing": "ignored wrapper",
                        "hourlyPrice": 1.25,
                        "cloudProvider": "nebius",
                        "location": "eu-west",
                    },
                ]
            }
        ),
    )
    responses[("brev", "ls", "--json")] = result(
        ("brev", "ls", "--json"),
        json.dumps(
            {
                "workspaces": [
                    {
                        "workspaceId": "instance-123",
                        "workspaceName": "wzhk-benchmark-01",
                        "state": "RUNNING",
                        "instanceType": "nebius.l40sx1.pcie",
                        "gpuName": "L40S",
                        "hourlyPrice": "$1.25",
                    }
                ]
            }
        ),
    )
    runner = FakeRunner(responses)
    provider = BrevProvider(runner=runner)

    capabilities = provider.inspect_capabilities()
    offers = provider.discover_gpu_offers()
    instances = provider.list_instances()

    assert capabilities.cli_version == "brev version 0.11.0"
    assert capabilities.supports("search", "--json")
    assert [offer.offer_id for offer in offers] == [
        "nebius.l40sx1.pcie",
        "aws.g6e.xlarge",
    ]
    assert offers[0].provider == "brev"
    assert offers[0].metadata["cloud_provider"] == "nebius"
    assert offers[0].vram_gib == Decimal("48")
    assert instances == (
        ProviderInstance(
            provider="brev",
            instance_id="instance-123",
            name="wzhk-benchmark-01",
            status="running",
            offer_id="nebius.l40sx1.pcie",
            gpu_name="L40S",
            hourly_price=Decimal("1.25"),
        ),
    )
    assert (("brev", "search", "--json"), 15.0) in runner.calls
    assert (("brev", "ls", "--json"), 15.0) in runner.calls
    assert sum(args == ("brev", "--version") for args, _ in runner.calls) == 1


def test_discovery_fails_closed_when_local_help_does_not_document_json() -> None:
    runner = FakeRunner(cli_responses(search_help="--wide --sort"))
    provider = BrevProvider(runner=runner)

    with pytest.raises(ProviderCapabilityError, match="did not document"):
        provider.discover_gpu_offers()

    assert all(args != ("brev", "search", "--json") for args, _ in runner.calls)


def test_invalid_discovery_json_is_a_safe_provider_error() -> None:
    responses = cli_responses()
    responses[("brev", "search", "--json")] = result(
        ("brev", "search", "--json"), "not-json token=do-not-repeat"
    )
    provider = BrevProvider(runner=FakeRunner(responses))

    with pytest.raises(ProviderCommandError, match="invalid JSON") as caught:
        provider.discover_gpu_offers()

    assert "do-not-repeat" not in str(caught.value)


def test_exact_authorization_token_is_bound_to_package_profile_and_budget() -> None:
    plan = make_plan(budget="25")

    assert benchmark_authorization_token(plan) == (
        "AUTHORIZE BREV BENCHMARK: CCCCCCCCCCCC | BBBBBBBBBBBB | MAX $25.00"
    )


def test_authorized_budget_requires_exact_cent_precision() -> None:
    with pytest.raises(ValueError, match="whole-cent precision"):
        make_plan(budget="25.001")


@pytest.mark.parametrize(
    ("plan", "limits", "authorization", "message"),
    [
        (
            make_plan(worker_count=2),
            make_limits(),
            authorized(make_plan(worker_count=2)),
            "exactly one worker",
        ),
        (
            make_plan(offer=make_offer(price="2.01")),
            make_limits(hourly="2"),
            authorized(make_plan(offer=make_offer(price="2.01"))),
            "hourly price",
        ),
        (
            make_plan(budget="30.01"),
            make_limits(budget="30"),
            authorized(make_plan(budget="30.01")),
            "budget",
        ),
    ],
)
def test_benchmark_budget_guards(
    plan: BenchmarkProvisioningPlan,
    limits: BudgetLimits,
    authorization: ProvisioningAuthorization,
    message: str,
) -> None:
    with pytest.raises(BudgetGuardError, match=message):
        validate_benchmark_provisioning(plan, limits, authorization)


def test_all_confirmation_gates_are_required() -> None:
    plan = make_plan()
    limits = make_limits()

    with pytest.raises(ProvisioningDeniedError, match="first confirmation"):
        validate_benchmark_provisioning(
            plan,
            limits,
            ProvisioningAuthorization(
                token=benchmark_authorization_token(plan),
                plan_locked=False,
                final_confirmed=True,
            ),
        )
    with pytest.raises(ProvisioningDeniedError, match="final confirmation"):
        validate_benchmark_provisioning(
            plan,
            limits,
            ProvisioningAuthorization(
                token=benchmark_authorization_token(plan),
                plan_locked=True,
                final_confirmed=False,
            ),
        )
    with pytest.raises(ProvisioningDeniedError, match="token"):
        validate_benchmark_provisioning(
            plan,
            limits,
            ProvisioningAuthorization(
                token=benchmark_authorization_token(plan) + " ",
                plan_locked=True,
                final_confirmed=True,
            ),
        )


def test_brev_provisioning_is_disabled_without_touching_the_runner() -> None:
    runner = FakeRunner({})
    provider = BrevProvider(runner=runner)
    plan = make_plan()

    with pytest.raises(ProvisioningDeniedError, match="disabled by default"):
        provider.provision_benchmark(plan, make_limits(), authorized(plan))

    assert runner.calls == []


def test_authorized_benchmark_constructs_exactly_one_documented_worker() -> None:
    responses = cli_responses()
    create_args = (
        "brev",
        "create",
        "wzhk-benchmark-01",
        "--type",
        "nebius.l40sx1.pcie",
        "--count",
        "1",
        "--detached",
    )
    responses[create_args] = result(create_args, "wzhk-benchmark-01\n")
    runner = FakeRunner(responses)
    provider = BrevProvider(runner=runner, allow_provisioning=True)
    plan = make_plan()

    instance = provider.provision_benchmark(plan, make_limits(), authorized(plan))

    assert instance.name == "wzhk-benchmark-01"
    assert runner.calls[-1][0] == create_args
    assert "--count" in create_args
    assert create_args[create_args.index("--count") + 1] == "1"


def test_missing_create_flag_fails_before_any_billable_command() -> None:
    runner = FakeRunner(cli_responses(create_help="--type --detached"))
    provider = BrevProvider(runner=runner, allow_provisioning=True)
    plan = make_plan()

    with pytest.raises(ProviderCapabilityError, match="--count"):
        provider.provision_benchmark(plan, make_limits(), authorized(plan))

    assert all(args[1:2] != ("create",) or args[-1:] == ("--help",) for args, _ in runner.calls)


def test_teardown_stops_then_deletes_only_the_named_instance() -> None:
    responses = cli_responses()
    responses[("brev", "stop", "wzhk-benchmark-01")] = result(
        ("brev", "stop", "wzhk-benchmark-01")
    )
    responses[("brev", "delete", "wzhk-benchmark-01")] = result(
        ("brev", "delete", "wzhk-benchmark-01")
    )
    runner = FakeRunner(responses)
    provider = BrevProvider(runner=runner)

    stopped, deleted = provider.teardown_instance("wzhk-benchmark-01")

    assert stopped.args == ("brev", "stop", "wzhk-benchmark-01")
    assert deleted.args == ("brev", "delete", "wzhk-benchmark-01")
    lifecycle_calls = [
        args for args, _ in runner.calls if len(args) > 1 and args[1] in {"stop", "delete"}
    ]
    assert lifecycle_calls[-2:] == [stopped.args, deleted.args]
    assert all("--all" not in args for args in lifecycle_calls)


def test_mock_provider_remains_in_memory_and_provisioning_is_opt_in() -> None:
    plan = make_plan(offer=make_offer(provider="mock"))
    provider = MockProvider(offers=[plan.offer])

    assert provider.discover_gpu_offers() == (plan.offer,)
    with pytest.raises(ProvisioningDeniedError, match="disabled by default"):
        provider.provision_benchmark(plan, make_limits(), authorized(plan))

    enabled = MockProvider(offers=[plan.offer], allow_provisioning=True)
    instance = enabled.provision_benchmark(plan, make_limits(), authorized(plan))
    enabled.teardown_instance(instance.instance_id)

    assert enabled.list_instances() == ()
    assert ("stop", instance.instance_id) in enabled.calls
    assert ("delete", instance.instance_id) in enabled.calls


def test_unavailable_offer_is_rejected_before_provisioning() -> None:
    plan = make_plan(offer=replace(make_offer(), available=False))

    with pytest.raises(ProvisioningDeniedError, match="not currently available"):
        validate_benchmark_provisioning(plan, make_limits(), authorized(plan))
