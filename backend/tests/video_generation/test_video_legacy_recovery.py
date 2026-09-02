from __future__ import annotations

import json
import shutil
from pathlib import Path
from uuid import uuid4

import pytest

from app.mission_control.store import MissionControlStore
from app.video_generation.audio import AudioEvidence, StagedAudio
from app.video_generation.jsonio import sha256_file
from app.video_generation.mission_controller import VideoGenerationController
from app.video_generation.mission_models import (
    VideoAnalysisDependencyState,
    VideoAudioBinding,
    VideoAuthorizationRequest,
    VideoJobState,
    VideoPlanCreateRequest,
    VideoReviewState,
    VideoShotAttempt,
    VideoShotState,
    utc_now,
)

PACKAGE_ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.asyncio
async def test_legacy_repair_and_normalized_timeline_preserve_paid_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_root = tmp_path / "state" / "mission-control"
    analysis_id = str(uuid4())
    analysis_root = state_root.parent / "jobs" / analysis_id
    analysis_root.mkdir(parents=True)
    (analysis_root / "analysis.json").write_text("{}\n", encoding="utf-8")
    store = MissionControlStore(state_root / "mission-control.sqlite3")
    provider_calls = 0

    def forbidden_provider(_project: str, _region: str):
        nonlocal provider_calls
        provider_calls += 1
        raise AssertionError("legacy repair must not instantiate a provider")

    async def notify(_: int) -> None:
        return None

    controller = VideoGenerationController(
        repository_root=PACKAGE_ROOT,
        state_root=state_root,
        store=store,
        notify_event=notify,
        provider_factory=forbidden_provider,
    )
    captured_shot_plan: list[Path | None] = []

    def fake_resolve_timeline(**values):  # type: ignore[no-untyped-def]
        captured_shot_plan.append(values["analysis_shot_plan_path"])
        return {
            "schemaVersion": "1.0.0",
            "timeline": {"durationSeconds": 218.32, "width": 1920, "height": 1080},
            "segments": [],
        }

    monkeypatch.setattr(
        "app.video_generation.mission_controller.resolve_timeline",
        fake_resolve_timeline,
    )
    try:
        planned = await controller.create_plan(
            VideoPlanCreateRequest(
                analysis_job_id=analysis_id,
                project_id="static-into-signal",
                profile_id="quality-1080p",
                gcp_project_id="test-project",
                gcs_bucket="example-trackprompt-video",
            )
        )
        await controller.authorize(
            planned.job_id,
            VideoAuthorizationRequest(confirmation=planned.authorization_phrase),
        )
        job = store.get_video_job(planned.job_id)
        assert job is not None
        job_root = controller._job_root(job)
        shutil.rmtree(job_root / "inputs")

        audio = job_root / "audio" / "artifacts" / "original" / "master.wav"
        audio.parent.mkdir(parents=True)
        audio.write_bytes(b"durable local audio")
        audio_sha256 = sha256_file(audio)
        binding = VideoAudioBinding(
            source="local-selection",
            audio_artifact_id=f"audio-{audio_sha256[:20]}",
            display_name="Static Into Signal master.wav",
            source_runtime_path=str(audio),
            artifact_path=str(audio),
            finishing_path=str(audio),
            sha256=audio_sha256,
            finishing_sha256=audio_sha256,
            container="wav",
            audio_codec="pcm_s16le",
            sample_rate_hz=48_000,
            channels=2,
            duration_seconds=218.32,
            analysis_job_id=analysis_id,
            bound_video_job_id=job.id,
            selected_at=utc_now(),
        )
        shots = []
        for shot in job.shots:
            clip = job_root / "clips" / shot.shot_id / "accepted" / "provider.mp4"
            clip.parent.mkdir(parents=True)
            clip.write_bytes(f"verified-{shot.shot_id}".encode())
            attempt = VideoShotAttempt(
                id=f"{shot.shot_id}-attempt-01-test",
                attempt=1,
                idempotency_key=sha256_file(clip),
                state=VideoShotState.VERIFIED,
                reserved_cost_usd=shot.estimated_cost_usd,
                operation_name=f"operations/{shot.shot_id}",
                local_clip_path=str(clip),
                clip_sha256=sha256_file(clip),
                created_at=utc_now(),
                updated_at=utc_now(),
            )
            shots.append(
                shot.model_copy(
                    update={
                        "attempts": (attempt,),
                        "review_state": VideoReviewState.ACCEPTED,
                        "accepted_attempt_id": attempt.id,
                    }
                )
            )
        legacy = job.model_copy(
            update={
                "state": VideoJobState.FAILED,
                "shots": tuple(shots),
                "reserved_cost_usd": sum(shot.estimated_cost_usd for shot in job.shots),
                "audio_path": str(audio),
                "audio_binding": binding,
                "analysis_dependency_state": VideoAnalysisDependencyState.LEGACY_ANALYSIS_MISSING,
                "dependency_manifest_path": None,
            }
        )
        store.put_video_job(legacy)
        shutil.rmtree(analysis_root)
        before = json.loads(legacy.model_dump_json())

        repaired = await controller.repair_legacy_dependency(legacy.id)
        repaired_again = await controller.repair_legacy_dependency(legacy.id)
        assert repaired.state is VideoJobState.REVIEW_READY
        assert repaired_again.state is VideoJobState.REVIEW_READY
        assert repaired.analysis_dependency.source_state is VideoAnalysisDependencyState.LEGACY_ANALYSIS_MISSING
        assert repaired.analysis_dependency.manifest_ready is True
        assert repaired.analysis_dependency.shot_plan_available is False
        repaired_record = store.get_video_job(legacy.id)
        assert repaired_record is not None
        after = json.loads(repaired_record.model_dump_json())
        assert after["plan_digest"] == before["plan_digest"]
        assert after["authorization"] == before["authorization"]
        assert after["reserved_cost_usd"] == before["reserved_cost_usd"]
        assert after["shots"] == before["shots"]

        monkeypatch.setattr(
            "app.video_generation.mission_controller.probe_audio",
            lambda *_args, **_kwargs: AudioEvidence(
                path=audio,
                sha256=audio_sha256,
                duration_seconds=218.32,
                container="wav",
                codec="pcm_s16le",
                sample_rate_hz=48_000,
                channels=2,
                size_bytes=audio.stat().st_size,
            ),
        )
        resolved = await controller.resolve(legacy.id)
        assert resolved.state is VideoJobState.TIMELINE_READY
        assert captured_shot_plan == [None]
        timeline = json.loads(Path(store.get_video_job(legacy.id).timeline_path).read_text(encoding="utf-8"))
        assert any("normalized chapter timing" in item for item in timeline["warnings"])
        assert provider_calls == 0
    finally:
        controller.close()
        store.close()


@pytest.mark.asyncio
async def test_audio_hash_mismatch_requires_explicit_local_delivery_revision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_root = tmp_path / "state" / "mission-control"
    analysis_id = str(uuid4())
    analysis_root = state_root.parent / "jobs" / analysis_id
    analysis_root.mkdir(parents=True)
    (analysis_root / "analysis.json").write_text("{}\n", encoding="utf-8")
    store = MissionControlStore(state_root / "mission-control.sqlite3")
    provider_calls = 0

    def forbidden_provider(_project: str, _region: str):
        nonlocal provider_calls
        provider_calls += 1
        raise AssertionError("audio repair must not instantiate a provider")

    async def notify(_: int) -> None:
        return None

    controller = VideoGenerationController(
        repository_root=PACKAGE_ROOT,
        state_root=state_root,
        store=store,
        notify_event=notify,
        provider_factory=forbidden_provider,
    )

    def fake_stage(source: Path, **_kwargs: object) -> StagedAudio:
        evidence = AudioEvidence(
            path=source,
            sha256=sha256_file(source),
            duration_seconds=218.32,
            container="wav",
            codec="pcm_s16le",
            sample_rate_hz=48_000,
            channels=2,
            size_bytes=source.stat().st_size,
        )
        return StagedAudio(source=evidence, artifact=evidence, finishing=evidence, display_name=source.name)

    monkeypatch.setattr("app.video_generation.mission_controller.stage_audio_master", fake_stage)
    original = tmp_path / "original.wav"
    replacement = tmp_path / "replacement.wav"
    original.write_bytes(b"original master")
    replacement.write_bytes(b"replacement master")
    try:
        planned = await controller.create_plan(
            VideoPlanCreateRequest(
                analysis_job_id=analysis_id,
                project_id="static-into-signal",
                profile_id="quality-1080p",
                gcp_project_id="test-project",
                gcs_bucket="example-trackprompt-video",
            )
        )
        await controller.authorize(
            planned.job_id,
            VideoAuthorizationRequest(confirmation=planned.authorization_phrase),
        )
        bound = await controller.bind_audio(planned.job_id, original, source="local-selection")
        assert bound.selected is True
        before = store.get_video_job(planned.job_id)
        assert before is not None

        rejected = await controller.bind_audio(planned.job_id, replacement, source="local-selection")
        assert rejected.selected is False
        assert rejected.error is not None
        assert rejected.error.code == "audio_hash_mismatch_confirmation_required"
        assert rejected.error.expected_hash_prefix == sha256_file(original)[:12]
        assert rejected.error.selected_hash_prefix == sha256_file(replacement)[:12]
        assert rejected.error.confirmation_phrase

        accepted = await controller.bind_audio(
            planned.job_id,
            replacement,
            source="local-selection",
            accept_local_delivery_revision=True,
            confirmation=rejected.error.confirmation_phrase,
        )
        assert accepted.selected is True
        after = store.get_video_job(planned.job_id)
        assert after is not None and after.local_delivery_revision is not None
        assert after.plan_digest == before.plan_digest
        assert after.authorization == before.authorization
        assert after.reserved_cost_usd == before.reserved_cost_usd
        assert after.local_delivery_revision["selectedSha256"] == sha256_file(replacement)
        assert provider_calls == 0
    finally:
        controller.close()
        store.close()
