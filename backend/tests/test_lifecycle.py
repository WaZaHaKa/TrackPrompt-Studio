from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import sys
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest

import app.adapters as adapters_module
import app.jobs as jobs_module
import app.media as media_module
import app.store as store_module
import app.subprocess_utils as subprocess_utils_module
from app.adapters import deep_adapters, run_demucs
from app.diagnostics.provision_demucs import provision_demucs_repository
from app.jobs import JobManager
from app.main import _media_type, _sse_event_name
from app.media import MediaCancelled, MediaValidationError, probe_media, sanitize_display_name
from app.schemas import AnalysisMode, JobStatus, LyricsAnalysisSummary
from app.security import LocalRequestBoundaryMiddleware
from app.store import DeletionError, JobStore, utc_now
from app.subprocess_utils import BoundedProcessResult, ProcessTimedOut, run_process_bounded

from .helpers import settings_for


def test_media_validation_uses_probe_not_extension(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "misleading.exe"
    path.write_bytes(b"synthetic payload")
    monkeypatch.setattr(
        media_module,
        "_run_probe",
        lambda _path, _settings, _cancel_requested=None: {
            "streams": [
                {
                    "codec_type": "audio",
                    "codec_name": "flac",
                    "sample_rate": "44100",
                    "channels": 2,
                }
            ],
            "format": {"format_name": "flac", "duration": "2.5", "bit_rate": "800000"},
        },
    )
    probe = probe_media(path, "../../track.exe", settings_for(tmp_path / "data"))
    assert probe.file.codec == "flac"
    assert probe.file.container == "flac"
    assert probe.file.display_name == "track.exe"


def test_media_validation_rejects_unsupported_actual_stream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "fake.wav"
    path.write_bytes(b"synthetic payload")
    monkeypatch.setattr(
        media_module,
        "_run_probe",
        lambda _path, _settings, _cancel_requested=None: {
            "streams": [
                {"codec_type": "video", "codec_name": "h264", "sample_rate": None, "channels": None}
            ],
            "format": {"format_name": "mp4", "duration": "2"},
        },
    )
    with pytest.raises(MediaValidationError) as error:
        probe_media(path, "fake.wav", settings_for(tmp_path / "data"))
    assert error.value.code == "no_audio_stream"
    assert str(path) not in error.value.safe_message


def test_display_name_removes_paths_controls_and_bidi() -> None:
    assert sanitize_display_name("../folder/track\u202egp3.wav") == "trackgp3.wav"
    assert sanitize_display_name("..\\..\\\x00name.mp3") == "name.mp3"


def test_media_probe_forwards_cancellation_and_maps_it_safely(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "probe.wav"
    path.write_bytes(b"synthetic private payload")
    cancellation_checks = 0

    def cancelled_process(*_args, **kwargs):
        nonlocal cancellation_checks
        cancel_requested = kwargs["cancel_requested"]
        assert cancel_requested is not None
        cancellation_checks += 1
        assert cancel_requested()
        raise subprocess_utils_module.ProcessWasCancelled

    monkeypatch.setattr(media_module, "run_process_bounded", cancelled_process)
    with pytest.raises(MediaCancelled, match="cancelled"):
        probe_media(
            path,
            "probe.wav",
            settings_for(tmp_path / "data"),
            cancel_requested=lambda: True,
        )
    assert cancellation_checks == 1


def test_sqlite_contains_lifecycle_metadata_only(tmp_path: Path) -> None:
    store = JobStore(settings_for(tmp_path / "data"))
    job_id = str(uuid4())
    store.create_job(job_id, AnalysisMode.FAST, "track.wav", True, False)
    store.write_json(job_id, "analysis.json", {"schemaVersion": "1.0.0"})
    with sqlite3.connect(store.settings.database_path) as connection:
        columns = [row[1] for row in connection.execute("PRAGMA table_info(jobs)")]
    assert "analysis" not in columns
    assert "prompt" not in columns
    assert (store.job_dir(job_id) / "analysis.json").is_file()


@pytest.mark.asyncio
async def test_ttl_cleanup_removes_metadata_and_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = settings_for(tmp_path / "data")
    store = JobStore(settings)
    job_id = str(uuid4())
    store.create_job(job_id, AnalysisMode.FAST, "track.wav", True, False)
    (store.job_dir(job_id) / "source.bin").write_bytes(b"private audio")
    (store.job_dir(job_id) / "visual-features.json").write_text("{}", encoding="utf-8")
    manager = JobManager(store, settings)
    monkeypatch.setattr(store, "expired_job_ids", lambda _now: [job_id])
    try:
        completed_task = asyncio.create_task(asyncio.sleep(0))
        await completed_task
        manager.tasks[job_id] = completed_task
        manager.sequences[job_id] = 7
        manager.subscribers[job_id].add(asyncio.Queue())
        manager.deleted_jobs.add(job_id)
        assert await manager.try_admit(job_id)
        assert await manager.cleanup_expired_once() == 1
        assert store.get_job(job_id) is None
        assert not store.job_dir(job_id).exists()
        assert job_id not in manager.tasks
        assert job_id not in manager.sequences
        assert job_id not in manager.subscribers
        assert job_id not in manager.deleted_jobs
        assert job_id not in manager.admitted_jobs
        assert job_id not in manager.job_locks
    finally:
        await manager.shutdown()


@pytest.mark.asyncio
async def test_cancellation_is_idempotent_and_cleans_upload(tmp_path: Path) -> None:
    settings = settings_for(tmp_path / "data")
    store = JobStore(settings)
    job_id = str(uuid4())
    store.create_job(job_id, AnalysisMode.FAST, "track.wav", True, False)
    (store.job_dir(job_id) / "source.bin").write_bytes(b"private audio")
    (store.job_dir(job_id) / "visual-features.json").write_text("{}", encoding="utf-8")
    manager = JobManager(store, settings)
    try:
        assert await manager.try_admit(job_id)
        manager.start(job_id)
        first = await manager.cancel(job_id)
        second = await manager.cancel(job_id)
        assert first.status == JobStatus.CANCELLED
        assert second.status == JobStatus.CANCELLED
        assert not (store.job_dir(job_id) / "source.bin").exists()
        assert not (store.job_dir(job_id) / "visual-features.json").exists()
    finally:
        await manager.shutdown()


@pytest.mark.asyncio
async def test_queued_cancel_does_not_wait_for_worker_slot(tmp_path: Path) -> None:
    settings = settings_for(tmp_path / "data")
    store = JobStore(settings)
    job_id = str(uuid4())
    store.create_job(job_id, AnalysisMode.FAST, "track.wav", True, False)
    (store.job_dir(job_id) / "source.bin").write_bytes(b"private audio")
    manager = JobManager(store, settings)
    await manager.worker_slots.acquire()
    try:
        assert await manager.try_admit(job_id)
        manager.start(job_id)
        record = await asyncio.wait_for(manager.cancel(job_id), timeout=2)
        assert record.status == JobStatus.CANCELLED
        assert not (store.job_dir(job_id) / "source.bin").exists()
        assert job_id not in manager.tasks
        assert job_id not in manager.admitted_jobs
    finally:
        manager.worker_slots.release()
        await manager.shutdown()


def test_deletion_failure_retains_metadata_for_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = JobStore(settings_for(tmp_path / "data"))
    job_id = str(uuid4())
    store.create_job(job_id, AnalysisMode.FAST, "track.wav", True, False)
    (store.job_dir(job_id) / "source.bin").write_bytes(b"private audio")

    def fail_remove(_path: Path) -> None:
        raise PermissionError("locked")

    monkeypatch.setattr(store_module.shutil, "rmtree", fail_remove)
    with pytest.raises(DeletionError):
        store.delete_job(job_id)
    assert store.get_job(job_id) is not None
    assert (store.job_dir(job_id) / "source.bin").exists()


def test_expiration_query_uses_utc_deadline(tmp_path: Path) -> None:
    store = JobStore(settings_for(tmp_path / "data", ttl_minutes=1))
    job_id = str(uuid4())
    store.create_job(job_id, AnalysisMode.FAST, "track.wav", True, False)
    assert job_id in store.expired_job_ids(utc_now() + timedelta(minutes=2))


@pytest.mark.asyncio
async def test_admission_control_is_bounded(tmp_path: Path) -> None:
    settings = settings_for(tmp_path / "data")
    store = JobStore(settings)
    manager = JobManager(store, settings)
    identifiers = [str(uuid4()) for _ in range(settings.max_pending_jobs + 1)]
    try:
        for identifier in identifiers[:-1]:
            assert await manager.try_admit(identifier)
        assert not await manager.try_admit(identifiers[-1])
        await manager.release_admission(identifiers[0])
        assert await manager.try_admit(identifiers[-1])
    finally:
        await manager.shutdown()


@pytest.mark.asyncio
async def test_deep_mode_is_reported_until_confirmed_fallback(tmp_path: Path) -> None:
    settings = settings_for(tmp_path / "data")
    store = JobStore(settings)
    job_id = str(uuid4())
    record = store.create_job(job_id, AnalysisMode.DEEP, "track.wav", True, False)
    manager = JobManager(store, settings)
    try:
        response = await manager.response(job_id)
        event = await manager._emit(record)
        assert response.requested_mode == AnalysisMode.DEEP
        assert response.mode == AnalysisMode.DEEP
        assert event.mode == AnalysisMode.DEEP
        fallback = store.update_job(job_id, effective_mode=AnalysisMode.FAST)
        fallback_event = await manager._emit(fallback)
        assert fallback_event.mode == AnalysisMode.FAST
    finally:
        await manager.shutdown()


@pytest.mark.asyncio
async def test_response_repairs_lyrics_summary_when_private_artifact_is_missing(
    tmp_path: Path,
    click_analysis,
) -> None:
    settings = settings_for(tmp_path / "data")
    store = JobStore(settings)
    job_id = str(uuid4())
    store.create_job(job_id, AnalysisMode.FAST, "track.wav", True, False)
    analysis = click_analysis.model_copy(update={"job_id": job_id}, deep=True)
    analysis.lyrics_summary = LyricsAnalysisSummary(
        enabled=True,
        status="completed",
        selected_device="cpu",
        transcript_available=True,
        segment_count=1,
        vocal_word_density="moderate",
    )
    store.write_json(
        job_id,
        "analysis.json",
        analysis.model_dump(mode="json", by_alias=True),
    )
    manager = JobManager(store, settings)
    try:
        response = await manager.response(job_id)
    finally:
        await manager.shutdown()

    assert response.analysis is not None
    assert response.analysis.lyrics_summary is not None
    summary = response.analysis.lyrics_summary
    assert summary.status == "artifact_missing"
    assert not summary.transcript_available
    assert summary.segment_count == 0
    assert summary.vocal_word_density is None
    assert any(
        "lyrics_completed_requires_private_artifact" in warning
        for warning in response.analysis.warnings
    )


@pytest.mark.asyncio
async def test_success_stages_are_monotonic_and_semantically_ordered(
    tmp_path: Path, click_analysis
) -> None:
    settings = settings_for(tmp_path / "data")
    store = JobStore(settings)
    job_id = str(uuid4())
    store.create_job(job_id, AnalysisMode.FAST, "track.wav", True, False)
    manager = JobManager(store, settings)
    subscription = await manager.open_subscription(job_id)
    analysis = click_analysis.model_copy(update={"job_id": job_id}, deep=True)
    try:
        await manager._finalize_success(job_id, analysis)
        events = [event async for event in subscription]
        stages = [event.stage for event in events]
        assert stages[-3:] == ["composing_prompt", "finalizing", "completed"]
        progress = [event.progress for event in events]
        assert progress == sorted(progress)
        assert progress[-3:] == [92, 98, 100]
        assert [event.sequence for event in events] == sorted(
            event.sequence for event in events
        )
    finally:
        await manager.delete(job_id)
        await manager.shutdown()


@pytest.mark.asyncio
async def test_expiry_emits_terminal_event_before_strict_deletion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = settings_for(tmp_path / "data")
    store = JobStore(settings)
    job_id = str(uuid4())
    store.create_job(job_id, AnalysisMode.FAST, "track.wav", True, False)
    manager = JobManager(store, settings)
    subscription = await manager.open_subscription(job_id)
    monkeypatch.setattr(store, "expired_job_ids", lambda _now: [job_id])
    try:
        assert await manager.cleanup_expired_once() == 1
        events = [event async for event in subscription]
        assert events[-1].status == JobStatus.EXPIRED
        assert events[-1].stage == "expired"
        assert store.get_job(job_id) is None
        assert job_id not in manager.job_locks
    finally:
        await manager.shutdown()


@pytest.mark.asyncio
async def test_subscription_registered_before_delete_gets_terminal_event(
    tmp_path: Path,
) -> None:
    settings = settings_for(tmp_path / "data")
    store = JobStore(settings)
    job_id = str(uuid4())
    store.create_job(job_id, AnalysisMode.FAST, "track.wav", True, False)
    manager = JobManager(store, settings)
    subscription = await manager.open_subscription(job_id)
    try:
        await manager.delete(job_id)
        events = [event async for event in subscription]
        assert events[-1].status == JobStatus.CANCELLED
        assert store.get_job(job_id) is None
    finally:
        await manager.shutdown()


@pytest.mark.asyncio
async def test_terminal_transition_is_serialized_with_subscription_registration(
    tmp_path: Path,
) -> None:
    settings = settings_for(tmp_path / "data")
    store = JobStore(settings)
    job_id = str(uuid4())
    store.create_job(job_id, AnalysisMode.FAST, "track.wav", True, False)
    manager = JobManager(store, settings)
    try:
        async with manager.job_lock(job_id):
            transition = asyncio.create_task(
                manager._terminal_transition(
                    job_id,
                    status=JobStatus.FAILED,
                    stage="failed",
                    message="Analysis failed safely",
                    error_code="analysis_failed",
                    error_message="Analysis failed safely.",
                )
            )
            await asyncio.sleep(0)
            assert not transition.done()

        await transition
        subscription = await manager.open_subscription(job_id)
        events = [event async for event in subscription]
        assert events[-1].status == JobStatus.FAILED
        assert events[-1].stage == "failed"
    finally:
        await manager.delete(job_id)
        await manager.shutdown()


@pytest.mark.asyncio
async def test_late_cancel_blocks_prompt_writes_and_completion(
    tmp_path: Path,
    click_analysis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = settings_for(tmp_path / "data")
    store = JobStore(settings)
    job_id = str(uuid4())
    store.create_job(job_id, AnalysisMode.FAST, "track.wav", True, False)
    manager = JobManager(store, settings)
    analysis = click_analysis.model_copy(update={"job_id": job_id}, deep=True)
    actual_compose = jobs_module.generate_prompt_package

    def cancel_during_compose(*args, **kwargs):
        manager._touch_cancel(job_id)
        return actual_compose(*args, **kwargs)

    monkeypatch.setattr(jobs_module, "generate_prompt_package", cancel_during_compose)
    try:
        with pytest.raises(jobs_module.AnalysisCancelled):
            await manager._finalize_success(job_id, analysis)
        assert store.read_json(job_id, "prompt.json") is None
        assert store.require_job(job_id).status != JobStatus.COMPLETED
    finally:
        await manager.delete(job_id)
        await manager.shutdown()


@pytest.mark.asyncio
async def test_post_worker_delete_retry_clears_registered_private_state(
    tmp_path: Path,
) -> None:
    settings = settings_for(tmp_path / "data")
    store = JobStore(settings)
    job_id = str(uuid4())
    store.create_job(job_id, AnalysisMode.FAST, "track.wav", True, False)
    (store.job_dir(job_id) / "source.bin").write_bytes(b"private")
    manager = JobManager(store, settings)
    manager.deleted_jobs.add(job_id)
    manager._touch_cancel(job_id)
    try:
        assert await manager._retry_deferred_delete(job_id)
        assert store.get_job(job_id) is None
        assert not store.job_dir(job_id).exists()
        assert job_id not in manager.deleted_jobs
        assert job_id not in manager.job_locks
    finally:
        await manager.shutdown()


@pytest.mark.asyncio
async def test_completed_delete_schedules_retry_for_open_file_race(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = settings_for(tmp_path / "data")
    store = JobStore(settings)
    job_id = str(uuid4())
    store.create_job(job_id, AnalysisMode.FAST, "track.wav", True, False)
    (store.job_dir(job_id) / "source.bin").write_bytes(b"private")
    manager = JobManager(store, settings)
    actual_delete = store.delete_job
    attempts = 0

    def transient_delete(identifier: str) -> bool:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise DeletionError("open playback handle")
        return actual_delete(identifier)

    monkeypatch.setattr(store, "delete_job", transient_delete)
    try:
        with pytest.raises(DeletionError, match="background deletion retry"):
            await manager.delete(job_id)
        deadline = asyncio.get_running_loop().time() + 2
        while store.get_job(job_id) is not None and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.05)
        assert store.get_job(job_id) is None
        assert attempts >= 2
        assert job_id not in manager.deletion_retry_tasks
    finally:
        await manager.shutdown()


def test_analysis_subprocess_timeout_uses_tree_kill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actual_kill = subprocess_utils_module._kill_process_tree
    calls = 0

    def observed_kill(process):
        nonlocal calls
        calls += 1
        actual_kill(process)

    monkeypatch.setattr(subprocess_utils_module, "_kill_process_tree", observed_kill)
    with pytest.raises(ProcessTimedOut):
        run_process_bounded(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            timeout_seconds=0.15,
        )
    assert calls >= 1


def test_aac_media_type_and_expired_sse_name(click_analysis) -> None:
    analysis = click_analysis.model_copy(deep=True)
    analysis.file.container = "aac"
    assert _media_type(analysis) == "audio/aac"
    assert _sse_event_name(JobStatus.EXPIRED) == "expired"


@pytest.mark.asyncio
async def test_asgi_body_limit_counts_chunked_requests() -> None:
    called = False

    async def inner(_scope, receive, send):
        nonlocal called
        called = True
        await receive()
        await receive()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    middleware = LocalRequestBoundaryMiddleware(
        inner,
        max_upload_request_bytes=5,
        allowed_origins=("http://localhost:5173",),
        allowed_hosts=("testserver",),
    )
    messages = iter(
        [
            {"type": "http.request", "body": b"123", "more_body": True},
            {"type": "http.request", "body": b"456", "more_body": False},
        ]
    )
    sent: list[dict] = []

    async def receive():
        return next(messages)

    async def send(message):
        sent.append(message)

    await middleware(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/analyses",
            "headers": [(b"host", b"testserver")],
        },
        receive,
        send,
    )
    assert called is True
    assert sent[0]["status"] == 413


def test_deep_capability_falls_back_without_explicit_local_setup(tmp_path: Path) -> None:
    settings = settings_for(tmp_path / "data")
    capability = deep_adapters(settings)[0]
    assert capability.available is False
    assert capability.enabled is False
    assert "Disabled by default" in (capability.reason or "")


def test_demucs_repo_rejects_any_unmanifested_file(tmp_path: Path) -> None:
    base = settings_for(tmp_path / "data")
    settings = base.__class__(
        **{
            field: getattr(base, field)
            for field in base.__dataclass_fields__
            if field not in {"enable_demucs"}
        },
        enable_demucs=True,
    )
    settings.model_cache_dir.mkdir(parents=True)
    weight = settings.model_cache_dir / "htdemucs.th"
    weight.write_bytes(b"reviewed synthetic placeholder")
    digest = hashlib.sha256(weight.read_bytes()).hexdigest()
    manifest = {
        "models": {settings.demucs_model_name: {"files": {weight.name: digest}}}
    }
    (settings.model_cache_dir / "demucs-models.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    assert adapters_module._reviewed_demucs_weights(settings) == [weight.resolve()]
    (settings.model_cache_dir / "unreviewed.yaml").write_text(
        "unsafe: config", encoding="utf-8"
    )
    assert adapters_module._reviewed_demucs_weights(settings) == []


def test_demucs_provisioning_is_verified_atomic_and_reusable(tmp_path: Path) -> None:
    source = tmp_path / "source"
    cache = tmp_path / "cache"
    destination = cache / "demucs"
    source.mkdir()
    weight = source / "tiny.th"
    weight.write_bytes(b"reviewed synthetic checkpoint")
    digest = hashlib.sha256(weight.read_bytes()).hexdigest()
    (source / "demucs-models.json").write_text(
        json.dumps({"models": {"htdemucs": {"files": {weight.name: digest}}}}),
        encoding="utf-8",
    )

    assert provision_demucs_repository(source, destination, cache, "htdemucs")
    assert (destination / weight.name).read_bytes() == weight.read_bytes()
    assert not provision_demucs_repository(source, destination, cache, "htdemucs")


def test_demucs_adapter_executes_only_against_local_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = settings_for(tmp_path / "data")
    settings = base.__class__(
        **{
            field: getattr(base, field)
            for field in base.__dataclass_fields__
            if field not in {"enable_demucs"}
        },
        enable_demucs=True,
    )
    source = tmp_path / "decoded.wav"
    source.write_bytes(b"local synthetic audio")
    output = tmp_path / "stems"
    stem_root = output / settings.demucs_model_name / source.stem
    stem_root.mkdir(parents=True)
    for name in ("vocals", "drums", "bass", "other"):
        (stem_root / f"{name}.wav").write_bytes(b"synthetic stem")
    observed: dict[str, object] = {}

    def fake_run(args, **kwargs):
        observed["args"] = args
        observed["env"] = kwargs["environment"]
        return BoundedProcessResult(0, b"", b"", False, False)

    monkeypatch.setattr(adapters_module, "demucs_ready", lambda _settings: True)
    monkeypatch.setattr(adapters_module, "run_process_bounded", fake_run)
    stems = run_demucs(source, output, settings)
    assert set(stems) == {"vocals", "drums", "bass", "other"}
    args = observed["args"]
    assert isinstance(args, list)
    assert "--repo" in args
    assert str(settings.model_cache_dir) in args
    assert args[args.index("--jobs") + 1] == "1"
    assert args[args.index("--segment") + 1] == "7"
    assert observed["env"]["HF_HUB_OFFLINE"] == "1"
    assert observed["env"]["TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD"] == "1"
