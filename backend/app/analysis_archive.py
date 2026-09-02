from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from .privacy import secure_private_directory, secure_private_file

ANALYSIS_ARCHIVE_SCHEMA_VERSION = "1.0.0"
ANALYSIS_ARCHIVE_MIGRATION_VERSION = 1
RETENTION_POLICY = "persistent"

_CANONICAL_ARTIFACTS = {
    "detected-analysis.json": "detected-analysis",
    "analysis.json": "analysis",
    "detected-lyrics.json": "detected-lyrics",
    "lyrics.json": "lyrics",
    "lyrics-summary.json": "lyrics-summary",
    "prompt.json": "prompt",
    "preferences.json": "preferences",
    "visual-features.json": "visual-features",
    "visual-cues.json": "visual-cues",
    "story-plan.json": "story-plan",
    "shot-plan.json": "shot-plan",
    "art-direction-reviews.json": "art-direction-reviews",
}
_ARTIFACT_FILENAMES = {kind: filename for filename, kind in _CANONICAL_ARTIFACTS.items()}


class AnalysisArchiveError(RuntimeError):
    pass


class AnalysisDependencyError(AnalysisArchiveError):
    pass


def _iso(value: datetime | None = None) -> str:
    return (value or datetime.now(UTC)).astimezone(UTC).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _json_object(path: Path) -> dict[str, Any] | None:
    if not path.is_file() or path.stat().st_size > 20_000_000:
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _bounded_display_name(value: str) -> str:
    normalized = " ".join(value.replace("\x00", " ").split()).strip()
    return (normalized or "Local analysis")[:160]


def _canonical_analysis_id(value: str) -> str:
    try:
        canonical = str(UUID(value))
    except ValueError as exc:
        raise KeyError("Analysis not found") from exc
    if canonical != value.casefold():
        raise KeyError("Analysis not found")
    return canonical


class AnalysisArchiveRepository:
    """Persistent single-track analysis catalogue in TrackPrompt's existing data/database roots."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir.resolve()
        self.database_path = self.data_dir / "trackprompt.sqlite3"
        self.archive_root = self.data_dir / "archive"
        self.analysis_root = self.archive_root / "analyses"
        self.blob_root = self.archive_root / "blobs"
        self._lock = threading.RLock()
        for directory in (self.data_dir, self.archive_root, self.analysis_root, self.blob_root):
            directory.mkdir(parents=True, exist_ok=True)
            secure_private_directory(directory)
        self._migrate()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")
        secure_private_file(self.database_path)
        return connection

    def _migrate(self) -> None:
        with self._lock, self.connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    component TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    applied_at TEXT NOT NULL,
                    PRIMARY KEY(component, version)
                );
                CREATE TABLE IF NOT EXISTS archive_blobs (
                    content_sha256 TEXT PRIMARY KEY,
                    byte_size INTEGER NOT NULL,
                    storage_key TEXT NOT NULL UNIQUE,
                    reference_count INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS analysis_catalogue (
                    analysis_id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    retention_policy TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    archived_at TEXT,
                    source_asset_id TEXT,
                    source_sha256 TEXT,
                    source_byte_size INTEGER,
                    duration_seconds REAL,
                    analysis_schema_version TEXT,
                    archive_health TEXT NOT NULL,
                    story_plan_available INTEGER NOT NULL DEFAULT 0,
                    shot_plan_available INTEGER NOT NULL DEFAULT 0,
                    retained_audio_available INTEGER NOT NULL DEFAULT 0,
                    legacy_missing INTEGER NOT NULL DEFAULT 0,
                    manifest_sha256 TEXT,
                    deleted_at TEXT
                );
                CREATE INDEX IF NOT EXISTS analysis_catalogue_created_idx
                    ON analysis_catalogue(created_at DESC);
                CREATE INDEX IF NOT EXISTS analysis_catalogue_updated_idx
                    ON analysis_catalogue(updated_at DESC);
                CREATE TABLE IF NOT EXISTS analysis_source_assets (
                    asset_id TEXT PRIMARY KEY,
                    analysis_id TEXT NOT NULL UNIQUE REFERENCES analysis_catalogue(analysis_id),
                    content_sha256 TEXT NOT NULL REFERENCES archive_blobs(content_sha256),
                    byte_size INTEGER NOT NULL,
                    media_summary_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS analysis_artifact_revisions (
                    revision_id TEXT PRIMARY KEY,
                    analysis_id TEXT NOT NULL REFERENCES analysis_catalogue(analysis_id),
                    artifact_kind TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    schema_version TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    byte_size INTEGER NOT NULL,
                    storage_key TEXT NOT NULL,
                    current INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(analysis_id, artifact_kind, sha256)
                );
                CREATE INDEX IF NOT EXISTS analysis_artifact_current_idx
                    ON analysis_artifact_revisions(analysis_id, artifact_kind, current);
                CREATE TABLE IF NOT EXISTS analysis_dependencies (
                    analysis_id TEXT NOT NULL REFERENCES analysis_catalogue(analysis_id),
                    dependent_kind TEXT NOT NULL,
                    dependent_id TEXT NOT NULL,
                    snapshot_complete INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(analysis_id, dependent_kind, dependent_id)
                );
                CREATE TABLE IF NOT EXISTS analysis_audit_events (
                    event_id TEXT PRIMARY KEY,
                    analysis_id TEXT NOT NULL REFERENCES analysis_catalogue(analysis_id),
                    sequence INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    previous_event_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL,
                    UNIQUE(analysis_id, sequence)
                );
                """
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO schema_migrations(component, version, applied_at)
                VALUES ('analysis_archive', ?, ?)
                """,
                (ANALYSIS_ARCHIVE_MIGRATION_VERSION, _iso()),
            )
        self._secure_database_files()

    def _secure_database_files(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            path = Path(f"{self.database_path}{suffix}")
            if path.exists():
                secure_private_file(path)

    def _analysis_directory(self, analysis_id: str) -> Path:
        canonical = _canonical_analysis_id(analysis_id)
        destination = (self.analysis_root / canonical).resolve()
        if destination.parent != self.analysis_root.resolve():
            raise KeyError("Analysis not found")
        return destination

    def _blob_destination(self, digest: str) -> tuple[Path, str]:
        relative = Path("blobs") / digest[:2] / f"{digest}.bin"
        destination = (self.archive_root / relative).resolve()
        if self.archive_root.resolve() not in destination.parents:
            raise AnalysisArchiveError("Archive blob identity is invalid")
        return destination, relative.as_posix()

    def _publish_copy(self, source: Path, destination: Path, expected_sha256: str) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        secure_private_directory(destination.parent)
        if destination.is_file():
            if destination.stat().st_size != source.stat().st_size or _sha256(destination) != expected_sha256:
                raise AnalysisArchiveError("Archive hash collision detected")
            return
        temporary = destination.with_name(f".{destination.name}.partial-{uuid4().hex}")
        try:
            shutil.copyfile(source, temporary)
            secure_private_file(temporary)
            if temporary.stat().st_size != source.stat().st_size or _sha256(temporary) != expected_sha256:
                raise AnalysisArchiveError("Archive copy verification failed")
            os.replace(temporary, destination)
            secure_private_file(destination)
        finally:
            temporary.unlink(missing_ok=True)

    def _append_audit(
        self,
        connection: sqlite3.Connection,
        analysis_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        previous = connection.execute(
            """
            SELECT sequence, event_hash FROM analysis_audit_events
            WHERE analysis_id = ? ORDER BY sequence DESC LIMIT 1
            """,
            (analysis_id,),
        ).fetchone()
        sequence = int(previous["sequence"]) + 1 if previous is not None else 1
        previous_hash = str(previous["event_hash"]) if previous is not None else "0" * 64
        timestamp = _iso()
        event_payload = {
            "analysisId": analysis_id,
            "sequence": sequence,
            "eventType": event_type,
            "timestamp": timestamp,
            "payload": payload,
            "previousEventHash": previous_hash,
        }
        event_hash = _canonical_sha256(event_payload)
        connection.execute(
            """
            INSERT INTO analysis_audit_events(
                event_id, analysis_id, sequence, event_type, timestamp,
                payload_json, previous_event_hash, event_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid4()),
                analysis_id,
                sequence,
                event_type,
                timestamp,
                json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                previous_hash,
                event_hash,
            ),
        )

    def archive_completed(
        self,
        *,
        analysis_id: str,
        display_name: str,
        created_at: datetime,
        updated_at: datetime,
        job_directory: Path,
    ) -> dict[str, Any]:
        canonical = _canonical_analysis_id(analysis_id)
        source = job_directory / "source.bin"
        analysis_path = job_directory / "analysis.json"
        analysis_value = _json_object(analysis_path)
        if not source.is_file() or analysis_value is None:
            raise AnalysisArchiveError("Completed analysis is missing canonical source or analysis data")
        source_sha256 = _sha256(source)
        blob_destination, blob_storage_key = self._blob_destination(source_sha256)
        self._publish_copy(source, blob_destination, source_sha256)

        file_summary = analysis_value.get("file")
        safe_file = file_summary if isinstance(file_summary, dict) else {}
        duration_value = safe_file.get("durationSeconds")
        duration_seconds = float(duration_value) if isinstance(duration_value, (int, float)) else None
        media_summary = {
            "durationSeconds": duration_seconds,
            "container": str(safe_file.get("container", "unknown"))[:80],
            "codec": str(safe_file.get("codec", "unknown"))[:80],
            "sampleRateHz": int(safe_file.get("sampleRateHz", 0) or 0),
            "channels": int(safe_file.get("channels", 0) or 0),
        }
        archive_directory = self._analysis_directory(canonical)
        archive_directory.mkdir(parents=True, exist_ok=True)
        secure_private_directory(archive_directory)
        artifact_entries: list[dict[str, Any]] = []
        artifact_rows: list[dict[str, Any]] = []
        for filename, kind in _CANONICAL_ARTIFACTS.items():
            artifact_source = job_directory / filename
            if not artifact_source.is_file():
                continue
            digest = _sha256(artifact_source)
            relative = Path("analyses") / canonical / "artifacts" / kind / f"{digest}.json"
            artifact_destination = self.archive_root / relative
            self._publish_copy(artifact_source, artifact_destination, digest)
            parsed = _json_object(artifact_source)
            schema_version = str(parsed.get("schemaVersion", "unknown"))[:80] if parsed else "unknown"
            row = {
                "revisionId": f"{canonical}:{kind}:{digest}",
                "kind": kind,
                "filename": filename,
                "schemaVersion": schema_version,
                "sha256": digest,
                "byteSize": artifact_source.stat().st_size,
                "storageKey": relative.as_posix(),
            }
            artifact_rows.append(row)
            artifact_entries.append({key: value for key, value in row.items() if key != "storageKey"})

        with self._lock, self.connect() as connection:
            dependency_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM analysis_dependencies WHERE analysis_id = ?",
                    (canonical,),
                ).fetchone()[0]
            )
            existing = connection.execute(
                "SELECT source_sha256, manifest_sha256, archived_at FROM analysis_catalogue WHERE analysis_id = ?",
                (canonical,),
            ).fetchone()
            if existing is not None and existing["source_sha256"] not in {None, source_sha256}:
                raise AnalysisArchiveError("Analysis source identity changed after archival")
            asset_id = f"analysis-source-{canonical}"
            archived_at = (
                str(existing["archived_at"])
                if existing is not None and existing["archived_at"]
                else _iso()
            )
            manifest_base = {
                "schemaVersion": ANALYSIS_ARCHIVE_SCHEMA_VERSION,
                "analysisId": canonical,
                "displayName": _bounded_display_name(display_name),
                "createdAt": _iso(created_at),
                "archivedAt": archived_at,
                "status": "completed",
                "retentionPolicy": RETENTION_POLICY,
                "sourceAssetId": asset_id,
                "sourceSha256": source_sha256,
                "sourceByteSize": source.stat().st_size,
                "sourceMediaSummary": media_summary,
                "analysisSchemaVersion": str(analysis_value.get("schemaVersion", "unknown"))[:80],
                "artifacts": artifact_entries,
                "dependencySummary": {"dependentVideoJobCount": dependency_count},
            }
            manifest_sha256 = _canonical_sha256(manifest_base)
            manifest = {**manifest_base, "manifestSha256": manifest_sha256}
            manifest_path = archive_directory / "manifest.json"
            temporary = manifest_path.with_name(f".{manifest_path.name}.partial-{uuid4().hex}")
            temporary.write_text(
                json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False),
                encoding="utf-8",
            )
            secure_private_file(temporary)
            if _canonical_sha256({key: value for key, value in manifest.items() if key != "manifestSha256"}) != manifest_sha256:
                temporary.unlink(missing_ok=True)
                raise AnalysisArchiveError("Analysis manifest verification failed")
            os.replace(temporary, manifest_path)
            secure_private_file(manifest_path)

            now = _iso()
            connection.execute(
                """
                INSERT INTO analysis_catalogue(
                    analysis_id, display_name, status, retention_policy, created_at, updated_at,
                    archived_at, source_asset_id, source_sha256, source_byte_size, duration_seconds,
                    analysis_schema_version, archive_health, story_plan_available,
                    shot_plan_available, retained_audio_available, legacy_missing,
                    manifest_sha256, deleted_at
                ) VALUES (?, ?, 'completed', ?, ?, ?, ?, ?, ?, ?, ?, ?, 'healthy', ?, ?, 1, 0, ?, NULL)
                ON CONFLICT(analysis_id) DO UPDATE SET
                    display_name=excluded.display_name, status='completed',
                    retention_policy=excluded.retention_policy, updated_at=excluded.updated_at,
                    archived_at=excluded.archived_at, source_asset_id=excluded.source_asset_id,
                    source_sha256=excluded.source_sha256, source_byte_size=excluded.source_byte_size,
                    duration_seconds=excluded.duration_seconds,
                    analysis_schema_version=excluded.analysis_schema_version,
                    archive_health='healthy', story_plan_available=excluded.story_plan_available,
                    shot_plan_available=excluded.shot_plan_available,
                    retained_audio_available=1, legacy_missing=0,
                    manifest_sha256=excluded.manifest_sha256, deleted_at=NULL
                """,
                (
                    canonical,
                    _bounded_display_name(display_name),
                    RETENTION_POLICY,
                    _iso(created_at),
                    _iso(updated_at),
                    manifest["archivedAt"],
                    asset_id,
                    source_sha256,
                    source.stat().st_size,
                    duration_seconds,
                    manifest_base["analysisSchemaVersion"],
                    int(any(row["kind"] == "story-plan" for row in artifact_rows)),
                    int(any(row["kind"] == "shot-plan" for row in artifact_rows)),
                    manifest_sha256,
                ),
            )
            source_asset = connection.execute(
                "SELECT content_sha256 FROM analysis_source_assets WHERE analysis_id = ?",
                (canonical,),
            ).fetchone()
            if source_asset is None:
                blob = connection.execute(
                    "SELECT byte_size, storage_key FROM archive_blobs WHERE content_sha256 = ?",
                    (source_sha256,),
                ).fetchone()
                if blob is None:
                    connection.execute(
                        """
                        INSERT INTO archive_blobs(content_sha256, byte_size, storage_key, reference_count, created_at)
                        VALUES (?, ?, ?, 1, ?)
                        """,
                        (source_sha256, source.stat().st_size, blob_storage_key, now),
                    )
                else:
                    if int(blob["byte_size"]) != source.stat().st_size or str(blob["storage_key"]) != blob_storage_key:
                        raise AnalysisArchiveError("Archive blob metadata conflicts with source identity")
                    connection.execute(
                        "UPDATE archive_blobs SET reference_count = reference_count + 1 WHERE content_sha256 = ?",
                        (source_sha256,),
                    )
                connection.execute(
                    """
                    INSERT INTO analysis_source_assets(
                        asset_id, analysis_id, content_sha256, byte_size, media_summary_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        asset_id,
                        canonical,
                        source_sha256,
                        source.stat().st_size,
                        json.dumps(media_summary, sort_keys=True, separators=(",", ":")),
                        now,
                    ),
                )
            for row in artifact_rows:
                connection.execute(
                    """
                    UPDATE analysis_artifact_revisions SET current = 0
                    WHERE analysis_id = ? AND artifact_kind = ?
                    """,
                    (canonical, row["kind"]),
                )
                connection.execute(
                    """
                    INSERT INTO analysis_artifact_revisions(
                        revision_id, analysis_id, artifact_kind, filename, schema_version,
                        sha256, byte_size, storage_key, current, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                    ON CONFLICT(analysis_id, artifact_kind, sha256) DO UPDATE SET current=1
                    """,
                    (
                        row["revisionId"],
                        canonical,
                        row["kind"],
                        row["filename"],
                        row["schemaVersion"],
                        row["sha256"],
                        row["byteSize"],
                        row["storageKey"],
                        now,
                    ),
                )
            if existing is None or str(existing["manifest_sha256"] or "") != manifest_sha256:
                self._append_audit(
                    connection,
                    canonical,
                    "analysis_archived",
                    {
                        "manifestSha256": manifest_sha256,
                        "sourceSha256": source_sha256,
                        "artifactCount": len(artifact_rows),
                        "retentionPolicy": RETENTION_POLICY,
                    },
                )
        self._secure_database_files()
        return manifest

    def publish_artifact(self, analysis_id: str, job_directory: Path, filename: str) -> None:
        if filename not in _CANONICAL_ARTIFACTS:
            return
        entry = self.get(analysis_id)
        if entry is None or entry["status"] != "completed":
            return
        created_at = datetime.fromisoformat(str(entry["createdAt"]))
        updated_at = datetime.fromisoformat(str(entry["updatedAt"]))
        self.archive_completed(
            analysis_id=analysis_id,
            display_name=str(entry["displayName"]),
            created_at=created_at,
            updated_at=updated_at,
            job_directory=job_directory,
        )

    def remove_artifact(self, analysis_id: str, filename: str) -> None:
        """Permanently remove every archived revision of an explicitly deleted artifact."""
        artifact_kind = _CANONICAL_ARTIFACTS.get(filename)
        if artifact_kind is None:
            return
        canonical = _canonical_analysis_id(analysis_id)
        manifest_path = self._analysis_directory(canonical) / "manifest.json"
        with self._lock, self.connect() as connection:
            rows = connection.execute(
                """
                SELECT storage_key FROM analysis_artifact_revisions
                WHERE analysis_id = ? AND artifact_kind = ?
                """,
                (canonical, artifact_kind),
            ).fetchall()
            if not rows:
                return
            for row in rows:
                path = (self.archive_root / str(row["storage_key"])).resolve()
                if self.archive_root.resolve() not in path.parents:
                    raise AnalysisArchiveError("Archived artifact identity is invalid")
                path.unlink(missing_ok=True)
                if path.exists():
                    raise AnalysisArchiveError("Archived artifact could not be explicitly deleted")

            manifest_sha256: str | None = None
            if manifest_path.is_file():
                manifest = _json_object(manifest_path)
                if manifest is None:
                    raise AnalysisArchiveError("Analysis manifest is invalid")
                artifacts = manifest.get("artifacts")
                manifest["artifacts"] = [
                    item
                    for item in artifacts if isinstance(item, dict) and item.get("kind") != artifact_kind
                ] if isinstance(artifacts, list) else []
                manifest_base = {
                    key: value for key, value in manifest.items() if key != "manifestSha256"
                }
                manifest_sha256 = _canonical_sha256(manifest_base)
                manifest["manifestSha256"] = manifest_sha256
                temporary = manifest_path.with_name(
                    f".{manifest_path.name}.partial-{uuid4().hex}"
                )
                try:
                    temporary.write_text(
                        json.dumps(
                            manifest,
                            ensure_ascii=False,
                            sort_keys=True,
                            indent=2,
                            allow_nan=False,
                        ),
                        encoding="utf-8",
                    )
                    secure_private_file(temporary)
                    os.replace(temporary, manifest_path)
                    secure_private_file(manifest_path)
                finally:
                    temporary.unlink(missing_ok=True)

            connection.execute(
                """
                DELETE FROM analysis_artifact_revisions
                WHERE analysis_id = ? AND artifact_kind = ?
                """,
                (canonical, artifact_kind),
            )
            availability_column = {
                "story-plan": "story_plan_available",
                "shot-plan": "shot_plan_available",
            }.get(artifact_kind)
            if availability_column is not None:
                connection.execute(
                    f"UPDATE analysis_catalogue SET {availability_column}=0 WHERE analysis_id=?",
                    (canonical,),
                )
            if manifest_sha256 is not None:
                connection.execute(
                    "UPDATE analysis_catalogue SET manifest_sha256=?, updated_at=? WHERE analysis_id=?",
                    (manifest_sha256, _iso(), canonical),
                )
            self._append_audit(
                connection,
                canonical,
                "artifact_explicitly_deleted",
                {"artifactKind": artifact_kind, "revisionCount": len(rows)},
            )
        self._secure_database_files()

    def resolve_artifact(self, analysis_id: str, artifact_kind: str) -> Path | None:
        canonical = _canonical_analysis_id(analysis_id)
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT storage_key, sha256 FROM analysis_artifact_revisions
                WHERE analysis_id = ? AND artifact_kind = ? AND current = 1
                ORDER BY created_at DESC LIMIT 1
                """,
                (canonical, artifact_kind),
            ).fetchone()
        if row is None:
            return None
        path = (self.archive_root / str(row["storage_key"])).resolve()
        if self.archive_root.resolve() not in path.parents or not path.is_file():
            return None
        if _sha256(path) != str(row["sha256"]):
            self.mark_degraded(canonical, "artifact_hash_mismatch")
            return None
        return path

    def resolve_source(self, analysis_id: str) -> Path | None:
        canonical = _canonical_analysis_id(analysis_id)
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT b.storage_key, b.content_sha256 FROM analysis_source_assets a
                JOIN archive_blobs b ON b.content_sha256 = a.content_sha256
                WHERE a.analysis_id = ?
                """,
                (canonical,),
            ).fetchone()
        if row is None:
            return None
        path = (self.archive_root / str(row["storage_key"])).resolve()
        if self.archive_root.resolve() not in path.parents or not path.is_file():
            return None
        if _sha256(path) != str(row["content_sha256"]):
            self.mark_degraded(canonical, "source_hash_mismatch")
            return None
        return path

    def register_dependency(
        self,
        analysis_id: str,
        *,
        dependent_kind: str,
        dependent_id: str,
        snapshot_complete: bool,
    ) -> None:
        canonical = _canonical_analysis_id(analysis_id)
        now = _iso()
        with self._lock, self.connect() as connection:
            if connection.execute(
                "SELECT 1 FROM analysis_catalogue WHERE analysis_id = ?", (canonical,)
            ).fetchone() is None:
                connection.execute(
                    """
                    INSERT INTO analysis_catalogue(
                        analysis_id, display_name, status, retention_policy, created_at, updated_at,
                        archive_health, story_plan_available, shot_plan_available,
                        retained_audio_available, legacy_missing
                    ) VALUES (?, ?, 'legacy_missing', ?, ?, ?, 'missing', 0, 0, 0, 1)
                    """,
                    (canonical, f"Legacy analysis {canonical[:8]}", RETENTION_POLICY, now, now),
                )
            connection.execute(
                """
                INSERT INTO analysis_dependencies(
                    analysis_id, dependent_kind, dependent_id, snapshot_complete, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(analysis_id, dependent_kind, dependent_id) DO UPDATE SET
                    snapshot_complete=excluded.snapshot_complete, updated_at=excluded.updated_at
                """,
                (canonical, dependent_kind[:80], dependent_id[:160], int(snapshot_complete), now, now),
            )

    def register_job(
        self,
        analysis_id: str,
        *,
        display_name: str,
        status: str,
        created_at: datetime,
        updated_at: datetime,
    ) -> None:
        canonical = _canonical_analysis_id(analysis_id)
        with self._lock, self.connect() as connection:
            connection.execute(
                """
                INSERT INTO analysis_catalogue(
                    analysis_id, display_name, status, retention_policy, created_at, updated_at,
                    archive_health, story_plan_available, shot_plan_available,
                    retained_audio_available, legacy_missing
                ) VALUES (?, ?, ?, ?, ?, ?, 'pending', 0, 0, 0, 0)
                ON CONFLICT(analysis_id) DO UPDATE SET
                    display_name=excluded.display_name, status=excluded.status,
                    retention_policy=excluded.retention_policy, updated_at=excluded.updated_at,
                    deleted_at=NULL
                """,
                (
                    canonical,
                    _bounded_display_name(display_name),
                    status[:80],
                    RETENTION_POLICY,
                    _iso(created_at),
                    _iso(updated_at),
                ),
            )

    def update_lifecycle(self, analysis_id: str, *, status: str, updated_at: datetime) -> None:
        canonical = _canonical_analysis_id(analysis_id)
        with self._lock, self.connect() as connection:
            connection.execute(
                """
                UPDATE analysis_catalogue SET status=?, updated_at=?
                WHERE analysis_id=? AND deleted_at IS NULL
                """,
                (status[:80], _iso(updated_at), canonical),
            )

    def register_legacy_tombstone(self, analysis_id: str, *, video_job_id: str) -> None:
        self.register_dependency(
            analysis_id,
            dependent_kind="video-generation",
            dependent_id=video_job_id,
            snapshot_complete=True,
        )

    def mark_degraded(self, analysis_id: str, reason: str) -> None:
        canonical = _canonical_analysis_id(analysis_id)
        with self._lock, self.connect() as connection:
            connection.execute(
                "UPDATE analysis_catalogue SET archive_health='degraded', updated_at=? WHERE analysis_id=?",
                (_iso(), canonical),
            )
            self._append_audit(connection, canonical, "archive_degraded", {"reason": reason[:160]})

    def get(self, analysis_id: str) -> dict[str, Any] | None:
        try:
            canonical = _canonical_analysis_id(analysis_id)
        except KeyError:
            return None
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT c.*, (
                    SELECT COUNT(*) FROM analysis_dependencies d
                    WHERE d.analysis_id = c.analysis_id AND d.dependent_kind = 'video-generation'
                ) AS dependent_video_job_count, (
                    SELECT COUNT(*) FROM analysis_dependencies d
                    WHERE d.analysis_id = c.analysis_id AND d.snapshot_complete = 0
                ) AS unsnapshotted_dependency_count
                FROM analysis_catalogue c WHERE c.analysis_id = ?
                """,
                (canonical,),
            ).fetchone()
        return self._row_view(row) if row is not None else None

    @staticmethod
    def _row_view(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "analysisId": str(row["analysis_id"]),
            "displayName": str(row["display_name"]),
            "status": str(row["status"]),
            "retentionPolicy": str(row["retention_policy"]),
            "createdAt": str(row["created_at"]),
            "updatedAt": str(row["updated_at"]),
            "archivedAt": str(row["archived_at"]) if row["archived_at"] else None,
            "durationSeconds": float(row["duration_seconds"]) if row["duration_seconds"] is not None else None,
            "analysisSchemaVersion": str(row["analysis_schema_version"]) if row["analysis_schema_version"] else None,
            "archiveHealth": str(row["archive_health"]),
            "retainedAudioAvailable": bool(row["retained_audio_available"]),
            "analysisAvailable": row["manifest_sha256"] is not None and str(row["archive_health"]) != "missing",
            "storyPlanAvailable": bool(row["story_plan_available"]),
            "shotPlanAvailable": bool(row["shot_plan_available"]),
            "dependentVideoJobCount": int(row["dependent_video_job_count"]),
            "explicitDeleteEligible": int(row["unsnapshotted_dependency_count"]) == 0,
            "legacyMissing": bool(row["legacy_missing"]),
            "deletedAt": str(row["deleted_at"]) if row["deleted_at"] else None,
        }

    def list(
        self,
        *,
        search: str = "",
        status: str | None = None,
        archive_health: str | None = None,
        sort: str = "created_desc",
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[dict[str, Any]], int]:
        clauses: list[str] = []
        parameters: list[object] = []
        normalized_search = search.strip()[:160]
        if normalized_search:
            clauses.append("(c.display_name LIKE ? ESCAPE '\\' OR c.analysis_id LIKE ? ESCAPE '\\')")
            escaped = normalized_search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            parameters.extend((f"%{escaped}%", f"%{escaped}%"))
        if status:
            clauses.append("c.status = ?")
            parameters.append(status[:80])
        if archive_health:
            clauses.append("c.archive_health = ?")
            parameters.append(archive_health[:80])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        order = {
            "created_asc": "c.created_at ASC",
            "updated_desc": "c.updated_at DESC",
            "updated_asc": "c.updated_at ASC",
            "display_name_asc": "c.display_name COLLATE NOCASE ASC",
            "display_name_desc": "c.display_name COLLATE NOCASE DESC",
        }.get(sort, "c.created_at DESC")
        bounded_offset = max(0, offset)
        bounded_limit = max(1, min(200, limit))
        with self.connect() as connection:
            total = int(connection.execute(
                f"SELECT COUNT(*) FROM analysis_catalogue c {where}", parameters
            ).fetchone()[0])
            rows = connection.execute(
                f"""
                SELECT c.*, (
                    SELECT COUNT(*) FROM analysis_dependencies d
                    WHERE d.analysis_id = c.analysis_id AND d.dependent_kind = 'video-generation'
                ) AS dependent_video_job_count, (
                    SELECT COUNT(*) FROM analysis_dependencies d
                    WHERE d.analysis_id = c.analysis_id AND d.snapshot_complete = 0
                ) AS unsnapshotted_dependency_count
                FROM analysis_catalogue c {where}
                ORDER BY {order} LIMIT ? OFFSET ?
                """,
                (*parameters, bounded_limit, bounded_offset),
            ).fetchall()
        return [self._row_view(row) for row in rows], total

    def counts(self) -> dict[str, int]:
        with self.connect() as connection:
            analyses = int(connection.execute(
                "SELECT COUNT(*) FROM analysis_catalogue WHERE deleted_at IS NULL"
            ).fetchone()[0])
            sources = int(connection.execute("SELECT COUNT(*) FROM analysis_source_assets").fetchone()[0])
            blobs = int(connection.execute("SELECT COUNT(*) FROM archive_blobs").fetchone()[0])
        return {"analyses": analyses, "sourceAssets": sources, "sourceBlobs": blobs}

    def explicit_delete(self, analysis_id: str) -> None:
        canonical = _canonical_analysis_id(analysis_id)
        removable_blob: Path | None = None
        analysis_directory = self._analysis_directory(canonical)
        with self._lock, self.connect() as connection:
            blockers = connection.execute(
                """
                SELECT dependent_kind, dependent_id FROM analysis_dependencies
                WHERE analysis_id = ? AND snapshot_complete = 0
                """,
                (canonical,),
            ).fetchall()
            if blockers:
                raise AnalysisDependencyError(
                    "Dependent video work must receive an immutable snapshot before this analysis can be deleted"
                )
            entry = connection.execute(
                "SELECT * FROM analysis_catalogue WHERE analysis_id = ?", (canonical,)
            ).fetchone()
            if entry is None:
                raise KeyError("Analysis not found")
            artifact_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM analysis_artifact_revisions WHERE analysis_id = ?",
                    (canonical,),
                ).fetchone()[0]
            )
            dependency_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM analysis_dependencies WHERE analysis_id = ?",
                    (canonical,),
                ).fetchone()[0]
            )
            source = connection.execute(
                "SELECT content_sha256 FROM analysis_source_assets WHERE analysis_id = ?", (canonical,)
            ).fetchone()
            if source is not None:
                digest = str(source["content_sha256"])
                blob = connection.execute(
                    "SELECT reference_count, storage_key FROM archive_blobs WHERE content_sha256 = ?",
                    (digest,),
                ).fetchone()
                connection.execute("DELETE FROM analysis_source_assets WHERE analysis_id = ?", (canonical,))
                if blob is not None:
                    remaining = int(blob["reference_count"]) - 1
                    if remaining <= 0:
                        removable_blob = (self.archive_root / str(blob["storage_key"])).resolve()
                        connection.execute("DELETE FROM archive_blobs WHERE content_sha256 = ?", (digest,))
                    else:
                        connection.execute(
                            "UPDATE archive_blobs SET reference_count = ? WHERE content_sha256 = ?",
                            (remaining, digest),
                        )
            connection.execute("DELETE FROM analysis_artifact_revisions WHERE analysis_id = ?", (canonical,))
            connection.execute("DELETE FROM analysis_dependencies WHERE analysis_id = ?", (canonical,))
            deleted_at = _iso()
            self._append_audit(
                connection,
                canonical,
                "analysis_explicitly_deleted",
                {
                    "artifactRevisionCount": artifact_count,
                    "dependencyCount": dependency_count,
                    "sourceRetainedByAnotherAnalysis": (
                        source is not None and removable_blob is None
                    ),
                },
            )
            connection.execute(
                """
                UPDATE analysis_catalogue SET display_name='Deleted analysis', status='explicitly_deleted',
                    updated_at=?, archived_at=NULL, source_asset_id=NULL, source_sha256=NULL,
                    source_byte_size=NULL, duration_seconds=NULL, analysis_schema_version=NULL,
                    archive_health='deleted', story_plan_available=0, shot_plan_available=0,
                    retained_audio_available=0, legacy_missing=0, manifest_sha256=NULL, deleted_at=?
                WHERE analysis_id=?
                """,
                (deleted_at, deleted_at, canonical),
            )
        if analysis_directory.is_dir():
            shutil.rmtree(analysis_directory)
        if removable_blob is not None and removable_blob.is_file():
            if self.archive_root.resolve() in removable_blob.parents:
                removable_blob.unlink()
        self._secure_database_files()
