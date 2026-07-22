from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import zlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import TypeAdapter, ValidationError

import app.mission_control.outputs as outputs_module
import app.mission_control.renderers as renderers_module
import app.mission_control.service as service_module
from app.mission_control.config import MissionControlConfig
from app.mission_control.discovery import load_json_object, sha256_file
from app.mission_control.errors import MissionControlError
from app.mission_control.models import (
    EncodeReadiness,
    EncodeStartRequest,
    FakeRenderOptions,
    JobRecord,
    JobState,
    OutputClassification,
    OutputEntry,
    PerformanceEnableRequest,
    PerformanceStatus,
    PreflightRequest,
    RendererKind,
    RenderIdentity,
    ResumeRequest,
    StartFakeRenderRequest,
    StartRenderRequest,
)
from app.mission_control.router import install_mission_control
from app.mission_control.service import MissionControlService
from app.mission_control.store import MissionControlStore

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SOURCE_PROFILE = (
    REPOSITORY_ROOT
    / "render-profiles"
    / "trip-to-andromeda"
    / "trip-to-andromeda-720p-hyper-optimized.json"
)
SOURCE_SCENE = (
    REPOSITORY_ROOT
    / "test-output"
    / "final-render-prep-20260720-011332"
    / "approved-candidate"
    / "trackprompt-space-journey-final-candidate.blend"
)
PROFILE_ID = "TRIP-TO-ANDROMEDA-720P-HYPER-OPTIMIZED"
SCENE_ID = "space-journey"
PROFILE_SHA256 = "DB27AA9DE2939ACA78819B58BB08C7DB408EED7092E83FA327363EE094779BF0"
SCENE_SHA256 = "225EE7124B62434FF66D68E2477E5523C99914C76D7304366B0EBB696E0EFED5"
AUTHORIZATION_TOKEN = (
    "AUTHORIZE FULL RENDER: TRIP-TO-ANDROMEDA | SPACE-JOURNEY | "
    "TRIP-TO-ANDROMEDA-720P-HYPER-OPTIMIZED | SCENE 225EE7124B62 | PROFILE DB27AA9DE293"
)


@dataclass(frozen=True, slots=True)
class MissionFixture:
    config: MissionControlConfig
    profile_path: Path
    scene_path: Path
    profile_sha256: str
    scene_sha256: str
    authorization_token: str


def _build_config(
    tmp_path: Path,
    *,
    profile_source: Path | None = None,
) -> MissionFixture:
    profile_root = tmp_path / "render-profiles"
    profile_directory = profile_root / "trip-to-andromeda"
    profile_directory.mkdir(parents=True)
    calibration_root = tmp_path / "calibrations"
    calibration_root.mkdir()
    output_root = tmp_path / "outputs"
    output_root.mkdir()
    if profile_source is not None:
        profile_path = profile_directory / profile_source.name
        shutil.copyfile(profile_source, profile_path)
        scene_path = SOURCE_SCENE
    else:
        scene_path = tmp_path / "synthetic-approved-scene.blend"
        scene_path.write_bytes(b"BLENDER-v300\x00trackprompt synthetic test scene\n")
        scene_hash = sha256_file(scene_path)
        profile_path = profile_directory / "synthetic-720p-profile.json"
        profile_path.write_text(
            json.dumps(
                {
                    "schemaVersion": "1.1.0",
                    "kind": "trackprompt-render-profile",
                    "id": "synthetic-mission-control-profile",
                    "project": "trip-to-andromeda",
                    "preset": SCENE_ID,
                    "profileId": PROFILE_ID,
                    "displayName": "Synthetic Mission Control Profile",
                    "approvedScenePath": str(scene_path.resolve()),
                    "approvedSceneSha256": scene_hash,
                    "approvedScene": {
                        "path": str(scene_path.resolve()),
                        "sha256": scene_hash,
                        "preset": SCENE_ID,
                    },
                    "authorization": {
                        "project": "TRIP-TO-ANDROMEDA",
                        "preset": "SPACE-JOURNEY",
                        "profile": PROFILE_ID,
                        "status": "pending-operator-approval",
                    },
                    "timeline": {"frameStart": 1, "frameEnd": 13029, "fps": 30},
                    "frameStart": 1,
                    "frameEnd": 13029,
                    "fps": 30,
                    "resolution": {"width": 1280, "height": 720, "label": "HD"},
                    "chunking": {"framesPerChunk": 600},
                    "production": {
                        "framesPerChunk": 600,
                        "resumeEnabled": True,
                        "verifyExistingFrames": True,
                        "atomicChunkCommit": True,
                        "overwriteValidFrames": False,
                    },
                    "imageSequence": {
                        "format": "PNG",
                        "filenamePattern": "frame_%06d.png",
                        "bitDepth": 8,
                        "colorMode": "RGB",
                    },
                    "calibration": {
                        "expectedTotalHours": 5.045,
                        "conservativeTotalHours": 5.891,
                        "qualityGateResult": "PASS WITH DOCUMENTED CAVEAT",
                    },
                    "storage": {
                        "plannedFrameSequenceGiB": 10.009,
                        "minimumLaunchFreeGiB": 24,
                    },
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    profile_hash = sha256_file(profile_path)
    scene_hash = sha256_file(scene_path)
    config = MissionControlConfig(
        repository_root=REPOSITORY_ROOT,
        state_root=tmp_path / "state",
        profile_root=profile_root,
        calibration_root=calibration_root,
        default_output_root=output_root,
        allow_fake_renderer=True,
        native_dialog_enabled=False,
    )
    token = (
        f"AUTHORIZE FULL RENDER: TRIP-TO-ANDROMEDA | SPACE-JOURNEY | {PROFILE_ID} | "
        f"SCENE {scene_hash[:12]} | PROFILE {profile_hash[:12]}"
    )
    return MissionFixture(
        config=config,
        profile_path=profile_path,
        scene_path=scene_path,
        profile_sha256=profile_hash,
        scene_sha256=scene_hash,
        authorization_token=token,
    )


@pytest.fixture
def mission_fixture(tmp_path: Path) -> MissionFixture:
    return _build_config(tmp_path)


@pytest.fixture
def mission_config(mission_fixture: MissionFixture) -> MissionControlConfig:
    return mission_fixture.config


@pytest.fixture
def service(mission_config: MissionControlConfig) -> MissionControlService:
    instance = MissionControlService(mission_config)
    yield instance
    instance.close()


def _copied_profile(config: MissionControlConfig) -> Path:
    profiles = [
        path
        for path in (config.profile_root / "trip-to-andromeda").glob("*.json")
        if ".authorization" not in path.name
    ]
    return profiles[0]


def _authorize(service: MissionControlService) -> None:
    service.authorize_profile(
        PROFILE_ID,
        SCENE_ID,
        settings_and_hashes_reviewed=True,
        production_render_authorized=True,
    )


def _assert_valid_png(payload: bytes) -> None:
    assert payload.startswith(b"\x89PNG\r\n\x1a\n")
    offset = 8
    chunk_types: list[bytes] = []
    compressed = bytearray()
    while offset < len(payload):
        length = struct.unpack(">I", payload[offset : offset + 4])[0]
        chunk_type = payload[offset + 4 : offset + 8]
        chunk_data = payload[offset + 8 : offset + 8 + length]
        expected_crc = struct.unpack(
            ">I", payload[offset + 8 + length : offset + 12 + length]
        )[0]
        assert zlib.crc32(chunk_type + chunk_data) & 0xFFFFFFFF == expected_crc
        chunk_types.append(chunk_type)
        if chunk_type == b"IDAT":
            compressed.extend(chunk_data)
        offset += length + 12
    assert offset == len(payload)
    assert chunk_types[0] == b"IHDR"
    assert chunk_types[-1] == b"IEND"
    assert zlib.decompress(compressed)


def test_production_preview_thumbnail_is_bounded_atomic_and_cached(
    service: MissionControlService,
    mission_fixture: MissionFixture,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frames = tmp_path / "thumbnail-output" / "frames"
    frames.mkdir(parents=True)
    source = frames / "frame_000001.png"
    source.write_bytes(renderers_module._FAKE_PREVIEW_PNG)
    now = datetime.now(UTC)
    job = JobRecord(
        id="preview-thumbnail-job",
        renderer=RendererKind.PRODUCTION,
        state=JobState.RUNNING,
        identity=RenderIdentity(
            project_id="trip-to-andromeda",
            scene_id=SCENE_ID,
            scene_sha256=mission_fixture.scene_sha256,
            profile_id=PROFILE_ID,
            profile_sha256=mission_fixture.profile_sha256,
            output_directory=str(frames.parent),
        ),
        created_at=now,
        updated_at=now,
        frame_start=1,
        frame_end=2,
        total_frame_count=2,
        chunks_total=1,
    )
    calls = 0

    def fake_run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        nonlocal calls
        calls += 1
        Path(args[-1]).write_bytes(renderers_module._FAKE_PREVIEW_PNG)
        return subprocess.CompletedProcess(args, 0, b"", b"")

    monkeypatch.setattr(service_module.shutil, "which", lambda _name: "ffmpeg")
    monkeypatch.setattr(service_module.subprocess, "run", fake_run)

    first = service._preview_thumbnail(job, source)
    second = service._preview_thumbnail(job, source)

    assert first == second
    assert first != source
    assert first.parent == service.config.state_root / "preview-thumbnails" / job.id
    assert service._png_dimensions(first) == (1, 1)
    assert calls == 1
    assert not list(first.parent.glob(".*.png"))


async def _wait_for_state(
    service: MissionControlService,
    job_id: str,
    states: set[JobState],
    *,
    timeout: float = 5.0,
) -> JobRecord:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        job = service.get_job(job_id)
        if job.state in states:
            return job
        await asyncio.sleep(0.01)
    raise AssertionError(f"Job {job_id} did not reach {states}")


def _stored_job(
    service: MissionControlService,
    mission_fixture: MissionFixture,
    output: Path,
    *,
    job_id: str,
    renderer: RendererKind = RendererKind.PRODUCTION,
    total_frames: int = 3,
) -> JobRecord:
    output.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    job = JobRecord(
        id=job_id,
        renderer=renderer,
        state=JobState.RUNNING,
        identity=RenderIdentity(
            project_id="trip-to-andromeda",
            scene_id=SCENE_ID,
            scene_sha256=mission_fixture.scene_sha256,
            profile_id=PROFILE_ID,
            profile_sha256=mission_fixture.profile_sha256,
            output_directory=str(output.resolve()),
        ),
        created_at=now,
        updated_at=now,
        renderer_active=True,
        watcher_active=True,
        frame_start=1,
        frame_end=total_frames,
        total_frame_count=total_frames,
        chunks_total=1,
    )
    service.store.put_job(job)
    return job


def _write_render_manifest(
    job: JobRecord,
    authorization_token: str,
    *,
    valid_frames: list[int],
    complete: bool,
    stopped_at: str | None = None,
    updated_at: str = "2026-07-21T10:00:00+00:00",
) -> Path:
    output = Path(job.identity.output_directory)
    manifests = output / "manifests"
    manifests.mkdir(parents=True, exist_ok=True)
    frame_hash = "A" * 64
    frame_index = {
        f"{frame:06d}": {"sha256": frame_hash, "sizeBytes": 1}
        for frame in valid_frames
    }
    digest = hashlib.sha256()
    for frame in sorted(valid_frames):
        digest.update(f"{frame:06d} {frame_hash}\n".encode())
    missing = [
        frame
        for frame in range(job.frame_start, job.frame_end + 1)
        if frame not in valid_frames
    ]
    missing_ranges = (
        [
            {
                "startFrame": missing[0],
                "endFrame": missing[-1],
                "frameCount": len(missing),
            }
        ]
        if missing
        else []
    )
    token_hash = hashlib.sha256(authorization_token.encode()).hexdigest().upper()
    manifest: dict[str, object] = {
        "schemaVersion": "1.0.0",
        "kind": "trackprompt-final-render-manifest",
        "status": "complete" if complete else "incomplete",
        "updatedAt": updated_at,
        "scene": {"sha256": job.identity.scene_sha256},
        "renderProfile": {"sha256": job.identity.profile_sha256},
        "outputDirectory": str(output.resolve()),
        "authorization": {
            "status": "operator-token-accepted",
            "expectedTokenSha256": token_hash,
            "acceptedTokenSha256": token_hash,
        },
        "frameContract": {
            "frameStart": job.frame_start,
            "frameEnd": job.frame_end,
            "frameCount": job.total_frame_count,
            "framesSubdirectory": "frames",
            "filenamePattern": "frame_%06d.png",
        },
        "frameSet": {
            "complete": complete,
            "expectedFrameCount": job.total_frame_count,
            "validFrameCount": len(valid_frames),
            "missingFrameCount": len(missing),
            "missingRanges": missing_ranges,
            "validRanges": [],
            "invalidFrames": [],
            "duplicateFrames": [],
            "unexpectedFrameFiles": [],
            "frameSetSha256": digest.hexdigest().upper(),
        },
        "frameIndex": frame_index,
        "chunks": (
            [
                {
                    "startFrame": valid_frames[0],
                    "endFrame": valid_frames[-1],
                    "frameCount": len(valid_frames),
                    "checkpoint": "checkpoints/chunk.json",
                    "completedAt": updated_at,
                }
            ]
            if valid_frames
            else []
        ),
    }
    if stopped_at is not None:
        manifest["runState"] = {
            "status": "stopped-after-current-chunk-by-operator",
            "stoppedAt": stopped_at,
        }
    path = manifests / "render-manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def test_discovers_synthetic_profile_and_exact_scene_identity(
    service: MissionControlService,
    mission_fixture: MissionFixture,
) -> None:
    profiles = service.profiles()
    assert len(profiles) == 1
    profile = profiles[0]
    assert profile.id == PROFILE_ID
    assert profile.saved_file_sha256 == mission_fixture.profile_sha256
    assert profile.scene_sha256 == mission_fixture.scene_sha256
    assert profile.resolution.width == 1280
    assert profile.resolution.height == 720
    assert profile.frame_start == 1
    assert profile.frame_end == 13029
    assert profile.total_frames == 13029
    assert profile.frames_per_chunk == 600
    assert profile.expected_hours == pytest.approx(5.045)
    assert profile.minimum_launch_free_gib == pytest.approx(24)
    assert profile.recommended is True
    assert profile.authorized is False

    scene = service.scene(SCENE_ID)
    assert scene.sha256 == mission_fixture.scene_sha256
    assert scene.expected_sha256 == mission_fixture.scene_sha256
    assert scene.verified is True


def test_discovers_real_saved_profile_and_exact_scene_identity_when_available(
    tmp_path: Path,
) -> None:
    if not SOURCE_PROFILE.is_file() or not SOURCE_SCENE.is_file():
        pytest.skip("private frozen render fixture is unavailable in this checkout")
    fixture = _build_config(tmp_path, profile_source=SOURCE_PROFILE)
    instance = MissionControlService(fixture.config)
    try:
        profile = instance.profile(PROFILE_ID)
        assert profile.saved_file_sha256 == PROFILE_SHA256
        assert profile.scene_sha256 == SCENE_SHA256
        assert profile.resolution.width == 1280
        assert profile.resolution.height == 720
        assert profile.frame_start == 1
        assert profile.frame_end == 13029
        assert profile.frames_per_chunk == 600
        assert profile.expected_hours == pytest.approx(5.045)
        assert profile.minimum_launch_free_gib == pytest.approx(24)
        scene = instance.scene(SCENE_ID)
        assert scene.sha256 == SCENE_SHA256
        assert scene.expected_sha256 == SCENE_SHA256
        assert scene.verified is True
    finally:
        instance.close()


def test_authorization_requires_both_confirmations_and_preserves_profile_bytes(
    service: MissionControlService,
    mission_fixture: MissionFixture,
) -> None:
    profile_path = _copied_profile(service.config)
    before_hash = sha256_file(profile_path)
    with pytest.raises(MissionControlError) as error:
        service.authorize_profile(
            PROFILE_ID,
            SCENE_ID,
            settings_and_hashes_reviewed=True,
            production_render_authorized=False,
        )
    assert error.value.error.code == "two_confirmations_required"
    request_path = profile_path.with_name(
        f"{profile_path.stem}.authorization-request.json"
    )
    record_path = profile_path.with_name(f"{profile_path.stem}.authorization.json")
    request_payload = load_json_object(request_path, "Authorization request")
    assert "authorizationToken" not in request_payload
    assert request_payload["tokenPreview"] == (
        f"SCENE {mission_fixture.scene_sha256[:12]} | "
        f"PROFILE {mission_fixture.profile_sha256[:12]}"
    )
    assert not record_path.exists()

    result = service.authorize_profile(
        PROFILE_ID,
        SCENE_ID,
        settings_and_hashes_reviewed=True,
        production_render_authorized=True,
    )
    assert result.authorized is True
    assert result.authorization_token == mission_fixture.authorization_token
    assert result.profile_sha256 == mission_fixture.profile_sha256
    assert result.scene_sha256 == mission_fixture.scene_sha256
    assert sha256_file(profile_path) == before_hash == mission_fixture.profile_sha256
    record = load_json_object(record_path, "Authorization record")
    assert record["confirmations"] == {
        "productionRenderAuthorized": True,
        "settingsAndHashesReviewed": True,
    }
    assert service.profile(PROFILE_ID).authorized is True


def test_python_authorization_record_passes_existing_powershell_validator(
    tmp_path: Path,
) -> None:
    if not SOURCE_PROFILE.is_file() or not SOURCE_SCENE.is_file():
        pytest.skip("private frozen render fixture is unavailable in this checkout")
    powershell = shutil.which("powershell.exe")
    if powershell is None:
        pytest.skip("Windows PowerShell is unavailable")
    fixture = _build_config(tmp_path / "real-fixture", profile_source=SOURCE_PROFILE)
    instance = MissionControlService(fixture.config)
    _authorize(instance)
    profile_path = fixture.profile_path
    record_path = profile_path.with_name(f"{profile_path.stem}.authorization.json")
    module_path = (
        REPOSITORY_ROOT / "tools" / "wzhk-launcher" / "WZHK.Profiles.psm1"
    )
    validator = tmp_path / "validate-authorization.ps1"
    validator.write_text(
        """
param([string]$ModulePath, [string]$ProfilePath, [string]$ScenePath, [string]$RecordPath)
$ErrorActionPreference = 'Stop'
Import-Module -Name $ModulePath -Force -DisableNameChecking
$result = Test-WzhkProfileAuthorizationRecord -ProfilePath $ProfilePath -ScenePath $ScenePath -RecordPath $RecordPath
$result | ConvertTo-Json -Compress
if (-not $result.Valid) { exit 7 }
""".strip()
        + "\n",
        encoding="utf-8",
    )
    try:
        completed = subprocess.run(
            [
                powershell,
                "-NoLogo",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(validator),
                "-ModulePath",
                str(module_path),
                "-ProfilePath",
                str(profile_path),
                "-ScenePath",
                str(SOURCE_SCENE),
                "-RecordPath",
                str(record_path),
            ],
            capture_output=True,
            check=False,
            text=True,
            timeout=60,
            shell=False,
        )
        assert completed.returncode == 0, completed.stderr or completed.stdout
        payload = json.loads(completed.stdout.splitlines()[-1])
        assert payload["Valid"] is True
        assert payload["AuthorizationToken"] == AUTHORIZATION_TOKEN
    finally:
        instance.close()


def test_changed_profile_invalidates_authorization(
    service: MissionControlService,
    mission_fixture: MissionFixture,
) -> None:
    _authorize(service)
    profile_path = _copied_profile(service.config)
    with profile_path.open("ab") as stream:
        stream.write(b" \n")
    profile = service.profile(PROFILE_ID)
    assert profile.saved_file_sha256 != mission_fixture.profile_sha256
    assert profile.authorized is False
    assert any("profile hash" in issue.lower() for issue in profile.authorization_issues)


@pytest.mark.asyncio
async def test_authorization_race_never_persists_a_dangling_starting_job(
    service: MissionControlService,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _authorize(service)
    profile_path = _copied_profile(service.config)
    record_path = profile_path.with_name(f"{profile_path.stem}.authorization.json")
    original_preflight = service.preflight

    async def invalidate_after_preflight(
        request: PreflightRequest,
        *,
        run_engine: bool = True,
    ) -> object:
        result = await original_preflight(request, run_engine=run_engine)
        record = load_json_object(record_path, "Authorization record")
        record["scene"]["sha256"] = "0" * 64
        record_path.write_text(json.dumps(record), encoding="utf-8")
        return result

    monkeypatch.setattr(service, "preflight", invalidate_after_preflight)
    output = tmp_path / "authorization-race" / "render"
    output.mkdir(parents=True)
    with pytest.raises(MissionControlError) as error:
        await service.start_render(
            StartFakeRenderRequest(
                project_id="trip-to-andromeda",
                scene_id=SCENE_ID,
                profile_id=PROFILE_ID,
                output_directory=str(output),
                renderer=RendererKind.FAKE,
            )
        )
    assert error.value.error.code == "authorization_invalidated"
    assert service.jobs() == []


def test_output_classification_and_unique_child_creation(
    service: MissionControlService,
    tmp_path: Path,
) -> None:
    empty = tmp_path / "output-parent" / "empty"
    empty.mkdir(parents=True)
    inspection = service.inspect_output(
        str(empty),
        profile_id=PROFILE_ID,
        scene_id=SCENE_ID,
    )
    assert inspection.classification == OutputClassification.EMPTY_DIRECTORY
    assert inspection.usable is True

    conflict = tmp_path / "output-parent" / "conflict"
    conflict.mkdir()
    (conflict / "notes.txt").write_text("unrelated", encoding="utf-8")
    (conflict / ".hidden-cache").mkdir()
    rejected = service.inspect_output(
        str(conflict),
        profile_id=PROFILE_ID,
        scene_id=SCENE_ID,
    )
    assert rejected.classification == OutputClassification.CONTAINS_HIDDEN_SYSTEM_ENTRIES
    assert rejected.usable is False
    assert {item.name for item in rejected.entries} == {"notes.txt", ".hidden-cache"}
    assert any("notes.txt" in entry for entry in rejected.conflicting_entries)
    assert any(".hidden-cache" in entry for entry in rejected.conflicting_entries)

    first = service.create_output_child(
        str(conflict),
        project_id="trip-to-andromeda",
        profile_id=PROFILE_ID,
        base_name="Trip / To ; Andromeda",
    )
    second = service.create_output_child(
        str(conflict),
        project_id="trip-to-andromeda",
        profile_id=PROFILE_ID,
        base_name="Trip / To ; Andromeda",
    )
    assert Path(first.path).is_dir()
    assert Path(second.path).is_dir()
    assert first.path != second.path
    assert ";" not in Path(first.path).name
    assert first.inspection.classification == OutputClassification.EMPTY_DIRECTORY


def test_output_manifest_requires_exact_saved_profile_and_frame_contract(
    service: MissionControlService,
    mission_fixture: MissionFixture,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output = tmp_path / "production" / "matching"
    manifests = output / "manifests"
    manifests.mkdir(parents=True)
    manifest_path = manifests / "render-manifest.json"
    manifest = {
        "kind": "trackprompt-final-render-manifest",
        "projectId": "trip-to-andromeda",
        "sceneId": SCENE_ID,
        "scene": {"sha256": mission_fixture.scene_sha256},
        "renderProfile": {
            "profileId": PROFILE_ID,
            "sha256": mission_fixture.profile_sha256,
        },
        "outputDirectory": str(output.resolve()),
        "frameContract": {
            "frameStart": 1,
            "frameEnd": 13029,
            "frameCount": 13029,
            "fps": 30.0,
            "width": 1280,
            "height": 720,
            "filenamePattern": "frame_%06d.png",
            "format": "PNG",
            "bitDepth": 8,
            "colorMode": "RGB",
        },
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    compatible = service.inspect_output(
        str(output),
        profile_id=PROFILE_ID,
        scene_id=SCENE_ID,
    )
    assert compatible.classification == OutputClassification.COMPATIBLE_RESUMABLE
    assert compatible.usable is True

    manifest["frameContract"]["fps"] = 29.97
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    wrong_fps = service.inspect_output(
        str(output),
        profile_id=PROFILE_ID,
        scene_id=SCENE_ID,
    )
    assert wrong_fps.classification == OutputClassification.INCOMPATIBLE_RENDER
    assert any("fps" in issue for issue in wrong_fps.issues)

    manifest["frameContract"]["fps"] = 30.0

    manifest["renderProfile"]["sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    incompatible = service.inspect_output(
        str(output),
        profile_id=PROFILE_ID,
        scene_id=SCENE_ID,
    )
    assert incompatible.classification == OutputClassification.INCOMPATIBLE_RENDER
    assert incompatible.usable is False
    assert any("saved profile" in issue for issue in incompatible.issues)

    manifest["renderProfile"]["sha256"] = mission_fixture.profile_sha256
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    (output / "frames").mkdir()
    original_entry = outputs_module._entry

    def linked_frames(path: Path) -> OutputEntry:
        entry = original_entry(path)
        return entry.model_copy(
            update={"reparse_point": path.name.casefold() == "frames"}
        )

    monkeypatch.setattr(outputs_module, "_entry", linked_frames)
    linked = service.inspect_output(
        str(output),
        profile_id=PROFILE_ID,
        scene_id=SCENE_ID,
    )
    assert linked.classification == OutputClassification.CONTAINS_HIDDEN_SYSTEM_ENTRIES
    assert linked.usable is False
    assert any("reparse-point" in item for item in linked.conflicting_entries)


@pytest.mark.asyncio
async def test_fake_render_stop_resume_persistence_and_event_replay(
    service: MissionControlService,
    mission_fixture: MissionFixture,
    tmp_path: Path,
) -> None:
    _authorize(service)
    output = tmp_path / "fake" / "render"
    output.mkdir(parents=True)
    request = StartFakeRenderRequest(
        project_id="trip-to-andromeda",
        scene_id=SCENE_ID,
        profile_id=PROFILE_ID,
        output_directory=str(output),
        renderer=RendererKind.FAKE,
        fake=FakeRenderOptions(
            total_frames=30,
            frames_per_chunk=5,
            step_delay_seconds=0.05,
            long_frame_at=2,
            storage_warning_at_frame=2,
        ),
    )
    started = await service.start_render(request, fake_options=request.fake)
    duplicate = await service.start_render(request, fake_options=request.fake)
    assert duplicate.id == started.id
    await _wait_for_state(service, started.id, {JobState.RUNNING})
    requested = await service.request_stop_after_chunk(started.id)
    assert requested.state == JobState.STOP_REQUESTED
    paused = await _wait_for_state(service, started.id, {JobState.PAUSED_SAFELY})
    assert paused.published_frame_count in {5, 10, 15, 20, 25, 30}
    assert paused.inflight_frame_count == 0
    assert paused.safe_stop_status.value == "paused"

    events = service.events_after(0, job_id=started.id)
    sequences = [event.sequence for event in events]
    assert sequences == sorted(sequences)
    assert len(sequences) == len(set(sequences))
    assert any(event.latest_log_line == "Rendering is still active" for event in events)
    assert any(event.warning for event in events)
    replay = service.events_after(sequences[-2], job_id=started.id)
    assert replay[0].sequence == sequences[-1]

    resumed = await service.resume(
        started.id,
        ResumeRequest(
            scene_sha256=mission_fixture.scene_sha256,
            profile_sha256=mission_fixture.profile_sha256,
        ),
    )
    assert resumed.state == JobState.STARTING
    complete = await _wait_for_state(service, started.id, {JobState.COMPLETE})
    assert complete.published_frame_count == 30
    assert complete.validated_frame_count == 30
    persisted_sequence = service.store.latest_event_sequence()

    service.close()
    restarted = MissionControlService(service.config)
    try:
        persisted = restarted.get_job(started.id)
        assert persisted.state == JobState.COMPLETE
        assert restarted.store.latest_event_sequence() == persisted_sequence
        assert restarted.events_after(0, job_id=started.id)[-1].state == JobState.COMPLETE
    finally:
        restarted.close()


@pytest.mark.asyncio
async def test_fake_renderer_failure_is_persisted(
    service: MissionControlService,
    tmp_path: Path,
) -> None:
    _authorize(service)
    output = tmp_path / "fake-failure" / "render"
    output.mkdir(parents=True)
    request = StartFakeRenderRequest(
        project_id="trip-to-andromeda",
        scene_id=SCENE_ID,
        profile_id=PROFILE_ID,
        output_directory=str(output),
        renderer=RendererKind.FAKE,
        fake=FakeRenderOptions(total_frames=4, frames_per_chunk=2, fail_at_frame=2),
    )
    job = await service.start_render(request, fake_options=request.fake)
    failed = await _wait_for_state(service, job.id, {JobState.FAILED})
    assert failed.error is not None
    assert failed.error.code == "fake_renderer_failure"
    assert service.logs(job.id, after_sequence=0, limit=100).items[-1].level == "error"


@pytest.mark.asyncio
async def test_unexpected_fake_renderer_task_failure_is_persisted(
    service: MissionControlService,
    mission_fixture: MissionFixture,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _authorize(service)

    def fail_preview(_output_directory: str, _frame: int) -> Path:
        raise OSError("synthetic preview publication failure")

    monkeypatch.setattr(renderers_module, "_publish_fake_preview", fail_preview)
    output = tmp_path / "fake-task-failure" / "render"
    output.mkdir(parents=True)
    request = StartFakeRenderRequest(
        project_id="trip-to-andromeda",
        scene_id=SCENE_ID,
        profile_id=PROFILE_ID,
        output_directory=str(output),
        renderer=RendererKind.FAKE,
        fake=FakeRenderOptions(total_frames=2, frames_per_chunk=2),
    )
    job = await service.start_render(request, fake_options=request.fake)
    failed = await _wait_for_state(service, job.id, {JobState.FAILED})
    assert failed.error is not None
    assert failed.error.code == "fake_renderer_task_failed"
    assert failed.renderer_active is False
    assert failed.watcher_active is False


@pytest.mark.asyncio
async def test_production_exit_zero_never_fabricates_completion(
    service: MissionControlService,
    mission_fixture: MissionFixture,
    tmp_path: Path,
) -> None:
    job = _stored_job(
        service,
        mission_fixture,
        tmp_path / "incomplete-exit" / "render",
        job_id="incomplete-exit-job",
    )
    _write_render_manifest(
        job,
        mission_fixture.authorization_token,
        valid_frames=[1, 2],
        complete=False,
    )
    await service.production_renderer._reconcile_exit(service, job.id, 0)
    failed = service.get_job(job.id)
    assert failed.state == JobState.FAILED
    assert failed.published_frame_count == 2
    assert failed.validated_frame_count == 2
    assert failed.error is not None
    assert failed.error.code == "render_incomplete_after_exit"
    assert failed.renderer_active is False
    assert failed.watcher_active is False

    paused_job = _stored_job(
        service,
        mission_fixture,
        tmp_path / "current-stop" / "render",
        job_id="current-stop-job",
    )
    stopped_at = "2026-07-21T10:00:00+00:00"
    _write_render_manifest(
        paused_job,
        mission_fixture.authorization_token,
        valid_frames=[1, 2],
        complete=False,
        stopped_at=stopped_at,
        updated_at=stopped_at,
    )
    await service.production_renderer._reconcile_exit(service, paused_job.id, 0)
    paused = service.get_job(paused_job.id)
    assert paused.state == JobState.PAUSED_SAFELY
    assert paused.published_frame_count == 2


@pytest.mark.asyncio
async def test_complete_manifest_wins_over_stale_stop_and_reports_chunk_output(
    service: MissionControlService,
    mission_fixture: MissionFixture,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    job = _stored_job(
        service,
        mission_fixture,
        tmp_path / "complete-exit" / "render",
        job_id="complete-exit-job",
    )
    _write_render_manifest(
        job,
        mission_fixture.authorization_token,
        valid_frames=[1, 2, 3],
        complete=True,
        stopped_at="2026-07-21T09:59:00+00:00",
    )

    class CompletedProcess:
        def __init__(self) -> None:
            self.pid = os.getpid()
            self.returncode: int | None = None
            self.stdout = asyncio.StreamReader()
            self.stdout.feed_data(
                b"Rendering chunk 1/1: frames 1-3 (3 frames)\n"
                b"Published frames 1-3; 3 valid of 3.\n"
            )
            self.stdout.feed_eof()

        async def wait(self) -> int:
            self.returncode = 0
            return 0

        def kill(self) -> None:
            self.returncode = -9

    process = CompletedProcess()

    async def create_process(*_args: object, **_kwargs: object) -> CompletedProcess:
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)
    await service.production_renderer._run(service, job.id, ["synthetic-renderer"])
    complete = service.get_job(job.id)
    assert complete.state == JobState.COMPLETE
    assert complete.published_frame_count == 3
    assert complete.safe_stop_status.value == "none"
    assert complete.renderer_active is False
    assert complete.watcher_active is False
    events = service.events_after(0, job_id=job.id)
    assert any(event.renderer_active is True for event in events)
    assert any(event.watcher_active is True for event in events)
    assert any(event.last_output_at is not None for event in events)
    assert any(
        event.phase is not None
        and event.phase.value == "PUBLISH_CHUNK"
        and event.published_frame_count == 3
        for event in events
    )
    assert service.encode_readiness(job.id).frame_sequence_complete is True

    nonzero_job = _stored_job(
        service,
        mission_fixture,
        tmp_path / "complete-manifest-nonzero-exit" / "render",
        job_id="complete-manifest-nonzero-exit-job",
    )
    _write_render_manifest(
        nonzero_job,
        mission_fixture.authorization_token,
        valid_frames=[1, 2, 3],
        complete=True,
    )
    await service.production_renderer._reconcile_exit(service, nonzero_job.id, 7)
    nonzero = service.get_job(nonzero_job.id)
    assert nonzero.state == JobState.FAILED
    assert nonzero.error is not None
    assert nonzero.error.code == "renderer_process_failed"
    assert service.encode_readiness(nonzero_job.id).frame_sequence_complete is False


def test_encode_status_defaults_to_idle_and_persists(service: MissionControlService, mission_fixture: MissionFixture, tmp_path: Path) -> None:
    job = _stored_job(
        service,
        mission_fixture,
        tmp_path / "encode-status" / "render",
        job_id="encode-status-job",
    )

    idle = service.encode_status(job.id)
    assert idle.status == "idle"
    assert idle.total_frames == 3
    assert idle.output_kinds == []

    queued = idle.model_copy(
        update={
            "status": "queued",
            "output_kinds": ["delivery", "master"],
            "detail": "Queued for local encoding.",
        }
    )
    service._put_encode_status(queued)
    assert service.encode_status(job.id) == queued


@pytest.mark.asyncio
async def test_encode_start_requires_explicit_operator_confirmation(
    service: MissionControlService,
    mission_fixture: MissionFixture,
    tmp_path: Path,
) -> None:
    job = _stored_job(
        service,
        mission_fixture,
        tmp_path / "encode-confirmation" / "render",
        job_id="encode-confirmation-job",
    )

    with pytest.raises(MissionControlError) as error:
        await service.start_encode(job.id, EncodeStartRequest())

    assert error.value.error.code == "encode_confirmation_required"


@pytest.mark.asyncio
async def test_encode_start_queues_delivery_then_master_with_managed_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _build_config(tmp_path, profile_source=SOURCE_PROFILE)
    service = MissionControlService(fixture.config)
    try:
        job = _stored_job(
            service,
            fixture,
            tmp_path / "encode-sequence" / "render",
            job_id="encode-sequence-job",
        )
        service.store.put_job(
            job.model_copy(
                update={
                    "state": JobState.COMPLETE,
                    "published_frame_count": 3,
                    "renderer_active": False,
                    "watcher_active": False,
                }
            )
        )
        monkeypatch.setattr(
            service,
            "encode_readiness",
            lambda job_id: EncodeReadiness(
                job_id=job_id,
                ready=True,
                frame_sequence_complete=True,
                published_frames=3,
                total_frames=3,
                ffmpeg_available=True,
                detail="Ready.",
            ),
        )
        observed = []

        async def capture(status: object) -> None:
            observed.append(status)

        monkeypatch.setattr(service, "_run_encode_sequence", capture)
        queued = await service.start_encode(
            job.id,
            EncodeStartRequest(operator_confirmed=True),
        )
        await asyncio.sleep(0)

        assert queued.status == "queued"
        assert queued.output_kinds == ["delivery", "master"]
        assert queued.output_paths["delivery"].endswith(
            "delivery\\trip-to-andromeda-720p-delivery.mp4"
        )
        assert queued.output_paths["master"].endswith(
            "master\\trip-to-andromeda-720p-master.mov"
        )
        assert observed == [queued]
    finally:
        service.close()


@pytest.mark.asyncio
async def test_event_wait_generation_closes_pre_wait_wakeup_window(
    service: MissionControlService,
    mission_fixture: MissionFixture,
    tmp_path: Path,
) -> None:
    job = _stored_job(
        service,
        mission_fixture,
        tmp_path / "event-generation" / "render",
        job_id="event-generation-job",
        renderer=RendererKind.FAKE,
    )
    observed = service.event_generation
    await service.update_job(job.id, warning="event arrived before wait")
    await asyncio.wait_for(
        service.wait_for_events(observed, timeout_seconds=5.0),
        timeout=0.2,
    )


def test_fake_preview_fallback_checks_only_a_bounded_direct_window(
    service: MissionControlService,
    mission_fixture: MissionFixture,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    job = _stored_job(
        service,
        mission_fixture,
        tmp_path / "bounded-preview" / "render",
        job_id="bounded-preview-job",
        renderer=RendererKind.FAKE,
        total_frames=1_000,
    )
    service.store.put_job(job.model_copy(update={"current_frame": 1_000}))
    (Path(job.identity.output_directory) / "frames").mkdir()
    inspected: list[Path] = []

    def reject_png(path: Path) -> bool:
        inspected.append(path)
        return False

    monkeypatch.setattr(service, "_valid_png", reject_png)
    assert service.preview_path(job.id) is None
    assert len(inspected) == 256
    assert inspected[0].name == "frame_001000.png"
    assert inspected[-1].name == "frame_000745.png"


@pytest.mark.asyncio
async def test_fake_preview_is_atomic_valid_versioned_and_never_cached(
    service: MissionControlService,
    tmp_path: Path,
) -> None:
    _authorize(service)
    output = tmp_path / "fake-preview" / "render"
    output.mkdir(parents=True)
    request = StartFakeRenderRequest(
        project_id="trip-to-andromeda",
        scene_id=SCENE_ID,
        profile_id=PROFILE_ID,
        output_directory=str(output),
        renderer=RendererKind.FAKE,
        fake=FakeRenderOptions(total_frames=4, frames_per_chunk=2),
    )
    job = await service.start_render(request, fake_options=request.fake)
    complete = await _wait_for_state(service, job.id, {JobState.COMPLETE})
    assert complete.latest_preview_frame == 4
    assert complete.latest_frame_preview == (
        f"/api/mission-control/render/{job.id}/preview?v=4"
    )
    frames = sorted((output / "frames").iterdir())
    assert [path.name for path in frames] == ["frame_000002.png", "frame_000004.png"]
    assert not list((output / "frames").glob("*.tmp"))
    for path in frames:
        payload = path.read_bytes()
        _assert_valid_png(payload)

    application = FastAPI()
    application.state.mission_control_service = service
    install_mission_control(application)
    with TestClient(application) as client:
        latest = client.get(complete.latest_frame_preview)
        assert latest.status_code == 200
        assert latest.headers["content-type"] == "image/png"
        assert latest.headers["cache-control"] == "no-store, no-cache, must-revalidate"
        assert latest.headers["pragma"] == "no-cache"
        assert latest.headers["x-content-type-options"] == "nosniff"
        assert latest.headers["x-trackprompt-preview-frame"] == "4"
        assert latest.content == frames[-1].read_bytes()

        earlier = client.get(f"/api/mission-control/render/{job.id}/preview?v=2")
        assert earlier.status_code == 200
        assert earlier.headers["x-trackprompt-preview-frame"] == "2"
        missing = client.get(f"/api/mission-control/render/{job.id}/preview?v=3")
        assert missing.status_code == 204
        assert missing.headers["cache-control"] == "no-store"

    final_event = service.events_after(0, job_id=job.id)[-1]
    assert final_event.latest_preview_frame == 4
    assert final_event.latest_frame_preview == complete.latest_frame_preview


def test_restart_reclassifies_dead_running_process_as_resumable(
    mission_config: MissionControlConfig,
    mission_fixture: MissionFixture,
) -> None:
    first = MissionControlService(mission_config)
    now = datetime.now(UTC)
    job = JobRecord(
        id="restart-job",
        renderer=RendererKind.PRODUCTION,
        state=JobState.RUNNING,
        identity=RenderIdentity(
            project_id="trip-to-andromeda",
            scene_id=SCENE_ID,
            scene_sha256=mission_fixture.scene_sha256,
            profile_id=PROFILE_ID,
            profile_sha256=mission_fixture.profile_sha256,
            output_directory=str(mission_config.default_output_root / "restart-job"),
        ),
        created_at=now,
        updated_at=now,
        process_id=2_147_483_647,
        frame_start=1,
        frame_end=9,
        total_frame_count=9,
        chunks_total=3,
    )
    first.store.put_job(job)
    first.close()
    restarted = MissionControlService(mission_config)
    try:
        recovered = restarted.get_job(job.id)
        assert recovered.state == JobState.RESUMABLE
        assert recovered.process_id is None
        assert recovered.warning is not None
        assert restarted.events_after(0, job_id=job.id)[-1].state == JobState.RESUMABLE
    finally:
        restarted.close()


@pytest.mark.asyncio
async def test_restart_monitor_reconciles_a_live_orphan_after_it_exits(
    mission_config: MissionControlConfig,
    mission_fixture: MissionFixture,
) -> None:
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        shell=False,
    )
    now = datetime.now(UTC)
    job = JobRecord(
        id="live-orphan-job",
        renderer=RendererKind.PRODUCTION,
        state=JobState.RUNNING,
        identity=RenderIdentity(
            project_id="trip-to-andromeda",
            scene_id=SCENE_ID,
            scene_sha256=mission_fixture.scene_sha256,
            profile_id=PROFILE_ID,
            profile_sha256=mission_fixture.profile_sha256,
            output_directory=str(mission_config.default_output_root / "live-orphan"),
        ),
        created_at=now,
        updated_at=now,
        process_id=child.pid,
        frame_start=1,
        frame_end=9,
        total_frame_count=9,
        chunks_total=3,
    )
    store = MissionControlStore(mission_config.database_path)
    store.put_job(job)
    store.close()
    restarted = MissionControlService(mission_config)
    try:
        recovered = restarted.get_job(job.id)
        assert recovered.state == JobState.RUNNING
        assert recovered.orphaned is True
        assert child.poll() is None
        child.terminate()
        child.wait(timeout=10)
        resumable = await _wait_for_state(
            restarted,
            job.id,
            {JobState.RESUMABLE},
            timeout=5,
        )
        assert resumable.orphaned is False
        assert resumable.process_id is None
        assert restarted.events_after(0, job_id=job.id)[-1].state == JobState.RESUMABLE
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=10)
        restarted.close()


def test_fixed_renderer_argv_preserves_untrusted_path_as_one_argument(
    service: MissionControlService,
    mission_fixture: MissionFixture,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        service.production_renderer,
        "powershell_path",
        lambda: r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
    )
    untrusted = tmp_path / "render; Write-Output INJECTED"
    command = service.production_renderer.command(
        scene_path=mission_fixture.scene_path,
        profile_path=_copied_profile(service.config),
        output_directory=untrusted,
        authorization_token=mission_fixture.authorization_token,
    )
    output_index = command.index("-OutputDirectory") + 1
    assert command[output_index] == str(untrusted)
    assert command.count(str(untrusted)) == 1
    assert "-Command" not in command
    assert command[-2:] == [
        "-AuthorizationToken",
        mission_fixture.authorization_token,
    ]


def test_api_contract_accepts_output_path_alias_and_returns_structured_errors(
    service: MissionControlService,
    mission_fixture: MissionFixture,
    tmp_path: Path,
) -> None:
    application = FastAPI()
    application.state.mission_control_service = service
    install_mission_control(application)

    @application.get("/outside-mission-control")
    async def outside_mission_control(required: int) -> dict[str, int]:
        return {"required": required}

    output = tmp_path / "api" / "render"
    output.mkdir(parents=True)
    with TestClient(application) as client:
        profiles = client.get("/api/mission-control/profiles")
        assert profiles.status_code == 200
        assert profiles.json()[0]["savedFileSha256"] == mission_fixture.profile_sha256
        preflight = client.post(
            "/api/mission-control/render/preflight",
            json={
                "projectId": "trip-to-andromeda",
                "sceneId": SCENE_ID,
                "profileId": PROFILE_ID,
                "outputPath": str(output),
                "renderer": "fake",
            },
        )
        assert preflight.status_code == 200
        assert preflight.json()["authorizationRequired"] is True
        assert preflight.json()["identity"]["outputDirectory"] == str(output.resolve())

        denied = client.post(
            f"/api/mission-control/profiles/{PROFILE_ID}/authorize",
            json={
                "sceneId": SCENE_ID,
                "settingsAndHashesReviewed": True,
                "productionRenderAuthorized": False,
            },
        )
        assert denied.status_code == 422
        assert denied.json()["error"]["code"] == "two_confirmations_required"
        assert denied.json()["error"]["recommendedAction"]

        malformed = client.post(
            "/api/mission-control/output/inspect",
            json={},
        )
        assert malformed.status_code == 422
        assert "detail" not in malformed.json()
        assert malformed.json()["error"]["code"] == "request_validation_failed"
        assert malformed.json()["error"]["context"]["errorCount"] >= 1

        outside = client.get("/outside-mission-control")
        assert outside.status_code == 422
        assert "detail" in outside.json()
        assert "error" not in outside.json()


def test_render_start_contract_defaults_to_production_and_discriminates_fake() -> None:
    adapter = TypeAdapter(StartRenderRequest | StartFakeRenderRequest)
    common = {
        "projectId": "trip-to-andromeda",
        "sceneId": SCENE_ID,
        "profileId": PROFILE_ID,
        "outputDirectory": r"C:\render-output\mission-control",
    }
    defaulted = adapter.validate_python(common)
    assert type(defaulted) is StartRenderRequest
    assert defaulted.renderer == RendererKind.PRODUCTION

    fake = adapter.validate_python(
        {**common, "renderer": "fake", "fake": {"totalFrames": 4}}
    )
    assert type(fake) is StartFakeRenderRequest
    assert fake.renderer == RendererKind.FAKE
    assert fake.fake.total_frames == 4

    with pytest.raises(ValidationError):
        adapter.validate_python(
            {**common, "renderer": "production", "fake": {"totalFrames": 4}}
        )


def test_native_picker_test_override_never_opens_gui(
    service: MissionControlService,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    selected = tmp_path / "picker" / "folder"
    selected.mkdir(parents=True)
    monkeypatch.setenv("TRACKPROMPT_MC_PICKER_RESULT", str(selected))
    response = asyncio.run(
        service.select_folder(
            __import__(
                "app.mission_control.models",
                fromlist=["NativePickerRequest"],
            ).NativePickerRequest()
        )
    )
    assert response.cancelled is False
    assert response.path == str(selected.resolve())


def test_performance_status_accepts_bridge_vram_acronym() -> None:
    status = PerformanceStatus.model_validate(
        {
            "available": True,
            "active": False,
            "restoreRequired": False,
            "vramUsedMiB": 3607,
            "detail": "Exclusive Performance Mode is not active.",
        }
    )
    assert status.vram_used_mib == 3607
    serialized = status.model_dump(mode="json", by_alias=True)
    assert serialized["vramUsedMib"] == 3607
    assert "vramUsedMiB" not in serialized


@pytest.mark.asyncio
async def test_settings_accepts_live_performance_bridge_payload(
    service: MissionControlService,
) -> None:
    settings = await service.settings()
    assert isinstance(settings.performance_mode_available, bool)
    assert settings.performance_mode_detail


@pytest.mark.asyncio
async def test_unbound_performance_session_is_explicitly_manual_restore(
    service: MissionControlService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def unbound_status() -> PerformanceStatus:
        return PerformanceStatus(
            available=True,
            active=True,
            restore_required=True,
            blender_process_id=None,
            detail="Exclusive Performance Mode is active.",
        )

    monkeypatch.setattr(service.performance, "status", unbound_status)
    status = await service.performance_status()
    settings = await service.settings()
    assert "manual restore" in status.detail.casefold()
    assert "manual restore" in settings.performance_mode_detail.casefold()


@pytest.mark.asyncio
async def test_active_job_performance_resolves_blender_descendant_internally(
    service: MissionControlService,
    mission_fixture: MissionFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)
    job = JobRecord(
        id="performance-job",
        renderer=RendererKind.PRODUCTION,
        state=JobState.RUNNING,
        identity=RenderIdentity(
            project_id="trip-to-andromeda",
            scene_id=SCENE_ID,
            scene_sha256=mission_fixture.scene_sha256,
            profile_id=PROFILE_ID,
            profile_sha256=mission_fixture.profile_sha256,
            output_directory=str(service.config.default_output_root / "performance-job"),
        ),
        created_at=now,
        updated_at=now,
        process_id=1234,
        frame_start=1,
        frame_end=9,
        total_frame_count=9,
        chunks_total=1,
    )
    service.store.put_job(job)
    monkeypatch.setattr(
        "app.mission_control.service.find_descendant_process_id",
        lambda supervisor, names: 5678 if supervisor == 1234 and "blender.exe" in names else None,
    )
    captured: dict[str, object] = {}

    async def enable(**kwargs: object) -> PerformanceStatus:
        captured.update(kwargs)
        return PerformanceStatus(
            available=True,
            active=True,
            restore_required=True,
            blender_process_id=5678,
            detail="active",
        )

    monkeypatch.setattr(service.performance, "enable", enable)
    result = await service.performance_enable(
        PerformanceEnableRequest(operator_confirmed=True, job_id=job.id)
    )
    assert result.blender_process_id == 5678
    assert captured["blender_process_id"] == 5678


@pytest.mark.asyncio
async def test_performance_enable_and_restore_are_serialized(
    service: MissionControlService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active = 0
    peak = 0

    async def operation(**_kwargs: object) -> PerformanceStatus:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.05)
        active -= 1
        return PerformanceStatus(
            available=True,
            active=False,
            restore_required=False,
            detail="serialized",
        )

    monkeypatch.setattr(service.performance, "_enable_locked", operation)
    monkeypatch.setattr(service.performance, "_restore_locked", operation)
    await asyncio.gather(
        service.performance.enable(
            operator_confirmed=True,
            use_high_performance_power_plan=True,
            blender_process_id=0,
        ),
        service.performance.restore(operator_confirmed=True),
    )
    assert peak == 1


def test_performance_daemon_auto_restores_when_bound_process_is_not_blender(
    tmp_path: Path,
) -> None:
    powershell = shutil.which("powershell.exe")
    if powershell is None:
        pytest.skip("Windows PowerShell is unavailable")
    module = tmp_path / "Fake.Performance.psm1"
    state = tmp_path / "performance-state.json"
    control = tmp_path / "performance-control.json"
    module.write_text(
        """
function Start-WzhkExclusivePerformanceMode {
    param([string]$StatePath, [switch]$OperatorConfirmed, [int]$BlenderProcessId, [switch]$UseHighPerformancePowerPlan)
    Set-Content -LiteralPath $StatePath -Value '{"restoreRequired":true}' -Encoding UTF8
}
function Stop-WzhkExclusivePerformanceMode {
    param([string]$StatePath)
    Set-Content -LiteralPath ($StatePath + '.restored') -Value 'restored' -Encoding UTF8
}
Export-ModuleMember -Function Start-WzhkExclusivePerformanceMode, Stop-WzhkExclusivePerformanceMode
""".strip()
        + "\n",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            powershell,
            "-NoLogo",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(
                REPOSITORY_ROOT
                / "backend"
                / "app"
                / "mission_control"
                / "performance_daemon.ps1"
            ),
            "-ModulePath",
            str(module),
            "-StatePath",
            str(state),
            "-ControlPath",
            str(control),
            "-BlenderProcessId",
            str(os.getpid()),
        ],
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
        shell=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    ready = json.loads(completed.stdout.splitlines()[0])
    assert ready["ready"] is True
    assert ready["blenderProcessId"] == os.getpid()
    assert Path(f"{state}.restored").read_text(encoding="utf-8-sig").strip() == "restored"


def test_latest_completed_calibration_precedes_newer_incomplete_plan(
    mission_config: MissionControlConfig,
) -> None:
    complete = mission_config.calibration_root / "machine" / "scene" / "complete"
    planned = mission_config.calibration_root / "machine" / "scene" / "planned"
    complete.mkdir(parents=True)
    planned.mkdir(parents=True)
    (complete / "calibration.json").write_text(
        json.dumps(
            {
                "kind": "trackprompt-render-calibration",
                "calibrationId": "complete-calibration",
                "status": "complete",
                "createdAt": "2026-07-20T10:00:00Z",
                "completedAt": "2026-07-20T12:00:00Z",
                "machine": {},
            }
        ),
        encoding="utf-8",
    )
    (planned / "calibration.json").write_text(
        json.dumps(
            {
                "kind": "trackprompt-render-calibration",
                "calibrationId": "newer-planned-calibration",
                "status": "planned",
                "createdAt": "2026-07-21T12:00:00Z",
                "machine": {},
            }
        ),
        encoding="utf-8",
    )
    instance = MissionControlService(mission_config)
    try:
        calibrations = instance.calibrations()
        assert [item.id for item in calibrations] == [
            "complete-calibration",
            "newer-planned-calibration",
        ]
    finally:
        instance.close()
