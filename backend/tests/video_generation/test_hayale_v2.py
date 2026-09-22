import asyncio
import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

from app.video_generation.gcp_veo import build_request_payload
from app.video_generation.jsonio import sha256_file

PROJECT = Path(__file__).resolve().parents[3] / "video-projects/hayale-flowers-promo/v2"
sys.path.insert(0, str(PROJECT))


def module():
    spec = importlib.util.spec_from_file_location("hayale_v2_plan", PROJECT / "plan.py")
    assert spec and spec.loader
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


@pytest.mark.parametrize("onsets", [[], [6.04, 13.9, 22.1, 30.02, 37.94, 46.1, 54.03], [1, 2, 3]])
def test_timeline_is_always_exactly_sixty_seconds_and_reuses_one_logo(onsets):
    value = module().edit_plan(onsets)
    events = value["segments"]
    assert value["timeline"]["durationFrames"] == 1440
    assert len(events) == 8
    assert events[0]["shotId"] == events[-1]["shotId"] == "shot-007"
    assert [e["shotId"] for e in events[1:-1]] == [f"shot-{n:03d}" for n in range(1, 7)]
    assert sum(e["durationFrames"] for e in events) == 1440
    cursor = 0
    for event in events:
        assert event["timelineStartFrames"] == cursor
        assert event["durationFrames"] > 0
        assert event["durationFrames"] * event["playbackRate"] <= 192.01
        cursor += event["durationFrames"]


def test_sync_uses_measured_onsets_without_claiming_they_are_downbeats():
    plan = module().edit_plan([6.04, 13.9, 22.1, 30.02, 37.94, 46.1, 54.03])
    assert plan["segments"][1]["timelineStartFrames"] == 145
    assert plan["segments"][2]["timelineStartFrames"] == 334
    assert plan["syncMethod"] == "nearest-detected-onset-within-0.35s; fixed-60s-clock"


def references_at(root):
    folder = root / "references"
    folder.mkdir()
    values = []
    for number in range(1, 8):
        path = folder / f"scene-{number:02d}.png"
        path.write_bytes(f"synthetic reference {number}".encode())
        values.append(
            {
                "shotId": f"shot-{number:03d}",
                "assetId": f"hayale-v2-reference-{number:03d}",
                "preparedPath": str(path),
                "sha256": sha256_file(path),
                "kind": "prepared-reference",
            }
        )
    (root / "references-manifest.json").write_text(json.dumps({"references": values}))
    return values


def test_provider_plan_has_seven_distinct_images_and_no_music_dependency(tmp_path):
    references = references_at(tmp_path)
    planner = module()
    first, paths = planner.provider_plan("test-project", "test-bucket", preparation=tmp_path)
    (tmp_path / "music-cues.json").write_text(
        json.dumps({"audioPath": "other-song.wav", "duration": 125, "onsets": [2, 5]})
    )
    second, _ = planner.provider_plan("test-project", "test-bucket", preparation=tmp_path)
    assert first.plan_digest == second.plan_digest
    assert len(first.shots) == len(paths) == 7
    assert sum(s.chapter_id == "store-tour" for s in first.shots) == 2
    assert first.shots[0].chapter_id == "storefront"
    assert first.base_estimated_cost_usd == 11.2
    uris = []
    for shot, reference in zip(first.shots, references, strict=True):
        request = build_request_payload(shot)
        assert request["parameters"]["generateAudio"] is False
        assert request["parameters"]["sampleCount"] == 1
        assert request["parameters"]["durationSeconds"] == 8
        assert shot.first_frame_reference.sha256 == reference["sha256"]
        assert "other-song.wav" not in json.dumps(request)
        uris.append(request["instances"][0]["image"]["gcsUri"])
    assert len(set(uris)) == 7


def test_preparation_is_local_and_leaves_authorization_and_jobs_absent(tmp_path, monkeypatch):
    references_at(tmp_path)
    planner = module()

    def forbidden(*args, **kwargs):
        raise AssertionError("No provider or credential access during preparation")

    monkeypatch.setattr("app.video_generation.gcp_veo.VeoRestClient.__init__", forbidden)
    monkeypatch.setattr("app.video_generation.gcp_veo.active_access_token", forbidden)
    report = planner.prepare(None, preparation=tmp_path)
    assert report["status"] == "prepared-not-generated"
    assert report["durationFrames"] == 1440
    assert len(list((tmp_path / "request-preview").glob("*.json"))) == 7
    assert not list(tmp_path.rglob("*.sqlite3"))
    assert not list(tmp_path.rglob("authorization.json"))
    assert not list(tmp_path.rglob("*.mp4"))


def test_manual_create_requires_explicit_mode_and_exact_authorization(monkeypatch):
    spec = importlib.util.spec_from_file_location("hayale_v2_runner", PROJECT / "run.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    monkeypatch.delenv("TRACKPROMPT_HAYALE_PREPARE_ONLY", raising=False)
    with pytest.raises(ValueError):
        runner.require_create_authorization(create=False, expected="exact plan", supplied="exact plan")
    with pytest.raises(ValueError):
        runner.require_create_authorization(create=True, expected="exact plan", supplied="wrong plan")
    monkeypatch.setenv("TRACKPROMPT_HAYALE_PREPARE_ONLY", "1")
    with pytest.raises(ValueError):
        runner.require_create_authorization(create=True, expected="exact plan", supplied="exact plan")


def test_changed_logo_is_rejected_before_finishing_writes(tmp_path):
    import finish

    references_at(tmp_path)
    output = tmp_path / "delivery"
    with pytest.raises(ValueError, match="logo plate changed"):
        finish.finish_package(
            output=output,
            clips={f"shot-{n:03d}": tmp_path / f"{n}.mp4" for n in range(1, 8)},
            cues={"onsets": [], "audioPath": None},
            provider_digest="a" * 64,
            ffmpeg="unused",
            ffprobe="unused",
            logo_sha256="b" * 64,
            preparation=tmp_path,
        )
    assert not output.exists()


def test_fixed_audio_command_pads_short_tracks_without_looping_or_time_stretch(tmp_path):
    import finish

    command = finish.audio_command("ffmpeg", tmp_path / "audio.wav", tmp_path / "master.wav")
    assert command[command.index("-af") + 1] == "apad,atrim=duration=60"
    assert "-stream_loop" not in command
    assert not any("atempo" in part for part in command)


@pytest.mark.asyncio
async def test_mocked_seven_scene_lifecycle_uses_canonical_receipts_once(tmp_path, monkeypatch):
    from app.mission_control.store import MissionControlStore
    from app.video_generation.authorization import authorization_phrase
    from app.video_generation.media import MediaProbe
    from app.video_generation.mission_controller import VideoGenerationController
    from app.video_generation.mission_models import VideoAuthorizationRequest, VideoJobState

    references_at(tmp_path)
    compiled, paths = module().provider_plan("test-project", "test-bucket", preparation=tmp_path)
    (tmp_path / "video-projects/hayale-flowers-promo").mkdir(parents=True)
    submitted = []
    uploaded = []
    by_id = {shot.shot_id: shot for shot in compiled.shots}

    class MockProvider:
        async def submit(self, shot, *, context):
            submitted.append(shot.shot_id)
            by_id[shot.shot_id] = shot
            return {"name": "operations/" + shot.shot_id}

        async def fetch(self, *, model_id, operation_name, context):
            shot = by_id[context.shot_id]
            return {"done": True, "response": {"videos": [{"gcsUri": shot.storage_uri + "result.mp4"}]}}

        async def download(self, uri, destination):
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(b"synthetic provider fixture")

        async def upload_reference(self, source, destination_uri, expected_sha256):
            assert sha256_file(source) == expected_sha256
            uploaded.append(destination_uri)

    def fixture_probe(path, **kwargs):
        return MediaProbe(str(path), sha256_file(path), 8.0, 1920, 1080, 24.0, "h264", "yuv420p", False)

    monkeypatch.setattr("app.video_generation.mission_controller.verify_generated_clip", fixture_probe)

    async def notify(sequence):
        pass

    store = MissionControlStore(tmp_path / "runtime/mission-control/state.sqlite3")
    controller = VideoGenerationController(
        repository_root=tmp_path,
        state_root=tmp_path / "runtime/mission-control",
        store=store,
        notify_event=notify,
        provider_factory=lambda project, region: MockProvider(),
        poll_interval_seconds=0.01,
    )
    view = await controller.create_prepared_plan(
        compiled, gcp_project_id="test-project", gcs_bucket="test-bucket", reference_paths=paths
    )
    await controller.authorize(
        view.job_id,
        VideoAuthorizationRequest(
            confirmation=authorization_phrase(
                compiled.project_id, compiled.plan_digest, compiled.max_spend_usd
            )
        ),
    )
    await controller.start(view.job_id)
    await asyncio.wait_for(controller._tasks[view.job_id], timeout=10)
    job = store.get_video_job(view.job_id)
    assert job.state is VideoJobState.REVIEW_READY
    assert job.reserved_cost_usd == 11.2
    assert submitted == [f"shot-{n:03d}" for n in range(1, 8)]
    assert len(uploaded) == len(set(uploaded)) == 7
    assert all(len(shot.attempts) == 1 and shot.latest_attempt.operation_name for shot in job.shots)
    assert not (controller._job_root(job) / "foreground-worker-lease.json").exists()
    again = await controller.create_prepared_plan(
        compiled, gcp_project_id="test-project", gcs_bucket="test-bucket", reference_paths=paths
    )
    assert again.job_id == view.job_id
    assert len(submitted) == 7
    controller.close()
    store.close()


def test_synthetic_retimes_preserve_fixed_clock_and_exact_audio_padding(tmp_path):
    import wave

    import finish

    from app.subprocess_utils import run_process_bounded
    from app.video_generation.assembly import build_assembly_plan

    ffmpeg = os.environ.get("FFMPEG_PATH") or shutil.which("ffmpeg")
    ffprobe = os.environ.get("FFPROBE_PATH") or shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        pytest.skip("Local FFmpeg tools required")
    source = tmp_path / "synthetic.mp4"
    command = [
        ffmpeg,
        "-v",
        "error",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=s=64x64:r=24",
        "-t",
        "8",
        "-c:v",
        "libx264",
        str(source),
    ]
    assert run_process_bounded(command, timeout_seconds=30).returncode == 0
    audio = tmp_path / "synthetic.wav"
    with wave.open(str(audio), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(24000)
        output.writeframes(b"\x00\x00" * 24000)
    audio_hash = sha256_file(audio)
    fixed_audio = tmp_path / "sixty.wav"
    assert (
        run_process_bounded(finish.audio_command(ffmpeg, audio, fixed_audio), timeout_seconds=30).returncode
        == 0
    )
    value = module().edit_plan([6.04, 13.9, 22.1, 30.02, 37.94, 46.1, 54.03])
    value["timeline"].update(width=64, height=64, audioPath=str(fixed_audio))
    for event in value["segments"]:
        event["clipPath"] = str(source)
    output = tmp_path / "preview.mp4"
    assembly = build_assembly_plan(value, output_path=output, work_root=tmp_path / "work", ffmpeg=ffmpeg)
    for command in assembly.commands:
        assert run_process_bounded(command, timeout_seconds=30).returncode == 0
    result = run_process_bounded(
        [ffprobe, "-v", "error", "-count_frames", "-show_streams", "-of", "json", str(output)],
        timeout_seconds=30,
    )
    assert result.returncode == 0
    streams = json.loads(result.stdout)["streams"]
    video = next(s for s in streams if s["codec_type"] == "video")
    sound = next(s for s in streams if s["codec_type"] == "audio")
    assert video["nb_read_frames"] == "1440"
    assert video["avg_frame_rate"] == "24/1"
    assert float(video["duration"]) == 60
    assert abs(float(sound["duration"]) - 60) < 0.03
    assert sha256_file(audio) == audio_hash
