from __future__ import annotations

import argparse
import json
import time
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .authorization import (
    BatchAuthorization,
    authorization_phrase,
    load_authorization,
    save_authorization,
)
from .contracts import CompiledReferenceImage, CompiledShot, ContractError
from .exporter import export_davinci_package
from .gcp_veo import (
    ProviderRequestContext,
    VeoRestClient,
    build_request_payload,
    copy_gcs_uri,
    doctor,
    response_output_uris,
)
from .jsonio import atomic_write_json, read_json
from .media import verify_generated_clip
from .operations import (
    OperationRecord,
    list_operations,
    reserved_cost,
    save_operation,
)
from .planning import compile_project_plan
from .timeline import resolve_timeline


def _emit(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2))


def _load_object(path: Path) -> dict[str, Any]:
    value = read_json(path)
    if not isinstance(value, dict):
        raise ContractError(f"JSON root must be an object: {path}")
    return value


def _compiled_shot(value: dict[str, Any]) -> CompiledShot:
    reference_value = value.get("firstFrameReference")
    reference = (
        CompiledReferenceImage(
            asset_id=str(reference_value["assetId"]),
            gcs_uri=str(reference_value["gcsUri"]),
            mime_type=str(reference_value["mimeType"]),
            sha256=str(reference_value["sha256"]),
            source_kind=str(reference_value["sourceKind"]),
        )
        if isinstance(reference_value, dict)
        else None
    )
    return CompiledShot(
        shot_id=str(value["shotId"]),
        chapter_id=str(value["chapterId"]),
        order=int(value["order"]),
        title=str(value["title"]),
        duration_seconds=int(value["durationSeconds"]),
        prompt=str(value["prompt"]),
        negative_prompt=str(value["negativePrompt"]),
        seed=int(value["seed"]),
        model_id=str(value["modelId"]),
        resolution=str(value["resolution"]),
        aspect_ratio=str(value["aspectRatio"]),
        sample_count=int(value["sampleCount"]),
        generate_audio=bool(value["generateAudio"]),
        enhance_prompt=bool(value.get("enhancePrompt", True)),
        compression_quality=str(value.get("compressionQuality", "optimized")),
        person_generation=str(value.get("personGeneration", "allow_adult")),
        storage_uri=value.get("storageUri"),
        required=bool(value.get("required", True)),
        estimated_cost_usd=float(value["estimatedCostUsd"]),
        source_section_hints=tuple(value.get("sourceSectionHints", [])),
        review_notes=tuple(value.get("reviewNotes", [])),
        variation_index=int(value.get("variationIndex", 0)),
        continuity_group_ids=tuple(value.get("continuityGroupIds", [])),
        previous_shot_id=value.get("previousShotId"),
        continuation_mode=str(value.get("continuationMode", "prompt-anchors")),
        first_frame_reference=reference,
    )


def _plan_shots(plan: dict[str, Any]) -> tuple[CompiledShot, ...]:
    values = plan.get("shots")
    if not isinstance(values, list):
        raise ContractError("compiled plan has no shots")
    return tuple(_compiled_shot(value) for value in values)


def _runtime_root(arguments: argparse.Namespace, project_id: str) -> Path:
    return Path(arguments.runtime_root) / project_id


def _authorization_path(root: Path) -> Path:
    return root / "authorization.json"


def command_compile(arguments: argparse.Namespace) -> None:
    plan = compile_project_plan(
        project_config_path=Path(arguments.project_config),
        creative_bible_path=Path(arguments.creative_bible),
        shot_bank_path=Path(arguments.shot_bank),
        gcs_bucket=arguments.gcs_bucket,
        analysis_job_id=arguments.analysis_job_id,
        audio_master_path=(Path(arguments.audio_master) if arguments.audio_master else None),
        story_plan_path=Path(arguments.story_plan) if arguments.story_plan else None,
        shot_plan_path=Path(arguments.shot_plan) if arguments.shot_plan else None,
    )
    output = Path(arguments.output)
    atomic_write_json(output, plan.to_dict())
    _emit(
        {
            "status": "compiled",
            "output": str(output),
            **plan.to_dict()["cost"],
            "planDigest": plan.plan_digest,
        }
    )


def command_show_authorization_phrase(arguments: argparse.Namespace) -> None:
    plan = _load_object(Path(arguments.plan))
    phrase = authorization_phrase(
        str(plan["projectId"]),
        str(plan["planDigest"]),
        float(plan["cost"]["maxSpendUsd"]),
    )
    _emit({"phrase": phrase, "planDigest": plan["planDigest"]})


def command_authorize(arguments: argparse.Namespace) -> None:
    plan = _load_object(Path(arguments.plan))
    project_id = str(plan["projectId"])
    max_spend = float(plan["cost"]["maxSpendUsd"])
    expected = authorization_phrase(project_id, str(plan["planDigest"]), max_spend)
    confirmation = arguments.confirm
    if confirmation is None:
        print(expected)
        confirmation = input("Type the phrase once to authorize this exact batch: ")
    authorization = BatchAuthorization.create(
        project_id=project_id,
        plan_digest=str(plan["planDigest"]),
        max_spend_usd=max_spend,
        confirmation=confirmation,
        valid_hours=arguments.valid_hours,
    )
    root = _runtime_root(arguments, project_id)
    save_authorization(_authorization_path(root), authorization)
    _emit(
        {
            "status": "authorized",
            "path": str(_authorization_path(root)),
            "expiresAt": authorization.expires_at,
        }
    )


def command_doctor(arguments: argparse.Namespace) -> None:
    result = doctor(project_id=arguments.project_id, bucket=arguments.gcs_bucket, region=arguments.region)
    _emit(result.to_dict())
    if not result.ok:
        raise SystemExit(2)


def command_request_preview(arguments: argparse.Namespace) -> None:
    plan = _load_object(Path(arguments.plan))
    output_root = Path(arguments.output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    outputs = []
    for shot in _plan_shots(plan):
        path = output_root / f"{shot.shot_id}.request.json"
        atomic_write_json(path, build_request_payload(shot))
        outputs.append(str(path))
    _emit({"status": "written", "requests": outputs})


def _validate_authorized_request(plan: dict[str, Any], root: Path, shot: CompiledShot) -> None:
    authorization = load_authorization(_authorization_path(root))
    authorization.validate_for(
        project_id=str(plan["projectId"]),
        plan_digest=str(plan["planDigest"]),
        current_reserved_usd=reserved_cost(root, plan_digest=str(plan["planDigest"])),
        next_request_usd=shot.estimated_cost_usd,
    )


def command_submit_batch(arguments: argparse.Namespace) -> None:
    plan = _load_object(Path(arguments.plan))
    project_id = str(plan["projectId"])
    root = _runtime_root(arguments, project_id)
    existing = list_operations(root)
    complete_or_active = {
        record.shot_id
        for record in existing
        if record.plan_digest == plan["planDigest"]
        and record.status in {"submitted", "running", "succeeded", "downloaded", "verified"}
    }
    client = VeoRestClient(
        project_id=arguments.project_id,
        region=arguments.region,
        diagnostics_root=Path(arguments.runtime_root) / "provider-errors",
    )
    submitted = []
    skipped = []
    selected_shot_ids = set(arguments.only_shot or [])
    known_shot_ids = {shot.shot_id for shot in _plan_shots(plan)}
    unknown = sorted(selected_shot_ids - known_shot_ids)
    if unknown:
        raise ContractError(f"unknown --only-shot IDs: {', '.join(unknown)}")
    for shot in _plan_shots(plan):
        if selected_shot_ids and shot.shot_id not in selected_shot_ids:
            skipped.append(shot.shot_id)
            continue
        if shot.shot_id in complete_or_active and not arguments.force_retry:
            skipped.append(shot.shot_id)
            continue
        _validate_authorized_request(plan, root, shot)
        response = client.submit(
            shot,
            context=ProviderRequestContext(
                phase="cli-submit",
                shot_id=shot.shot_id,
            ),
        )
        operation_name = str(response["name"])
        operation_id = str(uuid.uuid4())
        raw_path = root / "provider-responses" / f"{operation_id}-submit.json"
        atomic_write_json(raw_path, response)
        record = OperationRecord.new(
            operation_id=operation_id,
            project_id=project_id,
            plan_digest=str(plan["planDigest"]),
            shot_id=shot.shot_id,
            model_id=shot.model_id,
            reserved_cost_usd=shot.estimated_cost_usd,
            operation_name=operation_name,
            storage_uri=shot.storage_uri,
        ).updated(status="submitted", raw_response_path=str(raw_path))
        save_operation(root, record)
        submitted.append(
            {"shotId": shot.shot_id, "operationId": operation_id, "operationName": operation_name}
        )
    _emit(
        {
            "status": "submitted",
            "submitted": submitted,
            "skipped": skipped,
            "reservedUsd": reserved_cost(root, plan_digest=str(plan["planDigest"])),
        }
    )


def _poll_once(plan: dict[str, Any], root: Path, client: VeoRestClient) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in list_operations(root):
        if record.plan_digest != plan["planDigest"]:
            # Historical operations from another compiled plan must not affect
            # this plan's completion calculation or keep polling alive.
            continue
        if record.status in {
            "succeeded",
            "failed",
            "filtered",
            "downloaded",
            "verified",
            "rejected",
        }:
            counts[record.status] = counts.get(record.status, 0) + 1
            continue
        if not record.operation_name:
            updated = record.updated(status="failed", error={"message": "missing operationName"})
            save_operation(root, updated)
            counts["failed"] = counts.get("failed", 0) + 1
            continue
        response = client.fetch(
            model_id=record.model_id,
            operation_name=record.operation_name,
            context=ProviderRequestContext(
                phase="cli-poll",
                shot_id=record.shot_id,
                attempt_id=record.operation_id,
            ),
        )
        raw_path = root / "provider-responses" / f"{record.operation_id}-poll.json"
        atomic_write_json(raw_path, response)
        if not response.get("done"):
            updated = record.updated(status="running", raw_response_path=str(raw_path))
        elif isinstance(response.get("error"), dict):
            updated = record.updated(
                status="failed", error=response["error"], raw_response_path=str(raw_path)
            )
        else:
            uris = response_output_uris(response)
            filtered = int(response.get("response", {}).get("raiMediaFilteredCount", 0) or 0)
            if uris:
                updated = record.updated(
                    status="succeeded", output_uris=uris, raw_response_path=str(raw_path)
                )
            elif filtered:
                updated = record.updated(
                    status="filtered",
                    error={"raiMediaFilteredCount": filtered},
                    raw_response_path=str(raw_path),
                )
            else:
                updated = record.updated(
                    status="failed",
                    error={"message": "done response had no video URI"},
                    raw_response_path=str(raw_path),
                )
        save_operation(root, updated)
        counts[updated.status] = counts.get(updated.status, 0) + 1
    return counts


def command_poll(arguments: argparse.Namespace) -> None:
    plan = _load_object(Path(arguments.plan))
    project_id = str(plan["projectId"])
    root = _runtime_root(arguments, project_id)
    client = VeoRestClient(
        project_id=arguments.project_id,
        region=arguments.region,
        diagnostics_root=Path(arguments.runtime_root) / "provider-errors",
    )
    while True:
        counts = _poll_once(plan, root, client)
        _emit({"status": "poll", "counts": counts})
        active = counts.get("submitted", 0) + counts.get("running", 0)
        if active == 0 or not arguments.until_complete:
            break
        time.sleep(arguments.interval_seconds)


def command_download(arguments: argparse.Namespace) -> None:
    plan = _load_object(Path(arguments.plan))
    project_id = str(plan["projectId"])
    root = _runtime_root(arguments, project_id)
    clips_root = Path(arguments.clips_root)
    outputs = []
    for record in list_operations(root):
        if record.plan_digest != plan["planDigest"] or record.status != "succeeded":
            continue
        for index, uri in enumerate(record.output_uris, start=1):
            # Candidate 1 is always the canonical shot filename consumed by
            # verification, autonomous assembly and Resolve interchange.
            suffix = "" if index == 1 else f"-candidate-{index}"
            destination = clips_root / f"{record.shot_id}{suffix}.mp4"
            copy_gcs_uri(uri, destination)
            outputs.append(str(destination))
        save_operation(root, record.updated(status="downloaded"))
    _emit({"status": "downloaded", "clips": outputs})


def command_verify(arguments: argparse.Namespace) -> None:
    plan = _load_object(Path(arguments.plan))
    project_id = str(plan["projectId"])
    root = _runtime_root(arguments, project_id)
    shots = _plan_shots(plan)
    selected_shot_ids = set(arguments.only_shot or [])
    known_shot_ids = {shot.shot_id for shot in shots}
    unknown = sorted(selected_shot_ids - known_shot_ids)
    if unknown:
        raise ContractError(f"unknown --only-shot IDs: {', '.join(unknown)}")
    if selected_shot_ids:
        shots = tuple(shot for shot in shots if shot.shot_id in selected_shot_ids)

    records_by_shot: dict[str, list[OperationRecord]] = {}
    for record in list_operations(root):
        if record.plan_digest == plan["planDigest"]:
            records_by_shot.setdefault(record.shot_id, []).append(record)

    clips_root = Path(arguments.clips_root)
    verified = []
    failures = []
    for shot in shots:
        records = records_by_shot.get(shot.shot_id, [])
        eligible = [record for record in records if record.status in {"downloaded", "verified"}]
        if not eligible:
            statuses = sorted({record.status for record in records})
            failures.append(
                {
                    "shotId": shot.shot_id,
                    "error": "no downloaded clip is available for verification",
                    "operationStatuses": statuses,
                }
            )
            continue
        record = max(eligible, key=lambda item: item.updated_at)
        path = clips_root / f"{shot.shot_id}.mp4"
        try:
            result = verify_generated_clip(
                path,
                resolution=shot.resolution,
                aspect_ratio=shot.aspect_ratio,
                expected_duration_seconds=shot.duration_seconds,
                ffprobe=arguments.ffprobe,
            )
            verified.append(result.to_dict())
            save_operation(root, record.updated(status="verified"))
        except Exception as exc:  # preserve all QA failures in the report
            failures.append({"shotId": shot.shot_id, "error": str(exc)})
    report = {
        "schemaVersion": "1.0.0",
        "projectId": project_id,
        "planDigest": plan["planDigest"],
        "selectedShotIds": [shot.shot_id for shot in shots],
        "verified": verified,
        "failures": failures,
    }
    report_path = root / "clip-verification.json"
    atomic_write_json(report_path, report)
    _emit(
        {
            "status": "verified" if not failures else "verification-failed",
            "report": str(report_path),
            "verifiedCount": len(verified),
            "failureCount": len(failures),
        }
    )
    if failures:
        raise SystemExit(3)


def command_resolve_timeline(arguments: argparse.Namespace) -> None:
    value = resolve_timeline(
        project_id=arguments.project_id,
        title=arguments.title,
        audio_path=Path(arguments.audio),
        chapter_map_path=Path(arguments.chapter_map),
        clips_root=Path(arguments.clips_root),
        output_width=arguments.width,
        output_height=arguments.height,
        fps=24,
        generated_clip_duration_seconds=arguments.generated_clip_duration_seconds,
        target_edit_seconds=arguments.target_edit_seconds,
        analysis_shot_plan_path=Path(arguments.analysis_shot_plan) if arguments.analysis_shot_plan else None,
        ffprobe=arguments.ffprobe,
    )
    output = Path(arguments.output)
    atomic_write_json(output, value)
    _emit(
        {
            "status": "resolved",
            "output": str(output),
            "segments": len(value["segments"]),
            "durationSeconds": value["timeline"]["durationSeconds"],
        }
    )


def command_export(arguments: argparse.Namespace) -> None:
    value = _load_object(Path(arguments.timeline))
    outputs = export_davinci_package(value, output_root=Path(arguments.output_root), ffmpeg=arguments.ffmpeg)
    _emit({"status": "exported", "outputs": outputs})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="TrackPrompt GCP video fast lane")
    parser.add_argument("--runtime-root", default=".trackprompt-data/video-generation")
    subparsers = parser.add_subparsers(dest="command", required=True)

    compile_parser = subparsers.add_parser("compile")
    compile_parser.add_argument("--project-config", required=True)
    compile_parser.add_argument("--creative-bible", required=True)
    compile_parser.add_argument("--shot-bank", required=True)
    compile_parser.add_argument("--gcs-bucket")
    compile_parser.add_argument("--analysis-job-id")
    compile_parser.add_argument("--audio-master")
    compile_parser.add_argument("--story-plan")
    compile_parser.add_argument("--shot-plan")
    compile_parser.add_argument("--output", required=True)
    compile_parser.set_defaults(handler=command_compile)

    phrase_parser = subparsers.add_parser("authorization-phrase")
    phrase_parser.add_argument("--plan", required=True)
    phrase_parser.set_defaults(handler=command_show_authorization_phrase)

    authorize_parser = subparsers.add_parser("authorize")
    authorize_parser.add_argument("--plan", required=True)
    authorize_parser.add_argument("--confirm")
    authorize_parser.add_argument("--valid-hours", type=int, default=24)
    authorize_parser.set_defaults(handler=command_authorize)

    doctor_parser = subparsers.add_parser("doctor")
    doctor_parser.add_argument("--project-id", required=True)
    doctor_parser.add_argument("--gcs-bucket", required=True)
    doctor_parser.add_argument("--region", default="us-central1")
    doctor_parser.set_defaults(handler=command_doctor)

    preview_parser = subparsers.add_parser("request-preview")
    preview_parser.add_argument("--plan", required=True)
    preview_parser.add_argument("--output-root", required=True)
    preview_parser.set_defaults(handler=command_request_preview)

    submit_parser = subparsers.add_parser("submit-batch")
    submit_parser.add_argument("--plan", required=True)
    submit_parser.add_argument("--project-id", required=True)
    submit_parser.add_argument("--region", default="us-central1")
    submit_parser.add_argument("--force-retry", action="store_true")
    submit_parser.add_argument(
        "--only-shot",
        action="append",
        help="Submit only this shot ID; repeat for more than one. Uses the same plan-level authorization.",
    )
    submit_parser.set_defaults(handler=command_submit_batch)

    poll_parser = subparsers.add_parser("poll")
    poll_parser.add_argument("--plan", required=True)
    poll_parser.add_argument("--project-id", required=True)
    poll_parser.add_argument("--region", default="us-central1")
    poll_parser.add_argument("--until-complete", action="store_true")
    poll_parser.add_argument("--interval-seconds", type=int, default=15)
    poll_parser.set_defaults(handler=command_poll)

    download_parser = subparsers.add_parser("download")
    download_parser.add_argument("--plan", required=True)
    download_parser.add_argument("--clips-root", required=True)
    download_parser.set_defaults(handler=command_download)

    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--plan", required=True)
    verify_parser.add_argument("--clips-root", required=True)
    verify_parser.add_argument("--ffprobe")
    verify_parser.add_argument(
        "--only-shot",
        action="append",
        help="Verify only this shot ID; repeat for more than one.",
    )
    verify_parser.set_defaults(handler=command_verify)

    timeline_parser = subparsers.add_parser("resolve-timeline")
    timeline_parser.add_argument("--project-id", required=True)
    timeline_parser.add_argument("--title", required=True)
    timeline_parser.add_argument("--audio", required=True)
    timeline_parser.add_argument("--chapter-map", required=True)
    timeline_parser.add_argument("--clips-root", required=True)
    timeline_parser.add_argument("--analysis-shot-plan")
    timeline_parser.add_argument("--generated-clip-duration-seconds", type=int, default=8)
    timeline_parser.add_argument("--target-edit-seconds", type=float, default=6.0)
    timeline_parser.add_argument("--width", type=int, default=1920)
    timeline_parser.add_argument("--height", type=int, default=1080)
    timeline_parser.add_argument("--ffprobe")
    timeline_parser.add_argument("--output", required=True)
    timeline_parser.set_defaults(handler=command_resolve_timeline)

    export_parser = subparsers.add_parser("export")
    export_parser.add_argument("--timeline", required=True)
    export_parser.add_argument("--output-root", required=True)
    export_parser.add_argument("--ffmpeg")
    export_parser.set_defaults(handler=command_export)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    try:
        arguments.handler(arguments)
        return 0
    except (ContractError, FileNotFoundError, ValueError, RuntimeError) as exc:
        _emit({"status": "error", "errorType": type(exc).__name__, "message": str(exc)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
