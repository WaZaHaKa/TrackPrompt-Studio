from __future__ import annotations

import argparse
import json
import os
import uuid
from dataclasses import asdict
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Sequence

from .costs import rank_benchmarks
from .frame_validation import validate_image
from .imports import import_quarantined_return
from .manifests import (
    CHUNK_OUTPUT_KIND,
    PACKAGE_KIND,
    SCHEMA_VERSION,
    canonical_json_bytes,
    seal_manifest,
    validate_package_manifest,
    validate_sealed_manifest,
)
from .media import plan_cloud_video_only_encode, plan_local_audio_mux
from .models import (
    BenchmarkResult,
    BudgetLimits,
    FrameRange,
    GpuOffer,
    IdentityBundle,
    WorkerKind,
)
from .providers.base import (
    BenchmarkProvisioningPlan,
    ProviderError,
    ProvisioningAuthorization,
    benchmark_authorization_token,
)
from .providers.brev import BrevProvider
from .package_bridge import prepare_cloud_manifest
from .scheduler import SchedulerError, SqliteScheduler
from .worker.render_worker import run as run_worker

RESULT_KIND = "trackprompt-cloud-cli-result"


def _decimal(value: str) -> Decimal:
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError("value must be a decimal") from exc
    if not result.is_finite():
        raise argparse.ArgumentTypeError("value must be finite")
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Offline-first TrackPrompt cloud render control")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("readiness", help="Report package capabilities without invoking any provider")

    brev_ready = sub.add_parser("brev-readiness", help="Inspect only the local Brev CLI version/help")
    brev_ready.add_argument("--executable", default="brev")
    brev_discover = sub.add_parser("brev-discover", help="Query offers through an inspected Brev CLI")
    brev_discover.add_argument("--executable", default="brev")
    brev_list = sub.add_parser("brev-list", help="List instances through an inspected Brev CLI")
    brev_list.add_argument("--executable", default="brev")

    token = sub.add_parser("authorization-token", help="Build the exact bounded benchmark token")
    token.add_argument("--scene-sha", required=True)
    token.add_argument("--profile-sha", required=True)
    token.add_argument("--package-sha", required=True)
    token.add_argument("--max-budget", required=True, type=_decimal)

    validate = sub.add_parser("validate-manifest")
    validate.add_argument("--path", required=True)
    seal = sub.add_parser("seal-manifest")
    seal.add_argument("--input", required=True)
    seal.add_argument("--output", required=True)
    prepare = sub.add_parser(
        "prepare-manifest",
        help="Bridge an established sanitized remote package into the cloud contract",
    )
    prepare.add_argument("--remote-package", required=True)
    prepare.add_argument("--output", required=True)

    status = sub.add_parser("scheduler-status")
    status.add_argument("--database", required=True)
    status.add_argument("--job-id", required=True)
    scheduler_init = sub.add_parser("scheduler-init")
    scheduler_init.add_argument("--database", required=True)
    scheduler_init.add_argument("--job-id", required=True)
    scheduler_init.add_argument("--package-manifest")
    scheduler_init.add_argument("--scene-sha")
    scheduler_init.add_argument("--profile-sha")
    scheduler_init.add_argument("--package-sha")
    scheduler_init.add_argument("--frame-start", type=int)
    scheduler_init.add_argument("--frame-end", type=int)
    scheduler_init.add_argument("--frames-per-chunk", required=True, type=int)
    scheduler_init.add_argument("--max-attempts", type=int, default=3)
    scheduler_claim = sub.add_parser("scheduler-claim")
    scheduler_claim.add_argument("--database", required=True)
    scheduler_claim.add_argument("--job-id", required=True)
    scheduler_claim.add_argument("--worker-id", required=True)
    scheduler_claim.add_argument(
        "--worker-kind",
        choices=[item.value for item in WorkerKind],
        default=WorkerKind.CLOUD.value,
    )
    scheduler_claim.add_argument("--lease-seconds", type=int, default=900)
    scheduler_cancel = sub.add_parser("scheduler-cancel")
    scheduler_cancel.add_argument("--database", required=True)
    scheduler_cancel.add_argument("--job-id", required=True)

    tournament = sub.add_parser("tournament-rank")
    tournament.add_argument("--input", required=True)

    encode = sub.add_parser("encode-plan")
    encode.add_argument("--ffmpeg", default="ffmpeg")
    encode.add_argument("--frame-pattern", required=True)
    encode.add_argument("--frame-start", required=True, type=int)
    encode.add_argument("--frame-end", required=True, type=int)
    verified = encode.add_mutually_exclusive_group(required=True)
    verified.add_argument("--verified-frames")
    verified.add_argument(
        "--verified-frames-file",
        help="JSON array or comma/newline-delimited verified frame numbers",
    )
    encode.add_argument("--fps", required=True, type=int)
    encode.add_argument("--output", required=True)
    mux = sub.add_parser("mux-plan")
    mux.add_argument("--ffmpeg", default="ffmpeg")
    mux.add_argument("--video-only", required=True)
    mux.add_argument("--private-audio", required=True)
    mux.add_argument("--output", required=True)

    importing = sub.add_parser("import-return")
    importing.add_argument("--returned", required=True)
    importing.add_argument("--quarantine-root", required=True)
    importing.add_argument("--output-frames", required=True)
    importing.add_argument("--manifest", required=True)
    importing.add_argument("--package-manifest")
    importing.add_argument("--scene-sha")
    importing.add_argument("--profile-sha")
    importing.add_argument("--package-sha")
    importing.add_argument("--frame-start", type=int)
    importing.add_argument("--frame-end", type=int)
    importing.add_argument("--extension")

    mock_worker = sub.add_parser("mock-worker")
    mock_worker.add_argument("--package-manifest", required=True)
    mock_worker.add_argument("--database", required=True)
    mock_worker.add_argument("--storage-root", required=True)
    mock_worker.add_argument("--job-id", required=True)
    mock_worker.add_argument("--worker-id", required=True)
    mock_worker.add_argument("--run-until-idle", action="store_true")
    mock_worker.add_argument(
        "--no-work-timeout-seconds",
        type=float,
        default=0.0,
        help="Offline mock idle grace; bounded to 0-60 seconds",
    )

    provision = sub.add_parser("brev-provision-benchmark")
    provision.add_argument("--executable", default="brev")
    provision.add_argument("--enable-provisioning", action="store_true")
    provision.add_argument("--instance-name", required=True)
    provision.add_argument("--offer-id", required=True)
    provision.add_argument("--gpu-name", required=True)
    provision.add_argument("--region", required=True)
    provision.add_argument("--hourly-price", required=True, type=_decimal)
    provision.add_argument("--vram-gib", type=_decimal)
    provision.add_argument("--scene-sha", required=True)
    provision.add_argument("--profile-sha", required=True)
    provision.add_argument("--package-sha", required=True)
    provision.add_argument("--max-budget", required=True, type=_decimal)
    provision.add_argument("--max-hourly-price", required=True, type=_decimal)
    provision.add_argument("--max-workers", required=True, type=int)
    provision.add_argument("--total-budget", required=True, type=_decimal)
    provision.add_argument("--authorization-token", required=True)
    provision.add_argument("--plan-locked", action="store_true")
    provision.add_argument("--final-confirmed", action="store_true")

    teardown = sub.add_parser("brev-teardown")
    teardown.add_argument("--executable", default="brev")
    teardown.add_argument("--instance-ref", required=True)
    teardown.add_argument("--confirm-stop", required=True)
    teardown.add_argument("--confirm-delete", required=True)
    return parser


def _envelope(command: str, *, data: Any | None = None) -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": RESULT_KIND,
        "ok": True,
        "command": command,
        "data": data if data is not None else {},
    }


def _provider(executable: str, *, allow_provisioning: bool = False) -> BrevProvider:
    return BrevProvider(executable=(executable,), allow_provisioning=allow_provisioning)


def _identities(args: argparse.Namespace) -> IdentityBundle:
    return IdentityBundle(args.scene_sha, args.profile_sha, args.package_sha)


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object")
    return payload


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    output = path.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_bytes(
            json.dumps(payload, indent=2, sort_keys=True).encode("utf-8") + b"\n"
        )
        os.replace(temporary, output)
    finally:
        if temporary.exists():
            temporary.unlink()


def _verified_frames(args: argparse.Namespace) -> list[int]:
    if args.verified_frames is not None:
        raw: object = args.verified_frames
    else:
        path = Path(args.verified_frames_file)
        if path.stat().st_size > 2 * 1024 * 1024:
            raise ValueError("verified frame file exceeds the 2 MiB control-plane bound")
        text = path.read_text(encoding="utf-8-sig")
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            raw = text
        else:
            raw = parsed
    if isinstance(raw, list):
        if any(isinstance(item, bool) or not isinstance(item, int) for item in raw):
            raise ValueError("verified frame JSON must be an integer array")
        return list(raw)
    if isinstance(raw, str):
        tokens = raw.replace("\r", "\n").replace(",", "\n").splitlines()
        try:
            return [int(item.strip()) for item in tokens if item.strip()]
        except ValueError as exc:
            raise ValueError("verified frames must contain only integers") from exc
    raise ValueError("verified frame file must be a JSON array or delimited text")


def _returned_chunk_range(payload: dict[str, Any]) -> FrameRange:
    sealed = validate_sealed_manifest(payload, expected_kind=CHUNK_OUTPUT_KIND)
    frames = sealed.get("frames")
    if not isinstance(frames, list) or not frames:
        raise ValueError("returned chunk manifest must contain a non-empty frames array")
    frame_numbers: list[int] = []
    for item in frames:
        if not isinstance(item, dict):
            raise ValueError("returned chunk frame entries must be objects")
        frame = item.get("frame")
        if not isinstance(frame, int) or isinstance(frame, bool) or frame < 1:
            raise ValueError("returned chunk frame numbers must be positive integers")
        frame_numbers.append(frame)
    if len(frame_numbers) != len(set(frame_numbers)):
        raise ValueError("returned chunk frame numbers must not contain duplicates")
    frame_range = FrameRange(min(frame_numbers), max(frame_numbers))
    if set(frame_numbers) != set(range(frame_range.start, frame_range.end + 1)):
        raise ValueError("returned chunk frame numbers must form one contiguous range")
    return frame_range


def _run(args: argparse.Namespace) -> dict[str, Any]:
    if args.command == "readiness":
        return _envelope(
            args.command,
            data={
                "packageReady": True,
                "provisioningEnabled": False,
                "networkContacted": False,
                "providerProcessInvoked": False,
                "capabilities": [
                    "canonical-manifests",
                    "sanitized-package-bridge",
                    "dynamic-sqlite-leases",
                    "filesystem-storage",
                    "s3-compatible-storage",
                    "brev-cli-adapter",
                    "mock-worker",
                    "bounded-blender-worker",
                    "video-only-cloud-encode-plan",
                    "local-audio-mux-plan",
                ],
            },
        )
    if args.command == "brev-readiness":
        readiness = _provider(args.executable).readiness()
        return _envelope(args.command, data=asdict(readiness))
    if args.command == "brev-discover":
        offers = _provider(args.executable).discover_gpu_offers()
        return _envelope(args.command, data={"offers": [asdict(item) for item in offers]})
    if args.command == "brev-list":
        instances = _provider(args.executable).list_instances()
        return _envelope(
            args.command,
            data={"instances": [asdict(item) for item in instances]},
        )
    if args.command == "authorization-token":
        identities = _identities(args)
        placeholder = GpuOffer("brev", "pending", "pending", "pending", Decimal("0"))
        provisioning_plan = BenchmarkProvisioningPlan(
            "pending", placeholder, identities, args.max_budget
        )
        return _envelope(
            args.command,
            data={
                "authorizationToken": benchmark_authorization_token(
                    provisioning_plan
                )
            },
        )
    if args.command == "validate-manifest":
        payload = _read_json_object(Path(args.path), "cloud package manifest")
        identities, frame_range = validate_package_manifest(payload)
        return _envelope(
            args.command,
            data={
                "kind": PACKAGE_KIND,
                "identities": asdict(identities),
                "frameRange": asdict(frame_range),
            },
        )
    if args.command == "seal-manifest":
        payload = _read_json_object(Path(args.input), "manifest input")
        sealed = seal_manifest(payload)
        output = Path(args.output).resolve()
        _write_json_atomic(output, sealed)
        return _envelope(
            args.command,
            data={"output": str(output), "manifestSha256": sealed["manifestSha256"]},
        )
    if args.command == "prepare-manifest":
        remote_package = Path(args.remote_package).resolve(strict=True)
        output = Path(args.output).resolve()
        if output == remote_package or remote_package in output.parents:
            raise ValueError(
                "cloud manifest output must be outside the immutable remote package"
            )
        sealed = prepare_cloud_manifest(remote_package)
        _write_json_atomic(output, sealed)
        identities, frame_range = validate_package_manifest(sealed)
        return _envelope(
            args.command,
            data={
                "output": str(output),
                "manifestSha256": sealed["manifestSha256"],
                "identities": asdict(identities),
                "frameRange": asdict(frame_range),
                "sourcePackage": sealed["sourcePackage"],
                "sourcePackageValidated": True,
            },
        )
    if args.command == "scheduler-status":
        with SqliteScheduler(Path(args.database)) as scheduler:
            status = scheduler.status(args.job_id)
        return _envelope(args.command, data=asdict(status))
    if args.command == "scheduler-init":
        manifest_sha256: str | None = None
        if args.package_manifest is not None:
            if any(
                value is not None
                for value in (
                    args.scene_sha,
                    args.profile_sha,
                    args.package_sha,
                    args.frame_start,
                    args.frame_end,
                )
            ):
                raise ValueError(
                    "package-manifest cannot be mixed with manual identity or frame fields"
                )
            package_payload = _read_json_object(
                Path(args.package_manifest), "cloud package manifest"
            )
            identities, frame_range = validate_package_manifest(package_payload)
            manifest_sha256 = str(package_payload["manifestSha256"])
        else:
            if any(
                value is None
                for value in (
                    args.scene_sha,
                    args.profile_sha,
                    args.package_sha,
                    args.frame_start,
                    args.frame_end,
                )
            ):
                raise ValueError(
                    "scheduler-init requires package-manifest or all manual identity/frame fields"
                )
            identities = _identities(args)
            frame_range = FrameRange(args.frame_start, args.frame_end)
        with SqliteScheduler(Path(args.database)) as scheduler:
            chunks = scheduler.create_job(
                args.job_id,
                identities,
                frame_range,
                frames_per_chunk=args.frames_per_chunk,
                max_attempts=args.max_attempts,
                manifest_sha256=manifest_sha256,
            )
        return _envelope(
            args.command,
            data={
                "jobId": args.job_id,
                "chunkCount": chunks,
                "manifestSha256": manifest_sha256,
            },
        )
    if args.command == "scheduler-claim":
        with SqliteScheduler(Path(args.database)) as scheduler:
            lease = scheduler.claim_next(
                args.job_id,
                args.worker_id,
                WorkerKind(args.worker_kind),
                lease_seconds=args.lease_seconds,
            )
        return _envelope(
            args.command,
            data={"lease": asdict(lease) if lease is not None else None},
        )
    if args.command == "scheduler-cancel":
        with SqliteScheduler(Path(args.database)) as scheduler:
            scheduler.cancel_job(args.job_id)
        return _envelope(args.command, data={"jobId": args.job_id, "cancelled": True})
    if args.command == "tournament-rank":
        payload = json.loads(Path(args.input).read_text(encoding="utf-8-sig"))
        raw_results = payload.get("benchmarks") if isinstance(payload, dict) else None
        if not isinstance(raw_results, list):
            raise ValueError("tournament input must contain a benchmarks array")
        results: list[BenchmarkResult] = []
        for raw in raw_results:
            if not isinstance(raw, dict):
                raise ValueError("benchmark entries must be objects")
            offer = GpuOffer(
                str(raw["provider"]),
                str(raw["offerId"]),
                str(raw["gpuName"]),
                str(raw.get("region", "unknown")),
                Decimal(str(raw["hourlyPrice"])),
                Decimal(str(raw["vramGiB"])) if raw.get("vramGiB") is not None else None,
                bool(raw.get("available", True)),
            )
            results.append(
                BenchmarkResult(
                    offer,
                    Decimal(str(raw["secondsPerFrame"])),
                    Decimal(str(raw["p90SecondsPerFrame"])),
                    int(raw["validatedFrames"]),
                    visual_passed=bool(raw.get("visualPassed", False)),
                    technical_passed=bool(raw.get("technicalPassed", False)),
                    software_rendering=bool(raw.get("softwareRendering", False)),
                    stable=bool(raw.get("stable", True)),
                )
            )
        ranked = rank_benchmarks(results)
        return _envelope(
            args.command,
            data={"ranked": [asdict(item) for item in ranked]},
        )
    if args.command == "encode-plan":
        encode_plan = plan_cloud_video_only_encode(
            ffmpeg=args.ffmpeg,
            frame_pattern=args.frame_pattern,
            frame_range=FrameRange(args.frame_start, args.frame_end),
            verified_frames=_verified_frames(args),
            fps=args.fps,
            output=Path(args.output),
        )
        return _envelope(
            args.command,
            data={
                "arguments": list(encode_plan.arguments),
                "frameCount": encode_plan.frame_count,
                "audioIncluded": encode_plan.audio_included,
                "output": str(encode_plan.output),
            },
        )
    if args.command == "mux-plan":
        mux_plan = plan_local_audio_mux(
            ffmpeg=args.ffmpeg,
            video_only_input=Path(args.video_only),
            private_audio_input=Path(args.private_audio),
            output=Path(args.output),
        )
        return _envelope(
            args.command,
            data={
                "arguments": list(mux_plan.arguments),
                "audioLocation": mux_plan.audio_location,
                "shortestAllowed": mux_plan.shortest_allowed,
                "output": str(mux_plan.output),
            },
        )
    if args.command == "import-return":
        payload = _read_json_object(Path(args.manifest), "returned chunk manifest")
        expected_header: tuple[int, int, int, str, str] | None = None
        if args.package_manifest is not None:
            package_payload = _read_json_object(
                Path(args.package_manifest), "cloud package manifest"
            )
            identities, package_frame_range = validate_package_manifest(package_payload)
            resolution = package_payload.get("resolution")
            image = package_payload.get("image")
            if not isinstance(resolution, dict) or not isinstance(image, dict):
                raise ValueError("cloud package lacks its resolved image contract")
            expected_extension = str(image.get("extension", "")).lower().lstrip(".")
            if args.extension is not None and args.extension.lower().lstrip(".") != expected_extension:
                raise ValueError("manual extension differs from the cloud package")
            for supplied, expected, label in (
                (args.scene_sha, identities.scene_sha256, "scene SHA-256"),
                (args.profile_sha, identities.profile_sha256, "profile SHA-256"),
                (args.package_sha, identities.package_sha256, "package SHA-256"),
            ):
                if supplied is not None and str(supplied).upper() != str(expected).upper():
                    raise ValueError(f"manual {label} differs from the cloud package")
            if (args.frame_start is None) != (args.frame_end is None):
                raise ValueError("chunk frame start and end must be supplied together")
            if args.frame_start is None:
                frame_range = _returned_chunk_range(payload)
            else:
                frame_range = FrameRange(args.frame_start, args.frame_end)
            if (
                frame_range.start < package_frame_range.start
                or frame_range.end > package_frame_range.end
            ):
                raise ValueError("chunk frame range is outside the cloud package")
            extension = expected_extension
            expected_header = (
                int(resolution.get("width", 0)),
                int(resolution.get("height", 0)),
                int(image.get("bitDepth", 0)),
                str(image.get("format", "")).upper(),
                str(image.get("colorMode", "")).upper(),
            )
        else:
            if any(
                value is None
                for value in (
                    args.scene_sha,
                    args.profile_sha,
                    args.package_sha,
                    args.frame_start,
                    args.frame_end,
                    args.extension,
                )
            ):
                raise ValueError(
                    "import-return requires package-manifest or all manual contract fields"
                )
            identities = _identities(args)
            frame_range = FrameRange(args.frame_start, args.frame_end)
            extension = str(args.extension).lower().lstrip(".")

        def validate_returned(path: Path, frame: int) -> None:
            del frame
            header = validate_image(path, extension)
            if expected_header is not None and (
                header.width,
                header.height,
                header.bit_depth,
                header.image_format,
                header.color_mode,
            ) != expected_header:
                raise ValueError("returned frame header differs from the cloud package")

        result = import_quarantined_return(
            returned=Path(args.returned),
            quarantine_root=Path(args.quarantine_root),
            output_frames=Path(args.output_frames),
            manifest=payload,
            identities=identities,
            frame_range=frame_range,
            extension=extension,
            validate_frame=validate_returned,
        )
        return _envelope(
            args.command,
            data={
                "quarantine": str(result.quarantine),
                "publishedFrames": list(result.published_frames),
                "identicalFrames": list(result.identical_frames),
                "conflicts": [asdict(item) for item in result.conflicts],
            },
        )
    if args.command == "mock-worker":
        if not 0 <= args.no_work_timeout_seconds <= 60:
            raise ValueError("mock no-work timeout must be between 0 and 60 seconds")
        worker_arguments = [
            "--package-manifest",
            args.package_manifest,
            "--database",
            args.database,
            "--storage-root",
            args.storage_root,
            "--job-id",
            args.job_id,
            "--worker-id",
            args.worker_id,
            "--mock",
            "--no-work-timeout-seconds",
            str(args.no_work_timeout_seconds),
        ]
        if args.run_until_idle:
            worker_arguments.append("--run-until-idle")
        exit_code, payload = run_worker(worker_arguments)
        if exit_code not in {0}:
            raise ValueError(str(payload.get("error", payload))[:500])
        return _envelope(args.command, data=payload)
    if args.command == "brev-provision-benchmark":
        identities = _identities(args)
        offer = GpuOffer(
            "brev",
            args.offer_id,
            args.gpu_name,
            args.region,
            args.hourly_price,
            args.vram_gib,
        )
        provisioning_plan = BenchmarkProvisioningPlan(
            args.instance_name,
            offer,
            identities,
            args.max_budget,
            1,
        )
        limits = BudgetLimits(
            args.max_hourly_price,
            args.max_workers,
            args.total_budget,
        )
        authorization = ProvisioningAuthorization(
            args.authorization_token,
            args.plan_locked,
            args.final_confirmed,
        )
        instance = _provider(
            args.executable,
            allow_provisioning=args.enable_provisioning,
        ).provision_benchmark(provisioning_plan, limits, authorization)
        return _envelope(args.command, data=asdict(instance))
    if args.command == "brev-teardown":
        if args.confirm_stop != "STOP BREV WORKER":
            raise ValueError("exact stop confirmation is required")
        if args.confirm_delete != "DELETE BREV WORKER":
            raise ValueError("exact delete confirmation is required")
        stopped, deleted = _provider(args.executable).teardown_instance(args.instance_ref)
        return _envelope(
            args.command,
            data={"stopped": asdict(stopped), "deleted": asdict(deleted)},
        )
    raise ValueError("unknown cloud-render command")


def main(argv: Sequence[str] | None = None) -> int:
    command = "unknown"
    try:
        args = _parser().parse_args(argv)
        command = str(args.command)
        result = _run(args)
        print(canonical_json_bytes(result).decode("utf-8"))
        return 0
    except (ValueError, OSError, json.JSONDecodeError, ProviderError, SchedulerError) as exc:
        result = {
            "schemaVersion": SCHEMA_VERSION,
            "kind": RESULT_KIND,
            "ok": False,
            "command": command,
            "error": {"code": type(exc).__name__, "message": str(exc)[:500]},
        }
        print(canonical_json_bytes(result).decode("utf-8"))
        return 2
    except Exception as exc:  # pragma: no cover - final machine-readable boundary
        result = {
            "schemaVersion": SCHEMA_VERSION,
            "kind": RESULT_KIND,
            "ok": False,
            "command": command,
            "error": {"code": "unexpected-error", "message": type(exc).__name__},
        }
        print(canonical_json_bytes(result).decode("utf-8"))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
