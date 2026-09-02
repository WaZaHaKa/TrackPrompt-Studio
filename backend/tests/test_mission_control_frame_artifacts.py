from __future__ import annotations

import os
import struct
import zlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.mission_control.models import (
    JobRecord,
    JobState,
    RendererKind,
    RenderIdentity,
)
from app.mission_control.render_contracts import (
    CompositionProfile,
    OutputVariant,
    OutputVariantProgress,
)
from app.mission_control.service import MissionControlService


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


def _job(output: Path, *, width: int = 16, height: int = 16) -> JobRecord:
    now = datetime.now(UTC)
    return JobRecord(
        id="frame-artifact-job",
        renderer=RendererKind.PRODUCTION,
        state=JobState.RUNNING,
        identity=RenderIdentity(
            project_id="project",
            scene_id="scene",
            scene_sha256="A" * 64,
            profile_id="profile",
            profile_sha256="B" * 64,
            output_directory=str(output),
            output_variant_id="variant-a",
            output_width=width,
            output_height=height,
            composition_profile_id="composition-a",
        ),
        created_at=now,
        updated_at=now,
        frame_start=1,
        frame_end=30,
        total_frame_count=30,
        chunks_total=1,
        latest_preview_frame=7,
        latest_frame_artifact=(
            "checkpoints/.inflight-000001-000030-safe/frames/frame_000007.png"
        ),
    )


def test_latest_inflight_frame_is_stable_dimension_checked_and_openable(
    tmp_path: Path,
) -> None:
    source = (
        tmp_path
        / "checkpoints"
        / ".inflight-000001-000030-safe"
        / "frames"
        / "frame_000007.png"
    )
    source.parent.mkdir(parents=True)
    source.write_bytes(_png(16, 16))
    service = object.__new__(MissionControlService)
    job = _job(tmp_path)
    service.get_job = lambda _job_id: job  # type: ignore[method-assign]

    assert service._telemetry_artifact_path(job, frame=7) == source.resolve()
    assert service.full_frame_path(job.id, frame=7) == source.resolve()


def test_cross_dimension_or_traversing_inflight_frame_is_rejected(
    tmp_path: Path,
) -> None:
    source = (
        tmp_path
        / "checkpoints"
        / ".inflight-000001-000030-safe"
        / "frames"
        / "frame_000007.png"
    )
    source.parent.mkdir(parents=True)
    source.write_bytes(_png(16, 16))
    service = object.__new__(MissionControlService)

    assert service._telemetry_artifact_path(_job(tmp_path, width=32), frame=7) is None
    traversal = _job(tmp_path).model_copy(
        update={"latest_frame_artifact": "../frame_000007.png"}
    )
    assert service._telemetry_artifact_path(traversal, frame=7) is None


def _v2_job(
    output: Path,
    artifact: str,
    *,
    frame: int = 7,
    width: int = 16,
    written_at: datetime | None = None,
) -> JobRecord:
    now = written_at or datetime.now(UTC)
    variant = OutputVariant(
        id="wide",
        enabled=True,
        required=True,
        width=width,
        height=16,
        fps=30,
        deliverable_role="primary",
        render_profile_id="profile-wide",
        render_profile_sha256="a" * 64,
        composition_profile=CompositionProfile(
            id="composition-wide",
            revision="1",
            scene_sha256="b" * 64,
            camera_sha256="c" * 64,
            composition_sha256="d" * 64,
        ),
        output_variant_sha256="e" * 64,
        frames_root="variants/wide/frames",
        preview_root="variants/wide/previews",
        encode_root="variants/wide/encodes",
        qa_root="variants/wide/qa",
        progress=OutputVariantProgress(
            output_variant_id="wide",
            total_frames=30,
            rendered_frames=frame,
            latest_rendered_frame=frame,
            latest_frame_artifact=artifact,
            latest_frame_artifact_frame=frame,
            latest_frame_written_at=now,
            updated_at=now,
        ),
    )
    return _job(output, width=width).model_copy(
        update={
            "renderer": RendererKind.FAKE,
            "output_variants": (variant,),
            "active_variant_id": "wide",
            "latest_frame_artifact": artifact,
            "latest_preview_frame": frame,
            "latest_preview_at": now,
        }
    )


def test_v2_endpoint_exposes_stable_inflight_png_before_chunk_publication(
    tmp_path: Path,
) -> None:
    relative = (
        "variants/wide/checkpoints/.inflight-000001-000030-safe/"
        "frames/frame_000007.png"
    )
    source = tmp_path.joinpath(*relative.split("/"))
    source.parent.mkdir(parents=True)
    source.write_bytes(_png(16, 16))
    job = _v2_job(tmp_path, relative)
    service = object.__new__(MissionControlService)
    service._preview_cache = {}
    service.get_job = lambda _job_id: job  # type: ignore[method-assign]

    assert not (tmp_path / "variants" / "wide" / "frames").exists()
    assert service.full_frame_path(
        job.id,
        frame=7,
        output_variant_id="wide",
    ) == source.resolve()
    assert service.full_frame_path(job.id, frame=7) == source.resolve()
    assert service.preview_path(
        job.id,
        frame=7,
        output_variant_id="wide",
    ) == source.resolve()


def test_v2_inflight_endpoint_rejects_partial_stale_and_cross_geometry_png(
    tmp_path: Path,
) -> None:
    relative = (
        "variants/wide/checkpoints/.inflight-000001-000030-safe/"
        "frames/frame_000007.png"
    )
    source = tmp_path.joinpath(*relative.split("/"))
    source.parent.mkdir(parents=True)
    service = object.__new__(MissionControlService)
    service._preview_cache = {}

    source.write_bytes(_png(16, 16)[:-12])
    partial = _v2_job(tmp_path, relative)
    service.get_job = lambda _job_id: partial  # type: ignore[method-assign]
    assert service.full_frame_path(
        partial.id,
        frame=7,
        output_variant_id="wide",
    ) is None

    source.write_bytes(_png(16, 16))
    wrong_geometry = _v2_job(tmp_path, relative, width=32)
    service.get_job = lambda _job_id: wrong_geometry  # type: ignore[method-assign]
    assert service.full_frame_path(
        wrong_geometry.id,
        frame=7,
        output_variant_id="wide",
    ) is None

    written_at = datetime.now(UTC)
    stale = _v2_job(tmp_path, relative, written_at=written_at)
    old_timestamp = (written_at - timedelta(days=1)).timestamp()
    os.utime(source, (old_timestamp, old_timestamp))
    service.get_job = lambda _job_id: stale  # type: ignore[method-assign]
    assert service.full_frame_path(
        stale.id,
        frame=7,
        output_variant_id="wide",
    ) is None
    assert service.full_frame_path(
        stale.id,
        frame=7,
        output_variant_id="unknown",
    ) is None


def test_v2_inflight_endpoint_rejects_frame_outside_exact_variant_namespaces(
    tmp_path: Path,
) -> None:
    relative = "variants/wide/unmanaged/frame_000007.png"
    source = tmp_path.joinpath(*relative.split("/"))
    source.parent.mkdir(parents=True)
    source.write_bytes(_png(16, 16))
    job = _v2_job(tmp_path, relative)
    service = object.__new__(MissionControlService)
    service._preview_cache = {}
    service.get_job = lambda _job_id: job  # type: ignore[method-assign]

    assert service.full_frame_path(
        job.id,
        frame=7,
        output_variant_id="wide",
    ) is None
