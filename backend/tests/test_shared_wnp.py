from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from app.local_video.orchestrator import ResumableLocalVideoRun, UnitResult
from app.schemas import AnalysisMode, JobStatus
from app.store import JobStore
from app.wnp import WNPProjection, emit_lifecycle, emit_video_job, load_wnp_kit
from app.wnp.check import describe, local_check, probe

from .helpers import settings_for


@pytest.fixture
def node(tmp_path, monkeypatch):
    store_module, _ = load_wnp_kit()
    store = store_module.Store.initialize(tmp_path / "wnp", "trackprompt-studio", "synthetic-node")
    monkeypatch.setenv("TRACKPROMPT_WNP_ROOT", str(store.root))
    monkeypatch.setenv("TRACKPROMPT_WNP_RUN", "synthetic-run")
    with WNPProjection(store.root) as projection:
        projection.select_run("synthetic-run")
    return store


def video_job():
    return {"id": "synthetic-run", "state": "generating", "createdAt": "2026-09-20T12:00:00Z",
            "updatedAt": "2026-09-20T12:02:00Z", "workerHeartbeatAt": "2026-09-20T12:01:00Z",
            "shots": [{"shotId": f"shot-{index:03d}", "title": "private photo", "prompt": "secret prompt",
                       "attempts": [{"id": str(uuid4()), "attempt": 1, "state": "verified" if index < 16 else "running",
                                     "clipSha256": "a" * 64, "probeEvidence": {"checked": True},
                                     "lastProviderObservationAt": "2026-09-20T12:01:10Z",
                                     "operationName": "projects/private/locations/secret/operations/" + str(index),
                                     "localClipPath": "C:/Private/customer.png"}]} for index in range(1, 17)]}


def test_sixteen_tasks_standard_progress_schema_and_privacy(node):
    result = emit_video_job(video_job())
    assert result.ok
    snapshot = node.snapshot()
    assert snapshot["progress"] == {"current": 15, "total": 16, "unit": "source-clips"}
    assert len(snapshot["extensions"]["trackprompt"]["tasks"]) == 16
    assert snapshot["state"] == "running"
    assert snapshot["observations"]["provider"]["job"].startswith("operation-")
    assert "metrics" not in snapshot["observations"]
    for path in node.runtime.iterdir():
        if path.is_file():
            for secret in (b"private photo", b"secret prompt", b"projects/private", b"customer.png"):
                assert secret not in path.read_bytes()
    importlib.import_module("wnp_host.model").snapshot(snapshot, node.node()["stream"])


def test_optional_jsonschema_validation(node):
    jsonschema = pytest.importorskip("jsonschema")
    emit_video_job(video_job())
    schema = json.loads((Path(__file__).resolve().parents[2] / "tools/wnp-trackprompt-kit/master/schemas/state.schema.json").read_text())
    jsonschema.Draft202012Validator(schema).validate(node.snapshot())


def test_all_lifecycle_stages_replay_in_order_without_new_wire_enum(node):
    stages = ["preparation", "analysis", "planning", "generation", "transfer", "verification", "finishing", "music-mux", "export"]
    for index, stage in enumerate(stages):
        assert emit_lifecycle(run="synthetic-run", source_id=f"transition-{index}", stage=stage, state="running").ok
    assert [e["snapshot"]["detail"] for e in node.events()] == ["trackprompt." + s for s in stages]
    model = importlib.import_module("wnp_host.model")
    for event in node.events():
        model.event(event)
    assert node.sync(node.node()["stream"], 0)["snapshot"]["state"] == "running"


def test_old_native_complete_is_not_final_verification_or_creative_approval(node):
    job = video_job()
    job.update(state="complete", previewPath="C:/private/delivery.mp4")
    assert emit_video_job(job).ok
    assert node.snapshot()["state"] == "waiting"
    assert describe(node.snapshot())["technical_verified"] is False
    assert emit_lifecycle(run="synthetic-run", source_id="verified-manifest", stage="export", state="waiting",
                          technical_verified=True, final_manifest_sha256="f" * 64).ok
    summary = describe(node.snapshot())
    assert summary["technical_verified"] is True
    assert summary["creative_approval"] == "pending"


def test_monitor_poll_does_not_refresh_worker_or_provider_or_history(node):
    job = video_job()
    assert emit_video_job(job).ok
    before = node.snapshot()
    assert emit_video_job(job).ok
    after = node.snapshot()
    assert before["sequence"] == after["sequence"]
    assert after["observations"]["provider"]["observed_at"] == "2026-09-20T12:01:10Z"
    summary = describe(after)
    assert summary["worker_freshness"] == summary["provider_freshness"] == "stale"


def test_job_selection_prevents_concurrent_overwrite_and_keeps_cursor(node, monkeypatch):
    assert emit_video_job(video_job()).ok
    stream = node.node()["stream"]
    other = video_job()
    other["id"] = "another-job"
    assert emit_video_job(other) is None
    monkeypatch.setenv("TRACKPROMPT_WNP_RUN", "another-job")
    assert emit_video_job(other).error == "job_selection_required"
    with WNPProjection(node.root) as projection:
        projection.select_run("another-job")
    assert emit_video_job(other).ok
    assert node.node()["stream"] == stream
    assert [e["snapshot"]["run"] for e in node.events()] == ["synthetic-run", "another-job"]


def test_native_analysis_commit_hook_and_outage_leave_execution_intact(node, tmp_path, monkeypatch):
    job_id = str(uuid4())
    monkeypatch.setenv("TRACKPROMPT_WNP_RUN", job_id)
    with WNPProjection(node.root) as projection:
        projection.select_run(job_id)
    jobs = JobStore(settings_for(tmp_path / "data"))
    jobs.create_job(job_id, AnalysisMode.FAST, "secret-track.wav", True, False)
    assert node.snapshot()["detail"] == "trackprompt.preparation"
    with WNPProjection(node.root):  # competing observer owner: hook fails, execution commits
        updated = jobs.update_job(job_id, status=JobStatus.ANALYZING_CORE, stage="features", progress=40)
    assert updated.progress == 40
    assert jobs.require_job(job_id).status is JobStatus.ANALYZING_CORE
    jobs.update_job(job_id, status=JobStatus.COMPLETED, stage="complete", progress=100)
    assert node.snapshot()["detail"] == "trackprompt.analysis"
    assert "secret-track" not in json.dumps(node.snapshot())


def test_actual_local_runner_sixteen_units_and_final_qc(node, tmp_path):
    class Executor:
        async def execute(self, stage, unit_id):
            return UnitResult(hashlib.sha256(f"{stage}:{unit_id}".encode()).hexdigest())
    runner = ResumableLocalVideoRun(tmp_path / "run.json", project_id="synthetic-project",
                                   revision_id="synthetic-run", package_digest="a" * 64,
                                   alternate_shot_ids=frozenset())
    result = asyncio.run(runner.run(Executor()))
    assert result["finalQcPassed"] is True
    snapshot = node.snapshot()
    assert snapshot["progress"]["current"] == snapshot["progress"]["total"] == 16
    assert describe(snapshot)["technical_verified"] is True
    assert describe(snapshot)["creative_approval"] == "pending"


def test_shared_extension_failed_ack_replays_once(node, monkeypatch):
    original = WNPProjection._ack
    monkeypatch.setattr(WNPProjection, "_ack", lambda *args: (_ for _ in ()).throw(OSError("secret")))
    result = emit_video_job(video_job())
    assert not result.ok and result.pending == 1
    assert len(node.events()) == 1
    monkeypatch.setattr(WNPProjection, "_ack", original)
    with WNPProjection(node.root) as projection:
        assert projection.drain().ok
    assert len(node.events()) == 1


def test_local_check_is_read_only_and_transport_unknown(node):
    emit_video_job(video_job())
    before = {p.name: p.read_bytes() for p in node.runtime.iterdir() if p.is_file()}
    result = local_check(node.root)
    assert result["transport"] == "not-checked"
    assert result["companion_ui"] == "unverified"
    assert before == {p.name: p.read_bytes() for p in node.runtime.iterdir() if p.is_file()}


def test_real_frozen_host_authenticated_transport_and_identity(node):
    emit_video_job(video_job())
    api = importlib.import_module("wnp_host.api")
    token = "synthetic-private-reader-token-" + "x" * 32
    auth = node.runtime / "auth"
    auth.mkdir()
    (auth / "read.token").write_text(token)
    server = api.make_server(node, token, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        origin = f"http://127.0.0.1:{server.server_address[1]}"
        result = probe(node.root, origin, expected_project="trackprompt-studio", expected_node="synthetic-node")
        assert result["transport"] == "authenticated"
        assert result["progress"]["total"] == 16
        assert result["companion_ui"] == "unverified"
        with pytest.raises(ValueError, match="identity_conflict"):
            probe(node.root, origin, expected_project="other-project", expected_node="synthetic-node")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


def test_frozen_master_hashes_preserved():
    kit = Path(__file__).resolve().parents[2] / "tools/wnp-trackprompt-kit"
    manifest = json.loads((kit / "MASTER_CONTENTS.json").read_text())
    for relative, expected in manifest["files"].items():
        assert hashlib.sha256((kit / "master" / relative).read_bytes()).hexdigest() == expected


def test_future_source_timestamp_is_not_fresh():
    from app.wnp.check import freshness
    assert freshness("2099-01-01T00:00:00Z", now=datetime(2026, 9, 22, tzinfo=UTC)) == "stale"



def test_provider_timestamp_belongs_to_displayed_operation(node):
    job = video_job()
    job["shots"][0]["attempts"][0]["lastProviderObservationAt"] = "2026-09-21T23:00:00Z"
    emit_video_job(job)
    assert node.snapshot()["observations"]["provider"]["observed_at"] == "2026-09-20T12:01:10Z"


def test_technical_claim_without_manifest_rejected(node):
    result = emit_lifecycle(run="synthetic-run", source_id="unproven", stage="export", technical_verified=True)
    assert result.error == "verification_manifest_required"
    assert node.events() == []


def test_worker_heartbeat_keeps_same_job_tasks_provider_and_cursor(node):
    from app.wnp import observe_worker
    assert emit_video_job(video_job()).ok
    before = node.snapshot()
    for second in range(3):
        assert observe_worker(run='synthetic-run', heartbeat_at=f'2026-09-22T13:00:0{second}Z', pid=42).ok
    after = node.snapshot()
    assert after['sequence'] == before['sequence']
    assert after['extensions'] == before['extensions']
    assert after['progress'] == before['progress']
    assert after['observations']['provider'] == before['observations']['provider']
    assert after['observations']['worker']['heartbeat_at'] == '2026-09-22T13:00:02Z'


def test_real_finishing_control_flow_emits_scene_mux_verify_export_receipts(node, tmp_path, monkeypatch):
    from app.video_generation import hayale_finishing as finishing
    from app.video_generation.hayale_plan import MusicEdit, MusicSegment
    monkeypatch.setattr(finishing, 'executable', lambda name: name)
    monkeypatch.setattr(finishing, 'run', lambda command: Path(command[-1]).write_bytes(b'synthetic-media'))
    monkeypatch.setattr(finishing, 'probe_streams', lambda path: {'streams': [{'codec_type': 'video', 'duration': '4'}]})
    monkeypatch.setattr(finishing, 'verify_delivery', lambda path, **kwargs: {'technicalVerified': True, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'frames': kwargs['frames']})
    def music(source, edit, target, **kwargs):
        target.write_bytes(b'synthetic-music')
        return {'originalPreserved': True}
    monkeypatch.setattr(finishing, 'arrange_music', music)
    for name in ('export_fcpxml', 'export_fcp7_xml', 'export_edl'):
        monkeypatch.setattr(finishing, name, lambda timeline, path: path.write_text('synthetic-interchange'))
    slots = [{'shotId': f'shot-{i:03d}', 'timelineFrames': 96, 'startFrame': (i-1)*96,
              'mode': 'local-composition'} for i in range(1, 17)]
    source = tmp_path / 'source.mp4'
    source.write_bytes(b'synthetic-source')
    plan = {'title': 'Private business name', 'projectId': 'synthetic-project', 'planDigest': 'd'*64,
            'profile': {'resolution': '720p'}, 'continuity': {'hayaleEdit': {'fps':24, 'totalFrames':1536, 'scenes':slots}}}
    result = finishing.finish_edit(plan, {slot['shotId']: source for slot in slots}, music_source=source,
                                   music_edit=MusicEdit(source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(), approved=True,
                                                       segments=(MusicSegment(source_in=0,duration=64),)),
                                   output_root=tmp_path/'output', wnp_run='synthetic-run')
    events = node.events()
    stages = [event['snapshot']['detail'] for event in events]
    assert stages[:17] == ['trackprompt.finishing'] * 17
    assert stages[17:] == ['trackprompt.music-mux', 'trackprompt.verification', 'trackprompt.export', 'trackprompt.export']
    assert len({event['key'] for event in events}) == len(events)
    assert len(result['sceneVerification']) == 16
    # The operator persists native COMPLETE before its final verified projection.
    native = video_job()
    native['state'] = 'complete'
    assert emit_video_job(native).ok
    manifest = Path(result['exportRoot']) / 'verification.json'
    assert emit_lifecycle(run='synthetic-run', source_id='final-native-commit', stage='export',
                          technical_verified=True, final_manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest(),
                          state='waiting', tasks=result['sceneVerification']).ok
    assert describe(node.snapshot())['technical_verified'] is True
    assert node.snapshot()['progress'] == {'current': 16, 'total': 16, 'unit': 'scenes'}
    assert 'Private business name' not in json.dumps(node.snapshot())


@pytest.mark.parametrize(('code', 'action'), [
    ('provider_submission_outcome_unknown', 'reconcile'),
    ('auth_identity_mismatch', 'reconcile'),
    ('provider_authentication_required', 'reauthenticate'),
    ('provider_access_denied', 'repair-access'),
    ('local_disk_exhausted', 'repair-artifact'),
])
def test_error_actions_use_native_recovery_meaning(node, code, action):
    job = video_job()
    job['error'] = {'code': code, 'retryable': False, 'summary': 'private resource and credential context'}
    assert emit_video_job(job).ok
    pipeline = node.snapshot()['extensions']['trackprompt']['pipeline']
    assert pipeline['action'] == action
    assert pipeline['remedy']
    assert 'private resource' not in json.dumps(node.snapshot())
