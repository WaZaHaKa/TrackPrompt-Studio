from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from pathlib import Path
from uuid import uuid4

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from app.catalog.backup import create_backup, restore_backup, verify_backup
from app.catalog.reports import build_mastering_report, report_csv, report_json, report_markdown
from app.catalog.schemas import (
    BatchCreate,
    ClientCreate,
    ProjectCreate,
    QueueState,
    RetentionPolicy,
    ReviewState,
    SegmentationJobState,
    SegmentEvidence,
    SegmentResponse,
    TransitionType,
    UploadSessionCreate,
)
from app.catalog.segmentation import (
    Observation,
    build_segments,
    detect_candidates,
    segment_longform_source,
)
from app.catalog.store import CatalogueStore
from app.main import create_app
from app.media import probe_media
from app.schemas import Confidence

from .helpers import settings_for


def _catalog(store: CatalogueStore) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    client = store.create_client(ClientCreate(display_name="Private mastering client"))
    project = store.create_project(
        ProjectCreate(
            client_id=str(client["id"]),
            name="Two-set mastering project",
            retention_policy=RetentionPolicy.ARCHIVE,
        )
    )
    batch = store.create_batch(
        str(project["id"]),
        BatchCreate(name="Set A", lyrics_consent_confirmed=False),
    )
    return client, project, batch


def _asset(store: CatalogueStore, batch: dict[str, object], *, duration: float = 1200) -> dict[str, object]:
    session = store.create_upload_session(
        UploadSessionCreate(
            batch_id=str(batch["id"]),
            display_name="private-source.wav",
            total_bytes=1,
            idempotency_key=str(uuid4()),
            permission_confirmed=True,
        )
    )
    store.record_chunk(str(session["id"]), offset=0, length=1, chunk_sha256=hashlib.sha256(b"x").hexdigest())
    digest = hashlib.sha256(b"x").hexdigest()
    destination, storage_key = store.blob_destination(digest)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(b"x")
    return store.complete_asset(
        str(session["id"]),
        content_sha256=digest,
        duration_seconds=duration,
        codec="pcm_s16le",
        container="wav",
        sample_rate=16_000,
        channels=2,
        storage_key=storage_key,
    )


def test_catalogue_migration_crud_and_audit_chain(tmp_path: Path) -> None:
    store = CatalogueStore(settings_for(tmp_path / "data"))
    client, project, batch = _catalog(store)
    assert store.migration_version() == 3
    assert store.get_client(str(client["id"]))["project_count"] == 1
    assert store.get_project(str(project["id"]))["retention_policy"] == "archive"
    assert store.get_batch(str(batch["id"]))["state"] == "draft"
    verification = store.verify_audit(str(project["id"]))
    assert verification["valid"] is True
    assert verification["event_count"] == 2
    with sqlite3.connect(store.settings.database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE component = 'catalogue'"
        ).fetchone()[0] == 3


def test_segmentation_scan_state_recovers_and_cancels_durably(tmp_path: Path) -> None:
    settings = settings_for(tmp_path / "data")
    store = CatalogueStore(settings)
    _client, _project, batch = _catalog(store)
    asset = _asset(store, batch)
    job = store.create_segmentation_job(str(asset["id"]))
    store.update_segmentation_job(
        str(job["id"]),
        state=SegmentationJobState.RUNNING,
        stage="Coarse set scan",
        progress=31,
    )
    restarted = CatalogueStore(settings)
    assert restarted.recover_segmentation_jobs() == 1
    recovered = restarted.get_segmentation_job(str(job["id"]))
    assert recovered["state"] == "queued"
    assert recovered["progress"] == 31
    assert restarted.get_asset(str(asset["id"]))["segmentation_state"] == "queued"
    cancelled = restarted.request_segmentation_cancel(str(job["id"]))
    assert cancelled["state"] == "cancelled"


def test_boundary_edits_import_restore_and_revision_history(tmp_path: Path) -> None:
    settings = settings_for(tmp_path / "data")
    store = CatalogueStore(settings)
    _client, project, batch = _catalog(store)
    asset = _asset(store, batch, duration=120)
    detected = build_segments(str(asset["id"]), 120, [])
    detected[0].end_seconds = 60
    detected[0].stable_core_end_seconds = 60
    second = detected[0].model_copy(deep=True)
    second.id = str(uuid4())
    second.sequence_index = 1
    second.label = "Track 2"
    second.start_seconds = 60
    second.end_seconds = 120
    second.stable_core_start_seconds = 60
    second.stable_core_end_seconds = 120
    store.replace_segments(str(asset["id"]), [detected[0], second], reason="detected", detected=True)
    with TestClient(create_app(settings)) as client:
        asset_id = str(asset["id"])
        accepted = client.patch(
            f"/api/assets/{asset_id}/segments",
            json={"operation": "accept", "segmentId": detected[0].id, "reason": "review"},
        )
        assert accepted.status_code == 200, accepted.text
        renamed = client.patch(
            f"/api/assets/{asset_id}/segments",
            json={"operation": "rename", "segmentId": detected[0].id, "label": "Opening", "reason": "label"},
        )
        assert renamed.status_code == 200, renamed.text
        split = client.patch(
            f"/api/assets/{asset_id}/segments",
            json={"operation": "split", "segmentId": detected[0].id, "atSeconds": 30, "reason": "split"},
        )
        assert split.status_code == 200, split.text
        split_rows = split.json()
        merged = client.patch(
            f"/api/assets/{asset_id}/segments",
            json={
                "operation": "merge",
                "segmentId": split_rows[0]["id"],
                "adjacentSegmentId": split_rows[1]["id"],
                "reason": "merge",
            },
        )
        assert merged.status_code == 200, merged.text
        imported = client.post(
            f"/api/assets/{asset_id}/segments/import",
            json={"format": "csv", "content": "start_seconds,label\n0,Intro\n45,Main\n"},
        )
        assert imported.status_code == 200, imported.text
        assert [row["reviewState"] for row in imported.json()] == ["imported", "imported"]
        restored = client.patch(
            f"/api/assets/{asset_id}/segments",
            json={"operation": "restore", "reason": "restore detection"},
        )
        assert restored.status_code == 200, restored.text
        assert len(restored.json()) == 2
        accepted_id = restored.json()[0]["id"]
        client.patch(
            f"/api/assets/{asset_id}/segments",
            json={"operation": "accept", "segmentId": accepted_id, "reason": "analyze"},
        )
        queued = client.post(f"/api/assets/{asset_id}/segments/analyze")
        assert queued.status_code == 202, queued.text
        assert "projectId" not in queued.json()[0]
        assert "leaseOwner" not in queued.json()[0]
        revisions = client.get(f"/api/projects/{project['id']}/revisions")
        assert revisions.status_code == 200
    with store.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM segment_map_revisions WHERE source_asset_id = ?", (asset["id"],)
        ).fetchone()[0] >= 6


def test_explicit_project_deletion_preserves_shared_blob_and_tombstone(tmp_path: Path) -> None:
    store = CatalogueStore(settings_for(tmp_path / "data"))
    client = store.create_client(ClientCreate(display_name="Client"))
    projects_and_assets: list[tuple[dict[str, object], dict[str, object]]] = []
    for index in range(2):
        project = store.create_project(ProjectCreate(
            client_id=str(client["id"]), name=f"Project {index}", retention_policy=RetentionPolicy.ARCHIVE,
        ))
        batch = store.create_batch(str(project["id"]), BatchCreate(name="Set"))
        projects_and_assets.append((project, _asset(store, batch)))
    digest = str(projects_and_assets[0][1]["content_sha256"])
    blob, _storage_key = store.blob_destination(digest)
    assert blob.is_file()
    first = store.delete_project(str(projects_and_assets[0][0]["id"]))
    assert len(str(first["tombstone_hash"])) == 64
    assert blob.is_file()
    with store.connect() as connection:
        assert connection.execute(
            "SELECT reference_count FROM archive_blobs WHERE content_sha256 = ?", (digest,)
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM catalogue_deletion_events WHERE project_id = ?",
            (projects_and_assets[0][0]["id"],),
        ).fetchone()[0] == 1
    store.delete_project(str(projects_and_assets[1][0]["id"]))
    assert not blob.exists()


def test_resumable_chunk_contract_persists_across_restart(tmp_path: Path) -> None:
    settings = settings_for(tmp_path / "data")
    store = CatalogueStore(settings)
    _client, _project, batch = _catalog(store)
    payload = {
        "batchId": batch["id"],
        "displayName": "long set.wav",
        "totalBytes": 6,
        "idempotencyKey": "upload-idempotency-0001",
        "permissionConfirmed": True,
    }
    with TestClient(create_app(settings)) as client:
        created = client.post("/api/upload-sessions", json=payload)
        assert created.status_code == 201, created.text
        upload_id = created.json()["id"]
        chunk = b"abc"
        response = client.patch(
            f"/api/upload-sessions/{upload_id}",
            content=chunk,
            headers={
                "Content-Range": "bytes 0-2/6",
                "Upload-Offset": "0",
                "X-Chunk-SHA256": hashlib.sha256(chunk).hexdigest(),
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["receivedBytes"] == 3
        conflict = client.patch(
            f"/api/upload-sessions/{upload_id}",
            content=b"x",
            headers={"Content-Range": "bytes 0-0/6", "Upload-Offset": "0"},
        )
        assert conflict.status_code == 409
    with TestClient(create_app(settings)) as restarted:
        persisted = restarted.get(f"/api/upload-sessions/{upload_id}")
        assert persisted.status_code == 200
        assert persisted.json()["receivedBytes"] == 3


def test_thousand_child_items_have_no_arbitrary_product_cap(tmp_path: Path) -> None:
    store = CatalogueStore(settings_for(tmp_path / "data"))
    _client, _project, batch = _catalog(store)
    asset = _asset(store, batch, duration=1_000)
    segments = [
        SegmentResponse(
            id=str(uuid4()),
            source_asset_id=str(asset["id"]),
            sequence_index=index,
            label=f"Item {index + 1}",
            start_seconds=float(index),
            end_seconds=float(index + 1),
            stable_core_start_seconds=float(index),
            stable_core_end_seconds=float(index + 1),
            confidence=Confidence.UNKNOWN,
            transition_type=TransitionType.UNCERTAIN,
            review_state=ReviewState.IMPORTED,
            accepted=True,
            revision=1,
        )
        for index in range(1_000)
    ]
    store.replace_segments(str(asset["id"]), segments, reason="1000 item registration", detected=False)
    queued = store.enqueue_segments(str(batch["id"]), [item.id for item in segments])
    assert len(queued) == 1_000
    assert len(store.list_queue_items(str(batch["id"]))) == 1_000


def test_queue_running_state_recovers_without_duplicate_dispatch(tmp_path: Path) -> None:
    settings = settings_for(tmp_path / "data")
    store = CatalogueStore(settings)
    _client, _project, batch = _catalog(store)
    asset = _asset(store, batch)
    segment = build_segments(str(asset["id"]), 120.0, [])[0]
    segment.accepted = True
    segment.review_state = ReviewState.ACCEPTED
    store.replace_segments(str(asset["id"]), [segment], reason="manual review", detected=False)
    item = store.enqueue_segments(str(batch["id"]), [segment.id])[0]
    store.transition_queue_item(str(item["id"]), QueueState.RUNNING, job_id=str(uuid4()))
    restarted = CatalogueStore(settings)
    assert restarted.recover_queue() == 1
    recovered = restarted.get_queue_item(str(item["id"]))
    assert recovered["state"] == "queued"
    assert len(restarted.list_queue_items(str(batch["id"]))) == 1


def test_multisignal_boundary_detection_and_unresolved_fallback(tmp_path: Path) -> None:
    before = Observation(0, 0.4, 0.6, 0.3, 0.1, 0.2, 0.1, tuple([1.0] + [0.0] * 11), 0.2, 0.1)
    after = Observation(0, 0.5, 0.1, 0.3, 0.6, 0.8, 0.4, tuple([0.0] * 6 + [1.0] + [0.0] * 5), 0.8, 0.7)
    observations = [
        Observation(float(index), before.rms, before.low_ratio, before.mid_ratio, before.high_ratio,
                    before.centroid, before.flatness, before.chroma, before.onset_density, before.stereo_width)
        for index in range(12)
    ]
    observations.append(
        Observation(12.0, 0.005, 0.3, 0.3, 0.4, 0.5, 0.2, before.chroma, 0.0, 0.3)
    )
    observations.extend(
        Observation(float(index), after.rms, after.low_ratio, after.mid_ratio, after.high_ratio,
                    after.centroid, after.flatness, after.chroma, after.onset_density, after.stereo_width)
        for index in range(13, 28)
    )
    candidates = detect_candidates(observations, 1.0)
    assert candidates
    assert candidates[0].evidence.timbral_change > 0
    assert candidates[0].evidence.harmonic_change > 0
    unresolved = build_segments(str(uuid4()), 120.0, [])
    assert len(unresolved) == 1
    assert unresolved[0].review_state == ReviewState.UNRESOLVED


def test_real_streaming_scan_refines_synthetic_silence_transition(tmp_path: Path) -> None:
    settings = settings_for(tmp_path / "data")
    if settings.ffmpeg_path == "ffmpeg" and shutil.which("ffmpeg") is None:
        pytest.skip("FFmpeg is unavailable")
    sample_rate = 8_000
    track_seconds = 24
    time_axis = np.arange(sample_rate * track_seconds, dtype=np.float64) / sample_rate
    first = 0.24 * np.sin(2 * np.pi * 220 * time_axis)
    second_left = 0.3 * np.sin(2 * np.pi * 740 * time_axis)
    second_right = 0.3 * np.sin(2 * np.pi * 990 * time_axis + 0.7)
    first_stereo = np.column_stack((first, first))
    silence = np.zeros((sample_rate * 4, 2), dtype=np.float64)
    second_stereo = np.column_stack((second_left, second_right))
    source = tmp_path / "synthetic-two-track-set.wav"
    sf.write(source, np.vstack((first_stereo, silence, second_stereo)), sample_rate, subtype="PCM_16")
    result = segment_longform_source(
        str(uuid4()), source, 52.0, settings,
        minimum_expected_seconds=10, maximum_expected_seconds=40,
    )
    assert result.observation_count <= 53
    assert result.peak_buffer_bytes <= 20_000_000
    assert len(result.segments) == 2
    assert abs(result.segments[0].end_seconds - 26.0) <= 4.0
    assert result.segments[1].stable_core_start_seconds >= result.segments[1].start_seconds
    assert result.segments[0].transition_type in {
        TransitionType.SILENCE_GAP, TransitionType.FADE, TransitionType.HARD_CUT,
    }


def test_exact_twelve_hour_probe_contract(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    settings = settings_for(tmp_path / "data")
    source = tmp_path / "tiny.bin"
    source.write_bytes(b"x")

    def probe_payload(duration: float) -> dict[str, object]:
        return {
            "streams": [{"codec_type": "audio", "codec_name": "flac", "sample_rate": "48000", "channels": 2}],
            "format": {"format_name": "flac", "duration": str(duration)},
        }

    monkeypatch.setattr("app.media._run_probe", lambda *_args, **_kwargs: probe_payload(43_200))
    accepted = probe_media(
        source,
        "set.flac",
        settings,
        max_bytes=10,
        max_duration_seconds=43_200,
        source_kind="long-form source",
    )
    assert accepted.file.duration_seconds == 43_200
    monkeypatch.setattr("app.media._run_probe", lambda *_args, **_kwargs: probe_payload(43_200.001))
    with pytest.raises(ValueError, match="43200-second"):
        probe_media(
            source,
            "set.flac",
            settings,
            max_bytes=10,
            max_duration_seconds=43_200,
            source_kind="long-form source",
        )


def _analysis_payload(job_id: str, loudness: float, stereo: float) -> dict[str, object]:
    def feature(value: object) -> dict[str, object]:
        return {
            "value": value,
            "confidence": "high",
            "method": "synthetic-test",
            "alternatives": [],
            "evidenceKind": "direct_measurement",
            "userEdited": False,
            "userAccepted": False,
        }

    return {
        "schemaVersion": "1.4.0",
        "analysisVersion": "0.5.0",
        "jobId": job_id,
        "rhythm": {"bpm": feature(120.0), "onsetDensity": feature(0.5)},
        "harmony": {"key": feature("C"), "mode": feature("minor")},
        "signalQuality": {"phaseCorrelation": feature(0.8)},
        "timbre": {"spectralCentroidHz": feature(1800.0)},
        "production": {
            "integratedLoudnessLufs": feature(loudness),
            "loudnessRangeLu": feature(5.0),
            "peakDbfs": feature(-1.0),
            "macroDynamicRangeDb": feature(8.0),
            "stereoWidth": feature(stereo),
            "lowEndWeight": feature("balanced"),
            "highFrequencyBrightness": feature("balanced"),
            "transientEmphasis": feature("moderate"),
            "mixDensity": feature("moderate"),
            "monoCompatibility": feature("good"),
        },
        "vocals": {"presence": feature("present")},
        "warnings": [],
    }


def test_mastering_report_exports_and_backup_restore(tmp_path: Path) -> None:
    settings = settings_for(tmp_path / "data")
    store = CatalogueStore(settings)
    _client, project, batch = _catalog(store)
    asset = _asset(store, batch, duration=180)
    segments = []
    for index in range(3):
        segment = SegmentResponse(
            id=str(uuid4()), source_asset_id=str(asset["id"]), sequence_index=index,
            label=f"Track {index + 1}", start_seconds=index * 60, end_seconds=(index + 1) * 60,
            stable_core_start_seconds=index * 60 + 5, stable_core_end_seconds=(index + 1) * 60 - 5,
            confidence=Confidence.HIGH, transition_type=TransitionType.HARD_CUT,
            review_state=ReviewState.ACCEPTED, accepted=True,
            evidence=SegmentEvidence(timbral_change=0.8), revision=1,
        )
        segments.append(segment)
    store.replace_segments(str(asset["id"]), segments, reason="reviewed", detected=False)
    for index, segment in enumerate(segments):
        payload = _analysis_payload(str(uuid4()), [-14.0, -13.8, -8.0][index], [0.5, 0.52, 0.9][index])
        store.register_artifact(
            project_id=str(project["id"]), batch_id=str(batch["id"]), owner_type="segment",
            owner_id=segment.id, artifact_type="analysis_json", schema_version="1.4.0",
            media_type="application/json", content=json.dumps(payload).encode(),
            producer_versions={"trackprompt": "test"}, reason="synthetic test",
        )
    report = build_mastering_report(store, str(batch["id"]))
    assert len(report.tracks) == 3
    assert "integratedLoudnessLufs" in report.tracks[2].outliers
    assert json.loads(report_json(report))["batchId"] == str(batch["id"])
    assert "Sample peak" in report_markdown(report)
    assert report_csv(report).count("\n") == 4
    backup = tmp_path / "backup"
    created = create_backup(settings, backup)
    assert created["valid"] is True
    assert verify_backup(backup)["valid"] is True
    unexpected = backup / "unlisted.bin"
    unexpected.write_bytes(b"not in the manifest")
    assert verify_backup(backup)["valid"] is False
    unexpected.unlink()
    restored_dir = tmp_path / "restored"
    restored = restore_backup(backup, restored_dir, settings=settings)
    assert restored["valid"] is True
    restored_store = CatalogueStore(settings_for(restored_dir))
    assert restored_store.verify_audit(str(project["id"]))["valid"] is True
