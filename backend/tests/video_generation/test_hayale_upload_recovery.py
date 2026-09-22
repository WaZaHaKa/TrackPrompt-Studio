import asyncio
import json
import subprocess

import pytest

from app.video_generation import gcp_auth, gcp_veo
from app.video_generation.authorization import authorization_phrase
from app.video_generation.jsonio import sha256_file
from app.video_generation.media import MediaProbe
from app.video_generation.mission_models import (
    VideoAuthorizationRequest,
    VideoError,
    VideoJobState,
)

from .test_prepared_video_plan import controller_at, prepared_plan


def test_reference_upload_exposes_reauthentication_without_leaking_raw_stderr(tmp_path, monkeypatch):
    source = tmp_path / "reference.png"
    source.write_bytes(b"reference")
    monkeypatch.setattr(
        gcp_auth,
        "run_bound_gcloud",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            [],
            1,
            "",
            "Reauthentication failed. cannot prompt during non-interactive execution. Bearer PRIVATE_TOKEN",
        ),
    )
    with pytest.raises(gcp_veo.ProviderError) as error:
        gcp_veo.upload_reference_image(
            source, "gs://test-bucket/reference.png", expected_sha256=sha256_file(source)
        )
    assert error.value.code == "provider_reference_reauthentication_required"
    assert "gcloud auth login" in str(error.value)
    assert "PRIVATE_TOKEN" not in str(error.value)


async def failed_upload(root, code="provider_reference_upload_failed", operation=None):
    plan, references = prepared_plan(root)
    controller, store = controller_at(root)
    view = await controller.create_prepared_plan(
        plan, gcp_project_id="test-project", gcs_bucket="test-bucket", reference_paths=references
    )
    auth = VideoAuthorizationRequest(
        confirmation=authorization_phrase(plan.project_id, plan.plan_digest, plan.max_spend_usd)
    )
    await controller.authorize(view.job_id, auth)
    controller._claim_prepared_lease(store.get_video_job(view.job_id))
    _, _, attempt = await controller._reserve(view.job_id, "shot-001")
    if operation:
        await controller._update_attempt(
            view.job_id, "shot-001", attempt.model_copy(update={"operation_name": operation})
        )
    await controller._fail_attempt(
        view.job_id, "shot-001", attempt.id, VideoError(code=code, summary="Recorded failure")
    )
    controller._release_prepared_lease(view.job_id)
    return controller, store, view.job_id, plan, auth, attempt


@pytest.mark.asyncio
async def test_explicit_pre_submit_recovery_preserves_receipt_and_reuses_reservation(tmp_path, monkeypatch):
    controller, store, job_id, plan, auth, original = await failed_upload(tmp_path)
    failed = store.get_video_job(job_id)
    submitted = []

    class Provider:
        async def upload_reference(self, *args):
            pass

        async def submit(self, shot, *, context):
            submitted.append(shot.shot_id)
            self.prefix = shot.storage_uri
            return {"name": "operations/single"}

        async def fetch(self, **kwargs):
            return {"done": True, "response": {"videos": [{"gcsUri": self.prefix + "out.mp4"}]}}

        async def download(self, uri, path):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"mock provider video")

    controller.provider_factory = lambda *args: Provider()
    monkeypatch.setattr(
        "app.video_generation.mission_controller.verify_generated_clip",
        lambda path, **kwargs: MediaProbe(
            str(path), sha256_file(path), 8, 1920, 1080, 24, "h264", "yuv420p", False
        ),
    )
    await controller.authorize_pre_submit_recovery(job_id, auth)
    recovered = store.get_video_job(job_id)
    assert recovered.reserved_cost_usd == failed.reserved_cost_usd == 1.6
    assert recovered.shots[0].attempts == failed.shots[0].attempts
    receipts = list((controller._job_root(recovered) / "pre-submit-recovery").glob("*.json"))
    assert len(receipts) == 1
    receipt = json.loads(receipts[0].read_text())
    assert receipt["failedAttempts"][0]["error"]["code"] == "provider_reference_upload_failed"
    assert receipt["failedAttempts"][0]["id"] == original.id
    await controller.start(job_id)
    await asyncio.wait_for(controller._tasks[job_id], 10)
    completed = store.get_video_job(job_id)
    assert completed.state is VideoJobState.REVIEW_READY
    assert completed.reserved_cost_usd == 1.6
    assert len(completed.shots[0].attempts) == 1
    assert completed.shots[0].latest_attempt.id == original.id
    assert submitted == ["shot-001"]
    assert completed.pre_submit_resume_attempt_ids == ()
    assert receipts[0].is_file()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code,operation",
    [("provider_network_error", None), ("provider_reference_upload_failed", "operations/already-submitted")],
)
async def test_ambiguous_or_submitted_attempts_cannot_use_pre_submit_recovery(tmp_path, code, operation):
    controller, store, job_id, _, auth, _ = await failed_upload(tmp_path, code, operation)
    before = store.get_video_job(job_id).model_dump_json()
    with pytest.raises(ValueError, match="pre-submission"):
        await controller.authorize_pre_submit_recovery(job_id, auth)
    assert store.get_video_job(job_id).model_dump_json() == before


@pytest.mark.asyncio
async def test_recovery_permission_cannot_replay_a_later_uncertain_submit(tmp_path):
    controller, store, job_id, _, auth, _ = await failed_upload(tmp_path)
    calls = []

    class Provider:
        async def upload_reference(self, *args):
            pass

        async def submit(self, *args, **kwargs):
            calls.append("submit")
            raise gcp_veo.ProviderError("Connection failed", code="provider_network_error")

    controller.provider_factory = lambda *args: Provider()
    await controller.authorize_pre_submit_recovery(job_id, auth)
    await controller.start(job_id)
    await asyncio.wait_for(controller._tasks[job_id], 10)
    assert store.get_video_job(job_id).pre_submit_resume_attempt_ids == ()
    await controller.start(job_id)
    await asyncio.wait_for(controller._tasks[job_id], 10)
    assert calls == ["submit"]
    assert store.get_video_job(job_id).reserved_cost_usd == 1.6
    with pytest.raises(ValueError, match="pre-submission"):
        await controller.authorize_pre_submit_recovery(job_id, auth)


@pytest.mark.asyncio
async def test_recovery_refuses_conflicting_operation_receipt_and_wrong_confirmation(tmp_path):
    controller, store, job_id, _, auth, attempt = await failed_upload(tmp_path)
    root = controller._job_root(store.get_video_job(job_id))
    before = store.get_video_job(job_id).model_dump_json()
    with pytest.raises(ValueError, match="Confirmation"):
        await controller.authorize_pre_submit_recovery(
            job_id, VideoAuthorizationRequest(confirmation="wrong")
        )
    assert not (root / "pre-submit-recovery").exists()
    (root / "operations").mkdir()
    (root / "operations" / f"{attempt.id}.json").write_text('{"operationName":"operations/existing"}')
    with pytest.raises(ValueError, match="pre-submission"):
        await controller.authorize_pre_submit_recovery(job_id, auth)
    assert store.get_video_job(job_id).model_dump_json() == before


@pytest.mark.asyncio
async def test_explicit_recovery_reuses_valid_authorization_without_extending_it(tmp_path):
    controller, store, job_id, _, _, _ = await failed_upload(tmp_path)
    before = store.get_video_job(job_id)
    root = controller._job_root(before)
    authorization_bytes = (root / "authorization.json").read_bytes()
    await controller.authorize_pre_submit_recovery(job_id)
    after = store.get_video_job(job_id)
    assert after.authorization == before.authorization
    assert after.reserved_cost_usd == before.reserved_cost_usd
    assert (root / "authorization.json").read_bytes() == authorization_bytes
    receipt = json.loads(next((root / "pre-submit-recovery").glob("*.json")).read_text())
    assert receipt["authorizationSource"] == "existing-valid-authorization"
