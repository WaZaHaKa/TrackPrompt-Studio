from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.video_generation.gcp_veo import ProviderError
from app.video_generation.media import MediaProbe
from app.video_generation.mission_models import (
    VideoAuthorizationRequest,
    VideoError,
    VideoJobState,
    VideoShotState,
)
from tests.video_generation.test_prepared_video_plan import controller_at, prepared_plan


async def setup_job(tmp_path: Path):
    plan, references = prepared_plan(tmp_path)
    plan = replace(plan, continuity={"executionLimits": {"maxAttemptsPerShot": 2}}).with_digest()
    controller, store = controller_at(tmp_path)
    view = await controller.create_prepared_plan(
        plan, gcp_project_id="test-project", gcs_bucket="test-bucket", reference_paths=references
    )
    await controller.authorize(view.job_id, VideoAuthorizationRequest(confirmation=view.authorization_phrase))
    return controller, store, view.job_id


class FakeProvider:
    def __init__(self):
        self.submitted = []
        self.downloads = 0
        self.polls = []
        self.submit_error = None
        self.poll_error = None
        self.download_error = None
        self.prefix = None

    async def upload_reference(self, *args):
        pass

    async def submit(self, shot, *, context):
        self.submitted.append(shot)
        self.prefix = shot.storage_uri
        if self.submit_error:
            raise self.submit_error
        return {"name": "projects/test-project/locations/us-central1/publishers/google/models/veo-3.1-generate-001/operations/private-operation"}

    async def fetch(self, *, model_id, operation_name, context):
        self.polls.append(operation_name)
        if self.poll_error:
            raise self.poll_error
        return {"done": True, "response": {"videos": [{"gcsUri": self.prefix + "result.mp4"}]}}

    async def download(self, uri, destination):
        self.downloads += 1
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"complete" if not self.download_error else b"partial")
        if self.download_error:
            raise self.download_error


def fake_verify(path, **kwargs):
    from app.video_generation.jsonio import sha256_file
    assert path.read_bytes() == b"complete"
    return MediaProbe(str(path), sha256_file(path), 8, 1920, 1080, 24, "h264", "yuv420p", False)


@pytest.mark.asyncio
async def test_unknown_submit_is_never_replayed_by_repeated_resume(tmp_path):
    controller, store, job_id = await setup_job(tmp_path)
    provider = FakeProvider()
    provider.submit_error = ProviderError("Network unavailable", code="provider_network_error", retryable=True)
    assert not await controller._process_shot(job_id, "shot-001", provider)
    assert not await controller._process_shot(job_id, "shot-001", provider)
    job = store.get_video_job(job_id)
    assert len(provider.submitted) == 1
    assert job.reserved_cost_usd == 1.6
    assert job.shots[0].latest_attempt.error.code == "provider_submission_outcome_unknown"


@pytest.mark.asyncio
async def test_expired_spend_authorization_allows_accepted_poll_and_download(tmp_path, monkeypatch):
    controller, store, job_id = await setup_job(tmp_path)
    provider = FakeProvider()
    provider.poll_error = ProviderError("Reauthenticate", code="provider_authentication_required")
    await controller._process_shot(job_id, "shot-001", provider)
    job = store.get_video_job(job_id)
    operation = job.shots[0].latest_attempt.operation_name
    auth = {**job.authorization, "expiresAt": (datetime.now(UTC) - timedelta(hours=1)).isoformat()}
    store.put_video_job(job.model_copy(update={"authorization": auth, "state": VideoJobState.FAILED}))
    provider.poll_error = None
    controller.provider_factory = lambda *args: provider
    monkeypatch.setattr("app.video_generation.mission_controller.verify_generated_clip", fake_verify)
    await controller.resume(job_id)
    await asyncio.wait_for(controller._tasks[job_id], 5)
    assert len(provider.submitted) == 1
    assert provider.polls == [operation, operation]
    assert store.get_video_job(job_id).shots[0].latest_attempt.state is VideoShotState.VERIFIED


@pytest.mark.asyncio
async def test_download_failure_resumes_download_without_provider_poll_or_generation(tmp_path, monkeypatch):
    controller, store, job_id = await setup_job(tmp_path)
    provider = FakeProvider()
    provider.download_error = ProviderError("Transfer interrupted", code="provider_download_failed")
    assert not await controller._process_shot(job_id, "shot-001", provider)
    provider.download_error = None
    provider.poll_error = AssertionError("Provider unavailable after completion")
    monkeypatch.setattr("app.video_generation.mission_controller.verify_generated_clip", fake_verify)
    assert await controller._process_shot(job_id, "shot-001", provider)
    assert len(provider.submitted) == 1
    assert provider.downloads == 2
    assert len(provider.polls) == 1
    assert store.get_video_job(job_id).reserved_cost_usd == 1.6


@pytest.mark.asyncio
async def test_submission_intent_binds_authorization_inputs_and_unique_prefix(tmp_path):
    controller, store, job_id = await setup_job(tmp_path)
    job, shot, attempt = await controller._reserve(job_id, "shot-001")
    assert attempt.storage_uri.startswith(controller._compiled(job, "shot-001").storage_uri)
    assert job_id in attempt.storage_uri
    assert "attempt-01" in attempt.storage_uri
    assert attempt.authorization_digest
    assert attempt.request_digest
    assert attempt.submission_intent_at is None


@pytest.mark.asyncio
async def test_max_attempts_and_other_uncertain_reservation_block_paid_admission(tmp_path):
    controller, store, job_id = await setup_job(tmp_path)
    await controller._reserve(job_id, "shot-001")
    with pytest.raises(Exception, match="outstanding|uncertain"):
        await controller._reserve(job_id, "shot-001")
    job = store.get_video_job(job_id)
    attempt = job.shots[0].latest_attempt.model_copy(update={
        "state": VideoShotState.FAILED,
        "error": VideoError(code="provider_operation_failed", summary="Observed terminal failure"),
    })
    await controller._update_attempt(job_id, "shot-001", attempt)
    with pytest.raises(Exception, match="maximum|budget"):
        await controller._reserve(job_id, "shot-001")


@pytest.mark.asyncio
async def test_local_composition_never_submits_and_registers_verified_copy(tmp_path, monkeypatch):
    plan, references = prepared_plan(tmp_path)
    shot = replace(plan.shots[0], continuation_mode="local-composition", first_frame_reference=None,
                   storage_uri=None, estimated_cost_usd=0, duration_seconds=4)
    plan = replace(plan, shots=(shot,), continuity={}, base_estimated_cost_usd=0,
                   conservative_estimated_cost_usd=0).with_digest()
    controller, store = controller_at(tmp_path)
    view = await controller.create_prepared_plan(plan, gcp_project_id="test-project", gcs_bucket="test-bucket", reference_paths={})
    provider = FakeProvider()
    assert not await controller._process_shot(view.job_id, "shot-001", provider)
    assert controller.request_preview(view.job_id).requests == ()
    source = tmp_path / "approved-local.mp4"
    source.write_bytes(b"complete")
    from app.video_generation.jsonio import sha256_file
    monkeypatch.setattr("app.video_generation.mission_controller.verify_generated_clip", fake_verify)
    await controller.register_local_clip(view.job_id, "shot-001", source, expected_sha256=sha256_file(source))
    assert await controller._process_shot(view.job_id, "shot-001", provider)
    assert provider.submitted == []
    assert store.get_video_job(view.job_id).reserved_cost_usd == 0
    source.write_bytes(b"source changed after private import")
    assert Path(store.get_video_job(view.job_id).shots[0].latest_attempt.local_clip_path).read_bytes() == b"complete"


@pytest.mark.asyncio
async def test_projection_failure_does_not_change_accepted_submission(tmp_path):
    controller, store, job_id = await setup_job(tmp_path)
    async def unavailable(sequence):
        raise RuntimeError("observer offline")
    controller.notify_event = unavailable
    provider = FakeProvider()
    provider.poll_error = ProviderError("Reauthenticate", code="provider_authentication_required")
    await controller._process_shot(job_id, "shot-001", provider)
    assert len(provider.submitted) == 1
    assert store.get_video_job(job_id).shots[0].latest_attempt.operation_name.endswith("private-operation")


@pytest.mark.parametrize(("status", "body", "expected"), [
    (401, '{"error":{"message":"expired"}}', "provider_authentication_required"),
    (403, '{"error":{"message":"permission denied"}}', "provider_access_denied"),
    (403, '{"error":{"message":"billing disabled"}}', "provider_billing_disabled"),
    (429, '{"error":{"message":"rate limit"}}', "provider_rate_limited"),
    (404, '{"error":{"message":"model not found"}}', "provider_model_unavailable"),
])
def test_provider_errors_have_safe_remedy(status, body, expected, monkeypatch):
    import io
    import urllib.error

    from app.video_generation.gcp_veo import VeoRestClient
    def denied(*args, **kwargs):
        raise urllib.error.HTTPError("https://example.invalid", status, "denied", {}, io.BytesIO(body.encode()))
    monkeypatch.setattr("urllib.request.urlopen", denied)
    client = VeoRestClient(project_id="test-project", token_provider=lambda: "secret-test-token")
    with pytest.raises(ProviderError) as raised:
        client.fetch(model_id="veo-3.1-generate-001", operation_name="operations/id")
    assert raised.value.code == expected
    assert raised.value.remedy
    assert "secret-test-token" not in str(raised.value)


def test_legacy_paid_cli_cannot_bypass_mission_control():
    import argparse

    from app.video_generation.cli import command_submit_batch
    with pytest.raises(ValueError, match="Mission Control"):
        command_submit_batch(argparse.Namespace())


def test_transfer_is_pinned_and_partial_failure_cannot_publish(tmp_path, monkeypatch):
    import subprocess

    from app.video_generation import gcp_auth, gcp_veo
    calls = []
    def fake_run(arguments, **kwargs):
        calls.append(arguments)
        if "describe" in arguments:
            return subprocess.CompletedProcess([], 0, '{"generation":"42","size":"8"}', "")
        Path(arguments[-1]).write_bytes(b"complete")
        return subprocess.CompletedProcess([], 0, "", "")
    monkeypatch.setattr(gcp_auth, "run_bound_gcloud", fake_run)
    destination = tmp_path / "clip.mp4"
    gcp_veo.copy_gcs_uri("gs://test-bucket/run/clip.mp4", destination)
    assert destination.read_bytes() == b"complete"
    assert calls[-1][-2] == "gs://test-bucket/run/clip.mp4#42"


@pytest.mark.asyncio
async def test_changed_auth_binding_cannot_poll_or_submit(tmp_path, monkeypatch):
    from types import SimpleNamespace
    controller, store, job_id = await setup_job(tmp_path)
    store.put_video_job(store.get_video_job(job_id).model_copy(update={"credential_binding_digest": "a" * 64}))
    controller._uses_bound_credentials = True
    monkeypatch.setattr("app.video_generation.gcp_auth.load_binding", lambda **kwargs: SimpleNamespace(identity_digest="b" * 64))
    await controller.start(job_id)
    await controller._tasks[job_id]
    assert store.get_video_job(job_id).error.code == "auth_identity_mismatch"
    assert store.get_video_job(job_id).shots[0].attempts == ()


@pytest.mark.asyncio
async def test_live_owner_cannot_be_stolen_and_dead_exact_owner_is_archived(tmp_path, monkeypatch):
    from app.video_generation.jsonio import read_json
    controller, store, job_id = await setup_job(tmp_path)
    controller._claim_prepared_lease(store.get_video_job(job_id))
    path, token = controller._prepared_leases[job_id]
    receipt = read_json(path)
    assert receipt["jobId"] == job_id
    assert "processStartedAt" in receipt
    monkeypatch.setattr("app.mission_control.processes.process_is_alive", lambda pid: True)
    monkeypatch.setattr("app.mission_control.processes.process_started_at", lambda pid: None)
    with pytest.raises(ValueError, match="alive|uncertain"):
        controller.recover_stopped_worker(job_id, expected_token=token)
    monkeypatch.setattr("app.mission_control.processes.process_is_alive", lambda pid: False)
    with pytest.raises(ValueError, match="identity changed"):
        controller.recover_stopped_worker(job_id, expected_token="wrong")
    controller.recover_stopped_worker(job_id, expected_token=token)
    assert not path.exists()
    assert list((path.parent / "worker-history").glob("*.json"))


def test_corrupt_decoded_frames_fail_despite_valid_container_metadata(tmp_path, monkeypatch):
    from app.subprocess_utils import BoundedProcessResult
    from app.video_generation import media
    source = tmp_path / "corrupt.mp4"
    source.write_bytes(b"container can look valid while payload is corrupt")
    monkeypatch.setattr(media, "probe", lambda *args, **kwargs: MediaProbe(
        str(source), "a" * 64, 4, 1920, 1080, 24, "h264", "yuv420p", False))
    monkeypatch.setattr(media.shutil, "which", lambda name: "ffmpeg")
    monkeypatch.setattr(media, "run_process_bounded", lambda *args, **kwargs: BoundedProcessResult(
        returncode=1, stdout=b"", stderr=b"invalid NAL unit", stdout_exceeded=False, stderr_exceeded=False))
    with pytest.raises(ValueError, match="decode"):
        media.verify_generated_clip(source, resolution="1080p", aspect_ratio="16:9", expected_duration_seconds=4)


@pytest.mark.asyncio
async def test_crash_after_atomic_clip_promotion_reverifies_retained_file(tmp_path, monkeypatch):
    controller, store, job_id = await setup_job(tmp_path)
    job, shot, attempt = await controller._reserve(job_id, "shot-001")
    candidate = controller._job_root(job) / "clips" / "shot-001" / attempt.id / "provider.partial.mp4"
    candidate.parent.mkdir(parents=True)
    candidate.with_name("provider.mp4").write_bytes(b"complete")
    attempt = attempt.model_copy(update={"state": VideoShotState.DOWNLOADED, "local_clip_path": str(candidate)})
    await controller._update_attempt(job_id, "shot-001", attempt)
    monkeypatch.setattr("app.video_generation.mission_controller.verify_generated_clip", fake_verify)
    provider = FakeProvider()
    assert await controller._process_shot(job_id, "shot-001", provider)
    assert provider.submitted == [] and provider.downloads == 0


@pytest.mark.asyncio
async def test_corrupt_transfer_is_retained_then_redownloaded_without_new_spend(tmp_path, monkeypatch):
    from app.video_generation.contracts import ContractError
    controller, store, job_id = await setup_job(tmp_path)
    provider = FakeProvider()
    monkeypatch.setattr("app.video_generation.mission_controller.verify_generated_clip", lambda *args, **kwargs: (_ for _ in ()).throw(ContractError("video frame decode failed")))
    assert not await controller._process_shot(job_id, "shot-001", provider)
    monkeypatch.setattr("app.video_generation.mission_controller.verify_generated_clip", fake_verify)
    assert await controller._process_shot(job_id, "shot-001", provider)
    assert provider.downloads == 2
    assert len(provider.submitted) == 1


@pytest.mark.asyncio
async def test_bound_private_operation_receipt_repairs_acceptance_before_sqlite_update(tmp_path, monkeypatch):
    from app.video_generation.jsonio import atomic_write_json
    controller, store, job_id = await setup_job(tmp_path)
    job, shot, attempt = await controller._reserve(job_id, "shot-001")
    operation = "projects/test-project/locations/us-central1/publishers/google/models/veo-3.1-generate-001/operations/accepted-before-crash"
    atomic_write_json(controller._job_root(job) / "operations" / f"{attempt.id}.json", {
        "operationId": attempt.id, "planDigest": job.plan_digest, "shotId": shot.shot_id,
        "attempt": attempt.attempt, "operationName": operation,
        "reservedCostUsd": attempt.reserved_cost_usd,
    })
    provider = FakeProvider()
    provider.prefix = attempt.storage_uri
    monkeypatch.setattr("app.video_generation.mission_controller.verify_generated_clip", fake_verify)
    assert await controller._process_shot(job_id, "shot-001", provider)
    assert provider.submitted == []
    assert provider.polls == [operation]
    assert store.get_video_job(job_id).reserved_cost_usd == 1.6


@pytest.mark.asyncio
async def test_proven_token_failure_before_post_reuses_reservation_after_reauth(tmp_path, monkeypatch):
    controller, store, job_id = await setup_job(tmp_path)
    provider = FakeProvider()
    provider.submit_error = ProviderError("Token missing", code="auth_required", request_not_sent=True)
    assert not await controller._process_shot(job_id, "shot-001", provider)
    before = store.get_video_job(job_id)
    assert before.shots[0].latest_attempt.submission_intent_at is None
    assert before.shots[0].latest_attempt.error.code == "provider_pre_submission_authentication_required"
    provider.submit_error = None
    monkeypatch.setattr("app.video_generation.mission_controller.verify_generated_clip", fake_verify)
    assert await controller._process_shot(job_id, "shot-001", provider)
    after = store.get_video_job(job_id)
    assert len(after.shots[0].attempts) == 1
    assert after.reserved_cost_usd == before.reserved_cost_usd
