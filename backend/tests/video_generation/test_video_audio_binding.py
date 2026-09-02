# ruff: noqa: E402
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import wave
from pathlib import Path

import pytest
from pydantic import ValidationError

PACKAGE_ROOT = Path(__file__).resolve().parents[3]
if str(PACKAGE_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT / "backend"))

from app.mission_control.models import NativePickerResponse
from app.mission_control.store import MissionControlStore
from app.video_generation import audio as audio_module
from app.video_generation.audio import AudioBindingError, probe_audio, stage_audio_master
from app.video_generation.jsonio import atomic_write_json, read_json
from app.video_generation.mission_controller import VideoGenerationController
from app.video_generation.mission_models import (
    VideoAuthorizationRequest,
    VideoJobState,
    VideoPlanCreateRequest,
)


def _tools() -> tuple[str, str]:
    ffmpeg = os.getenv("TRACKPROMPT_MC_FFMPEG_PATH") or shutil.which("ffmpeg")
    ffprobe = os.getenv("TRACKPROMPT_MC_FFPROBE_PATH") or shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        pytest.skip("real FFmpeg audio contracts require ffmpeg and ffprobe")
    return ffmpeg, ffprobe


def _write_wav(path: Path, *, seconds: float = 0.25, sample_rate: int = 48_000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(b"\0\0\0\0" * round(seconds * sample_rate))


def test_native_picker_contract_has_real_boolean_selection() -> None:
    selected = NativePickerResponse(cancelled=False, path=r"C:\Music\master.wav")
    cancelled = NativePickerResponse(cancelled=True)
    assert selected.selected is True and selected.cancelled is False
    assert cancelled.selected is False and cancelled.path is None
    with pytest.raises(ValidationError):
        NativePickerResponse(selected=True, cancelled=True, path=r"C:\Music\master.wav")


def test_wav_with_unicode_and_spaces_is_hash_verified_and_copied(tmp_path: Path) -> None:
    _, ffprobe = _tools()
    source = tmp_path / "מקור audio with spaces.wav"
    _write_wav(source)
    staged = stage_audio_master(
        source,
        artifact_root=tmp_path / "private artifacts",
        ffprobe=ffprobe,
    )
    assert staged.source.sha256 == staged.artifact.sha256 == staged.finishing.sha256
    assert staged.artifact.path != source
    assert staged.finishing.sample_rate_hz == 48_000
    assert staged.finishing.channels == 2
    assert staged.artifact.path.read_bytes() == source.read_bytes()


def test_compressed_master_gets_non_destructive_48k_finishing_wav(tmp_path: Path) -> None:
    ffmpeg, ffprobe = _tools()
    wav = tmp_path / "source.wav"
    mp3 = tmp_path / "compressed master.mp3"
    _write_wav(wav, sample_rate=44_100)
    result = subprocess.run(
        [
            ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(wav),
            "-c:a",
            "libmp3lame",
            str(mp3),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
        shell=False,
    )
    assert result.returncode == 0, result.stderr
    staged = stage_audio_master(
        mp3,
        artifact_root=tmp_path / "private",
        ffmpeg=ffmpeg,
        ffprobe=ffprobe,
    )
    assert staged.source.sha256 == staged.artifact.sha256
    assert staged.finishing.path.suffix == ".wav"
    assert staged.finishing.codec == "pcm_s24le"
    assert staged.finishing.sample_rate_hz == 48_000
    assert staged.finishing.channels == 2
    assert abs(staged.finishing.duration_seconds - staged.source.duration_seconds) <= 0.02


def test_missing_and_invalid_audio_fail_with_domain_codes(tmp_path: Path) -> None:
    _, ffprobe = _tools()
    with pytest.raises(AudioBindingError) as missing:
        probe_audio(tmp_path / "missing.wav", ffprobe=ffprobe)
    assert missing.value.code == "audio_file_missing"
    invalid = tmp_path / "not audio.wav"
    invalid.write_text("not audio", encoding="utf-8")
    with pytest.raises(AudioBindingError) as bad:
        probe_audio(invalid, ffprobe=ffprobe)
    assert bad.value.code == "audio_media_invalid"


def test_ffprobe_failure_and_zero_duration_are_domain_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, ffprobe = _tools()
    source = tmp_path / "zero.wav"
    _write_wav(source, seconds=0)
    with pytest.raises(AudioBindingError) as zero:
        probe_audio(source, ffprobe=ffprobe)
    assert zero.value.code in {"audio_duration_zero", "audio_media_invalid"}

    _write_wav(source, seconds=0.1)
    monkeypatch.setattr(audio_module.subprocess, "run", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("blocked")))
    with pytest.raises(AudioBindingError) as failed:
        probe_audio(source, ffprobe=ffprobe)
    assert failed.value.code == "audio_probe_failed"


@pytest.mark.asyncio
async def test_plan_prefers_and_persists_verified_retained_analysis_audio(tmp_path: Path) -> None:
    ffmpeg, ffprobe = _tools()
    state_root = tmp_path / "state" / "mission-control"
    analysis_id = "33333333-3333-4333-8333-333333333333"
    analysis_root = state_root.parent / "jobs" / analysis_id
    analysis_root.mkdir(parents=True)
    retained = analysis_root / "source.bin"
    _write_wav(retained, seconds=0.2)
    store = MissionControlStore(state_root / "mission-control.sqlite3")

    async def notify(_: int) -> None:
        return None

    controller = VideoGenerationController(
        repository_root=PACKAGE_ROOT,
        state_root=state_root,
        store=store,
        notify_event=notify,
        provider_factory=lambda *_args: (_ for _ in ()).throw(AssertionError("provider called")),
        ffmpeg_path=lambda: Path(ffmpeg),
        ffprobe_path=lambda: Path(ffprobe),
    )
    try:
        planned = await controller.create_plan(
            VideoPlanCreateRequest(
                analysis_job_id=analysis_id,
                project_id="the-glitch-is-me",
                gcp_project_id="test-project",
                gcs_bucket="example-trackprompt-video",
            )
        )
        assert planned.audio_master_bound is True
        assert planned.audio.source == "analysis-retained"
        assert planned.audio.selected is True and planned.audio.verified is True
        assert planned.audio.duration_seconds == pytest.approx(0.2, abs=0.001)
        assert planned.audio.sha256
    finally:
        controller.close()
        store.close()


@pytest.mark.asyncio
async def test_binding_persists_replaces_and_clears_without_provider_or_source_deletion(
    tmp_path: Path,
) -> None:
    ffmpeg, ffprobe = _tools()
    state_root = tmp_path / "state" / "mission-control"
    analysis_id = "11111111-1111-4111-8111-111111111111"
    (state_root.parent / "jobs" / analysis_id).mkdir(parents=True)
    store = MissionControlStore(state_root / "mission-control.sqlite3")
    provider_calls = 0

    def forbidden_provider(_project: str, _region: str):
        nonlocal provider_calls
        provider_calls += 1
        raise AssertionError("local audio operations must not instantiate a provider")

    async def notify(_: int) -> None:
        return None

    controller = VideoGenerationController(
        repository_root=PACKAGE_ROOT,
        state_root=state_root,
        store=store,
        notify_event=notify,
        provider_factory=forbidden_provider,
        ffmpeg_path=lambda: Path(ffmpeg),
        ffprobe_path=lambda: Path(ffprobe),
    )
    source_one = tmp_path / "first master.wav"
    source_two = tmp_path / "שני master.wav"
    _write_wav(source_one, seconds=0.25)
    _write_wav(source_two, seconds=0.35)
    try:
        planned = await controller.create_plan(
            VideoPlanCreateRequest(
                analysis_job_id=analysis_id,
                project_id="the-glitch-is-me",
                gcp_project_id="test-project",
                gcs_bucket="example-trackprompt-video",
            )
        )
        authorized = await controller.authorize(
            planned.job_id,
            VideoAuthorizationRequest(confirmation=planned.authorization_phrase),
        )
        first = await controller.bind_audio(
            planned.job_id,
            source_one,
            source="local-selection",
        )
        assert first.selected is True and first.verified is True
        assert first.sha256 and first.duration_seconds
        stored = store.get_video_job(planned.job_id)
        assert stored is not None and stored.audio_binding is not None
        staged_original = Path(stored.audio_binding.artifact_path)
        assert staged_original.is_file()
        assert stored.plan_digest == authorized.plan_digest
        assert stored.authorization is not None

        finishing = controller._job_root(stored) / "davinci" / "final-fast-1080p"
        finishing.mkdir(parents=True)
        (finishing / "stale.txt").write_text("stale local output", encoding="utf-8")
        store.put_video_job(
            stored.model_copy(
                update={
                    "state": VideoJobState.COMPLETE,
                    "timeline_path": str(finishing / "resolved-timeline.json"),
                    "export_root": str(finishing),
                    "preview_path": str(finishing / "preview.mp4"),
                    "local_edit_digest": "e" * 64,
                }
            )
        )
        second = await controller.bind_audio(
            planned.job_id,
            source_two,
            source="local-selection",
        )
        assert second.selected is False
        assert second.error is not None
        assert second.error.code == "audio_hash_mismatch_confirmation_required"
        assert second.error.confirmation_phrase
        second = await controller.bind_audio(
            planned.job_id,
            source_two,
            source="local-selection",
            accept_local_delivery_revision=True,
            confirmation=second.error.confirmation_phrase,
        )
        assert second.selected is True
        assert second.sha256 != first.sha256
        replaced = store.get_video_job(planned.job_id)
        assert replaced is not None
        assert replaced.timeline_path is None and replaced.export_root is None
        assert replaced.local_edit_digest is None
        assert replaced.plan_digest == authorized.plan_digest
        assert replaced.authorization == stored.authorization
        assert list((controller._job_root(replaced) / "finishing-history").glob("*/*/stale.txt"))
        assert provider_calls == 0

        controller.close()
        controller = VideoGenerationController(
            repository_root=PACKAGE_ROOT,
            state_root=state_root,
            store=store,
            notify_event=notify,
            provider_factory=forbidden_provider,
            ffmpeg_path=lambda: Path(ffmpeg),
            ffprobe_path=lambda: Path(ffprobe),
        )
        restored = controller.get(planned.job_id)
        assert restored.audio_master_bound is True
        assert restored.audio.sha256 == second.sha256
        await controller.clear_audio(planned.job_id)
        assert source_one.is_file() and source_two.is_file() and staged_original.is_file()
        assert controller.get(planned.job_id).audio_master_bound is False
        assert provider_calls == 0
    finally:
        controller.close()
        store.close()


@pytest.mark.asyncio
async def test_understood_legacy_selected_path_is_verified_and_normalized(tmp_path: Path) -> None:
    ffmpeg, ffprobe = _tools()
    state_root = tmp_path / "state" / "mission-control"
    analysis_id = "22222222-2222-4222-8222-222222222222"
    (state_root.parent / "jobs" / analysis_id).mkdir(parents=True)
    store = MissionControlStore(state_root / "mission-control.sqlite3")

    async def notify(_: int) -> None:
        return None

    controller = VideoGenerationController(
        repository_root=PACKAGE_ROOT,
        state_root=state_root,
        store=store,
        notify_event=notify,
        provider_factory=lambda *_args: (_ for _ in ()).throw(AssertionError("provider called")),
        ffmpeg_path=lambda: Path(ffmpeg),
        ffprobe_path=lambda: Path(ffprobe),
    )
    source = tmp_path / "legacy selected.wav"
    _write_wav(source)
    try:
        planned = await controller.create_plan(
            VideoPlanCreateRequest(
                analysis_job_id=analysis_id,
                project_id="the-glitch-is-me",
                gcp_project_id="test-project",
                gcs_bucket="example-trackprompt-video",
            )
        )
        stored = store.get_video_job(planned.job_id)
        assert stored is not None
        selection_path = controller._job_root(stored) / "audio" / "audio-selection.json"
        atomic_write_json(selection_path, {"selected": str(source)})
        migrated = controller.get(planned.job_id)
        assert migrated.audio_master_bound is True
        normalized = read_json(selection_path)
        assert isinstance(normalized, dict)
        assert normalized["selected"] is True
        assert normalized["verified"] is True
        assert normalized["sha256"] == migrated.audio.sha256
    finally:
        controller.close()
        store.close()
