from __future__ import annotations

import json
from datetime import UTC, datetime

from app.mission_control.models import JobRecord, JobState, RendererKind, RenderIdentity
from app.mission_control.renderers import (
    RENDER_EVENT_PREFIX,
    RendererVariantExpectation,
    parse_renderer_telemetry_line,
    telemetry_event_is_new,
    telemetry_progress_changes,
)


def _line(**changes: object) -> str:
    payload: dict[str, object] = {
        "schemaVersion": "1.0.0",
        "eventType": "frame_written",
        "sequence": 2,
        "jobId": "job-test",
        "workerId": "local-42",
        "chunkId": "000601-001200",
        "chunkStart": 601,
        "chunkEnd": 1200,
        "frame": 811,
        "elapsedSeconds": 1.25,
        "rendererStatus": "awaiting_chunk_validation",
        "actId": "departure",
        "actName": "Departure",
        "shotId": "departure-wide",
        "shotName": "Crossing the threshold",
    }
    payload.update(changes)
    return RENDER_EVENT_PREFIX + json.dumps(payload, separators=(",", ":"))


def _line_v2(**changes: object) -> str:
    payload: dict[str, object] = {
        "schemaVersion": "2.0.0",
        "eventType": "frame_written",
        "sequence": 3,
        "jobId": "job-test",
        "workerId": "local-42",
        "chunkId": "000601-001200",
        "chunkStart": 601,
        "chunkEnd": 1200,
        "frame": 812,
        "elapsedSeconds": 1.5,
        "rendererStatus": "awaiting_chunk_validation",
        "projectId": "project",
        "sceneSha256": "A" * 64,
        "profileSha256": "B" * 64,
        "outputVariantId": "horizontal-master",
        "width": 1920,
        "height": 1080,
        "compositionProfileId": "horizontal-safe-v1",
        "artifactRelativePath": (
            "checkpoints/.inflight-000601-001200-safe/frames/frame_000812.png"
        ),
        "emittedAt": "2026-07-23T10:30:00Z",
    }
    payload.update(changes)
    return RENDER_EVENT_PREFIX + json.dumps(payload, separators=(",", ":"))


def _job() -> JobRecord:
    now = datetime.now(UTC)
    return JobRecord(
        id="job-test",
        renderer=RendererKind.PRODUCTION,
        state=JobState.RUNNING,
        identity=RenderIdentity(
            project_id="project",
            scene_id="scene",
            scene_sha256="A" * 64,
            profile_id="profile",
            profile_sha256="B" * 64,
            output_directory="C:\\renders\\safe",
        ),
        created_at=now,
        updated_at=now,
        frame_start=1,
        frame_end=13029,
        total_frame_count=13029,
        chunks_total=22,
        published_frame_count=600,
        rendered_frame_count=600,
    )


def test_exact_prefix_event_updates_rendered_but_not_safe_progress() -> None:
    event = parse_renderer_telemetry_line(_line(), expected_job_id="job-test")
    assert event is not None
    changes = telemetry_progress_changes(_job(), event, output_at=datetime.now(UTC))

    assert changes["latest_rendered_frame"] == 811
    assert changes["rendered_frame_count"] == 811
    assert changes["inflight_frame_count"] == 211
    assert "published_frame_count" not in changes
    assert changes["current_act_name"] == "Departure"
    assert changes["current_shot_name"] == "Crossing the threshold"


def test_malformed_wrong_job_and_non_exact_prefix_events_are_inert() -> None:
    assert parse_renderer_telemetry_line(_line(jobId="other"), expected_job_id="job-test") is None
    assert parse_renderer_telemetry_line(_line(frame=1201), expected_job_id="job-test") is None
    assert parse_renderer_telemetry_line(_line(elapsedSeconds=float("nan")), expected_job_id="job-test") is None
    assert parse_renderer_telemetry_line("INFO " + _line(), expected_job_id="job-test") is None
    assert parse_renderer_telemetry_line(RENDER_EVENT_PREFIX + "{bad", expected_job_id="job-test") is None


def test_renderer_names_cannot_inject_control_characters() -> None:
    assert parse_renderer_telemetry_line(_line(shotName="bad\nline"), expected_job_id="job-test") is None


def test_renderer_sequence_remains_monotonic_against_persisted_job_state() -> None:
    event = parse_renderer_telemetry_line(_line(sequence=19), expected_job_id="job-test")
    assert event is not None
    job = _job().model_copy(
        update={
            "worker_id": event.worker_id,
            "active_chunk_id": event.chunk_id,
            "renderer_event_sequence": 19,
        }
    )
    assert telemetry_event_is_new(job, event) is False
    newer = parse_renderer_telemetry_line(_line(sequence=20), expected_job_id="job-test")
    assert newer is not None
    assert telemetry_event_is_new(job, newer) is True


def test_v2_telemetry_binds_variant_dimensions_and_relative_artifact() -> None:
    event = parse_renderer_telemetry_line(
        _line_v2(),
        expected_job_id="job-test",
        expected_project_id="project",
        expected_scene_sha256="A" * 64,
        expected_profile_sha256="B" * 64,
        expected_output_variant_id="horizontal-master",
        expected_width=1920,
        expected_height=1080,
        expected_composition_profile_id="horizontal-safe-v1",
    )
    assert event is not None
    changes = telemetry_progress_changes(_job(), event, output_at=datetime.now(UTC))
    assert changes["output_variant_id"] == "horizontal-master"
    assert changes["latest_preview_frame"] == 812
    assert changes["latest_frame_preview"] == (
        "/api/mission-control/render/job-test/preview?v=812"
        "&output_variant_id=horizontal-master"
    )
    assert changes["latest_full_frame_url"] == (
        "/api/mission-control/render/job-test/frame?v=812"
        "&output_variant_id=horizontal-master"
    )
    assert changes["latest_frame_artifact"] == (
        "checkpoints/.inflight-000601-001200-safe/frames/frame_000812.png"
    )
    assert "published_frame_count" not in changes


def test_v2_cross_variant_and_unsafe_artifacts_are_rejected() -> None:
    expected = {
        "expected_job_id": "job-test",
        "expected_project_id": "project",
        "expected_scene_sha256": "A" * 64,
        "expected_profile_sha256": "B" * 64,
        "expected_output_variant_id": "horizontal-master",
        "expected_width": 1920,
        "expected_height": 1080,
        "expected_composition_profile_id": "horizontal-safe-v1",
    }
    assert (
        parse_renderer_telemetry_line(
            _line_v2(outputVariantId="vertical-social"),
            **expected,
        )
        is None
    )
    assert (
        parse_renderer_telemetry_line(
            _line_v2(artifactRelativePath="../frame_000812.png"),
            **expected,
        )
        is None
    )
    assert (
        parse_renderer_telemetry_line(
            _line_v2(width=1080, height=1920),
            **expected,
        )
        is None
    )


def test_v2_variant_registry_routes_identity_and_rejects_cross_format_metadata() -> None:
    variants = {
        "horizontal-master": RendererVariantExpectation(
            output_variant_id="horizontal-master",
            render_profile_sha256="B" * 64,
            width=1920,
            height=1080,
            composition_profile_id="horizontal-safe-v1",
        ),
        "vertical-social": RendererVariantExpectation(
            output_variant_id="vertical-social",
            render_profile_sha256="C" * 64,
            width=1080,
            height=1920,
            composition_profile_id="vertical-safe-v1",
        ),
    }
    common = {
        "expected_job_id": "job-test",
        "expected_project_id": "project",
        "expected_scene_sha256": "A" * 64,
        "expected_output_variants": variants,
    }

    horizontal = parse_renderer_telemetry_line(_line_v2(), **common)
    vertical = parse_renderer_telemetry_line(
        _line_v2(
            outputVariantId="vertical-social",
            profileSha256="C" * 64,
            width=1080,
            height=1920,
            compositionProfileId="vertical-safe-v1",
        ),
        **common,
    )

    assert horizontal is not None
    assert vertical is not None
    assert vertical.output_variant_id == "vertical-social"
    assert (
        parse_renderer_telemetry_line(
            _line_v2(outputVariantId="unknown"),
            **common,
        )
        is None
    )
    assert (
        parse_renderer_telemetry_line(
            _line_v2(
                outputVariantId="vertical-social",
                profileSha256="C" * 64,
                width=1920,
                height=1080,
                compositionProfileId="vertical-safe-v1",
            ),
            **common,
        )
        is None
    )
    assert (
        parse_renderer_telemetry_line(
            _line_v2(
                outputVariantId="vertical-social",
                profileSha256="C" * 64,
                width=1080,
                height=1920,
                compositionProfileId="horizontal-safe-v1",
            ),
            **common,
        )
        is None
    )


def test_stale_frame_event_cannot_replace_a_newer_rendered_frame() -> None:
    event = parse_renderer_telemetry_line(
        _line_v2(frame=812),
        expected_job_id="job-test",
    )
    assert event is not None
    job = _job().model_copy(update={"latest_rendered_frame": 900})

    assert telemetry_progress_changes(
        job,
        event,
        output_at=datetime.now(UTC),
    ) == {}
