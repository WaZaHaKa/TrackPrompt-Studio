"""Manual V2 entrypoint. Default mode prepares files; only --create may use GCP."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from finish import finish_package
from plan import (
    DEFAULT_AUDIO,
    PREPARATION,
    REPO,
    ensure_runtime_path,
    music_cues,
    prepare,
    provider_plan,
)

from app.mission_control.store import MissionControlStore
from app.video_generation.authorization import BatchAuthorization, authorization_phrase
from app.video_generation.contracts import ContractError
from app.video_generation.jsonio import atomic_write_json, sha256_file
from app.video_generation.mission_controller import VideoGenerationController
from app.video_generation.mission_models import (
    VideoAuthorizationRequest,
    VideoJobState,
    VideoJobRecord,
    VideoShotState,
)


def require_create_authorization(*, create: bool, expected: str, supplied: str) -> None:
    if (
        not create
        or os.getenv("TRACKPROMPT_HAYALE_PREPARE_ONLY") == "1"
        or supplied != expected
    ):
        raise ValueError(
            "Explicit creation and the exact plan authorization are required before GCP access"
        )


def has_valid_saved_authorization(job: VideoJobRecord) -> bool:
    if not job.authorization:
        return False
    try:
        BatchAuthorization.from_dict(job.authorization).validate_for(
            project_id=job.project_id,
            plan_digest=job.plan_digest,
            current_reserved_usd=job.reserved_cost_usd,
            next_request_usd=0,
        )
    except (ContractError, KeyError, TypeError, ValueError):
        return False
    return True


def ensure_no_automatic_retry(job) -> None:
    for shot in job.shots:
        attempt = shot.latest_attempt
        if attempt and attempt.state in {
            VideoShotState.RESERVED,
            VideoShotState.FAILED,
            VideoShotState.FILTERED,
        }:
            raise ValueError(generation_failure(job))


def generation_failure(job: VideoJobRecord) -> str:
    details = []
    for shot in job.shots:
        attempt = shot.latest_attempt
        if attempt and attempt.error:
            details.append(
                f"{shot.shot_id}: {attempt.error.code}: {attempt.error.summary}"
            )
    if not details and job.error:
        details.append(f"{job.error.code}: {job.error.summary}")
    hint = "No request was resent; saved receipts are preserved."
    candidates = [
        s.latest_attempt
        for s in job.shots
        if s.latest_attempt and s.latest_attempt.state is not VideoShotState.VERIFIED
    ]
    if candidates and all(
        VideoGenerationController.is_recoverable_pre_submit_failure(a)
        for a in candidates
    ):
        hint += " This is a recorded pre-submission reference-upload failure. After fixing gcloud access (run gcloud auth login if reauthentication is required), use -Create -ResumePreSubmit."
    return "\n".join(details + [hint])


def saved_status() -> dict:
    """Read the saved job without opening a provider or mutating any database."""
    ensure_runtime_path(PREPARATION)
    target = json.loads((PREPARATION / "operator-target.json").read_text())
    database = REPO / ".trackprompt-data/mission-control/mission-control.sqlite3"
    ensure_runtime_path(database)
    connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
    try:
        row = connection.execute(
            "SELECT payload_json FROM mission_control_video_generation_jobs WHERE id=?",
            (target["jobId"],),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise ValueError("Saved V2 job is missing")
    job = VideoJobRecord.model_validate_json(row[0])
    return {
        "jobId": job.id,
        "state": job.state.value,
        "planDigest": job.plan_digest,
        "reservedCostUsd": job.reserved_cost_usd,
        "trackPromptAuthorizationValid": has_valid_saved_authorization(job),
        "failure": generation_failure(job),
        "operationNamesRecorded": sum(
            bool(a.operation_name) for s in job.shots for a in s.attempts
        ),
        "networkContacted": False,
    }


async def create_or_finish(args: argparse.Namespace) -> dict:
    if os.getenv("TRACKPROMPT_HAYALE_PREPARE_ONLY") == "1":
        raise ValueError("This session is restricted to preparation")
    target = PREPARATION / "operator-target.json"
    settings = json.loads(target.read_text()) if target.is_file() else {}
    gcp_project = args.gcp_project or settings.get("gcpProjectId")
    bucket = args.bucket or settings.get("gcsBucket")
    if not gcp_project or not bucket:
        raise ValueError(
            "Supply --gcp-project and --bucket on first creation; no cloud setup is performed automatically"
        )
    compiled, references = provider_plan(gcp_project, bucket)
    audio = None if args.mute else Path(args.audio)
    cues = music_cues(audio)
    ffmpeg = args.ffmpeg or shutil.which("ffmpeg")
    ffprobe = args.ffprobe or shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise ValueError("Installed FFmpeg and ffprobe are required")
    # Only the explicit manual path opens the real Mission Control database.
    state_root = REPO / ".trackprompt-data/mission-control"
    for path in (state_root, PREPARATION, REPO / ".trackprompt-data/video-generation"):
        ensure_runtime_path(path)
    store = MissionControlStore(state_root / "mission-control.sqlite3")
    current_id = [""]
    last_status = [None]

    async def notify(sequence: int) -> None:
        if not current_id[0]:
            return
        current = store.get_video_job(current_id[0])
        if current:
            verified = sum(
                bool(
                    s.latest_attempt
                    and s.latest_attempt.state is VideoShotState.VERIFIED
                )
                for s in current.shots
            )
            status = (current.state.value, verified)
            if status != last_status[0]:
                print(
                    f"{current.state.value}: {verified}/7 scenes verified", flush=True
                )
                last_status[0] = status

    controller = VideoGenerationController(
        repository_root=REPO,
        state_root=state_root,
        store=store,
        notify_event=notify,
        ffmpeg_path=lambda: Path(ffmpeg),
        ffprobe_path=lambda: Path(ffprobe),
    )
    try:
        view = await controller.create_prepared_plan(
            compiled,
            gcp_project_id=gcp_project,
            gcs_bucket=bucket,
            reference_paths=references,
        )
        current_id[0] = view.job_id
        job = store.get_video_job(view.job_id)
        assert job is not None
        all_verified = all(
            s.latest_attempt and s.latest_attempt.state is VideoShotState.VERIFIED
            for s in job.shots
        )
        if not all_verified:
            if args.assemble_only:
                raise ValueError(
                    "Local assembly requires all seven saved, verified scenes"
                )
            if not args.resume_pre_submit:
                ensure_no_automatic_retry(job)
            expected = authorization_phrase(
                compiled.project_id, compiled.plan_digest, compiled.max_spend_usd
            )
            print(
                f"Veo 3.1 Quality 1080p. Seven 8-second requests; one logo clip reused.\nGeneration estimate/reservation cap: ${compiled.max_spend_usd:.2f}. Storage/transfer fees are separate.\nTarget project: {gcp_project}; bucket: {bucket}.\nAudio stays local. No automatic generation retries."
            )
            if not args.create:
                raise ValueError("Explicit creation is required")
            new_authorization = None
            if has_valid_saved_authorization(job):
                print(
                    f"Reusing your valid authorization for plan {compiled.plan_digest[:12]}; the reservation cap is unchanged."
                )
            else:
                if not args.resume_pre_submit and job.state not in {
                    VideoJobState.PLANNED,
                    VideoJobState.AUTHORIZED,
                }:
                    raise ValueError(
                        "Saved authorization expired; this non-recoverable job needs review before further execution"
                    )
                print(expected)
                confirmation = input(
                    "Type the exact line above to authorize this V2 batch: "
                )
                require_create_authorization(
                    create=args.create, expected=expected, supplied=confirmation
                )
                new_authorization = VideoAuthorizationRequest(confirmation=confirmation)
            if not shutil.which("gcloud"):
                raise ValueError(
                    "Install/configure gcloud and sign in before running creation; no credentials were requested"
                )
            if args.resume_pre_submit:
                await controller.authorize_pre_submit_recovery(
                    view.job_id, new_authorization
                )
            elif new_authorization is not None:
                await controller.authorize(view.job_id, new_authorization)
            atomic_write_json(
                target,
                {
                    "gcpProjectId": gcp_project,
                    "gcsBucket": bucket,
                    "jobId": view.job_id,
                    "planDigest": compiled.plan_digest,
                },
            )
            await controller.start(view.job_id)
            task = controller._tasks.get(view.job_id)
            if task is not None:
                # This wait exists only in the foreground command the operator
                # launches; preparation never schedules this controller task.
                await asyncio.wait_for(asyncio.shield(task), timeout=3600)
            job = store.get_video_job(view.job_id)
            assert job is not None
        clips = {}
        for shot in job.shots:
            attempt = shot.latest_attempt
            if (
                not attempt
                or attempt.state is not VideoShotState.VERIFIED
                or not attempt.local_clip_path
            ):
                raise ValueError(generation_failure(job))
            path = Path(attempt.local_clip_path)
            if not path.is_file() or sha256_file(path) != attempt.clip_sha256:
                raise ValueError(
                    "A saved provider clip is missing or changed; no regeneration was attempted"
                )
            clips[shot.shot_id] = path
        root = controller._job_root(job)
        output = (
            root
            / "deliveries"
            / (
                "v2-"
                + datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
                + "-"
                + uuid4().hex[:8]
            )
        )
        ensure_runtime_path(output)
        logo_reference = compiled.shots[-1].first_frame_reference
        assert logo_reference is not None
        report = await asyncio.to_thread(
            finish_package,
            output=output,
            clips=clips,
            cues=cues,
            provider_digest=compiled.plan_digest,
            ffmpeg=ffmpeg,
            ffprobe=ffprobe,
            logo_sha256=logo_reference.sha256,
        )
        report["outputDirectory"] = str(output)
        atomic_write_json(PREPARATION / "latest-delivery.json", report)
        return report
    finally:
        tasks = list(controller._tasks.values())
        controller.close()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        store.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--prepare", action="store_true")
    modes.add_argument("--create", action="store_true")
    modes.add_argument("--assemble-only", action="store_true")
    modes.add_argument("--status", action="store_true")
    parser.add_argument("--resume-pre-submit", action="store_true")
    parser.add_argument("--audio", default=str(DEFAULT_AUDIO))
    parser.add_argument("--mute", action="store_true")
    parser.add_argument("--gcp-project")
    parser.add_argument("--bucket")
    parser.add_argument("--ffmpeg")
    parser.add_argument("--ffprobe")
    args = parser.parse_args()
    if args.resume_pre_submit and not args.create:
        parser.error("--resume-pre-submit requires --create")
    if args.status:
        report = saved_status()
    elif not args.create and not args.assemble_only:
        report = prepare(None if args.mute else Path(args.audio))
    else:
        report = asyncio.run(create_or_finish(args))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    try:
        main()
    except ValueError as error:
        print(f"Hayale V2 stopped: {error}", file=sys.stderr)
        raise SystemExit(1) from None
