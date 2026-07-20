"""Fail-closed NVIDIA Brev CLI adapter.

The adapter never provisions by default.  It first inspects the locally
installed CLI's own help, records supported commands/flags as capabilities,
and only constructs argument arrays from that inspected capability set.
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from threading import Thread
from typing import BinaryIO, cast

from cloud_render.models import BudgetLimits, GpuOffer
from cloud_render.providers.base import (
    BenchmarkProvisioningPlan,
    CloudProvider,
    CommandCapability,
    CommandResult,
    CommandRunner,
    ProviderCapabilities,
    ProviderCapabilityError,
    ProviderCommandError,
    ProviderInstance,
    ProviderReadiness,
    ProviderUnavailableError,
    ProvisioningAuthorization,
    ProvisioningDeniedError,
    validate_benchmark_provisioning,
)

_ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_FLAG_PATTERN = re.compile(r"(?<![\w-])(--[a-z0-9][a-z0-9-]*)", re.IGNORECASE)
_NUMBER_PATTERN = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)")
_SAFE_INSTANCE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SAFE_INSTANCE_TYPE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
_KNOWN_COMMANDS = ("search", "ls", "list", "create", "stop", "delete")
_JSON_WRAPPERS = (
    "data",
    "results",
    "offers",
    "instances",
    "instance_types",
    "instanceTypes",
    "gpu_types",
    "gpuTypes",
    "workspaces",
    "items",
)
_IS_WINDOWS = os.name == "nt"
_WINDOWS_CREATE_NEW_PROCESS_GROUP = cast(
    int, getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
)
_WINDOWS_CTRL_BREAK_EVENT = cast(int, getattr(signal, "CTRL_BREAK_EVENT", 1))
_POSIX_SIGTERM = int(signal.SIGTERM)
_POSIX_SIGKILL = cast(int, getattr(signal, "SIGKILL", 9))
_TERMINATION_GRACE_SECONDS = 1.0
_FORCE_KILL_SECONDS = 2.0
_FINAL_REAP_SECONDS = 1.0


def _spawn_process(args: Sequence[str]) -> subprocess.Popen[bytes]:
    if _IS_WINDOWS:
        return subprocess.Popen(  # noqa: S603 - fixed argument arrays, shell disabled
            list(args),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            creationflags=_WINDOWS_CREATE_NEW_PROCESS_GROUP,
            text=False,
        )
    return subprocess.Popen(  # noqa: S603 - fixed argument arrays, shell disabled
        list(args),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        start_new_session=True,
        text=False,
    )


def _wait_for_exit(process: subprocess.Popen[bytes], timeout_seconds: float) -> bool:
    try:
        process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        return False
    return True


def _signal_process(process: subprocess.Popen[bytes], signal_number: int) -> None:
    try:
        process.send_signal(signal_number)
    except (OSError, ValueError):
        pass


def _kill_parent_process(process: subprocess.Popen[bytes]) -> None:
    try:
        process.kill()
    except OSError:
        pass


def _signal_posix_process_group(process: subprocess.Popen[bytes], signal_number: int) -> None:
    killpg = cast(Callable[[int, int], None] | None, getattr(os, "killpg", None))
    if killpg is None:
        _signal_process(process, signal_number)
        return
    try:
        killpg(process.pid, signal_number)
    except ProcessLookupError:
        pass
    except OSError:
        _signal_process(process, signal_number)


def _run_windows_tree_kill(process_id: int) -> None:
    try:
        subprocess.run(  # noqa: S603 - fixed taskkill executable and argument array
            ["taskkill.exe", "/PID", str(process_id), "/T", "/F"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
            check=False,
            timeout=_FORCE_KILL_SECONDS,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        pass


def _terminate_posix_process_tree(process: subprocess.Popen[bytes]) -> None:
    _signal_posix_process_group(process, _POSIX_SIGTERM)
    parent_exited = _wait_for_exit(process, _TERMINATION_GRACE_SECONDS)
    _signal_posix_process_group(process, _POSIX_SIGKILL)
    if parent_exited:
        return
    if _wait_for_exit(process, _FORCE_KILL_SECONDS):
        return
    _kill_parent_process(process)
    _wait_for_exit(process, _FINAL_REAP_SECONDS)


def _terminate_windows_process_tree(process: subprocess.Popen[bytes]) -> None:
    _signal_process(process, _WINDOWS_CTRL_BREAK_EVENT)
    parent_exited = _wait_for_exit(process, _TERMINATION_GRACE_SECONDS)
    _run_windows_tree_kill(process.pid)
    if parent_exited:
        return
    if _wait_for_exit(process, _FORCE_KILL_SECONDS):
        return
    _kill_parent_process(process)
    _wait_for_exit(process, _FINAL_REAP_SECONDS)


def _terminate_process_tree(process: subprocess.Popen[bytes]) -> None:
    if _IS_WINDOWS:
        _terminate_windows_process_tree(process)
    else:
        _terminate_posix_process_tree(process)


@dataclass(frozen=True, slots=True)
class SubprocessRunner:
    """Bounded, non-shell subprocess runner used outside tests."""

    default_timeout_seconds: float = 15.0
    maximum_timeout_seconds: float = 60.0
    max_output_chars: int = 1_048_576

    def __post_init__(self) -> None:
        if not 0 < self.default_timeout_seconds <= self.maximum_timeout_seconds:
            raise ValueError("default subprocess timeout must be positive and bounded")
        if self.maximum_timeout_seconds > 60:
            raise ValueError("provider subprocess timeout may not exceed 60 seconds")
        if self.max_output_chars < 1:
            raise ValueError("max_output_chars must be positive")

    def run(self, args: Sequence[str], *, timeout_seconds: float) -> CommandResult:
        if not args or any(not isinstance(arg, str) or "\x00" in arg for arg in args):
            raise ValueError("provider command must be a non-empty string argument array")
        bounded_timeout = min(
            max(float(timeout_seconds), 0.1), self.maximum_timeout_seconds
        )
        try:
            process = _spawn_process(args)
        except FileNotFoundError as exc:
            raise ProviderUnavailableError("the Brev CLI executable was not found") from exc
        except OSError as exc:
            raise ProviderUnavailableError("the Brev CLI executable could not be started") from exc
        assert process.stdout is not None
        assert process.stderr is not None
        stdout = bytearray()
        stderr = bytearray()
        readers = (
            Thread(
                target=self._drain_bounded,
                args=(cast(BinaryIO, process.stdout), stdout),
                daemon=True,
            ),
            Thread(
                target=self._drain_bounded,
                args=(cast(BinaryIO, process.stderr), stderr),
                daemon=True,
            ),
        )
        for reader in readers:
            reader.start()
        try:
            returncode = process.wait(timeout=bounded_timeout)
        except subprocess.TimeoutExpired as exc:
            _terminate_process_tree(process)
            for reader in readers:
                reader.join(timeout=1)
            raise ProviderCommandError("the Brev CLI command exceeded its bounded timeout") from exc
        for reader in readers:
            reader.join(timeout=1)
        return CommandResult(
            args=tuple(args),
            returncode=returncode,
            stdout=stdout.decode("utf-8", errors="replace"),
            stderr=stderr.decode("utf-8", errors="replace"),
        )

    def _drain_bounded(self, stream: BinaryIO, output: bytearray) -> None:
        truncated = False
        while chunk := stream.read(65_536):
            remaining = self.max_output_chars - len(output)
            if remaining > 0:
                output.extend(chunk[:remaining])
            if len(chunk) > remaining:
                truncated = True
        if truncated:
            marker = b"\n[output truncated]"
            if len(output) + len(marker) <= self.max_output_chars:
                output.extend(marker)


class BrevProvider(CloudProvider):
    """NVIDIA Brev VM provider adapter using the locally authenticated CLI."""

    def __init__(
        self,
        *,
        runner: CommandRunner | None = None,
        executable: Sequence[str] = ("brev",),
        timeout_seconds: float = 15.0,
        allow_provisioning: bool = False,
    ) -> None:
        executable_tuple = tuple(executable)
        if not executable_tuple or any(not item or "\x00" in item for item in executable_tuple):
            raise ValueError("executable must be a non-empty argument prefix")
        if not 0 < timeout_seconds <= 60:
            raise ValueError("timeout_seconds must be greater than zero and at most 60")
        self._runner = runner or SubprocessRunner(default_timeout_seconds=timeout_seconds)
        self._executable = executable_tuple
        self._timeout_seconds = timeout_seconds
        self._allow_provisioning = allow_provisioning
        self._capabilities: ProviderCapabilities | None = None

    @property
    def provider_name(self) -> str:
        return "brev"

    def readiness(self) -> ProviderReadiness:
        try:
            capabilities = self.inspect_capabilities()
        except (
            ProviderUnavailableError,
            ProviderCapabilityError,
            ProviderCommandError,
        ) as exc:
            return ProviderReadiness(
                provider=self.provider_name,
                available=False,
                cli_version=None,
                detail=str(exc),
            )
        return ProviderReadiness(
            provider=self.provider_name,
            available=True,
            cli_version=capabilities.cli_version,
            detail="Brev CLI inspected; provisioning remains disabled by default",
            capabilities=capabilities,
        )

    def inspect_capabilities(self, *, force: bool = False) -> ProviderCapabilities:
        if self._capabilities is not None and not force:
            return self._capabilities

        version_result = self._invoke(("--version",))
        root_help = self._invoke(("--help",))
        documented_commands = _parse_root_commands(root_help.stdout or root_help.stderr)
        commands: list[CommandCapability] = []
        for command_name in _KNOWN_COMMANDS:
            if command_name not in documented_commands:
                continue
            help_result = self._invoke((command_name, "--help"), check=False)
            if help_result.returncode != 0:
                continue
            commands.append(
                CommandCapability(
                    name=command_name,
                    flags=frozenset(
                        _FLAG_PATTERN.findall(help_result.stdout or help_result.stderr)
                    ),
                )
            )

        version = _first_nonempty_line(version_result.stdout or version_result.stderr)
        if not version:
            raise ProviderUnavailableError("the Brev CLI returned no version information")
        capabilities = ProviderCapabilities(
            provider=self.provider_name,
            cli_version=version,
            executable=self._executable,
            commands=tuple(commands),
        )
        self._capabilities = capabilities
        return capabilities

    def discover_gpu_offers(self) -> tuple[GpuOffer, ...]:
        self._require_capability("search", "--json")
        result = self._invoke(("search", "--json"))
        payload = _decode_json_output(result.stdout, context="Brev GPU discovery")
        offers_by_id: dict[str, GpuOffer] = {}
        for record in _records(payload):
            offer = _parse_gpu_offer(record)
            if offer is None:
                continue
            previous = offers_by_id.get(offer.offer_id)
            if previous is None or offer.hourly_price < previous.hourly_price:
                offers_by_id[offer.offer_id] = offer
        return tuple(
            sorted(offers_by_id.values(), key=lambda item: (item.hourly_price, item.offer_id))
        )

    def list_instances(self) -> tuple[ProviderInstance, ...]:
        capabilities = self.inspect_capabilities()
        list_command = "ls" if capabilities.supports("ls", "--json") else "list"
        if not capabilities.supports(list_command, "--json"):
            raise ProviderCapabilityError(
                "the inspected Brev CLI does not document ls/list with --json"
            )
        result = self._invoke((list_command, "--json"))
        payload = _decode_json_output(result.stdout, context="Brev instance listing")
        instances = [
            instance
            for record in _records(payload)
            if (instance := _parse_instance(record)) is not None
        ]
        return tuple(sorted(instances, key=lambda item: (item.name, item.instance_id)))

    def provision_benchmark(
        self,
        plan: BenchmarkProvisioningPlan,
        limits: BudgetLimits,
        authorization: ProvisioningAuthorization,
    ) -> ProviderInstance:
        if not self._allow_provisioning:
            raise ProvisioningDeniedError("Brev provisioning is disabled by default")
        if plan.offer.provider != self.provider_name:
            raise ProvisioningDeniedError("selected offer does not belong to the Brev adapter")
        _validate_instance_name(plan.instance_name)
        _validate_instance_type(plan.offer.offer_id)
        validate_benchmark_provisioning(plan, limits, authorization)
        self._require_capability("create", "--type", "--count", "--detached")

        args = (
            "create",
            plan.instance_name,
            "--type",
            plan.offer.offer_id,
            "--count",
            "1",
            "--detached",
        )
        self._invoke(args)
        return ProviderInstance(
            provider=self.provider_name,
            instance_id=plan.instance_name,
            name=plan.instance_name,
            status="provisioning",
            offer_id=plan.offer.offer_id,
            gpu_name=plan.offer.gpu_name,
            region=plan.offer.region,
            hourly_price=plan.offer.hourly_price,
        )

    def stop_instance(self, instance_ref: str) -> CommandResult:
        _validate_instance_name(instance_ref)
        self._require_capability("stop")
        return self._invoke(("stop", instance_ref))

    def delete_instance(self, instance_ref: str) -> CommandResult:
        _validate_instance_name(instance_ref)
        self._require_capability("delete")
        return self._invoke(("delete", instance_ref))

    def _require_capability(self, command: str, *flags: str) -> None:
        capabilities = self.inspect_capabilities()
        if not capabilities.supports(command, *flags):
            required = " ".join((command, *flags))
            raise ProviderCapabilityError(
                f"the installed Brev CLI help did not document required syntax: {required}"
            )

    def _invoke(
        self, suffix: Sequence[str], *, check: bool = True
    ) -> CommandResult:
        args = (*self._executable, *suffix)
        result = self._runner.run(args, timeout_seconds=self._timeout_seconds)
        if check and result.returncode != 0:
            command_name = suffix[0] if suffix else "brev"
            raise ProviderCommandError(
                f"Brev command {command_name!r} failed with exit code {result.returncode}"
            )
        return result


def _parse_root_commands(help_text: str) -> frozenset[str]:
    commands: set[str] = set()
    clean_help = _ANSI_ESCAPE.sub("", help_text)
    for line in clean_help.splitlines():
        stripped = line.strip()
        for candidate in _KNOWN_COMMANDS:
            if re.match(rf"^(?:brev\s+)?{re.escape(candidate)}(?:\s|,|$)", stripped):
                commands.add(candidate)
    return frozenset(commands)


def _first_nonempty_line(value: str) -> str:
    for line in _ANSI_ESCAPE.sub("", value).splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:256]
    return ""


def _decode_json_output(value: str, *, context: str) -> object:
    clean_value = _ANSI_ESCAPE.sub("", value).lstrip("\ufeff\r\n \t")
    if not clean_value:
        raise ProviderCommandError(f"{context} returned empty JSON output")
    try:
        return json.loads(clean_value)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        for index, character in enumerate(clean_value):
            if character not in "[{":
                continue
            try:
                payload, _ = decoder.raw_decode(clean_value[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(payload, (list, dict)):
                return payload
    raise ProviderCommandError(f"{context} returned invalid JSON")


def _records(payload: object) -> list[Mapping[str, object]]:
    if isinstance(payload, list):
        return [cast(Mapping[str, object], item) for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []

    mapping = cast(Mapping[str, object], payload)
    for wrapper in _JSON_WRAPPERS:
        wrapped = _lookup(mapping, wrapper)
        if isinstance(wrapped, (list, dict)):
            nested = _records(wrapped)
            if nested:
                return nested

    marker_names = {
        "id",
        "name",
        "type",
        "offerid",
        "instancetype",
        "gpuname",
        "status",
    }
    if marker_names.intersection(_normalized_mapping(mapping)):
        return [mapping]

    records: list[Mapping[str, object]] = []
    for key, item in mapping.items():
        if isinstance(item, dict):
            record = dict(cast(Mapping[str, object], item))
            record.setdefault("id", str(key))
            records.append(record)
    return records


def _parse_gpu_offer(record: Mapping[str, object]) -> GpuOffer | None:
    gpu_value = _lookup(record, "gpu")
    gpu_mapping = cast(Mapping[str, object], gpu_value) if isinstance(gpu_value, dict) else {}
    hourly_price = _hourly_price(record)
    if hourly_price is None or hourly_price < Decimal("0"):
        return None

    offer_id = _string_value(
        _lookup(record, "offer_id", "instance_type", "machine_type", "type", "id")
    )
    if not offer_id:
        return None
    gpu_name = _string_value(
        _lookup(record, "gpu_name", "gpu_type", "accelerator", "accelerator_name")
    ) or _string_value(_lookup(gpu_mapping, "name", "type", "model"))
    if not gpu_name and isinstance(gpu_value, str):
        gpu_name = gpu_value.strip()
    if not gpu_name:
        gpu_name = "unknown"

    cloud_provider = _string_value(_lookup(record, "provider", "cloud_provider", "cloud"))
    region = _string_value(_lookup(record, "region", "location", "zone"))
    vram_value = _lookup(record, "vram_gib", "vram_gb", "vram", "gpu_memory")
    if vram_value is None:
        vram_value = _lookup(gpu_mapping, "vram_gib", "vram_gb", "vram", "memory")
    vram_gib = _decimal_value(vram_value)
    if vram_gib is not None and vram_gib <= Decimal("0"):
        vram_gib = None
    available = _availability(record)

    metadata: dict[str, object] = {}
    if cloud_provider:
        metadata["cloud_provider"] = cloud_provider
    for output_name, aliases in (
        ("gpu_count", ("gpu_count", "count", "num_gpus")),
        ("total_vram_gib", ("total_vram_gib", "total_vram_gb", "total_vram")),
        ("boot_minutes", ("boot_minutes", "boot_time", "estimated_boot_minutes")),
        ("stoppable", ("stoppable",)),
        ("rebootable", ("rebootable",)),
        ("flex_ports", ("flex_ports", "flexible_ports")),
    ):
        item = _lookup(record, *aliases)
        if isinstance(item, (str, int, float, bool)):
            metadata[output_name] = item

    return GpuOffer(
        provider="brev",
        offer_id=offer_id,
        gpu_name=gpu_name,
        region=region or "unknown",
        hourly_price=hourly_price,
        vram_gib=vram_gib,
        available=available,
        metadata=metadata,
    )


def _parse_instance(record: Mapping[str, object]) -> ProviderInstance | None:
    instance_id = _string_value(
        _lookup(record, "instance_id", "workspace_id", "id", "uuid")
    )
    name = _string_value(_lookup(record, "instance_name", "workspace_name", "name"))
    if not instance_id and not name:
        return None
    instance_id = instance_id or cast(str, name)
    name = name or instance_id
    status = _string_value(_lookup(record, "status", "state", "phase")) or "unknown"
    offer_id = _string_value(
        _lookup(record, "offer_id", "instance_type", "machine_type", "type")
    )
    gpu_name = _string_value(_lookup(record, "gpu_name", "gpu_type", "gpu"))
    region = _string_value(_lookup(record, "region", "location", "zone"))
    hourly_price = _hourly_price(record)
    return ProviderInstance(
        provider="brev",
        instance_id=instance_id,
        name=name,
        status=status.lower(),
        offer_id=offer_id,
        gpu_name=gpu_name,
        region=region,
        hourly_price=hourly_price,
    )


def _normalized_mapping(mapping: Mapping[str, object]) -> dict[str, object]:
    return {re.sub(r"[^a-z0-9]", "", str(key).lower()): value for key, value in mapping.items()}


def _lookup(mapping: Mapping[str, object], *aliases: str) -> object | None:
    normalized = _normalized_mapping(mapping)
    for alias in aliases:
        key = re.sub(r"[^a-z0-9]", "", alias.lower())
        if key in normalized:
            return normalized[key]
    return None


def _string_value(value: object) -> str | None:
    if value is None or isinstance(value, (dict, list, bool)):
        return None
    text = str(value).strip()
    return text or None


def _decimal_value(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    if isinstance(value, (int, float)):
        parsed = Decimal(str(value))
        return parsed if parsed.is_finite() else None
    match = _NUMBER_PATTERN.search(str(value).replace(",", ""))
    if match is None:
        return None
    try:
        parsed = Decimal(match.group(0))
        return parsed if parsed.is_finite() else None
    except InvalidOperation:
        return None


def _hourly_price(record: Mapping[str, object]) -> Decimal | None:
    value = _lookup(
        record,
        "hourly_price",
        "hourlyPrice",
        "price_per_hour",
        "cost_per_hour",
        "price",
        "pricing",
    )
    if isinstance(value, dict):
        value = _lookup(
            cast(Mapping[str, object], value),
            "hourly",
            "hourly_price",
            "per_hour",
            "usd_per_hour",
            "amount",
            "value",
        )
    return _decimal_value(value)


def _availability(record: Mapping[str, object]) -> bool:
    value = _lookup(record, "available", "availability", "capacity", "status")
    if value is None:
        # `brev search --json` is itself an available-instance-type query.
        return True
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value > 0
    normalized = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    return normalized not in {
        "0",
        "false",
        "no",
        "none",
        "unavailable",
        "sold_out",
        "out_of_capacity",
        "disabled",
    }


def _validate_instance_name(value: str) -> None:
    if not _SAFE_INSTANCE_NAME.fullmatch(value):
        raise ValueError("instance name/reference contains unsupported characters")


def _validate_instance_type(value: str) -> None:
    if not _SAFE_INSTANCE_TYPE.fullmatch(value):
        raise ValueError("Brev instance type contains unsupported characters")
