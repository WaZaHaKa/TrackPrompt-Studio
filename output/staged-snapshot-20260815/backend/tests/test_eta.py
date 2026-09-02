from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import pytest

from app.mission_control.eta import (
    EstimateState,
    EtaPersistentState,
    EtaSample,
    EtaService,
    StageWorkload,
)
from app.mission_control.render_contracts import (
    CompositionProfile,
    MediaRenderJob,
    OutputVariant,
    OutputVariantMatrixIdentity,
    OutputVariantProgress,
    PackageIdentity,
    ProjectRef,
    RenderStage,
    TenantRef,
)

NOW = datetime(2026, 1, 2, 3, 4, tzinfo=UTC)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _variant(variant_id: str, *, enabled: bool, required: bool) -> OutputVariant:
    root = f"jobs/job-eta/variants/{variant_id}"
    return OutputVariant(
        id=variant_id,
        enabled=enabled,
        required=required,
        width=960 if variant_id == "landscape" else 540,
        height=540 if variant_id == "landscape" else 960,
        fps=24,
        deliverable_role="primary" if required else "alternate",
        render_profile_id=f"profile-{variant_id}",
        render_profile_sha256=_digest(f"profile-{variant_id}"),
        composition_profile=CompositionProfile(
            id=f"composition-{variant_id}",
            revision="revision-1",
            scene_sha256=_digest(f"scene-{variant_id}"),
            camera_sha256=_digest(f"camera-{variant_id}"),
            composition_sha256=_digest(f"composition-{variant_id}"),
        ),
        output_variant_sha256=_digest(f"variant-{variant_id}"),
        frames_root=f"{root}/frames",
        preview_root=f"{root}/previews",
        encode_root=f"{root}/encodes",
        qa_root=f"{root}/qa",
        progress=OutputVariantProgress(
            output_variant_id=variant_id,
            total_frames=100 if enabled else 0,
            updated_at=NOW,
        ),
    )


def _job(*, portrait_enabled: bool) -> MediaRenderJob:
    variants = (
        _variant("landscape", enabled=True, required=True),
        _variant("portrait", enabled=portrait_enabled, required=False),
    )
    enabled = tuple(variant for variant in variants if variant.enabled)
    package_hash = _digest("eta-package")
    return MediaRenderJob(
        id="job-eta",
        project=ProjectRef(
            tenant=TenantRef(namespace="synthetic"),
            project_id="eta-project",
        ),
        package=PackageIdentity(
            package_id="eta-package",
            package_sha256=package_hash,
            source_revision="revision-1",
        ),
        output_matrix=OutputVariantMatrixIdentity(
            matrix_id="eta-matrix",
            matrix_sha256=_digest(
                "eta-matrix-" + "-".join(variant.id for variant in enabled)
            ),
            package_sha256=package_hash,
            enabled_variant_ids=tuple(variant.id for variant in enabled),
            variant_sha256_by_id={
                variant.id: variant.output_variant_sha256 for variant in enabled
            },
        ),
        output_variants=variants,
        created_at=NOW,
        updated_at=NOW,
    )


def _record_rates(
    service: EtaService,
    variant_id: str,
    stage: RenderStage,
    rates: list[float],
    *,
    complexity_class: str = "default",
) -> None:
    for index, rate in enumerate(rates):
        service.record_sample(
            EtaSample(
                output_variant_id=variant_id,
                stage=stage,
                complexity_class=complexity_class,
                task_id=f"{variant_id}-{stage.value}-{complexity_class}-{index}",
                worker_id="worker-1",
                duration_seconds=rate,
                completed_units=1,
                recorded_at=NOW + timedelta(seconds=index),
            )
        )


def test_dual_variant_forecast_reports_stage_variant_and_matrix_bounds() -> None:
    service = EtaService(clock=lambda: NOW)
    _record_rates(service, "landscape", RenderStage.RENDERING, [10] * 5)
    _record_rates(service, "landscape", RenderStage.ENCODING, [2] * 5)
    _record_rates(service, "portrait", RenderStage.RENDERING, [20] * 5)
    _record_rates(service, "portrait", RenderStage.ENCODING, [3] * 5)
    service.set_worker_count("landscape", RenderStage.RENDERING, 2, observed_at=NOW)
    service.set_worker_count("portrait", RenderStage.RENDERING, 1, observed_at=NOW)

    forecast = service.forecast_matrix(
        _job(portrait_enabled=True),
        (
            StageWorkload(
                output_variant_id="landscape",
                stage=RenderStage.RENDERING,
                remaining_units=100,
            ),
            StageWorkload(
                output_variant_id="landscape",
                stage=RenderStage.ENCODING,
                remaining_units=100,
                parallelizable=False,
            ),
            StageWorkload(
                output_variant_id="portrait",
                stage=RenderStage.RENDERING,
                remaining_units=50,
            ),
            StageWorkload(
                output_variant_id="portrait",
                stage=RenderStage.ENCODING,
                remaining_units=50,
                parallelizable=False,
            ),
        ),
    )

    assert forecast.enabled_variant_ids == ("landscape", "portrait")
    assert len(forecast.variant_forecasts) == 2
    assert [len(item.estimates) for item in forecast.variant_forecasts] == [2, 2]
    assert forecast.variant_forecasts[0].p50_remaining_seconds == pytest.approx(700)
    assert forecast.variant_forecasts[1].p50_remaining_seconds == pytest.approx(1150)
    assert forecast.p50_remaining_seconds == pytest.approx(1850)
    assert forecast.p90_remaining_seconds == pytest.approx(1850)
    assert forecast.state is EstimateState.STABLE


def test_disabled_variant_contributes_no_phantom_work() -> None:
    service = EtaService(clock=lambda: NOW)
    _record_rates(service, "landscape", RenderStage.RENDERING, [4] * 5)
    job = _job(portrait_enabled=False)

    forecast = service.forecast_matrix(
        job,
        (
            StageWorkload(
                output_variant_id="landscape",
                stage=RenderStage.RENDERING,
                remaining_units=10,
            ),
            StageWorkload(
                output_variant_id="portrait",
                stage=RenderStage.RENDERING,
                remaining_units=999,
                measured_fallback_p50_seconds_per_unit=100,
                measured_fallback_p90_seconds_per_unit=200,
            ),
        ),
    )

    assert forecast.enabled_variant_ids == ("landscape",)
    assert job.output_variants[1].progress.total_frames == 0
    assert tuple(item.output_variant_id for item in forecast.variant_forecasts) == (
        "landscape",
    )
    assert forecast.p50_remaining_seconds == pytest.approx(40)
    assert forecast.p90_remaining_seconds == pytest.approx(40)


def test_serialized_state_reconstructs_the_same_forecast_after_restart() -> None:
    service = EtaService(clock=lambda: NOW)
    _record_rates(service, "landscape", RenderStage.RENDERING, [8, 9, 10, 11, 12])
    service.set_worker_count("landscape", RenderStage.RENDERING, 2, observed_at=NOW)
    workload = StageWorkload(
        output_variant_id="landscape",
        stage=RenderStage.RENDERING,
        remaining_units=40,
    )
    before = service.estimate(workload)

    serialized = service.snapshot().model_dump_json(by_alias=True)
    restored_state = EtaPersistentState.model_validate_json(serialized)
    restored = EtaService.from_state(restored_state, clock=lambda: NOW)
    after = restored.estimate(workload)

    assert restored.snapshot() == service.snapshot()
    assert after.p50_remaining_seconds == before.p50_remaining_seconds
    assert after.p90_remaining_seconds == before.p90_remaining_seconds
    assert after.effective_worker_count == 2
    assert '"schemaVersion":"2.0.0"' in serialized


def test_retries_and_outliers_are_excluded_from_robust_bounds() -> None:
    service = EtaService(clock=lambda: NOW)
    _record_rates(service, "landscape", RenderStage.RENDERING, [10] * 5 + [1000])
    service.record_sample(
        EtaSample(
            output_variant_id="landscape",
            stage=RenderStage.RENDERING,
            task_id="failed-task",
            worker_id="worker-1",
            succeeded=False,
            duration_seconds=200,
            completed_units=1,
            recorded_at=NOW,
        )
    )
    service.record_sample(
        EtaSample(
            output_variant_id="landscape",
            stage=RenderStage.RENDERING,
            task_id="retried-task",
            worker_id="worker-1",
            attempt=2,
            retried=True,
            duration_seconds=100,
            completed_units=1,
            recorded_at=NOW,
        )
    )

    estimate = service.estimate(
        StageWorkload(
            output_variant_id="landscape",
            stage=RenderStage.RENDERING,
            remaining_units=20,
        )
    )

    assert estimate.retained_sample_count == 5
    assert estimate.excluded_retry_count == 2
    assert estimate.excluded_outlier_count == 1
    assert estimate.p50_seconds_per_unit == pytest.approx(10)
    assert estimate.p90_seconds_per_unit == pytest.approx(10)
    assert estimate.p90_remaining_seconds == pytest.approx(200)


def test_worker_count_changes_and_worker_loss_update_eta_truthfully() -> None:
    service = EtaService(clock=lambda: NOW)
    _record_rates(service, "landscape", RenderStage.RENDERING, [10] * 5)
    workload = StageWorkload(
        output_variant_id="landscape",
        stage=RenderStage.RENDERING,
        remaining_units=10,
    )

    one_worker = service.estimate(workload)
    service.set_worker_count("landscape", RenderStage.RENDERING, 2, observed_at=NOW)
    two_workers = service.estimate(workload)
    service.set_worker_count(
        "landscape",
        RenderStage.RENDERING,
        0,
        observed_at=NOW + timedelta(seconds=1),
    )
    no_workers = service.estimate(workload)

    assert one_worker.p50_remaining_seconds == pytest.approx(100)
    assert two_workers.p50_remaining_seconds == pytest.approx(50)
    assert no_workers.p50_remaining_seconds is None
    assert no_workers.state is EstimateState.DEGRADED


def test_unknown_stage_becomes_stable_only_after_measured_samples() -> None:
    service = EtaService(clock=lambda: NOW)
    workload = StageWorkload(
        output_variant_id="landscape",
        stage=RenderStage.CACHE_BAKE,
        remaining_units=10,
    )

    unknown = service.estimate(workload)
    _record_rates(service, "landscape", RenderStage.CACHE_BAKE, [3, 4, 5, 6, 7])
    measured = service.estimate(workload)

    assert unknown.state is EstimateState.CALIBRATING
    assert unknown.p50_remaining_seconds is None
    assert measured.state is EstimateState.STABLE
    assert measured.p50_remaining_seconds == pytest.approx(50)


def test_changing_enabled_variants_invalidates_the_bound_matrix_forecast() -> None:
    service = EtaService(clock=lambda: NOW)
    job = _job(portrait_enabled=True)
    job.output_variants[1].enabled = False

    with pytest.raises(ValueError, match="no longer matches the bound matrix"):
        service.forecast_matrix(job, ())
