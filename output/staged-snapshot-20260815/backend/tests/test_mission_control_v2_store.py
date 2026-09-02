from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from app.mission_control.eta import (
    EtaPersistentState,
    EtaSample,
    EtaService,
)
from app.mission_control.render_contracts import RenderStage
from app.mission_control.store import MissionControlStore


def test_eta_samples_round_trip_through_canonical_mission_control_database(
    tmp_path: Path,
) -> None:
    now = datetime.now(UTC)
    estimator = EtaService(EtaPersistentState(updated_at=now))
    estimator.record_sample(
        EtaSample(
            output_variant_id="wide-master",
            stage=RenderStage.RENDERING,
            complexity_class="dense-architecture",
            task_id="frame-17",
            worker_id="local-gpu-1",
            duration_seconds=2.5,
            completed_units=1,
            recorded_at=now,
        )
    )
    store = MissionControlStore(tmp_path / "mission-control.sqlite3")
    try:
        store.put_eta_state("job-1", estimator.snapshot())
        restored = store.get_eta_state("job-1")
    finally:
        store.close()

    assert restored is not None
    assert restored.revision == 1
    assert restored.samples[0].complexity_class == "dense-architecture"
    assert restored.samples[0].duration_seconds == 2.5
