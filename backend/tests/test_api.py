from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
import app.media as media_module
from app.adapters import inspect_tool
from app.main import create_app
from app.store import DeletionError

from .helpers import settings_for


@pytest.fixture
def api_client(tmp_path: Path):
    settings = settings_for(tmp_path / "api-data")
    if not inspect_tool(settings.ffmpeg_path).available or not inspect_tool(settings.ffprobe_path).available:
        pytest.skip("FFmpeg and ffprobe are required for API integration tests")
    with TestClient(create_app(settings)) as client:
        yield client


def _upload(client: TestClient, path: Path, *, mode: str = "fast") -> dict:
    response = client.post(
        "/api/analyses",
        files={"file": (path.name, path.read_bytes(), "application/octet-stream")},
        data={
            "mode": mode,
            "permissionConfirmed": "true",
            "enableLyricalAnalysis": "false",
        },
    )
    assert response.status_code == 202, response.text
    return response.json()


def _wait(client: TestClient, job_id: str, timeout: float = 45) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/analyses/{job_id}")
        assert response.status_code == 200, response.text
        payload = response.json()
        if payload["status"] in {"completed", "failed", "cancelled"}:
            return payload
        time.sleep(0.1)
    raise AssertionError("analysis did not reach a terminal state")


def test_health_and_capabilities_are_truthful(api_client: TestClient) -> None:
    health = api_client.get("/api/health")
    capabilities = api_client.get("/api/capabilities")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert health.json()["databaseAvailable"] is True
    assert health.json()["networkFeaturesEnabled"] is False
    assert capabilities.status_code == 200
    assert capabilities.json()["fastMode"]["available"] is True
    assert capabilities.json()["deepMode"]["willFallback"] is True


def test_permission_and_invalid_media_are_safe_4xx(
    api_client: TestClient, fixture_dir: Path
) -> None:
    denied = api_client.post(
        "/api/analyses",
        files={"file": ("click.wav", (fixture_dir / "120bpm_click.wav").read_bytes())},
        data={"mode": "fast", "permissionConfirmed": "false"},
    )
    assert denied.status_code == 400
    assert denied.json()["error"]["code"] == "permission_required"
    invalid = api_client.post(
        "/api/analyses",
        files={"file": ("looks-valid.wav", (fixture_dir / "invalid_media.bin").read_bytes())},
        data={"mode": "fast", "permissionConfirmed": "true"},
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "invalid_media"
    assert "jobId" not in invalid.json()
    assert "Traceback" not in invalid.text
    assert "source.bin" not in invalid.text


def test_unexpected_api_failure_uses_safe_structured_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_detail = str(tmp_path / "private" / "source.wav")

    def unexpected_failure(_settings):
        raise RuntimeError(private_detail)

    monkeypatch.setattr(main_module, "get_capabilities", unexpected_failure)
    with TestClient(
        create_app(settings_for(tmp_path / "error-data")),
        raise_server_exceptions=False,
    ) as client:
        response = client.get("/api/health")
    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["error"]["code"] == "internal_error"
    assert response.json()["error"]["message"] == "The request could not be completed safely."
    assert private_detail not in response.text
    assert "Traceback" not in response.text


@pytest.mark.parametrize(
    ("method", "path", "headers", "content", "status", "code"),
    [
        (
            "POST",
            "/api/analyses",
            {"Content-Type": "multipart/form-data; boundary=broken"},
            b"not-a-valid-multipart-body",
            400,
            "bad_request",
        ),
        ("GET", "/api/not-a-route", {}, None, 404, "route_not_found"),
        ("GET", "/api/analyses", {}, None, 405, "method_not_allowed"),
    ],
)
def test_framework_http_errors_use_safe_structured_envelope(
    api_client: TestClient,
    method: str,
    path: str,
    headers: dict[str, str],
    content: bytes | None,
    status: int,
    code: str,
) -> None:
    response = api_client.request(method, path, headers=headers, content=content)
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert "detail" not in response.json()


def test_api_rejects_untrusted_origin_host_and_cross_site_context(api_client: TestClient) -> None:
    bad_origin = api_client.post(
        "/api/analyses",
        headers={"Origin": "https://evil.example", "Host": "testserver"},
        content=b"",
    )
    assert bad_origin.status_code == 403
    assert bad_origin.json()["error"]["code"] == "origin_not_allowed"
    bad_host = api_client.post(
        "/api/analyses",
        headers={"Host": "evil.example"},
        content=b"",
    )
    assert bad_host.status_code == 403
    assert bad_host.json()["error"]["code"] == "host_not_allowed"
    bad_read_host = api_client.get("/api/health", headers={"Host": "evil.example"})
    assert bad_read_host.status_code == 403
    assert bad_read_host.json()["error"]["code"] == "host_not_allowed"
    cross_site = api_client.get(
        "/api/health",
        headers={"Host": "testserver", "Sec-Fetch-Site": "cross-site"},
    )
    assert cross_site.status_code == 403
    assert cross_site.json()["error"]["code"] == "cross_site_request_rejected"


def test_rejected_upload_cleanup_failure_stays_registered_for_retry(
    api_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = api_client.app.state.store
    actual_delete = store.delete_job

    def locked_delete(_job_id: str) -> bool:
        raise DeletionError("simulated open upload handle")

    monkeypatch.setattr(store, "delete_job", locked_delete)
    response = api_client.post(
        "/api/analyses",
        files={"file": ("empty.wav", b"")},
        data={"mode": "fast", "permissionConfirmed": "true"},
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "cleanup_pending"
    job_id = response.json()["error"]["details"]["jobId"]
    record = store.require_job(job_id)
    assert record.stage == "cleanup_pending"
    assert record.error_code == "cleanup_pending"
    monkeypatch.setattr(store, "delete_job", actual_delete)
    assert actual_delete(job_id)


def test_non_upload_api_body_has_preparse_limit(api_client: TestClient) -> None:
    response = api_client.patch(
        "/api/analyses/11111111-1111-4111-8111-111111111111",
        headers={"Content-Type": "application/json"},
        content=b"x" * (256 * 1024 + 1),
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "request_body_too_large"


def test_complete_local_vertical_slice(
    api_client: TestClient,
    fixture_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actual_run_probe = media_module._run_probe
    probe_cancellation_callbacks: list[bool] = []

    def tracked_run_probe(path, settings, cancel_requested=None):
        probe_cancellation_callbacks.append(cancel_requested is not None)
        return actual_run_probe(path, settings, cancel_requested)

    monkeypatch.setattr(media_module, "_run_probe", tracked_run_probe)
    queued = _upload(api_client, fixture_dir / "120bpm_click.wav")
    assert queued["status"] == "queued"
    job_id = queued["jobId"]
    completed = _wait(api_client, job_id)
    assert completed["status"] == "completed", completed
    assert probe_cancellation_callbacks == [False, True]
    analysis = completed["analysis"]
    assert abs(analysis["rhythm"]["bpm"]["value"] - 120) < 3
    assert analysis["waveformPeaks"]
    assert analysis["structure"]["sections"]
    assert analysis["production"]["integratedLoudnessLufs"]["value"] is not None
    assert "original melody" in completed["promptPackage"]["primaryPrompt"]

    events = api_client.get(f"/api/analyses/{job_id}/events")
    assert events.status_code == 200
    assert "event: completed" in events.text
    assert '"progress":100' in events.text

    audio = api_client.get(f"/api/analyses/{job_id}/audio", headers={"Range": "bytes=0-99"})
    assert audio.status_code == 206
    assert len(audio.content) == 100
    assert audio.headers["content-range"].startswith("bytes 0-99/")
    assert "source" not in audio.headers["content-disposition"]

    edited = api_client.patch(
        f"/api/analyses/{job_id}",
        json={
            "updates": [
                {"path": "rhythm.bpm", "value": 135, "disabledForPrompt": True}
            ]
        },
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["analysis"]["rhythm"]["bpm"]["value"] == 135
    assert "rhythm.bpm" in edited.json()["analysis"]["disabledFeaturePaths"]
    assert edited.json()["promptPackage"] is None
    assert api_client.get(f"/api/analyses/{job_id}").json()["promptPackage"] is None

    prompt = api_client.post(
        f"/api/analyses/{job_id}/prompt",
        json={
            "targetMood": "dark",
            "promptLength": "compact",
            "variationSeed": 17,
            "exclusions": ["No vocal chops"],
            "disabledFeaturePaths": [],
            "userOverrides": {},
        },
    )
    assert prompt.status_code == 200, prompt.text
    assert "135 BPM" not in prompt.json()["primaryPrompt"]
    assert prompt.json()["exclusions"] == ["No vocal chops"]

    json_export = api_client.get(f"/api/analyses/{job_id}/export.json")
    markdown_export = api_client.get(f"/api/analyses/{job_id}/export.md")
    assert json_export.status_code == 200
    assert json_export.json()["schemaVersion"] == "1.1.0"
    assert markdown_export.status_code == 200
    assert "# TrackPrompt Studio analysis" in markdown_export.text

    assert api_client.delete(f"/api/analyses/{job_id}").status_code == 204
    assert api_client.delete(f"/api/analyses/{job_id}").status_code == 204
    assert api_client.get(f"/api/analyses/{job_id}").status_code == 404
    assert api_client.get(f"/api/analyses/{job_id}/audio").status_code == 404


@pytest.mark.parametrize(
    "path",
    [
        "/api/analyses/not-a-uuid",
        "/api/analyses/not-a-uuid/audio",
        "/api/analyses/not-a-uuid/export.json",
    ],
)
def test_malformed_job_id_is_structured_404(api_client: TestClient, path: str) -> None:
    response = api_client.get(path)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "job_not_found"


def test_prompt_rejects_unknown_override_path(api_client: TestClient, fixture_dir: Path) -> None:
    job_id = _upload(api_client, fixture_dir / "120bpm_click.wav")["jobId"]
    assert _wait(api_client, job_id)["status"] == "completed"
    response = api_client.post(
        f"/api/analyses/{job_id}/prompt",
        json={"userOverrides": {"lyrics": "do not include me"}},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_prompt_override"
    api_client.delete(f"/api/analyses/{job_id}")
