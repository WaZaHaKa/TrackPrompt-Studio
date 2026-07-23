from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.mission_control.server as server_module
from app.mission_control.models import RuntimeIdentity
from app.mission_control.processes import (
    _find_descendant_in_snapshot,
    process_is_alive,
    process_started_at,
)
from app.mission_control.server import (
    InstanceDescriptorLease,
    SPAStaticFiles,
    _loopback_host,
    create_server_app,
)


def _runtime(instance_id: str = "test-instance") -> RuntimeIdentity:
    return RuntimeIdentity(
        instance_id=instance_id,
        pid=os.getpid(),
        host="127.0.0.1",
        port=43123,
        started_at=datetime(2026, 7, 21, 8, 0, tzinfo=UTC),
    )


def test_spa_static_files_preserve_api_and_serve_deep_links(tmp_path: Path) -> None:
    static = tmp_path / "dist"
    static.mkdir()
    (static / "index.html").write_text(
        "<!doctype html><title>Mission Control</title>",
        encoding="utf-8",
    )
    (static / "asset.txt").write_text("asset", encoding="utf-8")
    application = FastAPI()

    @application.get("/api/ping")
    async def ping() -> dict[str, bool]:
        return {"ok": True}

    application.mount("/", SPAStaticFiles(directory=static, html=True))
    with TestClient(application) as client:
        api = client.get("/api/ping")
        assert api.status_code == 200
        assert api.json() == {"ok": True}
        missing_api = client.get("/api/missing")
        assert missing_api.status_code == 404
        assert "Mission Control" not in missing_api.text
        assert client.get("/asset.txt").text == "asset"
        deep_link = client.get("/render/jobs/abc-123")
        assert deep_link.status_code == 200
        assert "Mission Control" in deep_link.text
        head = client.head("/settings/performance")
        assert head.status_code == 200


def test_standalone_server_allows_only_its_dynamic_loopback_origin(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("TRACKPROMPT_DATA_DIR", str(tmp_path / "data"))
    runtime = _runtime()
    application = create_server_app(
        repository_root=Path(__file__).resolve().parents[2],
        static_dir=None,
        runtime=runtime,
    )
    endpoint = "/api/mission-control/output/inspect"
    origin = f"http://{runtime.host}:{runtime.port}"
    with TestClient(application) as client:
        allowed = client.post(endpoint, json={}, headers={"Origin": origin})
        assert allowed.status_code == 422
        assert allowed.headers["access-control-allow-origin"] == origin

        rejected = client.post(
            endpoint,
            json={},
            headers={"Origin": "https://evil.example"},
        )
        assert rejected.status_code == 403
        assert rejected.json()["error"]["code"] == "origin_not_allowed"


def test_instance_descriptor_claim_is_atomic_and_release_is_owned(
    tmp_path: Path,
) -> None:
    descriptor = tmp_path / "runtime" / "instance.json"
    descriptor.parent.mkdir()
    descriptor.write_text(
        json.dumps({"kind": "pending", "port": 43123}),
        encoding="utf-8",
    )
    runtime = _runtime()
    lease = InstanceDescriptorLease(descriptor, runtime)
    lease.claim()
    payload = json.loads(descriptor.read_text(encoding="utf-8"))
    assert payload == {
        "healthUrl": "http://127.0.0.1:43123/api/mission-control/health",
        "host": "127.0.0.1",
        "instanceId": runtime.instance_id,
        "kind": "trackprompt-mission-control-instance",
        "pid": runtime.pid,
        "port": 43123,
        "schemaVersion": "1.0.0",
        "startedAt": "2026-07-21T08:00:00+00:00",
        "url": "http://127.0.0.1:43123",
    }
    assert not list(descriptor.parent.glob("*.tmp"))
    assert lease.lock_path.is_file()
    lease.release()
    assert not descriptor.exists()
    assert not lease.lock_path.exists()


def test_windows_liveness_probe_never_terminates_the_process() -> None:
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        shell=False,
    )
    try:
        assert process_is_alive(child.pid) is True
        assert process_is_alive(child.pid) is True
        started_at = process_started_at(child.pid)
        assert started_at is not None
        assert abs((datetime.now(UTC) - started_at).total_seconds()) < 30
        assert child.poll() is None
    finally:
        child.terminate()
        child.wait(timeout=10)
    assert process_is_alive(child.pid) is False


def test_blender_descendant_resolution_walks_the_supervisor_tree() -> None:
    processes = [
        (100, 1, "powershell.exe"),
        (200, 100, "python.exe"),
        (300, 200, "blender.exe"),
        (400, 1, "blender.exe"),
    ]
    assert _find_descendant_in_snapshot(processes, 100, {"blender.exe"}) == 300
    assert _find_descendant_in_snapshot(processes, 999, {"blender.exe"}) is None


def test_instance_descriptor_release_never_deletes_another_owner(
    tmp_path: Path,
) -> None:
    descriptor = tmp_path / "instance.json"
    lease = InstanceDescriptorLease(descriptor, _runtime("first"))
    lease.claim()
    descriptor.write_text(
        json.dumps({"instanceId": "replacement", "pid": os.getpid()}),
        encoding="utf-8",
    )
    lease.release()
    assert json.loads(descriptor.read_text(encoding="utf-8"))["instanceId"] == "replacement"


def test_instance_descriptor_rejects_a_live_other_process(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    descriptor = tmp_path / "instance.json"
    descriptor.write_text(
        json.dumps({"instanceId": "other", "pid": os.getpid() + 1000}),
        encoding="utf-8",
    )
    monkeypatch.setattr(server_module, "_pid_alive", lambda _pid: True)
    with pytest.raises(RuntimeError, match="already owns"):
        InstanceDescriptorLease(descriptor, _runtime("new")).claim()
    assert json.loads(descriptor.read_text(encoding="utf-8"))["instanceId"] == "other"


def test_instance_startup_lock_rejects_parallel_claim_and_reclaims_stale_lock(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        server_module,
        "_pid_started_at",
        lambda _pid: datetime(2026, 7, 21, 8, 0, tzinfo=UTC),
    )
    descriptor = tmp_path / "instance.json"
    first = InstanceDescriptorLease(descriptor, _runtime("first"))
    first.claim()
    second = InstanceDescriptorLease(descriptor, _runtime("second"))
    with pytest.raises(RuntimeError, match="already owns"):
        second.claim()
    first.release()

    second.lock_path.write_text(
        json.dumps({"instanceId": "stale", "pid": 2_147_483_647}),
        encoding="utf-8",
    )
    monkeypatch.setattr(server_module, "_pid_alive", lambda _pid: False)
    second.claim()
    assert json.loads(second.lock_path.read_text(encoding="utf-8"))["instanceId"] == "second"
    second.release()


def test_instance_startup_lock_reclaims_a_reused_live_pid(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    descriptor = tmp_path / "instance.json"
    lease = InstanceDescriptorLease(descriptor, _runtime("replacement"))
    lease.lock_path.write_text(
        json.dumps(
            {
                "instanceId": "previous-boot",
                "pid": os.getpid(),
                "startedAt": "2026-07-20T08:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(server_module, "_pid_alive", lambda _pid: True)
    monkeypatch.setattr(
        server_module,
        "_pid_started_at",
        lambda _pid: datetime(2026, 7, 21, 8, 0, tzinfo=UTC),
    )

    lease.claim()

    payload = json.loads(lease.lock_path.read_text(encoding="utf-8"))
    assert payload["instanceId"] == "replacement"
    assert payload["startedAt"] == "2026-07-21T08:00:00+00:00"
    lease.release()


@pytest.mark.parametrize(
    ("value", "expected"),
    [("localhost", "127.0.0.1"), ("127.0.0.1", "127.0.0.1")],
)
def test_loopback_host_accepts_only_loopback(value: str, expected: str) -> None:
    assert _loopback_host(value) == expected


@pytest.mark.parametrize("value", ["0.0.0.0", "192.0.2.10", "example.test", "::1"])
def test_loopback_host_rejects_non_loopback(value: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        _loopback_host(value)
