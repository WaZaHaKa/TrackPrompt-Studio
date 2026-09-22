"""Offline contract tests: never start ComfyUI or generate real Hayale media."""

import importlib.util
import io
import json
import os
import struct
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
PROJECT = ROOT / "video-projects/hayale-flowers-promo/v2.1"


def load(name):
    key = f"hayale_v21_{name}"
    if key in sys.modules:
        return sys.modules[key]
    spec = importlib.util.spec_from_file_location(key, PROJECT / f"{name}.py")
    assert spec and spec.loader
    value = importlib.util.module_from_spec(spec)
    sys.modules[key] = value
    try:
        spec.loader.exec_module(value)
    except BaseException:
        del sys.modules[key]
        raise
    return value


def bound_attempt(runtime, monkeypatch, graph):
    from types import SimpleNamespace

    from app.video_generation.jsonio import sha256_json

    owner = SimpleNamespace(held=True, progress=lambda *args, **kwargs: None)
    monkeypatch.setattr(runtime.durable_module(), "CURRENT_OWNER", owner)
    return {
        "run": "synthetic",
        "shot": "shot-002",
        "attempt": "attempt-001",
        "workflow_sha256": sha256_json(graph),
        "input_sha256": "b" * 64,
        "seed": 17,
        "settings": {"width": 832, "height": 480, "frames": 129, "steps": 40},
        "output_prefix": "shot-002/frame",
        "provider_instance": {
            "pid": 123,
            "started_at": "2026-09-18T00:00:00Z",
            "endpoint": "http://127.0.0.1:8189",
        },
    }


def test_prepared_edit_preserves_clock_roles_and_cuts():
    prep = load("prepare")
    original = json.loads((PROJECT.parent / "v2/shot-bank.json").read_text())
    assert len(original["shots"]) == 7
    source = {
        "title": "V2",
        "projectId": "hayale-flowers-promo",
        "timeline": {"durationFrames": 1440, "fps": 24, "width": 1920, "height": 1080},
        "segments": [{"shotId": x} for x in ["shot-007", *[f"shot-{n:03}" for n in range(1, 7)], "shot-007"]],
        "editorial": {"exportFiles": {"fcpxml": "old.fcpxml"}, "handoff": {}},
    }
    value = prep.local_edit(source)
    assert source["title"] == "V2"
    assert value["segments"] == source["segments"]
    assert value["timeline"] == source["timeline"]
    assert value["editorial"]["exportFiles"]["fcpxml"] == "hayale-promo-v21.fcpxml"


@pytest.mark.parametrize("profile,size", [("quality", (832, 480)), ("low-memory", (640, 368))])
def test_graph_uses_installed_full_step_models_and_lossless_frames(profile, size):
    prep = load("prepare")
    scene = {"shotId": "shot-001", "prompt": "Real storefront, gentle dolly.", "seed": 17}
    graph = prep.scene_workflow(scene, profile, "reference.png", "safe-prefix")
    assert graph["latent"]["inputs"]["length"] == 129
    assert (graph["latent"]["inputs"]["width"], graph["latent"]["inputs"]["height"]) == size
    assert graph["high_sampler"]["inputs"]["steps"] == 40
    assert graph["high_sampler"]["inputs"]["end_at_step"] == 20
    assert graph["low_sampler"]["inputs"]["start_at_step"] == 20
    assert graph["low_sampler"]["inputs"]["end_at_step"] == 40
    assert graph["decode"]["class_type"] == "VAEDecodeTiled"
    assert graph["text_encoder"]["inputs"]["device"] == "cpu"
    assert "photorealism" not in graph["negative"]["inputs"]["text"]
    assert "save_video" not in graph
    assert graph["save_frames"]["inputs"]["filename_prefix"] == "safe-prefix"
    assert all("api" not in row["class_type"].lower() for row in graph.values())


def test_submission_intent_is_durable_before_queue_and_unknown_is_not_retried(tmp_path, monkeypatch):
    runtime = load("runtime")
    receipt = tmp_path / "scene.json"
    binding = bound_attempt(runtime, monkeypatch, {"a": {}})

    class Client:
        calls = 0

        def queue_prompt(self, graph, *, client_id):
            self.calls += 1
            assert json.loads(receipt.read_text())["state"] == "submission-intent"
            raise TimeoutError("accepted or not: unknown")

    client = Client()
    with pytest.raises(TimeoutError):
        runtime.submit_once(client, {"a": {}}, receipt, "identity", binding=binding)
    with pytest.raises(ValueError, match="ambiguous"):
        runtime.submit_once(client, {"a": {}}, receipt, "identity")
    assert client.calls == 1


def test_known_submission_is_reused_and_changed_graph_is_refused(tmp_path, monkeypatch):
    runtime = load("runtime")
    receipt = tmp_path / "scene.json"
    binding = bound_attempt(runtime, monkeypatch, {"a": {}})

    class Client:
        calls = 0

        def queue_prompt(self, graph, *, client_id):
            self.calls += 1
            return "prompt-123"

    client = Client()
    assert runtime.submit_once(client, {"a": {}}, receipt, "identity", binding=binding) == "prompt-123"
    assert runtime.submit_once(client, {"a": {}}, receipt, "identity") == "prompt-123"
    with pytest.raises(ValueError, match="changed"):
        runtime.submit_once(client, {"b": {}}, receipt, "identity")
    assert client.calls == 1


def test_preparation_guard_prevents_creation_before_any_work(monkeypatch):
    runner = load("run")
    monkeypatch.setenv("TRACKPROMPT_HAYALE_PREPARE_ONLY", "1")
    with pytest.raises(ValueError, match="preparation-only"):
        runner.require_creation_allowed()


def test_postprocessing_uses_photo_model_and_exact_eight_seconds(tmp_path):
    finish = load("finish")
    tools = {
        "ffmpeg": "ffmpeg.exe",
        "rife": "rife.exe",
        "rifeModel": "rife-v4.6",
        "upscaler": "upscale.exe",
        "upscaleModels": "models",
    }
    commands = finish.post_commands(tools, tmp_path / "raw", tmp_path)
    assert commands[0][commands[0].index("-n") + 1] == "193"
    assert "realesrgan-x4plus" in commands[1]
    assert "anime" not in json.dumps(commands)
    assert commands[2][commands[2].index("-frames:v") + 1] == "192"
    assert commands[2][commands[2].index("-framerate") + 1] == "24"
    assert "-n" in commands[2]
    assert "out_color_matrix=bt709:out_range=tv" in commands[2][commands[2].index("-vf") + 1]


def test_server_command_contains_only_local_isolated_writable_paths(tmp_path):
    runtime = load("runtime")
    command = runtime.server_command(Path("D:/TrackPrompt-ComfyUI"), tmp_path)
    assert command[command.index("--listen") + 1] == "127.0.0.1"
    assert command[command.index("--port") + 1] == "8189"
    assert "--disable-api-nodes" in command
    assert "--disable-all-custom-nodes" in command
    assert "ComfyUI-GGUF" in command
    assert "--cache-none" in command
    for flag in ("--input-directory", "--output-directory", "--temp-directory", "--user-directory"):
        assert Path(command[command.index(flag) + 1]).is_relative_to(tmp_path)


def test_output_frame_manifest_detects_changed_frames(tmp_path):
    runtime = load("runtime")
    from app.video_generation.jsonio import atomic_write_json, sha256_file

    raw = tmp_path / "raw"
    raw.mkdir()
    rows = []
    for number in range(1, 130):
        path = raw / f"frame_{number:08d}.png"
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0\0\0\rIHDR" + struct.pack(">II", 832, 480))
        rows.append({"name": path.name, "sha256": sha256_file(path)})
    atomic_write_json(tmp_path / "raw-verified.json", {"frames": rows, "dimensions": [832, 480]})
    assert len(runtime.verified_frames(tmp_path, (832, 480))) == 129
    (raw / rows[14]["name"]).write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed"):
        runtime.verified_frames(tmp_path, (832, 480))


def test_missing_prompt_does_not_poll_indefinitely_or_resubmit(tmp_path):
    runtime = load("runtime")

    class Client:
        _queue_contains = staticmethod(lambda queue, prompt: False)

        def history(self, prompt_id):
            return {}

        def queue_snapshot(self):
            return {"queue_running": [], "queue_pending": []}

    with pytest.raises(ValueError, match="unknown"):
        runtime.wait_history(Client(), "lost", tmp_path / "receipt.json")


def test_completed_and_error_history_are_saved_before_returning(tmp_path):
    runtime = load("runtime")

    class Client:
        failed = False

        def history(self, prompt_id):
            return {
                prompt_id: {
                    "status": {"completed": True, "status_str": "error" if self.failed else "success"}
                }
            }

    client = Client()
    runtime.wait_history(client, "done", tmp_path / "receipt.json")
    assert (tmp_path / "completed-history.json").is_file()
    client.failed = True
    with pytest.raises(ValueError, match="no automatic reroll"):
        runtime.wait_history(client, "error", tmp_path / "receipt.json")
    assert (tmp_path / "provider-error.json").is_file()


def test_completion_between_history_and_queue_is_reconciled(tmp_path):
    runtime = load("runtime")

    class Client:
        reads = 0
        _queue_contains = staticmethod(lambda queue, prompt: False)

        def history(self, prompt_id):
            self.reads += 1
            return (
                {}
                if self.reads == 1
                else {prompt_id: {"status": {"completed": True, "status_str": "success"}}}
            )

        def queue_snapshot(self):
            return {"queue_running": [], "queue_pending": []}

    client = Client()
    result = runtime.wait_history(client, "done", tmp_path / "receipt.json")
    assert result["done"]["status"]["completed"]
    assert client.reads == 2


def test_completed_frames_recover_locally_after_server_shutdown(tmp_path):
    runtime = load("runtime")
    from app.video_generation.jsonio import atomic_write_json

    scene = tmp_path / "shot-001"
    output = tmp_path / "comfy/output/shot-001"
    output.mkdir(parents=True)
    rows = []
    for number in range(1, 130):
        name = f"wan_{number:05d}_.png"
        (output / name).write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0\0\0\rIHDR" + struct.pack(">II", 832, 480))
        rows.append({"filename": name, "subfolder": "shot-001", "type": "output"})
    atomic_write_json(scene / "submission.json", {"promptId": "done"})
    atomic_write_json(
        scene / "completed-history.json",
        {
            "done": {
                "status": {"completed": True, "status_str": "success"},
                "outputs": {"save_frames": {"images": rows}},
            }
        },
    )
    assert runtime.recover_completed(scene, output.parent, (832, 480))
    assert len(runtime.verified_frames(scene, (832, 480))) == 129
    # Repeat recovery reads the checksummed downloads and performs no inference.
    assert runtime.recover_completed(scene, output.parent, (832, 480))


def test_owned_client_checks_listener_before_mutations(monkeypatch):
    runtime = load("runtime")
    client = runtime.OwnedClient()
    client.owner_pid = 123

    def reject(pid):
        assert pid == 123
        raise ValueError("wrong owner")

    monkeypatch.setattr(runtime, "require_listener_owner", reject)
    with pytest.raises(ValueError, match="wrong owner"):
        client.queue_prompt({}, client_id="test")


def test_server_receipt_failure_stops_owned_process_tree(tmp_path, monkeypatch):
    runtime = load("runtime")

    class Socket:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def settimeout(self, timeout):
            pass

        def connect_ex(self, address):
            return 1

    class Process:
        pid = 987
        stdout = io.BytesIO(b"synthetic log")
        stopped = False

        def poll(self):
            return 0 if self.stopped else None

        def wait(self, timeout):
            assert self.stopped
            return 0

    process = Process()
    monkeypatch.setattr(runtime.socket, "socket", Socket)
    monkeypatch.setattr(runtime.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(runtime, "_kill_process_tree", lambda owned: setattr(owned, "stopped", True))

    def fail_receipt(*args, **kwargs):
        raise OSError("synthetic disk full")

    monkeypatch.setattr(runtime, "atomic_write_json", fail_receipt)
    with pytest.raises(OSError, match="disk full"):
        with runtime.local_server(Path("D:/unused-test-install"), tmp_path):
            pytest.fail("Failed startup must not yield a provider client")
    assert process.stopped


def test_port_conflict_never_starts_or_stops_an_existing_process(tmp_path, monkeypatch):
    runtime = load("runtime")

    class Socket:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def settimeout(self, timeout):
            pass

        def connect_ex(self, address):
            return 0

    monkeypatch.setattr(runtime.socket, "socket", Socket)
    monkeypatch.setattr(
        runtime.subprocess,
        "Popen",
        lambda *args, **kwargs: pytest.fail("No process may start on a port conflict"),
    )
    with pytest.raises(ValueError, match="occupied"):
        with runtime.local_server(Path("D:/unused-test-install"), tmp_path):
            pytest.fail("Existing server must never be reused")


def test_output_count_checked_before_retrieval(tmp_path):
    runtime = load("runtime")

    class Client:
        def history_outputs(self, history, prompt_id):
            return [{"nodeId": "save_frames", "filename": "single.png"}]

    with pytest.raises(ValueError, match="129"):
        runtime.collect_frames(Client(), {}, "done", tmp_path, (832, 480))


def test_prepared_manifest_rejects_changed_local_inputs(tmp_path, monkeypatch):
    prep = load("prepare")
    monkeypatch.setattr(prep, "REPO", tmp_path)
    monkeypatch.setattr(prep, "ROOT", tmp_path)
    reference = tmp_path / "prepared/reference.png"
    reference.parent.mkdir()
    reference.write_bytes(b"approved")
    manifest = {"installed": [], "sources": [], "files": [], "references": [prep.file_record(reference)]}
    manifest["digest"] = prep.sha256_json(manifest)
    prep.atomic_write_json(tmp_path / "prepared/manifest.json", manifest)
    assert prep.verify_prepared()["digest"] == manifest["digest"]
    reference.write_bytes(b"modified")
    with pytest.raises(ValueError, match="changed"):
        prep.verify_prepared()


@pytest.mark.skipif(
    not os.environ.get("HAYALE_V21_SYNTHETIC_FFMPEG"), reason="Explicit synthetic native finishing check"
)
def test_synthetic_sixty_second_delivery_with_original_exporters(tmp_path):
    """CPU-only colored test signal; no real photos, audio, inference or APIs."""
    finish = load("finish")
    prep = load("prepare")
    ffmpeg = os.environ["HAYALE_V21_SYNTHETIC_FFMPEG"]
    tools = {"ffmpeg": ffmpeg, "ffprobe": str(Path(ffmpeg).with_name("ffprobe.exe"))}
    source = tmp_path / "synthetic.mp4"
    plate = tmp_path / "prepared/references/scene-07.png"
    plate.parent.mkdir(parents=True)
    finish.run(
        [
            ffmpeg,
            "-nostdin",
            "-v",
            "error",
            "-n",
            "-f",
            "lavfi",
            "-i",
            "color=c=0x356850:s=1920x1080:r=24:d=8",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ]
    )
    finish.run(
        [
            ffmpeg,
            "-nostdin",
            "-v",
            "error",
            "-n",
            "-f",
            "lavfi",
            "-i",
            "color=c=ivory:s=1920x1080",
            "-frames:v",
            "1",
            str(plate),
        ]
    )
    # Reuse the committed fixed edit specification; all media remains synthetic.
    v2_spec = importlib.util.spec_from_file_location(
        "synthetic_hayale_v2_plan", PROJECT.parent / "v2/plan.py"
    )
    v2 = importlib.util.module_from_spec(v2_spec)
    v2_spec.loader.exec_module(v2)
    edit = prep.local_edit(v2.edit_plan([]))
    prep.atomic_write_json(tmp_path / "prepared/edit-plan.json", edit)
    audio = tmp_path / "synthetic.wav"
    finish.run(
        [
            ffmpeg,
            "-nostdin",
            "-v",
            "error",
            "-n",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=59.88",
            "-ac",
            "2",
            str(audio),
        ]
    )
    prep.atomic_write_json(
        tmp_path / "prepared/music-cues.json",
        {"audioPath": str(audio), "sourceSha256": prep.sha256_file(audio)},
    )
    report = finish.finish_package(
        tools,
        {f"shot-{n:03d}": source for n in range(1, 8)},
        tmp_path / "prepared",
        tmp_path / "delivery",
        "synthetic-offline-test",
    )
    assert report["frames"] == 1440 and report["audioPresent"] is True
    assert report["creativeReview"] == "pending"
    assert report["resolveImport"] == "not-run"
