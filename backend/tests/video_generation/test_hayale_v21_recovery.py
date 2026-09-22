"""Mocked legacy Shot 001 checkpoint -> submitted Shot 002 recovery boundary."""

import importlib.util
import json
import struct
import sys
import zlib
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.video_generation.jsonio import atomic_write_json, sha256_file, sha256_json

PROJECT = Path(__file__).resolve().parents[3] / "video-projects/hayale-flowers-promo/v2.1"


def load(name):
    key = f"hayale_v21_{name}"
    if key in sys.modules:
        return sys.modules[key]
    spec = importlib.util.spec_from_file_location(key, PROJECT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[key] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        del sys.modules[key]
        raise
    return module


class Client:
    def __init__(self, state="running"):
        self.state = state
        self.submissions = 0

    def history(self, prompt_id):
        if self.state in ("completed", "failed"):
            return {
                prompt_id: {
                    "prompt": [1, prompt_id, {"node": {}}, {"client_id": "run-shot-002"}, []],
                    "status": {
                        "completed": self.state == "completed",
                        "status_str": "success" if self.state == "completed" else "error",
                    },
                    "outputs": {},
                }
            }
        return {}

    def queue_snapshot(self):
        item = [1, "existing-shot002", {"node": {}}, {"client_id": "run-shot-002"}, []]
        return {
            "queue_running": [item] if self.state == "running" else [],
            "queue_pending": [item] if self.state == "queued" else [],
        }

    def queue_prompt(self, graph, *, client_id):
        self.submissions += 1
        pytest.fail("Recovery must inspect/adopt existing work, never blindly submit")


def fixture_run(tmp_path):
    for shot in ("shot-001", "shot-002"):
        (tmp_path / shot).mkdir()
    raw = tmp_path / "shot-001/raw"
    raw.mkdir()

    def chunk(name, value):
        return struct.pack(">I", len(value)) + name + value + struct.pack(">I", zlib.crc32(name + value))

    # Valid, tiny, synthetic 832x480 PNGs; no real media is used in the regression.
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 832, 480, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress((b"\0" + b"\x22\x66\x44" * 832) * 480)) + chunk(b"IEND", b"")
    rows = []
    for number in range(1, 130):
        image = raw / f"frame_{number:08d}.png"
        image.write_bytes(png)
        rows.append({"name": image.name, "sha256": sha256_file(image)})
    atomic_write_json(
        tmp_path / "shot-001/raw-verified.json",
        {"frames": rows, "dimensions": [832, 480], "promptId": "completed-shot001"},
    )
    atomic_write_json(
        tmp_path / "shot-002/submission.json",
        {
            "state": "submitted",
            "promptId": "existing-shot002",
            "identity": "run-shot-002",
            "workflowDigest": sha256_json({"node": {}}),
        },
    )
    atomic_write_json(tmp_path / "shot-002/submitted-workflow.json", {"node": {}})
    atomic_write_json(
        tmp_path / "supervisor.json",
        {"activeShot": "shot-001", "updatedAt": "2000-01-01T00:00:00Z", "state": "generating"},
    )


@pytest.mark.parametrize(
    "state,expected",
    [
        ("running", "generating"),
        ("queued", "queued"),
        ("completed", "frames_complete"),
        ("failed", "failed"),
        ("absent", "missing"),
    ],
)
def test_restart_twice_preserves_checkpoint_and_adopts_exact_submission(
    tmp_path, monkeypatch, state, expected
):
    recovery = load("recovery")
    fixture_run(tmp_path)
    client = Client(state)
    for _ in range(2):
        view = recovery.inspect_boundary(tmp_path, client, (832, 480))
        assert view["scenes"]["shot-001"]["state"] == "raw_verified"
        assert view["activeShot"] == "shot-002"
        assert view["scenes"]["shot-002"]["state"] == expected
        assert view["scenes"]["shot-002"]["promptId"] == "existing-shot002"
        assert view["submissionAllowed"] is False
    assert client.submissions == 0


def test_completed_between_queue_and_history_reads_is_reconciled(tmp_path):
    recovery = load("recovery")
    fixture_run(tmp_path)
    client = Client("absent")
    original = client.queue_snapshot

    def transition():
        result = original()
        client.state = "completed"
        return result

    client.queue_snapshot = transition
    view = recovery.inspect_submission(tmp_path / "shot-002", client)
    assert view["state"] == "frames_complete"


def test_changed_graph_or_duplicate_jobs_prevent_adoption(tmp_path):
    recovery = load("recovery")
    fixture_run(tmp_path)
    client = Client()
    original = client.queue_snapshot

    def duplicates():
        snapshot = original()
        other = list(snapshot["queue_running"][0])
        other[1] = "duplicate-id"
        snapshot["queue_pending"].append(other)
        return snapshot

    client.queue_snapshot = duplicates
    with pytest.raises(ValueError, match="multiple"):
        recovery.inspect_submission(tmp_path / "shot-002", client)
    client = Client()
    atomic_write_json(tmp_path / "shot-002/submitted-workflow.json", {"tampered": {}})
    with pytest.raises(ValueError, match="workflow"):
        recovery.inspect_submission(tmp_path / "shot-002", client)


def test_history_without_workflow_identity_cannot_be_adopted(tmp_path):
    recovery = load("recovery")
    fixture_run(tmp_path)
    client = Client("completed")
    client.history = lambda prompt: {
        prompt: {"status": {"completed": True, "status_str": "success"}, "outputs": {}}
    }
    with pytest.raises(ValueError, match="identity"):
        recovery.inspect_submission(tmp_path / "shot-002", client)


@pytest.mark.parametrize("lease_held", [True, False])
def test_real_recover_entrypoint_twice_keeps_or_reconnects_supervisor(tmp_path, monkeypatch, lease_held):
    recovery = load("recovery")
    runtime = load("runtime")
    run_id = "a" * 12
    run_root = tmp_path / "runs" / run_id
    run_root.mkdir(parents=True)
    fixture_run(run_root)
    # This test isolates controller adoption; full decode/provenance is covered
    # separately against real synthetic PNGs in test_hayale_durable.
    lifecycle = runtime.durable_module()
    monkeypatch.setattr(lifecycle, "verify_checkpoint", lambda root, shot, manifest, **kwargs:
                        {"count": len(runtime.verified_frames(root / shot, (832, 480)) or []), "manifest_sha256": "a" * 64})
    monkeypatch.setattr(lifecycle, "scene_binding", lambda *args: None)
    original = {
        "preparation": "prepared",
        "profile": "quality",
        "silent": False,
        "digest": "original-run-digest",
        "code": {name: sha256_file(PROJECT / name) for name in ("prepare.py", "finish.py")},
    }
    atomic_write_json(run_root / "run.json", original)
    before = (run_root / "run.json").read_bytes()
    prep = SimpleNamespace(
        ROOT=tmp_path,
        PROFILES={"quality": (832, 480)},
        contained=lambda path: path,
        verify_prepared=lambda: {"digest": "prepared"},
    )
    real_sibling = recovery.sibling
    monkeypatch.setattr(
        recovery,
        "sibling",
        lambda name: prep if name == "prepare" else runtime if name == "runtime" else real_sibling(name),
    )
    client = Client()

    @contextmanager
    def borrow(root):
        assert root == run_root
        yield client

    monkeypatch.setattr(recovery, "existing_server", borrow)
    monkeypatch.setattr(recovery, "lease_is_held", lambda root: lease_held)
    waits = []
    copies = []

    def wait(existing_client, prompt_id, receipt):
        assert existing_client is client and prompt_id == "existing-shot002"
        waits.append(prompt_id)
        client.state = "completed"
        return client.history(prompt_id)

    def collect(existing_client, history, prompt_id, scene_root, size):
        assert prompt_id == "existing-shot002" and scene_root.name == "shot-002"
        copies.append(prompt_id)
        import shutil

        shutil.copytree(run_root / "shot-001/raw", scene_root / "raw")
        value = json.loads((run_root / "shot-001/raw-verified.json").read_text())
        value["promptId"] = prompt_id
        atomic_write_json(scene_root / "raw-verified.json", value)
        return runtime.verified_frames(scene_root, size)

    monkeypatch.setattr(runtime, "wait_history", wait)
    monkeypatch.setattr(runtime, "collect_frames", collect)
    for _ in range(2):
        result = recovery.recover(run_id, resume=True)
        if lease_held:
            assert result["action"] == "existing-supervisor-retained"
            assert result["activeShot"] == "shot-002"
        else:
            assert result["action"] in {"existing-prompt-recovered", "checkpoint-already-recovered"}
            assert result["laterShotsSubmitted"] == 0
    assert (run_root / "run.json").read_bytes() == before
    assert client.submissions == 0
    assert waits == ([] if lease_held else ["existing-shot002"])
    assert copies == ([] if lease_held else ["existing-shot002"])
    approval = json.loads((run_root / "recovery/compatible-code.json").read_text())
    assert approval["originalRunDigest"] == original["digest"]


def test_wait_publishes_correct_stage_without_mutating_submission(tmp_path, monkeypatch):
    runtime = load("runtime")
    fixture_run(tmp_path)
    receipt = tmp_path / "shot-002/submission.json"
    before = receipt.read_bytes()
    client = Client("queued")
    client._queue_contains = runtime.ComfyUIClient._queue_contains
    monkeypatch.setattr(runtime.time, "sleep", lambda seconds: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        runtime.wait_history(client, "existing-shot002", receipt)
    stage = json.loads((receipt.parent / "stage.json").read_text())
    assert stage["state"] == "queued"
    assert stage["waitingFor"] == "provider_queue"
    assert receipt.read_bytes() == before
    supervisor = json.loads((tmp_path / "supervisor.json").read_text())
    assert supervisor["activeShot"] == "shot-002"
    assert supervisor["state"] == "queued"


def test_status_uses_stages_but_never_demotes_a_raw_checkpoint(tmp_path):
    runtime = load("runtime")
    fixture_run(tmp_path)
    runtime.publish_stage(
        tmp_path / "shot-002", "generating", "provider_history", prompt_id="existing-shot002"
    )
    current = runtime.recorded_scene_state(tmp_path / "shot-002")
    assert current["state"] == "generating"
    assert current["submissionReceiptState"] == "submitted"
    assert current["stageObservedAt"]
    runtime.publish_stage(tmp_path / "shot-001", "queued", "provider_queue")
    assert runtime.recorded_scene_state(tmp_path / "shot-001")["state"] == "raw_verified"


def test_legacy_status_can_show_a_matching_timestamped_observation(tmp_path):
    runtime = load("runtime")
    fixture_run(tmp_path)
    observation = {
        "state": "generating",
        "promptId": "existing-shot002",
        "workflowDigest": sha256_json({"node": {}}),
        "waitingFor": "provider_history",
    }
    result = runtime.recorded_scene_state(tmp_path / "shot-002", observation, "2026-09-17T08:30:00Z")
    assert result["state"] == "generating"
    assert result["source"] == "recovery-observation"
    assert result["stageObservedAt"] == "2026-09-17T08:30:00Z"


@pytest.mark.parametrize(
    "listening,alive,expected", [(True, True, True), (False, False, False), (False, True, None)]
)
def test_server_receipt_is_not_proof_of_a_live_server(tmp_path, monkeypatch, listening, alive, expected):
    runtime = load("runtime")
    atomic_write_json(tmp_path / "comfy/server-process.json", {"pid": 123})

    class Socket:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def settimeout(self, value):
            pass

        def connect_ex(self, address):
            return 0 if listening else 1

    monkeypatch.setattr(runtime.socket, "socket", Socket)
    monkeypatch.setattr(runtime, "process_is_alive", lambda pid: alive)
    if expected is None:
        with pytest.raises(ValueError, match="before any restart"):
            runtime.existing_server_available(tmp_path)
    else:
        assert runtime.existing_server_available(tmp_path) is expected


def test_gpu_handoff_rejects_busy_provider_and_accepts_empty_free_response():
    runtime = load("runtime")

    class Provider:
        busy = True
        freed = 0

        def queue_snapshot(self):
            return {"queue_running": [["existing"]] if self.busy else [], "queue_pending": []}

        def _request_bytes(self, method, path, **kwargs):
            assert (method, path) == ("POST", "/free")
            self.freed += 1
            return b""

        def _request_json(self, method, path):
            assert (method, path) == ("GET", "/system_stats")
            return {"devices": [{"torch_vram_total": 0}]}

    provider = Provider()
    with pytest.raises(ValueError, match="busy"):
        runtime.handoff_gpu(provider)
    assert provider.freed == 0
    provider.busy = False
    runtime.handoff_gpu(provider)
    assert provider.freed == 1
