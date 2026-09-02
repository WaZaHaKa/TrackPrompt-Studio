from __future__ import annotations

import json
import struct
import zlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

import app.mission_control.service as service_module
from app.mission_control.config import MissionControlConfig
from app.mission_control.discovery import sha256_file
from app.mission_control.errors import MissionControlError
from app.mission_control.models import (
    FakeRenderOptions,
    JobRecord,
    JobState,
    ProfileSummary,
    RendererKind,
    RenderIdentity,
    Resolution,
    StartFakeRenderRequest,
)
from app.mission_control.render_contracts import (
    CompositionProfile,
    OutputVariant,
    OutputVariantProgress,
    RenderStage,
)
from app.mission_control.service import MissionControlService

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def _png(width: int, height: int) -> bytes:
    scanlines = b"".join(
        b"\x00" + b"\x10\x20\x30" * width for _ in range(height)
    )
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(scanlines))
        + _chunk(b"IEND", b"")
    )


def _digest(character: str) -> str:
    return character * 64


def test_checked_in_final_singular_profile_activates_horizontal_v2_contract() -> None:
    profile_path = (
        REPOSITORY_ROOT
        / "render-profiles"
        / "trip-to-andromeda"
        / "andromeda-v2-horizontal-1080p-final.json"
    )
    payload = json.loads(profile_path.read_text(encoding="utf-8"))
    profile = ProfileSummary(
        id=payload["profileId"],
        project_id=payload["project"],
        scene_id=payload["preset"],
        display_name=payload["displayName"],
        path=str(profile_path),
        saved_file_sha256=sha256_file(profile_path),
        scene_sha256=payload["approvedSceneSha256"].upper(),
        resolution=Resolution(
            width=payload["resolution"]["width"],
            height=payload["resolution"]["height"],
            label="1920x1080",
        ),
        fps=float(payload["fps"]),
        frame_start=payload["frameStart"],
        frame_end=payload["frameEnd"],
        total_frames=payload["frameEnd"] - payload["frameStart"] + 1,
        frames_per_chunk=payload["chunking"]["framesPerChunk"],
        quality_role="release",
        calibrated=True,
        authorization_status="technical-ready",
        authorized=False,
    )
    service = object.__new__(MissionControlService)

    definitions = service._output_variant_definitions(profile, payload)

    assert len(definitions) == 1
    assert definitions[0].id == "horizontal-16x9-1080p"
    assert definitions[0].enabled is True
    assert definitions[0].required is True
    assert (definitions[0].width, definitions[0].height) == (1920, 1080)
    assert (
        definitions[0].composition_profile.id
        == "andromeda-v2-horizontal-master-v1"
    )


def _runtime_variant(
    variant_id: str,
    *,
    enabled: bool,
    required: bool,
    frames_root: str,
) -> OutputVariant:
    now = datetime.now(UTC)
    return OutputVariant(
        id=variant_id,
        enabled=enabled,
        required=required,
        width=16,
        height=16 if variant_id == "wide" else 32,
        fps=30,
        deliverable_role="primary" if required else "alternate",
        render_profile_id=f"profile-{variant_id}",
        render_profile_sha256=_digest("a"),
        composition_profile=CompositionProfile(
            id=f"composition-{variant_id}",
            revision="1",
            scene_sha256=_digest("b"),
            camera_sha256=_digest("c"),
            composition_sha256=_digest("d"),
        ),
        output_variant_sha256=_digest("e" if enabled else "f"),
        frames_root=frames_root,
        preview_root=f"variant-artifacts/{variant_id}/previews",
        encode_root=f"variant-artifacts/{variant_id}/encodes",
        qa_root=f"variant-artifacts/{variant_id}/qa",
        progress=OutputVariantProgress(
            output_variant_id=variant_id,
            total_frames=10 if enabled else 0,
            latest_rendered_frame=7 if enabled else None,
            updated_at=now,
        ),
    )


def _artifact_job(output: Path) -> JobRecord:
    now = datetime.now(UTC)
    return JobRecord(
        id="variant-artifact-job",
        renderer=RendererKind.FAKE,
        state=JobState.RUNNING,
        identity=RenderIdentity(
            project_id="project",
            scene_id="scene",
            scene_sha256="A" * 64,
            profile_id="profile",
            profile_sha256="B" * 64,
            output_directory=str(output),
            output_variant_id="wide",
            output_width=16,
            output_height=16,
            composition_profile_id="composition-wide",
        ),
        created_at=now,
        updated_at=now,
        frame_start=1,
        frame_end=10,
        total_frame_count=10,
        chunks_total=1,
        latest_preview_frame=7,
        output_variants=(
            _runtime_variant(
                "wide",
                enabled=True,
                required=True,
                frames_root="variants/wide/frames",
            ),
            _runtime_variant(
                "portrait",
                enabled=False,
                required=False,
                frames_root="variants/portrait/frames",
            ),
        ),
        active_variant_id="wide",
    )


def test_explicit_variant_artifact_requests_fail_closed_without_legacy_fallback(
    tmp_path: Path,
) -> None:
    legacy_frames = tmp_path / "frames"
    legacy_frames.mkdir(parents=True)
    for frame in (7, 8, 9):
        (legacy_frames / f"frame_{frame:06d}.png").write_bytes(_png(16, 16))
    wide_frames = tmp_path / "variants" / "wide" / "frames"
    wide_frames.mkdir(parents=True)
    (wide_frames / "frame_000007.png").write_bytes(_png(16, 16))
    portrait_frames = tmp_path / "variants" / "portrait" / "frames"
    portrait_frames.mkdir(parents=True)
    (portrait_frames / "frame_000007.png").write_bytes(_png(16, 32))
    (portrait_frames / "frame_000009.png").write_bytes(_png(16, 32))

    job = _artifact_job(tmp_path)
    service = object.__new__(MissionControlService)
    service._preview_cache = {}
    service.get_job = lambda _job_id: job  # type: ignore[method-assign]

    assert service.full_frame_path(job.id, frame=7) == (
        wide_frames / "frame_000007.png"
    ).resolve()
    assert service.preview_path(job.id, frame=7) == (
        wide_frames / "frame_000007.png"
    ).resolve()
    assert service.full_frame_path(
        job.id,
        frame=7,
        output_variant_id="wide",
    ) == (wide_frames / "frame_000007.png").resolve()
    assert service.preview_path(
        job.id,
        frame=7,
        output_variant_id="wide",
    ) == (wide_frames / "frame_000007.png").resolve()

    for variant_id, frame in (
        ("unknown", 7),
        ("portrait", 7),
        ("wide", 8),
        ("wide", 9),
    ):
        assert service.full_frame_path(
            job.id,
            frame=frame,
            output_variant_id=variant_id,
        ) is None
        assert service.preview_path(
            job.id,
            frame=frame,
            output_variant_id=variant_id,
        ) is None


def _profile_payload(
    scene_path: Path,
    *,
    portrait_enabled: bool,
    include_v2: bool = True,
    singular_v2: bool = False,
) -> dict[str, object]:
    scene_hash = sha256_file(scene_path)
    payload: dict[str, object] = {
        "schemaVersion": "1.1.0",
        "kind": "trackprompt-render-profile",
        "project": "synthetic-project",
        "preset": "synthetic-scene",
        "profileId": "SYNTHETIC-V2-PROFILE",
        "displayName": "Synthetic V2 Profile",
        "approvedScenePath": str(scene_path.resolve()),
        "approvedSceneSha256": scene_hash,
        "authorization": {
            "project": "SYNTHETIC-PROJECT",
            "preset": "SYNTHETIC-SCENE",
            "profile": "SYNTHETIC-V2-PROFILE",
            "status": "pending-operator-approval",
        },
        "timeline": {"frameStart": 1, "frameEnd": 10, "fps": 30},
        "resolution": {"width": 16, "height": 16, "label": "synthetic"},
        "chunking": {"framesPerChunk": 5},
        "production": {
            "resumeEnabled": True,
            "verifyExistingFrames": True,
            "atomicChunkCommit": True,
            "overwriteValidFrames": False,
        },
    }
    if include_v2:
        variants = [
            {
                "id": "wide",
                "enabledByDefault": True,
                "required": True,
                "width": 16,
                "height": 16,
                "fps": 30,
                "compositionMode": "authored",
                "deliverableRole": "primary-master",
                "compositionProfileId": "wide-authored-v1",
                "cameraName": "WIDE_CAMERA",
            },
            {
                "id": "portrait",
                "enabledByDefault": False,
                "required": False,
                "width": 16,
                "height": 32,
                "fps": 30,
                "compositionMode": "authored",
                "deliverableRole": "optional-deliverable",
                "compositionProfileId": "portrait-authored-v1",
                "cameraName": "PORTRAIT_CAMERA",
            },
        ]
        if singular_v2:
            payload["outputVariant"] = {
                "id": "wide",
                "compositionMode": "authored",
                "compositionProfileId": "wide-authored-v1",
                "cameraName": "WIDE_CAMERA",
            }
        else:
            payload["outputVariants"] = variants
            if portrait_enabled:
                payload["enabledOutputVariantIds"] = ["wide", "portrait"]
        payload["shotPlan"] = {
            "shots": [
                {
                    "id": "shot-light",
                    "frameStart": 1,
                    "frameEnd": 4,
                    "complexityClass": "light",
                },
                {
                    "id": "shot-heavy",
                    "frameStart": 5,
                    "frameEnd": 10,
                    "complexityClass": "heavy",
                },
            ]
        }
    return payload


def _runtime_service(
    tmp_path: Path,
    *,
    portrait_enabled: bool,
    include_v2: bool = True,
    singular_v2: bool = False,
) -> MissionControlService:
    profile_root = tmp_path / "profiles"
    profile_root.mkdir(parents=True)
    calibration_root = tmp_path / "calibrations"
    calibration_root.mkdir()
    output_root = tmp_path / "outputs"
    output_root.mkdir()
    scene_path = tmp_path / "synthetic.blend"
    scene_path.write_bytes(b"BLENDER-v300 synthetic scene")
    profile_path = profile_root / "synthetic.json"
    profile_path.write_text(
        json.dumps(
            _profile_payload(
                scene_path,
                portrait_enabled=portrait_enabled,
                include_v2=include_v2,
                singular_v2=singular_v2,
            ),
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return MissionControlService(
        MissionControlConfig(
            repository_root=Path(__file__).resolve().parents[2],
            state_root=tmp_path / "state",
            profile_root=profile_root,
            calibration_root=calibration_root,
            default_output_root=output_root,
            allow_fake_renderer=True,
            native_dialog_enabled=False,
        )
    )


async def _start_without_renderer_task(
    service: MissionControlService,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> JobRecord:
    service.authorize_profile(
        "SYNTHETIC-V2-PROFILE",
        "synthetic-scene",
        settings_and_hashes_reviewed=True,
        production_render_authorized=True,
    )
    monkeypatch.setattr(
        service.fake_renderer,
        "start",
        lambda *_args, **_kwargs: None,
    )
    output = tmp_path / "selected-output"
    output.mkdir()
    return await service.start_render(
        StartFakeRenderRequest(
            project_id="synthetic-project",
            scene_id="synthetic-scene",
            profile_id="SYNTHETIC-V2-PROFILE",
            output_directory=str(output),
            renderer=RendererKind.FAKE,
            fake=FakeRenderOptions(total_frames=10, frames_per_chunk=5),
        ),
        fake_options=FakeRenderOptions(total_frames=10, frames_per_chunk=5),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("portrait_enabled", [False, True])
async def test_start_materializes_and_persists_exact_v2_output_matrix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    portrait_enabled: bool,
) -> None:
    monkeypatch.setattr(service_module, "_physical_memory_bytes", lambda: 8 * 1024**3)
    service = _runtime_service(
        tmp_path,
        portrait_enabled=portrait_enabled,
    )
    try:
        job = await _start_without_renderer_task(service, tmp_path, monkeypatch)
        expected_enabled = (
            ("wide", "portrait") if portrait_enabled else ("wide",)
        )

        assert job.identity.output_variant_id == "wide"
        assert job.active_variant_id == "wide"
        assert tuple(
            variant.id for variant in job.output_variants if variant.enabled
        ) == expected_enabled
        assert job.output_variants[0].progress.total_frames == 10
        assert job.output_variants[1].progress.total_frames == (
            10 if portrait_enabled else 0
        )
        if portrait_enabled:
            assert job.output_variants[1].progress.stages
        else:
            assert job.output_variants[1].progress.stages == ()
        assert {stage.stage for stage in job.stages} == set(RenderStage)
        assert job.workers[0].worker_id == "local-render-worker"
        assert job.aggregate_eta is not None
        assert job.aggregate_eta.enabled_variant_ids == expected_enabled
        assert job.aggregate_eta.p50_remaining_seconds is None
        assert job.aggregate_eta.p90_remaining_seconds is None

        canonical = service.store.get_media_render_job(job.id)
        assert canonical is not None
        assert canonical.output_matrix.enabled_variant_ids == expected_enabled
        assert canonical.output_matrix.variant_sha256_by_id == {
            variant.id: variant.output_variant_sha256
            for variant in canonical.output_variants
            if variant.enabled
        }
    finally:
        service.close()


@pytest.mark.asyncio
async def test_frame_updates_use_remaining_shot_complexity_and_persist_canonical_job(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service_module, "_physical_memory_bytes", lambda: 8 * 1024**3)
    service = _runtime_service(tmp_path, portrait_enabled=False)
    try:
        job = await _start_without_renderer_task(service, tmp_path, monkeypatch)
        first = await service.update_job(
            job.id,
            renderer_event_type="frame_written",
            rendered_frame_count=1,
            latest_rendered_frame=1,
            current_seconds_per_frame=2.0,
            current_complexity_class="light",
            worker_id="local-render-worker",
        )

        assert first.aggregate_eta is not None
        estimates = first.aggregate_eta.variant_forecasts[0].estimates
        assert {
            estimate.complexity_class: estimate.remaining_units
            for estimate in estimates
        } == {"heavy": 6, "light": 3}
        assert first.stages[-1].eta_p50_seconds is None

        second = await service.update_job(
            job.id,
            renderer_event_type="frame_written",
            rendered_frame_count=5,
            latest_rendered_frame=5,
            current_seconds_per_frame=4.0,
            current_complexity_class="heavy",
            worker_id="local-render-worker",
        )
        assert second.aggregate_eta is not None
        assert second.aggregate_eta.p50_remaining_seconds == pytest.approx(20)
        assert second.aggregate_eta.p90_remaining_seconds == pytest.approx(20)
        assert second.stages[-1].eta_p50_seconds == pytest.approx(20)

        canonical = service.store.get_media_render_job(job.id)
        assert canonical is not None
        assert canonical.updated_at == second.updated_at
        assert canonical.output_variants[0].progress.rendered_frames == 5
        assert canonical.output_variants[0].progress.latest_rendered_frame == 5
    finally:
        service.close()


@pytest.mark.asyncio
async def test_variant_events_update_independent_progress_eta_and_event_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service_module, "_physical_memory_bytes", lambda: 8 * 1024**3)
    service = _runtime_service(tmp_path, portrait_enabled=True)
    try:
        job = await _start_without_renderer_task(service, tmp_path, monkeypatch)
        wide = await service.update_job(
            job.id,
            output_variant_id="wide",
            renderer_event_type="frame_written",
            rendered_frame_count=1,
            latest_rendered_frame=1,
            current_seconds_per_frame=2.0,
            current_complexity_class="light",
            worker_id="local-render-worker",
        )
        portrait = await service.update_job(
            job.id,
            output_variant_id="portrait",
            renderer_event_type="frame_written",
            rendered_frame_count=5,
            latest_rendered_frame=5,
            current_seconds_per_frame=4.0,
            current_complexity_class="heavy",
            worker_id="local-render-worker",
        )

        assert wide.active_variant_id == "wide"
        assert portrait.active_variant_id == "portrait"
        progress = {
            variant.id: variant.progress for variant in portrait.output_variants
        }
        assert progress["wide"].rendered_frames == 1
        assert progress["portrait"].rendered_frames == 5
        assert portrait.aggregate_eta is not None
        forecasts = {
            forecast.output_variant_id: forecast
            for forecast in portrait.aggregate_eta.variant_forecasts
        }
        assert {
            estimate.complexity_class: estimate.remaining_units
            for estimate in forecasts["wide"].estimates
        } == {"heavy": 6, "light": 3}
        assert {
            estimate.complexity_class: estimate.remaining_units
            for estimate in forecasts["portrait"].estimates
        } == {"heavy": 5}

        event = service.events_after(0, job_id=job.id)[-1]
        assert event.output_variant_id == "portrait"
        assert event.output_width == 16
        assert event.output_height == 32
        assert event.composition_profile_id == "portrait-authored-v1"
        canonical = service.store.get_media_render_job(job.id)
        assert canonical is not None
        assert canonical.output_variants[0].progress.rendered_frames == 1
        assert canonical.output_variants[1].progress.rendered_frames == 5

        with pytest.raises(MissionControlError) as error:
            await service.update_job(
                job.id,
                output_variant_id="unknown",
                renderer_event_type="frame_written",
                rendered_frame_count=1,
                latest_rendered_frame=1,
                current_seconds_per_frame=1.0,
            )
        assert error.value.error.code == "render_event_variant_mismatch"
    finally:
        service.close()


@pytest.mark.asyncio
async def test_singular_output_variant_profile_materializes_one_entry_v2_job(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service_module, "_physical_memory_bytes", lambda: 8 * 1024**3)
    service = _runtime_service(
        tmp_path,
        portrait_enabled=False,
        singular_v2=True,
    )
    try:
        job = await _start_without_renderer_task(service, tmp_path, monkeypatch)

        assert job.identity.output_variant_id == "wide"
        assert job.identity.output_width == 16
        assert job.identity.output_height == 16
        assert job.active_variant_id == "wide"
        assert len(job.output_variants) == 1
        assert job.output_variants[0].enabled is True
        assert job.output_variants[0].required is True
        assert job.output_variants[0].composition_profile.id == "wide-authored-v1"
        canonical = service.store.get_media_render_job(job.id)
        assert canonical is not None
        assert canonical.output_matrix.enabled_variant_ids == ("wide",)
    finally:
        service.close()


@pytest.mark.asyncio
async def test_legacy_profile_keeps_legacy_job_without_phantom_v2_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _runtime_service(
        tmp_path,
        portrait_enabled=False,
        include_v2=False,
    )
    try:
        job = await _start_without_renderer_task(service, tmp_path, monkeypatch)
        assert job.identity.output_variant_id == "primary"
        assert job.output_variants == ()
        assert job.active_variant_id is None
        assert job.aggregate_eta is None
        assert service.store.get_media_render_job(job.id) is None
        updated = await service.update_job(
            job.id,
            renderer_event_type="frame_written",
            rendered_frame_count=1,
            latest_rendered_frame=1,
            current_seconds_per_frame=2.0,
            current_complexity_class="legacy",
            worker_id="local-render-worker",
        )
        render_stage = next(
            stage
            for stage in updated.stages
            if stage.stage is RenderStage.RENDERING
        )
        assert render_stage.eta_p50_seconds == pytest.approx(18)
        assert updated.aggregate_eta is None
    finally:
        service.close()
