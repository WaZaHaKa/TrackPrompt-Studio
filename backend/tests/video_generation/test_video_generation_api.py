# ruff: noqa: E402
from __future__ import annotations

import sys
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

PACKAGE_ROOT = Path(__file__).resolve().parents[3]
if str(PACKAGE_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT / "backend"))

from app.mission_control.config import MissionControlConfig
from app.mission_control.router import install_mission_control
from app.mission_control.service import MissionControlService


def test_video_plan_api_uses_mission_control_persistence_and_authorization_gate(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state" / "mission-control"
    analysis_id = str(uuid4())
    analysis_root = state_root.parent / "jobs" / analysis_id
    analysis_root.mkdir(parents=True)
    (analysis_root / "analysis.json").write_text("{}\n", encoding="utf-8")
    config = MissionControlConfig.from_repository(
        PACKAGE_ROOT,
        state_root=state_root,
        allow_fake_renderer=True,
        native_dialog_enabled=False,
    )
    service = MissionControlService(config)
    application = FastAPI()
    application.state.mission_control_service = service
    install_mission_control(application)
    try:
        with TestClient(application) as client:
            catalog = client.get("/api/mission-control/video/catalog")
            assert catalog.status_code == 200
            body = catalog.json()
            assert body["providerNetworkContacted"] is False
            assert body["analyses"][0]["analysisJobId"] == analysis_id
            assert body["packages"][0]["profiles"][0]["id"] == "fast-1080p"

            planned = client.post(
                "/api/mission-control/video/plans",
                json={
                    "analysisJobId": analysis_id,
                    "projectId": "the-glitch-is-me",
                    "profileId": "fast-1080p",
                    "gcpProjectId": "test-gcp-project",
                    "gcsBucket": "example-trackprompt-video",
                    "audioPath": None,
                },
            )
            assert planned.status_code == 200
            plan = planned.json()
            assert plan["state"] == "planned"
            assert plan["totalShotCount"] == 16
            assert plan["cost"]["maxSpendUsd"] == 24

            blocked = client.post(f"/api/mission-control/video/jobs/{plan['jobId']}/start")
            assert blocked.status_code == 409
            assert blocked.json()["error"]["code"] == "video_authorization_required"

            wrong = client.post(
                f"/api/mission-control/video/plans/{plan['jobId']}/authorize",
                json={"confirmation": "AUTHORIZE SOMETHING ELSE"},
            )
            assert wrong.status_code == 422
            assert wrong.json()["error"]["code"] == "video_authorization_invalid"

            authorized = client.post(
                f"/api/mission-control/video/plans/{plan['jobId']}/authorize",
                json={"confirmation": plan["authorizationPhrase"]},
            )
            assert authorized.status_code == 200
            assert authorized.json()["state"] == "authorized"
    finally:
        service.close()

    restarted = MissionControlService(config)
    try:
        restored = restarted.video_generation.get(plan["jobId"])
        assert restored.state == "authorized"
        assert restored.plan_digest == plan["planDigest"]
    finally:
        restarted.close()
