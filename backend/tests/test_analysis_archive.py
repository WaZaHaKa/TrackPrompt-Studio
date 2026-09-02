from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.analysis_archive import ANALYSIS_ARCHIVE_MIGRATION_VERSION, AnalysisDependencyError
from app.config import Settings
from app.main import create_app
from app.schemas import AnalysisMode, JobStatus
from app.store import JobStore
from tests.helpers import FIXTURES, analysis_for, settings_for


def _completed_analysis(store: JobStore, *, source: bytes, display_name: str) -> str:
    analysis_id = str(uuid4())
    store.create_job(analysis_id, AnalysisMode.FAST, display_name, True, False)
    directory = store.job_dir(analysis_id)
    (directory / "source.bin").write_bytes(source)
    store.write_json(
        analysis_id,
        "analysis.json",
        {
            "schemaVersion": "1.4.0",
            "file": {
                "durationSeconds": 218.32,
                "container": "wav",
                "codec": "pcm_s16le",
                "sampleRateHz": 48000,
                "channels": 2,
            },
        },
    )
    store.write_json(
        analysis_id,
        "lyrics.json",
        {"schemaVersion": "1.0.0", "segments": [{"text": "private words must not leak"}]},
    )
    store.write_json(analysis_id, "prompt.json", {"schemaVersion": "1.0.0", "prompt": "safe"})
    store.update_job(analysis_id, status=JobStatus.COMPLETED, stage="completed", progress=100)
    store.archive_completed(analysis_id)
    return analysis_id


def test_archive_is_atomic_private_deduplicated_and_readable_without_workspace(
    tmp_path: Path,
) -> None:
    store = JobStore(settings_for(tmp_path / "data"))
    first = _completed_analysis(store, source=b"same source bytes", display_name="First.wav")
    second = _completed_analysis(store, source=b"same source bytes", display_name="Second.wav")

    with store.archive.connect() as connection:
        version = connection.execute(
            "SELECT MAX(version) FROM schema_migrations WHERE component='analysis_archive'"
        ).fetchone()[0]
        blob_rows = connection.execute(
            "SELECT content_sha256, reference_count FROM archive_blobs"
        ).fetchall()
        audit_before = int(connection.execute("SELECT COUNT(*) FROM analysis_audit_events").fetchone()[0])
    assert version == ANALYSIS_ARCHIVE_MIGRATION_VERSION
    assert len(blob_rows) == 1
    assert int(blob_rows[0]["reference_count"]) == 2

    manifest = store.archive._analysis_directory(first) / "manifest.json"
    manifest_text = manifest.read_text(encoding="utf-8")
    manifest_value = json.loads(manifest_text)
    assert manifest_value["retentionPolicy"] == "persistent"
    assert manifest_value["manifestSha256"]
    assert "private words must not leak" not in manifest_text
    assert str(tmp_path) not in manifest_text
    assert not list(manifest.parent.glob("*.partial-*"))

    (store.job_dir(first) / "analysis.json").unlink()
    (store.job_dir(first) / "source.bin").unlink()
    assert store.read_json(first, "analysis.json")["schemaVersion"] == "1.4.0"
    assert store.source_path(first) is not None

    first_pass = store.reconcile_archive()
    second_pass = store.reconcile_archive()
    assert first_pass == second_pass == {"archived": 2, "degraded": 0}
    with store.archive.connect() as connection:
        audit_after = int(connection.execute("SELECT COUNT(*) FROM analysis_audit_events").fetchone()[0])
        assert int(connection.execute("SELECT COUNT(*) FROM archive_blobs").fetchone()[0]) == 1
    assert audit_after == audit_before

    page, total = store.archive.list(search="First", limit=10)
    assert total == 1
    assert page[0]["analysisId"] == first
    assert page[0]["archiveHealth"] == "healthy"
    assert page[0]["retainedAudioAvailable"] is True
    assert page[0]["retentionPolicy"] == "persistent"
    assert second != first


def test_dependency_aware_explicit_delete_blocks_unsnapshotted_video_job(tmp_path: Path) -> None:
    store = JobStore(settings_for(tmp_path / "data"))
    analysis_id = _completed_analysis(store, source=b"source", display_name="Protected.wav")
    store.archive.register_dependency(
        analysis_id,
        dependent_kind="video-generation",
        dependent_id=str(uuid4()),
        snapshot_complete=False,
    )
    with pytest.raises(AnalysisDependencyError):
        store.archive.explicit_delete(analysis_id)
    assert store.archive.get(analysis_id)["status"] == "completed"


def test_explicit_artifact_and_analysis_deletion_remove_private_bytes_and_keep_audit(
    tmp_path: Path,
) -> None:
    store = JobStore(settings_for(tmp_path / "data"))
    analysis_id = _completed_analysis(store, source=b"private source", display_name="Delete.wav")
    archived_lyrics = store.archive.resolve_artifact(analysis_id, "lyrics")
    assert archived_lyrics is not None

    store.delete_json(analysis_id, "lyrics.json")

    assert not archived_lyrics.exists()
    assert store.archive.resolve_artifact(analysis_id, "lyrics") is None
    with store.archive.connect() as connection:
        artifact_event = connection.execute(
            """
            SELECT event_type FROM analysis_audit_events
            WHERE analysis_id=? ORDER BY sequence DESC LIMIT 1
            """,
            (analysis_id,),
        ).fetchone()
    assert artifact_event["event_type"] == "artifact_explicitly_deleted"

    source = store.archive.resolve_source(analysis_id)
    assert source is not None
    store.delete_job(analysis_id)

    assert not source.exists()
    tombstone = store.archive.get(analysis_id)
    assert tombstone is not None
    assert tombstone["status"] == "explicitly_deleted"
    assert tombstone["displayName"] == "Deleted analysis"
    with store.archive.connect() as connection:
        deletion_event = connection.execute(
            """
            SELECT event_type FROM analysis_audit_events
            WHERE analysis_id=? ORDER BY sequence DESC LIMIT 1
            """,
            (analysis_id,),
        ).fetchone()
    assert deletion_event["event_type"] == "analysis_explicitly_deleted"


def test_corrupt_archived_artifact_is_reported_as_degraded(tmp_path: Path) -> None:
    store = JobStore(settings_for(tmp_path / "data"))
    analysis_id = _completed_analysis(store, source=b"source", display_name="Corrupt.wav")
    artifact = store.archive.resolve_artifact(analysis_id, "analysis")
    assert artifact is not None
    artifact.write_bytes(b"corrupt")
    assert store.archive.resolve_artifact(analysis_id, "analysis") is None
    assert store.archive.get(analysis_id)["archiveHealth"] == "degraded"


def test_jobs_schema_retains_ignored_legacy_expiry_column(tmp_path: Path) -> None:
    store = JobStore(settings_for(tmp_path / "data"))
    with sqlite3.connect(store.settings.database_path) as connection:
        columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(jobs)")}
        indexes = {str(row[1]) for row in connection.execute("PRAGMA index_list(jobs)")}
    assert "expires_at" in columns
    assert "retention_policy" in columns
    assert "jobs_expires_at_idx" not in indexes


def test_analysis_library_api_survives_workspace_loss_and_exposes_no_private_text(
    tmp_path: Path,
) -> None:
    settings = settings_for(tmp_path / "data")
    store = JobStore(settings)
    source_path = FIXTURES / "120bpm_click.wav"
    analysis_id = _completed_analysis(
        store,
        source=source_path.read_bytes(),
        display_name="Persistent.wav",
    )
    full_analysis = analysis_for(
        source_path,
        tmp_path / "analysis-work",
        display_name="Persistent.wav",
    ).model_copy(update={"job_id": analysis_id})
    store.write_json(
        analysis_id,
        "analysis.json",
        full_analysis.model_dump(mode="json", by_alias=True),
    )
    store.delete_json(analysis_id, "prompt.json")
    shutil.rmtree(store.job_dir(analysis_id))

    with TestClient(create_app(settings)) as client:
        page_response = client.get("/api/analyses?search=Persistent&sort=updated_desc")
        assert page_response.status_code == 200, page_response.text
        page = page_response.json()
        assert page["total"] == 1
        assert page["items"][0]["analysisId"] == analysis_id
        assert page["items"][0]["archiveHealth"] == "healthy"
        assert "private words must not leak" not in page_response.text
        assert str(tmp_path) not in page_response.text

        analysis_response = client.get(f"/api/analyses/{analysis_id}")
        assert analysis_response.status_code == 200, analysis_response.text
        assert analysis_response.json()["analysis"]["schemaVersion"] == "1.4.0"

        audio_response = client.get(
            f"/api/analyses/{analysis_id}/audio",
            headers={"Range": "bytes=0-6"},
        )
        assert audio_response.status_code == 206, audio_response.text
        assert audio_response.content == source_path.read_bytes()[:7]

        capabilities = client.get("/api/capabilities").json()
        assert capabilities["retentionPolicy"] == "explicit-delete-only"
        assert capabilities["automaticAnalysisDeletionEnabled"] is False
        assert "jobTtlMinutes" not in capabilities


def test_legacy_job_ttl_environment_is_ignored(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TRACKPROMPT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("JOB_TTL_MINUTES", "1")

    settings = Settings.from_env()

    assert not hasattr(settings, "job_ttl_minutes")
