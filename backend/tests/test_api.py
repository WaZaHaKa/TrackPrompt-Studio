from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
import app.media as media_module
from app.adapters import inspect_tool
from app.main import create_app
from app.prompting.engine import generate_prompt_package as actual_generate_prompt_package
from app.prompting.local_writer import FakePromptWriterAdapter
from app.schemas import (
    Confidence,
    LyricsAnalysisSummary,
    LyricsSegment,
    LyricsSegmentQualityDecision,
    PrivateLyricsTranscript,
)
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


def test_theme_patch_accepts_safe_open_vocabulary_and_rejects_private_fragments(
    api_client: TestClient,
    fixture_dir: Path,
) -> None:
    created = _upload(api_client, fixture_dir / "120bpm_click.wav")
    completed = _wait(api_client, created["jobId"])
    assert completed["status"] == "completed"
    job_id = created["jobId"]
    transcript = PrivateLyricsTranscript(
        job_id=job_id,
        language="en",
        model_id="test-lyrics-model",
        selected_device="cpu",
        segments=[
            LyricsSegment(
                id="segment-1",
                start_seconds=0.0,
                end_seconds=2.0,
                text="hidden silver words remain private tonight",
                confidence=Confidence.HIGH,
                quality_decision=LyricsSegmentQualityDecision.ACCEPTED,
            ),
            LyricsSegment(
                id="segment-2",
                start_seconds=2.0,
                end_seconds=3.0,
                text="the signal bends toward",
                confidence=Confidence.HIGH,
                quality_decision=LyricsSegmentQualityDecision.ACCEPTED,
            ),
            LyricsSegment(
                id="segment-3",
                start_seconds=3.0,
                end_seconds=4.0,
                text="a restless dawn returns",
                confidence=Confidence.HIGH,
                quality_decision=LyricsSegmentQualityDecision.ACCEPTED,
            ),
        ],
    )
    store = api_client.app.state.store
    transcript_payload = transcript.model_dump(mode="json", by_alias=True)
    store.write_json(job_id, "lyrics.json", transcript_payload)
    store.write_json(job_id, "detected-lyrics.json", transcript_payload)
    analysis_payload = store.read_json(job_id, "analysis.json")
    assert analysis_payload is not None
    analysis_payload["lyricsSummary"] = LyricsAnalysisSummary(
        enabled=True,
        status="completed",
        transcript_available=True,
        segment_count=3,
    ).model_dump(mode="json", by_alias=True)
    store.write_json(job_id, "analysis.json", analysis_payload)

    approved = api_client.patch(
        f"/api/analyses/{job_id}/lyrics",
        json={"abstractThemes": ["courage and renewed hope"]},
    )
    assert approved.status_code == 200, approved.text
    reloaded = api_client.get(f"/api/analyses/{job_id}").json()
    assert reloaded["analysis"]["lyricsSummary"]["abstractThemes"] == [
        "courage and renewed hope"
    ]
    assert reloaded["analysis"]["lyricsSummary"]["themesUserApproved"] is True

    private_fragment = api_client.patch(
        f"/api/analyses/{job_id}/lyrics",
        json={"abstractThemes": ["silver words remain private"]},
    )
    assert private_fragment.status_code == 200, private_fragment.text
    reloaded = api_client.get(f"/api/analyses/{job_id}").json()
    assert reloaded["analysis"]["lyricsSummary"]["abstractThemes"] == []
    assert reloaded["analysis"]["lyricsSummary"]["themesUserApproved"] is False

    boundary_fragment = api_client.patch(
        f"/api/analyses/{job_id}/lyrics",
        json={"abstractThemes": ["bends toward a restless dawn"]},
    )
    assert boundary_fragment.status_code == 200, boundary_fragment.text
    reloaded = api_client.get(f"/api/analyses/{job_id}").json()
    assert reloaded["analysis"]["lyricsSummary"]["abstractThemes"] == []
    assert reloaded["analysis"]["lyricsSummary"]["themesUserApproved"] is False


def test_transcript_and_section_edits_refresh_mapping_and_clear_stale_themes(
    api_client: TestClient,
    fixture_dir: Path,
) -> None:
    created = _upload(api_client, fixture_dir / "120bpm_click.wav")
    completed = _wait(api_client, created["jobId"])
    assert completed["status"] == "completed"
    job_id = created["jobId"]
    store = api_client.app.state.store
    analysis_payload = store.read_json(job_id, "analysis.json")
    assert analysis_payload is not None
    duration = float(analysis_payload["file"]["durationSeconds"])
    assert duration > 9.0
    template = dict(analysis_payload["structure"]["sections"][0])
    analysis_payload["structure"]["sections"] = [
        {
            **template,
            "id": "section-a",
            "neutralLabel": "section A",
            "inferredLabel": None,
            "startSeconds": 0.0,
            "endSeconds": 8.0,
        },
        {
            **template,
            "id": "section-b",
            "neutralLabel": "section B",
            "inferredLabel": None,
            "startSeconds": 8.0,
            "endSeconds": duration,
        },
    ]
    analysis_payload["lyricsSummary"] = LyricsAnalysisSummary(
        enabled=True,
        status="no_reliable_words",
        transcript_available=False,
        segment_count=0,
        abstract_themes=["old approved theme"],
        theme_confidence=Confidence.MEDIUM,
        themes_user_approved=True,
    ).model_dump(mode="json", by_alias=True)
    store.write_json(job_id, "analysis.json", analysis_payload)
    store.write_json(job_id, "detected-analysis.json", analysis_payload)
    transcript = PrivateLyricsTranscript(
        job_id=job_id,
        language="en",
        model_id="test-lyrics-model",
        selected_device="cpu",
        segments=[
            LyricsSegment(
                id="segment-rejected",
                start_seconds=7.5,
                end_seconds=8.5,
                text="synthetic uncertain phrase",
                confidence=Confidence.LOW,
                quality_decision=LyricsSegmentQualityDecision.REJECTED_AS_LIKELY_HALLUCINATION,
                active_section_ids=[],
            )
        ],
    )
    transcript_payload = transcript.model_dump(mode="json", by_alias=True)
    store.write_json(job_id, "lyrics.json", transcript_payload)
    store.write_json(job_id, "detected-lyrics.json", transcript_payload)

    made_usable = api_client.patch(
        f"/api/analyses/{job_id}/lyrics",
        json={"updates": [{"segmentId": "segment-rejected", "markUncertain": True}]},
    )
    assert made_usable.status_code == 200, made_usable.text
    segment = made_usable.json()["segments"][0]
    assert segment["qualityDecision"] == "uncertain"
    assert segment["activeSectionIds"] == ["section-a", "section-b"]
    refreshed = api_client.get(f"/api/analyses/{job_id}").json()["analysis"]["lyricsSummary"]
    assert refreshed["segmentCount"] == 1
    assert refreshed["activeSectionIds"] == ["section-a", "section-b"]
    assert refreshed["abstractThemes"] == []
    assert refreshed["themesUserApproved"] is False

    moved_boundary = api_client.patch(
        f"/api/analyses/{job_id}",
        json={
            "updates": [
                {"path": "structure.sections.0.endSeconds", "value": 7.0},
                {"path": "structure.sections.1.startSeconds", "value": 7.0},
            ]
        },
    )
    assert moved_boundary.status_code == 200, moved_boundary.text
    remapped = api_client.get(f"/api/analyses/{job_id}/lyrics").json()["segments"][0]
    assert remapped["activeSectionIds"] == ["section-b"]
    final_summary = api_client.get(f"/api/analyses/{job_id}").json()["analysis"]["lyricsSummary"]
    assert final_summary["activeSectionIds"] == ["section-b"]
    stored_summary = store.read_json(job_id, "lyrics-summary.json")
    assert stored_summary is not None
    assert stored_summary["activeSectionIds"] == ["section-b"]

    emptied = api_client.patch(
        f"/api/analyses/{job_id}/lyrics",
        json={"updates": [{"segmentId": "segment-rejected", "text": "   "}]},
    )
    assert emptied.status_code == 200, emptied.text
    emptied_segment = emptied.json()["segments"][0]
    assert emptied_segment["qualityDecision"] == "non_lexical"
    assert emptied_segment["activeSectionIds"] == []
    empty_summary = api_client.get(f"/api/analyses/{job_id}").json()["analysis"]["lyricsSummary"]
    assert empty_summary["status"] == "no_reliable_words"
    assert empty_summary["segmentCount"] == 0
    assert empty_summary["transcriptAvailable"] is False
    assert empty_summary["activeSectionIds"] == []
    assert empty_summary["nonLexicalVocalizationTendency"] == "possible"
    assert not any("uncertain segment(s)" in warning for warning in empty_summary["warnings"])


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
    selection_without_package = api_client.patch(
        f"/api/analyses/{job_id}/prompt",
        json={"candidateId": "candidate-reliable-0"},
    )
    assert selection_without_package.status_code == 409
    assert selection_without_package.json()["error"]["code"] == "prompt_not_generated"

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
    assert json_export.json()["schemaVersion"] == "1.4.0"
    assert json_export.json()["promptPackage"] == prompt.json()
    assert api_client.get(f"/api/analyses/{job_id}").json()["promptPackage"] == prompt.json()
    assert markdown_export.status_code == 200
    assert "# TrackPrompt Studio analysis" in markdown_export.text
    assert prompt.json()["primaryPrompt"] in markdown_export.text

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


def test_all_prompt_modes_persist_selected_candidate_across_reload_and_exports(
    api_client: TestClient,
    fixture_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def generate_with_fake_writer(analysis, preferences, settings, transcript=None):  # type: ignore[no-untyped-def]
        return actual_generate_prompt_package(
            analysis,
            preferences,
            settings,
            transcript,
            adapter=FakePromptWriterAdapter(),
        )

    monkeypatch.setattr(main_module, "generate_prompt_package", generate_with_fake_writer)
    job_id = _upload(api_client, fixture_dir / "120bpm_click.wav")["jobId"]
    assert _wait(api_client, job_id)["status"] == "completed"

    for mode in ("reliable", "creative", "experimental"):
        requested_count = 1 if mode == "reliable" else 3
        generated = api_client.post(
            f"/api/analyses/{job_id}/prompt",
            json={
                "promptEngineMode": mode,
                "candidateCount": requested_count,
                "variationSeed": 20260718,
                "genreInterpretationMode": "disabled",
                "lyricsInfluenceMode": "none",
            },
        )
        assert generated.status_code == 200, generated.text
        package = generated.json()
        assert package["engineMode"] == mode
        assert len(package["candidates"]) == requested_count
        assert package["selectedCandidateId"] == package["candidates"][0]["id"]

        chosen = package["candidates"][-1]
        selected = api_client.patch(
            f"/api/analyses/{job_id}/prompt",
            json={"candidateId": chosen["id"]},
        )
        assert selected.status_code == 200, selected.text
        assert selected.json()["selectedCandidateId"] == chosen["id"]
        assert selected.json()["primaryPrompt"] == chosen["prompt"]
        if mode != "reliable":
            assert selected.json()["compactPrompt"] == chosen["prompt"]
            assert selected.json()["detailedPrompt"] == chosen["prompt"]
            assert selected.json()["rationale"]
            assert selected.json()["arrangementBlueprint"]

        reloaded = api_client.get(f"/api/analyses/{job_id}")
        exported_json = api_client.get(f"/api/analyses/{job_id}/export.json")
        exported_markdown = api_client.get(f"/api/analyses/{job_id}/export.md")
        assert reloaded.json()["promptPackage"] == selected.json()
        assert exported_json.json()["promptPackage"] == selected.json()
        assert chosen["prompt"] in exported_markdown.text
        assert chosen["id"] in exported_markdown.text
        assert "(selected)" in exported_markdown.text

        unknown = api_client.patch(
            f"/api/analyses/{job_id}/prompt",
            json={"candidateId": "candidate-not-in-this-package"},
        )
        assert unknown.status_code == 422
        assert unknown.json()["error"]["code"] == "prompt_candidate_not_found"
        assert api_client.get(f"/api/analyses/{job_id}").json()["promptPackage"] == selected.json()

    api_client.delete(f"/api/analyses/{job_id}")


def test_missing_private_lyrics_artifact_disables_theme_evidence_at_all_boundaries(
    api_client: TestClient,
    fixture_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def generate_with_fake_writer(analysis, preferences, settings, transcript=None):  # type: ignore[no-untyped-def]
        return actual_generate_prompt_package(
            analysis,
            preferences,
            settings,
            transcript,
            adapter=FakePromptWriterAdapter(),
        )

    monkeypatch.setattr(main_module, "generate_prompt_package", generate_with_fake_writer)
    job_id = _upload(api_client, fixture_dir / "120bpm_click.wav")["jobId"]
    assert _wait(api_client, job_id)["status"] == "completed"
    store = api_client.app.state.store
    transcript = PrivateLyricsTranscript(
        job_id=job_id,
        language="en",
        model_id="test-lyrics-model",
        selected_device="cpu",
        segments=[
            LyricsSegment(
                id="segment-1",
                start_seconds=0.0,
                end_seconds=2.0,
                text="synthetic words for a private artifact",
                confidence=Confidence.HIGH,
                quality_decision=LyricsSegmentQualityDecision.ACCEPTED,
            )
        ],
    )
    transcript_payload = transcript.model_dump(mode="json", by_alias=True)
    store.write_json(job_id, "lyrics.json", transcript_payload)
    store.write_json(job_id, "detected-lyrics.json", transcript_payload)
    analysis_payload = store.read_json(job_id, "analysis.json")
    assert analysis_payload is not None
    analysis_payload["lyricsSummary"] = LyricsAnalysisSummary(
        enabled=True,
        status="completed",
        transcript_available=True,
        segment_count=1,
        abstract_themes=["patient forward motion"],
        theme_confidence=Confidence.MEDIUM,
        themes_user_approved=True,
    ).model_dump(mode="json", by_alias=True)
    store.write_json(job_id, "analysis.json", analysis_payload)

    with_theme = api_client.post(
        f"/api/analyses/{job_id}/prompt",
        json={
            "promptEngineMode": "reliable",
            "lyricsInfluenceMode": "abstract_themes",
            "includeLyricalThemes": True,
        },
    )
    assert with_theme.status_code == 200, with_theme.text
    assert "lyricsSummary.abstractThemes" in {
        fact["path"] for fact in with_theme.json()["factsUsed"]
    }

    store.delete_json(job_id, "lyrics.json")
    missing = api_client.get(f"/api/analyses/{job_id}")
    assert missing.status_code == 200
    missing_summary = missing.json()["analysis"]["lyricsSummary"]
    assert missing_summary["status"] == "artifact_missing"
    assert missing_summary["abstractThemes"] == []
    assert missing_summary["themesUserApproved"] is False

    latest_package = None
    for mode in ("reliable", "creative", "experimental"):
        generated = api_client.post(
            f"/api/analyses/{job_id}/prompt",
            json={
                "promptEngineMode": mode,
                "candidateCount": 1,
                "variationSeed": 20260718,
                "genreInterpretationMode": "disabled",
                "lyricsInfluenceMode": "abstract_themes",
                "includeLyricalThemes": True,
            },
        )
        assert generated.status_code == 200, generated.text
        latest_package = generated.json()
        assert "lyricsSummary.abstractThemes" not in {
            fact["path"] for fact in latest_package["factsUsed"]
        }
        assert "patient forward motion" not in str(latest_package).casefold()

    exported_json = api_client.get(f"/api/analyses/{job_id}/export.json")
    exported_markdown = api_client.get(f"/api/analyses/{job_id}/export.md")
    assert exported_json.status_code == 200
    assert exported_json.json()["analysis"]["lyricsSummary"]["status"] == "artifact_missing"
    assert exported_json.json()["promptPackage"] == latest_package
    assert "patient forward motion" not in exported_json.text.casefold()
    assert "patient forward motion" not in exported_markdown.text.casefold()

    api_client.delete(f"/api/analyses/{job_id}")
