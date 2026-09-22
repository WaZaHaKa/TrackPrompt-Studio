"""Crash/blocked-observer regressions for the actual project runner (no GPU)."""

import importlib.util
import io
import struct
import sys
import threading
import zlib
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT = Path(__file__).resolve().parents[3] / "video-projects/hayale-flowers-promo/v2.1"


def module(name):
    key = f"hayale_v21_{name}"
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, PROJECT / f"{name}.py")
        value = importlib.util.module_from_spec(spec)
        sys.modules[key] = value
        try:
            spec.loader.exec_module(value)
        except BaseException:
            del sys.modules[key]
            raise
    return sys.modules[key]


def test_blocked_console_cannot_prevent_recording_provider_completion(tmp_path, monkeypatch):
    runtime = module("runtime")
    writer_entered, release_writer, completed = threading.Event(), threading.Event(), threading.Event()
    errors = []

    class Provider:
        _queue_contains = staticmethod(runtime.ComfyUIClient._queue_contains)
        calls = 0

        def history(self, prompt_id):
            self.calls += 1
            if self.calls == 1:
                return {}
            return {prompt_id: {"status": {"status_str": "success", "completed": True}}}

        def queue_snapshot(self):
            return {"queue_running": [[0, "existing-job"]], "queue_pending": []}

    def stalled_print(*args, **kwargs):
        writer_entered.set()
        release_writer.wait(3)

    monkeypatch.setattr("builtins.print", stalled_print)
    monkeypatch.setattr(runtime.time, "sleep", lambda seconds: None)

    def run():
        try:
            runtime.wait_history(Provider(), "existing-job", tmp_path / "shot-002/submission.json")
            completed.set()
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=run)
    worker.start()
    try:
        assert writer_entered.wait(1)
        assert completed.wait(0.5), (
            "Synchronous console output blocked completion persistence and timeout checks"
        )
        assert (tmp_path / "shot-002/completed-history.json").is_file()
        assert not errors
    finally:
        release_writer.set()
        worker.join(3)


def test_display_write_failure_does_not_abort_accepted_render(tmp_path, monkeypatch):
    runtime = module("runtime")

    def disk_full(*args, **kwargs):
        raise OSError("synthetic stage-display failure")

    monkeypatch.setattr(runtime, "atomic_write_json", disk_full)
    runtime.publish_stage(tmp_path / "shot-002", "generating", "provider_history", prompt_id="accepted")


def test_failed_log_sink_does_not_stop_draining_provider_output(tmp_path, monkeypatch):
    runtime = module("runtime")
    source = io.BytesIO(b"x" * 16384)
    monkeypatch.setattr(
        Path, "open", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("log unavailable"))
    )
    runtime.drain_output(source, tmp_path / "unwritable.log")
    assert source.tell() == 16384


def attempt_identity():
    return {
        "run": "run-1",
        "shot": "shot-002",
        "attempt": "attempt-001",
        "workflow_sha256": "a" * 64,
        "input_sha256": "b" * 64,
        "seed": 17,
        "settings": {"width": 832, "height": 480, "frames": 129, "steps": 40},
        "output_prefix": "shot-002/attempt-001/frame",
        "provider_instance": {
            "pid": 123,
            "started_at": "2026-09-18T00:00:00Z",
            "endpoint": "http://127.0.0.1:8189",
        },
    }


def test_attempt_sending_is_irreversible_even_without_saved_prompt(tmp_path):
    durable = module("durable")
    owner = type("Owner", (), {"held": True})()
    ledger = durable.Attempts(tmp_path, owner)
    ledger.prepare(attempt_identity())
    assert ledger.get()["state"] == "prepared"
    ledger.begin_send()
    assert ledger.get()["state"] == "sending"
    with pytest.raises(ValueError, match="already"):
        ledger.begin_send()
    ledger.accept("accepted-job")
    ledger.accept("accepted-job")
    assert ledger.get()["state"] == "accepted"
    with pytest.raises(ValueError, match="identity"):
        ledger.accept("different-job")


def test_attempt_needs_exclusive_owner_and_identity_never_changes(tmp_path):
    durable = module("durable")
    owner = type("Owner", (), {"held": False})()
    ledger = durable.Attempts(tmp_path, owner)
    with pytest.raises(ValueError, match="ownership"):
        ledger.prepare(attempt_identity())
    owner.held = True
    ledger.prepare(attempt_identity())
    changed = {**attempt_identity(), "input_sha256": "c" * 64}
    with pytest.raises(ValueError, match="identity"):
        ledger.prepare(changed)


@pytest.mark.parametrize("provider", ["absent", "unreachable", "unknown"])
def test_uncertain_acceptance_never_allows_second_post(provider):
    durable = module("durable")
    decision = durable.decide_verified(
        raw="absent", provider=provider, submission="sending", identity_match=True, matches=0, owner_held=True
    )
    assert decision.may_submit is False
    assert decision.action in {"hold_ambiguous", "wait_provider"}


def test_one_native_lock_excludes_legacy_controller(tmp_path):
    durable = module("durable")
    from app.local_video.production_lease import production_lease

    with production_lease(tmp_path):
        with pytest.raises(Exception, match="owner|lock"):
            with durable.ControllerLease(tmp_path, "run-1"):
                pytest.fail("Second controller must not enter")


def bound_scene(tmp_path):
    durable = module("durable")
    root = tmp_path / "runs" / "0123456789ab"
    scene = root / "shot-001"
    prepared = tmp_path / "prepared"
    raw = scene / "raw"
    raw.mkdir(parents=True)
    (scene / "downloads").mkdir()
    (root / "comfy/input").mkdir(parents=True)
    (prepared / "references").mkdir(parents=True)
    (prepared / "workflows/quality").mkdir(parents=True)

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 8, 8, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress((b"\0" + b"\x22\x66\x44" * 8) * 8))
        + chunk(b"IEND", b"")
    )
    reference = prepared / "references/scene-01.png"
    reference.write_bytes(png)
    (root / "comfy/input/reference.png").write_bytes(png)
    graph = {
        "start_image": {"inputs": {"image": "reference.png"}},
        "save_frames": {"inputs": {"filename_prefix": "shot-001/frame"}},
        "latent": {"inputs": {"width": 8, "height": 8}},
        "high_sampler": {"inputs": {"noise_seed": 17}},
    }
    durable.atomic_write_json(prepared / "workflows/quality/shot-001.json", graph)
    manifest = {
        "references": [
            {"shotId": "shot-001", "path": str(reference), "sha256": durable.sha256_file(reference)}
        ]
    }
    manifest["digest"] = durable.sha256_json(manifest)
    durable.atomic_write_json(root / "run.json", {"preparation": manifest["digest"], "profile": "quality"})
    receipt = {
        "promptId": "proved-provider",
        "identity": "bound-client",
        "workflowDigest": durable.sha256_json(graph),
    }
    durable.atomic_write_json(scene / "submission.json", receipt)
    durable.atomic_write_json(scene / "submitted-workflow.json", graph)
    outputs = []
    for i in range(1, 130):
        target = raw / f"frame_{i:08d}.png"
        target.write_bytes(png)
        output = {"filename": f"frame_{i:05d}_.png", "subfolder": "shot-001", "type": "output"}
        outputs.append(output)
        durable.atomic_write_json(
            scene / "downloads" / f"{i:03d}.json",
            {"sha256": durable.sha256_file(target), "output": {"nodeId": "save_frames", **output}},
        )
    history = {
        "proved-provider": {
            "prompt": [0, "proved-provider", graph, {"client_id": "bound-client"}, []],
            "status": {"completed": True, "status_str": "success"},
            "outputs": {"save_frames": {"images": outputs}},
        }
    }
    durable.atomic_write_json(scene / "completed-history.json", history)
    return root, manifest


def test_129_complete_without_marker_gets_decode_proof_not_rerender(tmp_path):
    pytest.importorskip("PIL")
    durable = module("durable")
    root, manifest = bound_scene(tmp_path)
    report = durable.verify_checkpoint(root, "shot-001", manifest)
    assert report["count"] == 129 and report["workflow_binding"] == "verified"
    assert not (root / "shot-001/raw-verified.json").exists()  # Read-only observation.


@pytest.mark.parametrize("fault", ["corrupt", "missing", "input_changed", "false_marker"])
def test_bad_frames_or_provenance_never_pass_or_overwrite(tmp_path, fault):
    pytest.importorskip("PIL")
    durable = module("durable")
    root, manifest = bound_scene(tmp_path)
    image = root / "shot-001/raw/frame_00000020.png"
    if fault == "corrupt":
        image.write_bytes(b"not a PNG")
    elif fault == "missing":
        image.unlink()  # Synthetic-only fixture.
    elif fault == "input_changed":
        (root / "comfy/input/reference.png").write_bytes(b"changed")
    else:
        durable.atomic_write_json(
            root / "shot-001/raw-verified.json", {"promptId": "wrong", "dimensions": [8, 8], "frames": []}
        )
    before = image.read_bytes() if image.exists() else None
    with pytest.raises((ValueError, durable._fs.WNPError)):
        durable.verify_checkpoint(root, "shot-001", manifest)
    assert (image.read_bytes() if image.exists() else None) == before


def test_acceptance_crash_rediscovers_job_but_never_sends_again(tmp_path, monkeypatch):
    runtime = module("runtime")
    durable = module("durable")
    recovery = module("recovery")
    owner = type("Owner", (), {"held": True, "progress": lambda *args, **kwargs: None})()
    monkeypatch.setattr(durable, "CURRENT_OWNER", owner)
    graph = {"a": {}}
    binding = {**attempt_identity(), "workflow_sha256": durable.sha256_json(graph)}
    receipt = tmp_path / "submission.json"
    durable.atomic_write_json(tmp_path / "submitted-workflow.json", graph)

    class Provider:
        calls = 0

        def queue_prompt(self, graph, *, client_id):
            self.calls += 1
            return "accepted-job"

        def queue_snapshot(self):
            return {
                "queue_running": [[0, "accepted-job", graph, {"client_id": "bound-client"}, []]],
                "queue_pending": [],
            }

        def history(self, prompt_id):
            return {}

        def _request_json(self, method, path):
            return {}

    client = Provider()
    monkeypatch.setattr(
        durable.Attempts,
        "accept",
        lambda *args: (_ for _ in ()).throw(OSError("crash after provider accepted")),
    )
    with pytest.raises(OSError):
        runtime.submit_once(client, graph, receipt, "bound-client", binding=binding)
    for _ in range(2):
        result = recovery.inspect_submission(tmp_path, client)
        assert result["state"] == "generating" and result["promptId"] == "accepted-job"
    assert client.calls == 1


def test_verified_promotion_and_finalization_failure_reuse_same_raw(tmp_path, monkeypatch):
    pytest.importorskip("PIL")
    runtime = module("runtime")
    durable = module("durable")
    root, manifest = bound_scene(tmp_path)
    scene = root / "shot-001"
    durable.atomic_write_json(tmp_path / "prepared/manifest.json", manifest)
    # Move only synthetic fixture bytes to stand in for retained provider files.
    provider = root / "comfy/output/shot-001"
    provider.mkdir(parents=True)
    history = durable.read_json(scene / "completed-history.json")
    pid = "proved-provider"
    for i, p in enumerate(sorted((scene / "raw").glob("*.png")), 1):
        p.replace(provider / f"frame_{i:05d}_.png")
    (scene / "raw").rmdir()
    owner = type("Owner", (), {"held": True, "run_id": root.name, "progress": lambda *args, **kwargs: None})()
    monkeypatch.setattr(durable, "CURRENT_OWNER", owner)
    client = runtime.RetainedOutputs(root / "comfy/output")
    runtime.collect_frames(client, history, pid, scene, (8, 8))
    before = {p.name: durable.sha256_file(p) for p in (scene / "raw").glob("*.png")}
    assert len(before) == 129 and (scene / "verified-provenance.json").exists()
    # A finalization-only failure does not remove or resubmit raw generation.
    durable.atomic_write_json(scene / "stage.json", {"state": "failed", "waitingFor": "local_postproduction"})
    runtime.collect_frames(client, history, pid, scene, (8, 8))
    assert {p.name: durable.sha256_file(p) for p in (scene / "raw").glob("*.png")} == before


def test_invalid_staged_decode_never_promotes(tmp_path, monkeypatch):
    pytest.importorskip("PIL")
    runtime = module("runtime")
    durable = module("durable")
    root, manifest = bound_scene(tmp_path)
    scene = root / "shot-001"
    durable.atomic_write_json(tmp_path / "prepared/manifest.json", manifest)
    provider = root / "comfy/output/shot-001"
    provider.mkdir(parents=True)
    history = durable.read_json(scene / "completed-history.json")
    for i, p in enumerate(sorted((scene / "raw").glob("*.png")), 1):
        p.replace(provider / f"frame_{i:05d}_.png")
    (scene / "raw").rmdir()
    # Same header/dimensions but broken PNG payload exercises full decode.
    bad = provider / "frame_00017_.png"
    bad.write_bytes(bad.read_bytes()[:24])
    for p in (scene / "downloads").glob("*.json"):
        p.unlink()  # synthetic fixture only
    (scene / "downloads").rmdir()
    owner = type("Owner", (), {"held": True, "run_id": root.name, "progress": lambda *args, **kwargs: None})()
    monkeypatch.setattr(durable, "CURRENT_OWNER", owner)
    with pytest.raises(durable._fs.WNPError, match="decoding"):
        runtime.collect_frames(
            runtime.RetainedOutputs(root / "comfy/output"), history, "proved-provider", scene, (8, 8)
        )
    assert not (scene / "raw").exists() and not (scene / "raw-verified.json").exists()
    assert bad.exists()


def test_retained_completion_recovers_after_provider_exit_without_submission(tmp_path, monkeypatch):
    pytest.importorskip("PIL")
    runtime, durable, recovery = module("runtime"), module("durable"), module("recovery")
    root, manifest = bound_scene(tmp_path)
    scene = root / "shot-001"
    durable.atomic_write_json(tmp_path / "prepared/manifest.json", manifest)
    history = durable.read_json(scene / "completed-history.json")
    (scene / "completed-history.json").unlink()  # synthetic interrupted observer
    provider = root / "comfy/output/shot-001"
    provider.mkdir(parents=True)
    for i, p in enumerate(sorted((scene / "raw").glob("*.png")), 1):
        p.replace(provider / f"frame_{i:05d}_.png")
    (scene / "raw").rmdir()
    server = root / "comfy/server-process.json"
    durable.atomic_write_json(server, {"pid": 123, "endpoint": "http://127.0.0.1:8189"})
    instance = {
        "pid": 123,
        "started_at": "2026-09-18T00:00:00Z",
        "run": root.name,
        "server_record_sha256": durable.sha256_file(server),
    }
    durable.atomic_write_json(
        root / "recovery/completed-provider-snapshot.json",
        {"run": root.name, "provider_instance": instance, "history": history},
    )
    owner = type(
        "Owner",
        (),
        {"held": True, "root": tmp_path, "run_id": root.name, "progress": lambda *args, **kwargs: None},
    )()
    monkeypatch.setattr(durable, "CURRENT_OWNER", owner)
    before = (scene / "submission.json").read_bytes()
    durable.restore_completed(root, manifest, runtime, recovery)
    first = (scene / "raw-verified.json").read_bytes()
    durable.restore_completed(root, manifest, runtime, recovery)
    assert (scene / "raw-verified.json").read_bytes() == first
    assert (scene / "submission.json").read_bytes() == before
    assert (
        durable.read_json(scene / "attempt.json")["identity"]["provider_instance"]["endpoint"]
        == "http://127.0.0.1:8189"
    )


def test_create_crash_before_send_reuses_uploaded_input_and_sends_once(tmp_path, monkeypatch):
    runner, runtime, durable = module("run"), module("runtime"), module("durable")
    prep, recovery = runner.sibling("prepare"), runner.sibling("recovery")
    root = tmp_path / "hayale"
    run_root = root / "runs" / "0123456789ab"
    refs = root / "prepared/references"
    refs.mkdir(parents=True)
    run_root.mkdir(parents=True)
    reference = refs / "scene-02.png"
    reference.write_bytes(b"synthetic reference bytes")
    manifest = {
        "comfyRoot": str(tmp_path / "unused"),
        "tools": {},
        "references": [
            {"shotId": "shot-002", "path": str(reference), "sha256": durable.sha256_file(reference)}
        ],
    }
    manifest["digest"] = durable.sha256_json(manifest)
    graph = prep.scene_workflow(
        {"shotId": "shot-002", "prompt": "Synthetic scene", "seed": 17},
        "quality",
        "scene-02.png",
        "shot-002/frame",
    )
    durable.atomic_write_json(root / "prepared/workflows/quality/shot-002.json", graph)
    identity = {
        "preparation": manifest["digest"],
        "profile": "quality",
        "silent": False,
        "code": {p.name: durable.sha256_file(p) for p in sorted(PROJECT.glob("*.py"))},
    }
    durable.atomic_write_json(run_root / "run.json", {**identity, "digest": durable.sha256_json(identity)})
    durable.atomic_write_json(root / "latest-run.json", {"runRoot": str(run_root)})
    monkeypatch.setattr(prep, "ROOT", root)
    monkeypatch.setattr(prep, "contained", lambda path, *args: path)
    monkeypatch.setattr(prep, "verify_prepared", lambda: manifest)
    monkeypatch.delenv("TRACKPROMPT_HAYALE_PREPARE_ONLY", raising=False)
    monkeypatch.setattr(
        runtime,
        "verified_frames",
        lambda path, size: None if path.name == "shot-002" else [Path("synthetic")] * 129,
    )
    monkeypatch.setattr(runtime, "recover_completed", lambda *args: False)
    monkeypatch.setattr(runtime, "existing_server_available", lambda path: True)
    client = SimpleNamespace(
        provider_instance={
            "pid": 123,
            "started_at": "2026-09-18T00:00:00Z",
            "endpoint": "http://127.0.0.1:8189",
        },
        uploads=0,
        posts=0,
    )
    names = tuple(
        graph[node]["inputs"][field]
        for node, field in [
            ("high_model", "unet_name"),
            ("low_model", "unet_name"),
            ("text_encoder", "clip_name"),
            ("vae", "vae_name"),
        ]
    )
    client.health = lambda: SimpleNamespace(
        object_info={row["class_type"]: {} for row in graph.values()}, model_names=names
    )

    def upload(path, *, upload_name):
        client.uploads += 1
        name = f"uploaded-{client.uploads}.png"
        dest = run_root / "comfy/input" / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(path.read_bytes())
        return name

    def post(graph, *, client_id):
        client.posts += 1
        return "accepted-once"

    client.upload_image = upload
    client.queue_prompt = post

    @contextmanager
    def existing(path):
        yield client

    monkeypatch.setattr(recovery, "existing_server", existing)
    original = durable.Attempts.begin_send
    monkeypatch.setattr(
        durable.Attempts, "begin_send", lambda self: (_ for _ in ()).throw(RuntimeError("crash-before-send"))
    )
    with pytest.raises(RuntimeError, match="crash-before-send"):
        runner.create(profile="quality", new_run=False, silent=False)
    saved = (run_root / "shot-002/submitted-workflow.json").read_bytes()
    monkeypatch.setattr(durable.Attempts, "begin_send", original)
    monkeypatch.setattr(
        runtime,
        "wait_history",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("test-stops-after-acceptance")),
    )
    with pytest.raises(RuntimeError, match="test-stops-after-acceptance"):
        runner.create(profile="quality", new_run=False, silent=False)
    assert client.uploads == 1 and client.posts == 1
    assert (run_root / "shot-002/submitted-workflow.json").read_bytes() == saved
    (run_root / "shot-002/submission.json").unlink()  # synthetic crash window
    monkeypatch.setattr(runtime, "existing_server_available", lambda path: False)
    monkeypatch.setattr(
        runtime,
        "local_server",
        lambda *args: pytest.fail("Must not start a provider before reconciling canonical intent"),
    )
    monkeypatch.setattr(
        runtime,
        "resource_check",
        lambda *args: pytest.fail("Must reconcile canonical intent before GPU preflight"),
    )
    for state in ("accepted", "sending"):
        attempt = durable.read_json(run_root / "shot-002/attempt.json")
        attempt["state"] = state
        if state == "sending":
            attempt["prompt_id"] = None
        durable.atomic_write_json(run_root / "shot-002/attempt.json", attempt)
        with pytest.raises(ValueError, match="unresolved"):
            runner.create(profile="quality", new_run=False, silent=False)
    assert client.posts == 1


def test_actual_create_finalization_retry_never_enters_generation(tmp_path, monkeypatch):
    runner, runtime, durable = module("run"), module("runtime"), module("durable")
    prep, finish = runner.sibling("prepare"), runner.sibling("finish")
    root = tmp_path / "hayale"
    run_root = root / "runs" / "0123456789ab"
    run_root.mkdir(parents=True)
    manifest = {"digest": "synthetic-preparation", "tools": {}}
    identity = {
        "preparation": manifest["digest"],
        "profile": "quality",
        "silent": False,
        "code": {p.name: durable.sha256_file(p) for p in sorted(PROJECT.glob("*.py"))},
    }
    durable.atomic_write_json(run_root / "run.json", {**identity, "digest": durable.sha256_json(identity)})
    durable.atomic_write_json(root / "latest-run.json", {"runRoot": str(run_root)})
    markers = {}
    for i in range(1, 8):
        path = run_root / f"shot-{i:03d}" / "raw-verified.json"
        durable.atomic_write_json(path, {"synthetic_checkpoint": i})
        markers[path] = path.read_bytes()
    monkeypatch.setattr(prep, "ROOT", root)
    monkeypatch.setattr(prep, "contained", lambda path, *args: path)
    monkeypatch.setattr(prep, "verify_prepared", lambda: manifest)
    monkeypatch.delenv("TRACKPROMPT_HAYALE_PREPARE_ONLY", raising=False)
    monkeypatch.setattr(runtime, "verified_frames", lambda *args: [Path("synthetic-frame")] * 129)
    monkeypatch.setattr(runtime, "existing_server_available", lambda *args: False)
    monkeypatch.setattr(
        runtime, "submit_once", lambda *args, **kwargs: pytest.fail("Verified raw must not be regenerated")
    )
    monkeypatch.setattr(
        runtime, "local_server", lambda *args: pytest.fail("Finishing-only retry must not start ComfyUI")
    )
    calls = []

    def finish_scene(tools, scene, size):
        calls.append(scene.name)
        if calls.count("shot-002") == 1 and scene.name == "shot-002":
            raise ValueError("synthetic finalization failure")
        path = scene / "synthetic-master.mov"
        path.write_bytes(b"synthetic-master")
        return path

    def finish_package(tools, clips, prepared, output, digest, **kwargs):
        assert len(clips) == 7
        output.mkdir(parents=True)
        (output / "synthetic-delivery.json").write_text("{}")
        return {"status": "synthetic-technical-check"}

    monkeypatch.setattr(finish, "finish_scene", finish_scene)
    monkeypatch.setattr(finish, "finish_package", finish_package)
    with pytest.raises(ValueError, match="synthetic finalization failure"):
        runner.create(profile="quality", new_run=False, silent=False)
    assert not (run_root / "delivery.json").exists()
    # A changed convenience pointer cannot redirect the pinned continuation.
    durable.atomic_write_json(root / "latest-run.json", {"runRoot": str(root / "runs" / "ffffffffffff")})
    result = runner.create(profile="quality", new_run=False, silent=False, existing_run_id=run_root.name)
    assert result["status"] == "synthetic-technical-check"
    assert all(path.read_bytes() == contents for path, contents in markers.items())
