from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import timedelta

import pytest

from app.mission_control.models import (
    EncodeJobStatus,
    JobPhase,
    JobRecord,
    JobState,
    RendererKind,
    RenderIdentity,
)
from app.mission_control.render_contracts import (
    EncodeTask,
    OutputVariantArtifact,
    QaCheck,
    QaResult,
    QaStatus,
    StageProgress,
)
from app.mission_control.store import MissionControlStore
from app.wnp import WNPProjection, load_wnp_kit
from app.wnp.check import describe

from .test_render_contracts import NOW, _job, _variant


@pytest.fixture
def native_store(tmp_path, monkeypatch):
    module, _ = load_wnp_kit()
    node = module.Store.initialize(tmp_path / "wnp", "trackprompt-studio", "synthetic-render")
    monkeypatch.setenv("TRACKPROMPT_WNP_ROOT", str(node.root))
    monkeypatch.setenv("TRACKPROMPT_WNP_RUN", "job-1")
    with WNPProjection(node.root) as projection:
        projection.select_run("job-1")
    store = MissionControlStore(tmp_path / "native.sqlite3")
    yield store, node
    store.close()


def render_job(**changes):
    value = JobRecord(id="job-1", renderer=RendererKind.PRODUCTION, state=JobState.RUNNING,
        phase=JobPhase.RENDER_FRAME, identity=RenderIdentity(project_id="private-project", scene_id="private-customer",
            scene_sha256="a" * 64, profile_id="render-final", profile_sha256="b" * 64,
            output_directory="C:/Private/source-photo.png"), created_at=NOW, updated_at=NOW,
        frame_start=1, frame_end=120, total_frame_count=120, chunks_total=1,
        latest_log_line="private credential and soundtrack", gpu_utilization_percent=50)
    return value.model_copy(update=changes)


def verified_media_job():
    variant = _variant("landscape", enabled=True, required=True, width=1920, height=1080)
    encode = EncodeTask(id="encode-1", job_id="job-1", output_variant_id=variant.id, deliverable_role="delivery",
        state="complete", manifest_sha256="a" * 64, input_frames_root=variant.frames_root,
        output_artifact_key="private/customer-delivery.mp4", completed_units=120, total_units=120, updated_at=NOW)
    qa = QaResult(id="qa-1", job_id="job-1", output_variant_id=variant.id, status="pass",
        checks=(QaCheck(id="media-contract", status="pass", summary="private QA details"),),
        report_artifact_key="private/customer-qa.json", report_sha256="b" * 64, completed_at=NOW)
    artifact = OutputVariantArtifact(id="delivery", job_id="job-1", output_variant_id=variant.id,
        deliverable_role="delivery", artifact_key=encode.output_artifact_key, sha256="c" * 64,
        byte_size=100, width=1920, height=1080, fps=30, created_at=NOW)
    progress = variant.progress.model_copy(update={
        "rendered_frames": 120, "validated_frames": 120, "encode_tasks": (encode,),
        "qa_results": (qa,), "artifacts": (artifact,),
        "stages": (StageProgress(stage="final-qa", state="complete", completed_units=1,
                                 total_units=1, updated_at=NOW),),
    })
    return _job(variant.model_copy(update={"progress": progress}))


def test_real_render_store_phase_hooks_run_after_commit(native_store, monkeypatch):
    store, node = native_store
    import app.mission_control.store as store_module
    original = store_module.emit_render_job
    def after_commit(payload, **kwargs):
        assert not store._connection.in_transaction
        with sqlite3.connect(store.database_path) as connection:
            assert connection.execute("SELECT count(*) FROM mission_control_jobs").fetchone()[0] == 1
        return original(payload, **kwargs)
    monkeypatch.setattr(store_module, "emit_render_job", after_commit)
    phases = [(JobPhase.SCENE_LOAD, "preparation"), (JobPhase.RENDER_FRAME, "generation"),
              (JobPhase.PUBLISH_CHUNK, "transfer"), (JobPhase.ENCODE_MASTER, "finishing"),
              (JobPhase.MUX_AUDIO, "music-mux"), (JobPhase.FINAL_VERIFY, "verification")]
    for index, (phase, stage) in enumerate(phases):
        store.put_job(render_job(phase=phase, updated_at=NOW + timedelta(seconds=index)))
        assert node.snapshot()["detail"] == "trackprompt." + stage
    snapshot = node.snapshot()
    assert snapshot["observations"]["worker"]["heartbeat_at"] is None
    assert snapshot["observations"]["provider"]["state"] == "unknown"
    assert "metrics" not in snapshot["observations"]
    for path in node.runtime.iterdir():
        if path.is_file():
            assert b"private credential" not in path.read_bytes()
            assert b"source-photo" not in path.read_bytes()


def test_render_frame_progress_is_transient_and_monitor_failure_does_not_stop_native_commit(native_store):
    store, node = native_store
    for count in range(1, 4):
        store.put_job(render_job(rendered_frame_count=count, updated_at=NOW + timedelta(seconds=count)))
    assert len(node.events()) == 1
    assert node.snapshot()["observations"]["progress"] == {"current": 3, "total": 120, "unit": "rendered-frames"}
    with WNPProjection(node.root):
        store.put_job(render_job(rendered_frame_count=4))
    assert store.get_job("job-1").rendered_frame_count == 4


def test_native_complete_without_hash_bound_qa_never_claims_delivery(native_store):
    store, node = native_store
    store.put_job(render_job(state=JobState.COMPLETE, phase=JobPhase.FINAL_VERIFY, validated_frame_count=120))
    assert node.snapshot()["state"] == "waiting"
    assert describe(node.snapshot())["technical_verified"] is False


def test_media_store_requires_bound_qa_and_artifacts_then_reports_verified_record(native_store):
    store, node = native_store
    job = verified_media_job()
    progress = job.output_variants[0].progress
    variant = job.output_variants[0].model_copy(update={"progress": progress.model_copy(update={"qa_results": ()})})
    store.put_media_render_job(job.model_copy(update={"output_variants": (variant,)}))
    assert describe(node.snapshot())["technical_verified"] is False
    store.put_media_render_job(job)
    snapshot = node.snapshot()
    assert describe(snapshot)["technical_verified"] is True
    assert describe(snapshot)["creative_approval"] == "pending"
    expected = hashlib.sha256(job.model_dump_json(by_alias=False).encode()).hexdigest()
    assert snapshot["extensions"]["trackprompt"]["final_manifest_sha256"] == expected
    assert "customer-delivery" not in json.dumps(snapshot)


def test_later_failed_qa_prevents_reusing_older_pass(native_store):
    store, node = native_store
    job = verified_media_job()
    progress = job.output_variants[0].progress
    failed = progress.qa_results[0].model_copy(update={"id": "qa-2", "status": QaStatus.FAIL,
        "completed_at": NOW + timedelta(seconds=10),
        "checks": (QaCheck(id="media-contract", status="fail", summary="private corrupt media"),)})
    variant = job.output_variants[0].model_copy(update={"progress": progress.model_copy(update={"qa_results": (*progress.qa_results, failed)})})
    store.put_media_render_job(job.model_copy(update={"output_variants": (variant,)}))
    assert describe(node.snapshot())["technical_verified"] is False


def test_media_update_does_not_erase_precise_native_mux_phase(native_store):
    store, node = native_store
    store.put_job(render_job(state=JobState.ENCODING, phase=JobPhase.MUX_AUDIO))
    variant = _variant("landscape", enabled=True, required=True, width=1920, height=1080)
    variant = variant.model_copy(update={"progress": variant.progress.model_copy(update={
        "stages": (StageProgress(stage="encoding", state="running", completed_units=3,
                                 total_units=120, unit="frames", updated_at=NOW),)})})
    store.put_media_render_job(_job(variant))
    assert node.snapshot()["detail"] == "trackprompt.music-mux"


def test_standalone_encode_setting_hook_is_typed_and_keeps_completion_unverified(native_store):
    store, node = native_store
    status = EncodeJobStatus(id="encode-1", render_job_id="job-1", status="encoding", progress=25,
        output_kinds=["delivery"], total_frames=120, updated_at=NOW, detail="private encoder log",
        output_paths={"delivery": "C:/Private/customer.mp4"})
    store.put_setting("unrelated-settings", status.model_dump(mode="json"))
    assert node.events() == []
    for index, (state, stage) in enumerate([("encoding", "finishing"), ("verifying", "verification"), ("complete", "export")]):
        record = status.model_copy(update={"status": state, "updated_at": NOW + timedelta(seconds=index)})
        store.put_setting("encode_job:job-1", record.model_dump(mode="json"))
        assert node.snapshot()["detail"] == "trackprompt." + stage
    assert node.snapshot()["state"] == "waiting"
    assert describe(node.snapshot())["technical_verified"] is False
    assert node.snapshot()["observations"]["progress"]["current"] == 25
    assert "Private" not in json.dumps(node.snapshot())
