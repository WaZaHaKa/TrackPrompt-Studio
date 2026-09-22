import argparse
import asyncio
from dataclasses import replace

import pytest

from app.mission_control.errors import MissionControlError
from app.video_generation import hayale_operator
from app.video_generation.jsonio import atomic_write_json
from app.video_generation.mission_models import VideoAuthorizationRequest, VideoJobState
from tests.video_generation.test_prepared_video_plan import controller_at, prepared_plan


async def ordinary_job(tmp_path):
    plan, references = prepared_plan(tmp_path)
    plan = replace(plan, continuity={}).with_digest()
    controller, store = controller_at(tmp_path)
    view = await controller.create_prepared_plan(
        plan, gcp_project_id="test-project", gcs_bucket="test-bucket", reference_paths=references
    )
    await controller.authorize(view.job_id, VideoAuthorizationRequest(confirmation=view.authorization_phrase))
    return controller, store, view.job_id, plan, references


def retain_owner(controller, job):
    path = controller._job_root(job) / "foreground-worker-lease.json"
    atomic_write_json(path, {
        "pid": 2147483647, "token": "retained-owner", "jobId": job.id,
        "planDigest": job.plan_digest, "processStartedAt": "2026-01-01T00:00:00+00:00",
    })
    return path


@pytest.mark.asyncio
async def test_startup_preserves_foreign_lease_and_schedules_other_jobs(tmp_path, monkeypatch):
    controller, store, job_id, plan, references = await ordinary_job(tmp_path)
    foreign = store.get_video_job(job_id).model_copy(update={"state": VideoJobState.GENERATING})
    store.put_video_job(foreign)
    lease = retain_owner(controller, foreign)
    original_lease = lease.read_bytes()
    other = await controller.create_prepared_plan(
        replace(plan, title="Another batch").with_digest(), gcp_project_id="test-project",
        gcs_bucket="test-bucket", reference_paths=references,
    )
    store.put_video_job(store.get_video_job(other.job_id).model_copy(update={"state": VideoJobState.GENERATING}))
    scheduled = []

    async def record_schedule(current):
        scheduled.append(current)
        controller._release_prepared_lease(current)

    monkeypatch.setattr(controller, "_run", record_schedule)
    try:
        controller.start_background_tasks()
        await asyncio.sleep(0)
        assert scheduled == [other.job_id]
        assert store.get_video_job(job_id) == foreign
        assert lease.read_bytes() == original_lease
    finally:
        controller.close()
        store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["start", "cancel", "authorize"])
async def test_foreign_ordinary_owner_blocks_state_mutation(tmp_path, action):
    controller, store, job_id, _, _ = await ordinary_job(tmp_path)
    before = store.get_video_job(job_id)
    lease = retain_owner(controller, before)
    original_lease = lease.read_bytes()
    authorization_path = controller._job_root(before) / "authorization.json"
    original_authorization = authorization_path.read_bytes()
    try:
        with pytest.raises(MissionControlError):
            if action == "authorize":
                await controller.authorize(job_id, VideoAuthorizationRequest(
                    confirmation=controller.get(job_id).authorization_phrase
                ))
            else:
                await getattr(controller, action)(job_id)
        assert store.get_video_job(job_id) == before
        assert lease.read_bytes() == original_lease
        assert authorization_path.read_bytes() == original_authorization
        assert not controller._tasks
    finally:
        controller.close()
        store.close()


@pytest.mark.asyncio
async def test_generic_worker_recovery_is_explicit_and_does_not_start_work(tmp_path, monkeypatch):
    controller, store, job_id, _, _ = await ordinary_job(tmp_path)
    before = store.get_video_job(job_id)
    lease = retain_owner(controller, before)
    monkeypatch.setattr(hayale_operator, "controller", lambda: controller)
    monkeypatch.setattr("app.mission_control.processes.process_is_alive", lambda pid: False)
    args = argparse.Namespace(action="recoverworker", job_id=job_id, lease_token="retained-owner")
    try:
        result = await hayale_operator.command(args)
        assert result["jobId"] == job_id
        assert result["workerLeaseRecovered"] is True
        assert result["generationSubmitted"] is False
        assert not lease.exists()
        assert len(list((controller._job_root(before) / "worker-history").glob("*.json"))) == 1
        assert store.get_video_job(job_id) == before
        assert not controller._tasks
    finally:
        controller.close()
        store.close()


@pytest.mark.asyncio
async def test_generic_recovery_preserves_unknown_process_identity(tmp_path, monkeypatch):
    controller, store, job_id, _, _ = await ordinary_job(tmp_path)
    before = store.get_video_job(job_id)
    lease = retain_owner(controller, before)
    monkeypatch.setattr(hayale_operator, "controller", lambda: controller)
    monkeypatch.setattr("app.mission_control.processes.process_is_alive", lambda pid: True)
    monkeypatch.setattr("app.mission_control.processes.process_started_at", lambda pid: None)
    args = argparse.Namespace(action="recoverworker", job_id=job_id, lease_token="retained-owner")
    try:
        with pytest.raises(ValueError, match="identity is uncertain"):
            await hayale_operator.command(args)
        assert lease.exists()
        assert store.get_video_job(job_id) == before
    finally:
        controller.close()
        store.close()
