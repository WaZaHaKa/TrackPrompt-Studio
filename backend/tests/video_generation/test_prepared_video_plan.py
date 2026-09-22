from dataclasses import replace
from pathlib import Path

import pytest

from app.mission_control.store import MissionControlStore
from app.video_generation.authorization import authorization_phrase
from app.video_generation.contracts import (
    CompiledPlan,
    CompiledReferenceImage,
    CompiledShot,
    GenerationProfile,
)
from app.video_generation.jsonio import sha256_file
from app.video_generation.mission_controller import VideoGenerationController
from app.video_generation.mission_models import (
    VideoAuthorizationRequest,
    VideoJobState,
    VideoRetryRequest,
    VideoReviewRequest,
    VideoReviewState,
    VideoShotState,
)


def prepared_plan(root: Path):
    source = root / "reference.png"
    source.write_bytes(b"test reference bytes")
    profile = GenerationProfile("quality-1080p", "veo-3.1-generate-001", "1080p")
    reference = CompiledReferenceImage(
        "reference-001",
        "gs://test-bucket/references/one.png",
        "image/png",
        sha256_file(source),
        "prepared-reference",
    )
    shot = CompiledShot(
        "shot-001",
        "scene-01",
        1,
        "Storefront",
        8,
        "A restrained camera move showing the supplied authentic storefront.",
        "No new architecture or text.",
        42,
        profile.model_id,
        "1080p",
        "16:9",
        1,
        False,
        True,
        "optimized",
        "disallow",
        "gs://test-bucket/output/shot-001/",
        True,
        1.6,
        (),
        (),
        first_frame_reference=reference,
    )
    plan = CompiledPlan(
        "1.0.0",
        "prepared-promo",
        "Prepared promo",
        profile,
        (shot,),
        1.6,
        1.6,
        1.6,
        {},
        rate_usd_per_output_second=0.2,
        continuity={"executionPolicy": "foreground-prepared-single-owner", "masterSeed": 42},
    ).with_digest()
    return plan, {reference.asset_id: source}


def controller_at(root: Path):
    (root / "video-projects/prepared-promo").mkdir(parents=True)
    store = MissionControlStore(root / "runtime/mission-control/state.sqlite3")

    async def notify(sequence):
        pass

    def forbidden_provider(*args):
        raise AssertionError("Preparing a job must not instantiate the provider")

    return VideoGenerationController(
        repository_root=root,
        state_root=root / "runtime/mission-control",
        store=store,
        notify_event=notify,
        provider_factory=forbidden_provider,
    ), store


@pytest.mark.asyncio
async def test_prepared_plan_uses_canonical_job_store_without_analysis_or_provider(tmp_path):
    plan, references = prepared_plan(tmp_path)
    controller, store = controller_at(tmp_path)
    first = await controller.create_prepared_plan(
        plan, gcp_project_id="test-project", gcs_bucket="test-bucket", reference_paths=references
    )
    second = await controller.create_prepared_plan(
        plan, gcp_project_id="test-project", gcs_bucket="test-bucket", reference_paths=references
    )
    assert first.job_id == second.job_id
    job = store.get_video_job(first.job_id)
    assert job.analysis_job_id == ""
    assert job.authorization is None
    assert job.reserved_cost_usd == 0
    assert job.shots[0].attempts == ()
    assert job.reference_assets["reference-001"]["sha256"] == plan.shots[0].first_frame_reference.sha256


@pytest.mark.asyncio
async def test_prepared_plan_rejects_changed_reference_before_job_creation(tmp_path):
    plan, references = prepared_plan(tmp_path)
    references["reference-001"].write_bytes(b"changed")
    controller, store = controller_at(tmp_path)
    with pytest.raises(ValueError, match="reference"):
        await controller.create_prepared_plan(
            plan, gcp_project_id="test-project", gcs_bucket="test-bucket", reference_paths=references
        )
    assert store.list_video_jobs() == []


@pytest.mark.asyncio
async def test_prepared_plan_rejects_stale_digest_and_foreign_bucket(tmp_path):
    plan, references = prepared_plan(tmp_path)
    controller, store = controller_at(tmp_path)
    with pytest.raises(ValueError, match="digest"):
        await controller.create_prepared_plan(
            replace(plan, title="changed"),
            gcp_project_id="test-project",
            gcs_bucket="test-bucket",
            reference_paths=references,
        )
    with pytest.raises(ValueError, match="bucket"):
        await controller.create_prepared_plan(
            plan, gcp_project_id="test-project", gcs_bucket="different-bucket", reference_paths=references
        )
    assert store.list_video_jobs() == []


@pytest.mark.asyncio
async def test_prepared_job_never_auto_resumes_on_service_start(tmp_path, monkeypatch):
    plan, references = prepared_plan(tmp_path)
    controller, store = controller_at(tmp_path)
    view = await controller.create_prepared_plan(
        plan, gcp_project_id="test-project", gcs_bucket="test-bucket", reference_paths=references
    )
    job = store.get_video_job(view.job_id)
    store.put_video_job(job.model_copy(update={"state": VideoJobState.GENERATING}))

    def forbidden_schedule(job_id):
        raise AssertionError("Foreground jobs must not auto-resume")

    monkeypatch.setattr(controller, "_schedule", forbidden_schedule)
    controller.start_background_tasks()


@pytest.mark.asyncio
async def test_stale_registration_cannot_erase_reserved_progress(tmp_path, monkeypatch):
    plan, references = prepared_plan(tmp_path)
    controller, store = controller_at(tmp_path)
    view = await controller.create_prepared_plan(
        plan, gcp_project_id="test-project", gcs_bucket="test-bucket", reference_paths=references
    )
    job = store.get_video_job(view.job_id).model_copy(
        update={"state": VideoJobState.GENERATING, "reserved_cost_usd": 1.6}
    )
    store.put_video_job(job)
    real_get = store.get_video_job
    first_read = [True]

    def stale_get(job_id):
        if first_read[0]:
            first_read[0] = False
            return None
        return real_get(job_id)

    monkeypatch.setattr(store, "get_video_job", stale_get)
    await controller.create_prepared_plan(
        plan, gcp_project_id="test-project", gcs_bucket="test-bucket", reference_paths=references
    )
    assert real_get(view.job_id).reserved_cost_usd == 1.6
    assert real_get(view.job_id).state is VideoJobState.GENERATING


@pytest.mark.asyncio
async def test_losing_controller_cannot_change_authorization_or_reserved_progress(tmp_path):
    plan, references = prepared_plan(tmp_path)
    owner, store = controller_at(tmp_path)
    view = await owner.create_prepared_plan(
        plan, gcp_project_id="test-project", gcs_bucket="test-bucket", reference_paths=references
    )
    request = VideoAuthorizationRequest(
        confirmation=authorization_phrase(plan.project_id, plan.plan_digest, plan.max_spend_usd)
    )
    await owner.authorize(view.job_id, request)
    owner._claim_prepared_lease(store.get_video_job(view.job_id))
    await owner._reserve(view.job_id, "shot-001")
    other_store = MissionControlStore(store.database_path)
    other = VideoGenerationController(
        repository_root=tmp_path,
        state_root=tmp_path / "runtime/mission-control",
        store=other_store,
        notify_event=owner.notify_event,
    )
    before = store.get_video_job(view.job_id).model_dump_json()
    try:
        with pytest.raises(Exception, match="existing worker lease"):
            await other.start(view.job_id)
        assert store.get_video_job(view.job_id).model_dump_json() == before
        with pytest.raises(Exception, match="existing worker lease"):
            await other.authorize(view.job_id, request)
        assert store.get_video_job(view.job_id).model_dump_json() == before
        # The reservation has no operation name: it may not be submitted again.
        assert await owner._process_shot(view.job_id, "shot-001", None) is False
        assert store.get_video_job(view.job_id).model_dump_json() == before
        with pytest.raises(Exception, match="foreground owner"):
            await other.cancel(view.job_id)
        assert store.get_video_job(view.job_id).model_dump_json() == before
        stale = store.get_video_job(view.job_id).model_copy(
            update={"reserved_cost_usd": 0, "state": VideoJobState.PLANNED}
        )
        with pytest.raises(Exception, match="foreground owner"):
            await other._save(stale)
        assert store.get_video_job(view.job_id).model_dump_json() == before
        job = store.get_video_job(view.job_id)
        attempt = job.shots[0].latest_attempt.model_copy(update={"state": VideoShotState.VERIFIED})
        verified_shot = job.shots[0].model_copy(update={"attempts": (attempt,)})
        store.put_video_job(
            job.model_copy(update={"shots": (verified_shot,), "state": VideoJobState.REVIEW_READY})
        )
        before = store.get_video_job(view.job_id).model_dump_json()
        root = owner._job_root(job)
        artifacts = {str(path): path.read_bytes() for path in root.rglob("*.json")}
        with pytest.raises(Exception, match="foreground owner"):
            await other.retry(view.job_id, "shot-001", VideoRetryRequest(mode="new_variation"))
        with pytest.raises(Exception, match="foreground owner"):
            await other.review(
                view.job_id, "shot-001", VideoReviewRequest(decision=VideoReviewState.ACCEPTED)
            )
        with pytest.raises(Exception, match="foreground owner"):
            await other.bind_audio(view.job_id, tmp_path / "never-open.wav", source="local-selection")
        assert store.get_video_job(view.job_id).model_dump_json() == before
        assert {str(path): path.read_bytes() for path in root.rglob("*.json")} == artifacts
    finally:
        owner._release_prepared_lease(view.job_id)
        other_store.close()
