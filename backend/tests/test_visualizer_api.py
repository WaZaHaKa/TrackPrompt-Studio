from __future__ import annotations

import time
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.adapters import inspect_tool
from app.main import create_app
from app.schemas import AnalysisMode, AnalysisResult, JobStatus

from .helpers import settings_for


@pytest.fixture
def visualizer_client(tmp_path: Path):  # type: ignore[no-untyped-def]
    settings = settings_for(tmp_path / "data")
    if not inspect_tool(settings.ffmpeg_path).available or not inspect_tool(settings.ffprobe_path).available:
        pytest.skip("FFmpeg and ffprobe are required for API integration tests")
    with TestClient(create_app(settings)) as client:
        yield client


def _wait(client: TestClient, job_id: str) -> dict[str, object]:
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        payload = client.get(f"/api/analyses/{job_id}").json()
        if payload["status"] in {"completed", "failed", "cancelled"}:
            return payload
        time.sleep(0.05)
    raise AssertionError("analysis did not complete")


def test_visual_cue_contract_is_advertised_in_capabilities_and_openapi(
    tmp_path: Path,
) -> None:
    application = create_app(settings_for(tmp_path / "contract-data"))
    openapi = application.openapi()

    post_operation = openapi["paths"]["/api/analyses/{job_id}/visual-cues"]["post"]
    post_schema = post_operation["requestBody"]["content"]["application/json"]["schema"]
    assert post_schema["$ref"].endswith("/CuePreferences")
    assert post_operation["responses"]["200"]["content"]["application/json"]["schema"][
        "$ref"
    ].endswith("/TrackPromptVisualCueSheet")

    export_operation = openapi["paths"]["/api/analyses/{job_id}/visual-cues/export"]["get"]
    query_parameters = {
        parameter["name"]: parameter
        for parameter in export_operation["parameters"]
        if parameter["in"] == "query"
    }
    assert set(query_parameters) == {
        "fps",
        "includeBeats",
        "includeOnsets",
        "includeStemEvidence",
        "includeCurves",
        "curveDetail",
    }
    assert query_parameters["fps"]["schema"]["enum"] == [24, 25, 30, 50, 60]
    export_response = export_operation["responses"]["200"]
    assert export_response["content"]["application/json"]["schema"]["$ref"].endswith(
        "/TrackPromptVisualCueSheet"
    )
    assert set(export_response["headers"]) == {"Cache-Control", "Content-Disposition"}

    capability_properties = openapi["components"]["schemas"]["CapabilitiesResponse"][
        "properties"
    ]
    assert capability_properties["visualCueExportAvailable"]["default"] is True
    assert capability_properties["visualCueSheetSchemaVersion"]["default"] == "1.1.0"
    assert capability_properties["visualFeatureArtifactSchemaVersion"]["default"] == "1.0.0"
    assert capability_properties["blenderVisualizerPreset"]["default"] == "abstract-geometry"

    health_properties = openapi["components"]["schemas"]["HealthResponse"]["properties"]
    for field_name in (
        "visualCueExportAvailable",
        "visualCueSheetSchemaVersion",
        "visualFeatureArtifactSchemaVersion",
        "blenderVisualizerPreset",
    ):
        assert health_properties[field_name] == capability_properties[field_name]

    with TestClient(application) as client:
        capabilities = client.get("/api/capabilities")
        health = client.get("/api/health")
    assert capabilities.status_code == 200
    assert capabilities.json()["visualCueExportAvailable"] is True
    assert capabilities.json()["visualCueSheetSchemaVersion"] == "1.1.0"
    assert capabilities.json()["visualFeatureArtifactSchemaVersion"] == "1.0.0"
    assert capabilities.json()["blenderVisualizerPreset"] == "abstract-geometry"
    assert capabilities.json()["networkFeaturesEnabled"] is False
    assert health.status_code == 200
    assert health.json()["visualCueExportAvailable"] is True
    assert health.json()["visualCueSheetSchemaVersion"] == "1.1.0"
    assert health.json()["visualFeatureArtifactSchemaVersion"] == "1.0.0"
    assert health.json()["blenderVisualizerPreset"] == "abstract-geometry"
    assert health.json()["networkFeaturesEnabled"] is False


def test_visual_cue_missing_artifact_and_download_headers_are_safe(
    tmp_path: Path,
    click_analysis: AnalysisResult,
) -> None:
    settings = settings_for(tmp_path / "safe-route-data")
    with TestClient(create_app(settings)) as client:
        job_id = str(uuid4())
        store = client.app.state.store
        store.create_job(
            job_id,
            AnalysisMode.FAST,
            "private-source-name.wav",
            True,
            False,
        )
        analysis = click_analysis.model_copy(
            update={
                "job_id": job_id,
                "file": click_analysis.file.model_copy(
                    update={"display_name": "private-source-name.wav"}
                ),
            }
        )
        store.write_json(
            job_id,
            "analysis.json",
            analysis.model_dump(mode="json", by_alias=True),
        )
        store.update_job(
            job_id,
            status=JobStatus.COMPLETED,
            stage="completed",
            message="Analysis complete",
            progress=100,
        )

        unavailable = client.post(
            f"/api/analyses/{job_id}/visual-cues",
            json={"fps": 30},
        )
        assert unavailable.status_code == 409
        assert unavailable.json()["error"]["code"] == "visual_features_unavailable"
        assert "private-source-name" not in unavailable.text
        assert "visual-features.json" not in unavailable.text
        assert "source.bin" not in unavailable.text

        unavailable_download = client.get(
            f"/api/analyses/{job_id}/visual-cues/export"
        )
        assert unavailable_download.status_code == 409
        assert unavailable_download.json()["error"]["code"] == (
            "visual_features_unavailable"
        )
        assert "private-source-name" not in unavailable_download.text
        assert "visual-features.json" not in unavailable_download.text
        assert "source.bin" not in unavailable_download.text

        legacy = client.get(
            f"/api/analyses/{job_id}/visual-cues/export"
            "?fps=25&includeBeats=false&includeOnsets=false"
            "&includeStemEvidence=false&includeCurves=false&curveDetail=compact"
        )
        assert legacy.status_code == 200, legacy.text
        assert legacy.headers["content-type"].startswith("application/json")
        assert legacy.headers["content-disposition"] == (
            f'attachment; filename="trackprompt-{job_id}-visual-cues.json"'
        )
        assert legacy.headers["cache-control"] == "no-store"
        assert legacy.headers["x-content-type-options"] == "nosniff"
        assert legacy.headers["referrer-policy"] == "no-referrer"
        payload = legacy.json()
        assert payload["timeline"]["fps"] == 25
        assert payload["beats"] == []
        assert payload["onsets"] == []
        assert payload["curves"] == {}
        assert all(section["stemActivity"] == {} for section in payload["sections"])
        assert "private-source-name" not in legacy.text
        assert "source.bin" not in legacy.text

        assert client.delete(f"/api/analyses/{job_id}").status_code == 204


def test_visual_cue_compile_and_download_are_safe(
    visualizer_client: TestClient,
    fixture_dir: Path,
) -> None:
    response = visualizer_client.post(
        "/api/analyses",
        files={"file": ("private-source-name.wav", (fixture_dir / "120bpm_click.wav").read_bytes())},
        data={"mode": "fast", "permissionConfirmed": "true"},
    )
    assert response.status_code == 202, response.text
    job_id = response.json()["jobId"]
    assert _wait(visualizer_client, job_id)["status"] == "completed"
    store = visualizer_client.app.state.store
    assert (store.job_dir(job_id) / "visual-features.json").is_file()

    compiled = visualizer_client.post(
        f"/api/analyses/{job_id}/visual-cues",
        json={"fps": 30, "curveDetail": "compact", "includeOnsets": False},
    )
    assert compiled.status_code == 200, compiled.text
    assert compiled.json()["schemaVersion"] == "1.1.0"
    assert compiled.json()["curves"]["masterEnergy"]["points"]
    assert compiled.json()["onsets"] == []
    assert "private-source-name" not in compiled.text
    assert "source.bin" not in compiled.text

    downloaded = visualizer_client.get(
        f"/api/analyses/{job_id}/visual-cues/export?fps=60&curveDetail=compact"
    )
    assert downloaded.status_code == 200
    assert downloaded.headers["content-type"].startswith("application/json")
    assert downloaded.headers["content-disposition"] == (
        f'attachment; filename="trackprompt-{job_id}-visual-cues.json"'
    )
    assert downloaded.json()["timeline"]["fps"] == 60

    invalid_fps = visualizer_client.get(
        f"/api/analyses/{job_id}/visual-cues/export?fps=29"
    )
    assert invalid_fps.status_code == 422
    assert invalid_fps.json()["error"]["code"] == "invalid_visual_cue_preferences"

    store.delete_json(job_id, "visual-features.json")
    unavailable = visualizer_client.get(f"/api/analyses/{job_id}/visual-cues/export")
    assert unavailable.status_code == 409
    assert unavailable.json()["error"]["code"] == "visual_features_unavailable"
    legacy = visualizer_client.get(
        f"/api/analyses/{job_id}/visual-cues/export?includeCurves=false"
    )
    assert legacy.status_code == 200
    assert legacy.json()["curves"] == {}
    assert legacy.json()["sections"]

    job_dir = store.job_dir(job_id)
    assert visualizer_client.delete(f"/api/analyses/{job_id}").status_code == 204
    assert not job_dir.exists()
