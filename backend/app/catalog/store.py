from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import threading
from collections import Counter
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

from ..config import Settings
from ..privacy import secure_private_directory, secure_private_file
from . import AUDIT_SCHEMA_VERSION
from .schemas import (
    BatchCreate,
    BatchPatch,
    BatchState,
    ClientCreate,
    ClientPatch,
    ProjectCreate,
    ProjectPatch,
    QueueState,
    RetentionPolicy,
    SegmentationJobState,
    SegmentResponse,
    UploadSessionCreate,
    UploadState,
)


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso(value: datetime | None = None) -> str:
    return (value or utc_now()).astimezone(UTC).isoformat()


def canonical_id(value: str) -> str:
    try:
        parsed = UUID(value)
    except ValueError as exc:
        raise KeyError("Catalogue entity was not found") from exc
    canonical = str(parsed)
    if canonical != value.lower():
        raise KeyError("Catalogue entity was not found")
    return canonical


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _decode_json(value: Any, default: Any) -> Any:
    if not isinstance(value, str):
        return default
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return default
    return parsed


class CatalogueConflict(RuntimeError):
    pass


class StorageAdmissionError(RuntimeError):
    pass


class CatalogueStore:
    """Versioned private catalogue and append-only audit journal.

    Public records store relative UUID/hash-derived storage keys only. Callers
    never accept or return a client-provided filesystem path.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.settings.ensure_directories()
        self._write_lock = threading.RLock()
        self._initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.settings.database_path, timeout=20)
        secure_private_file(self.settings.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 20000")
        return connection

    def _initialize(self) -> None:
        migrations: list[tuple[int, str]] = [
            (1, _MIGRATION_1),
            (2, _MIGRATION_2),
            (3, _MIGRATION_3),
        ]
        with self._write_lock, self.connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    component TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    applied_at TEXT NOT NULL,
                    PRIMARY KEY (component, version)
                )
                """
            )
            applied = {
                int(row[0])
                for row in connection.execute(
                    "SELECT version FROM schema_migrations WHERE component = 'catalogue'"
                )
            }
            for version, script in migrations:
                if version in applied:
                    continue
                connection.executescript(script)
                connection.execute(
                    "INSERT INTO schema_migrations(component, version, applied_at) VALUES ('catalogue', ?, ?)",
                    (version, iso()),
                )
        self._secure_database_files()

    def _secure_database_files(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            path = Path(f"{self.settings.database_path}{suffix}")
            if path.exists():
                secure_private_file(path)

    def migration_version(self) -> int:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(version), 0) FROM schema_migrations WHERE component = 'catalogue'"
            ).fetchone()
        return int(row[0]) if row else 0

    @staticmethod
    def _require(connection: sqlite3.Connection, table: str, entity_id: str) -> sqlite3.Row:
        if table not in {"clients", "projects", "batches", "source_assets", "upload_sessions", "segments"}:
            raise ValueError("Unsupported catalogue table")
        row = connection.execute(f"SELECT * FROM {table} WHERE id = ?", (canonical_id(entity_id),)).fetchone()
        if row is None:
            raise KeyError("Catalogue entity was not found")
        return cast(sqlite3.Row, row)

    def append_audit(
        self,
        connection: sqlite3.Connection,
        *,
        project_id: str,
        batch_id: str | None,
        entity_type: str,
        entity_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
        actor_type: str = "local_user",
        request_id: str | None = None,
        correlation_id: str | None = None,
    ) -> str:
        safe_payload = payload or {}
        serialized_payload = _json(safe_payload)
        if len(serialized_payload.encode("utf-8")) > 64 * 1024:
            raise ValueError("Audit payload exceeds the 64 KiB safety limit")
        previous = connection.execute(
            "SELECT sequence, event_hash FROM audit_events WHERE project_id = ? ORDER BY sequence DESC LIMIT 1",
            (project_id,),
        ).fetchone()
        sequence = int(previous["sequence"]) + 1 if previous else 1
        previous_hash = str(previous["event_hash"]) if previous else "0" * 64
        event_id = str(uuid4())
        timestamp = iso()
        correlation = correlation_id or str(uuid4())
        canonical = {
            "actorType": actor_type,
            "batchId": batch_id,
            "correlationId": correlation,
            "entityId": entity_id,
            "entityType": entity_type,
            "eventId": event_id,
            "eventType": event_type,
            "payload": safe_payload,
            "previousEventHash": previous_hash,
            "projectId": project_id,
            "requestId": request_id,
            "schemaVersion": AUDIT_SCHEMA_VERSION,
            "sequence": sequence,
            "timestamp": timestamp,
        }
        event_hash = hashlib.sha256(_json(canonical).encode("utf-8")).hexdigest()
        connection.execute(
            """
            INSERT INTO audit_events(
                event_id, timestamp, sequence, project_id, batch_id, entity_type, entity_id,
                event_type, actor_type, request_id, correlation_id, schema_version, payload_json,
                previous_event_hash, event_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id, timestamp, sequence, project_id, batch_id, entity_type, entity_id,
                event_type, actor_type, request_id, correlation, AUDIT_SCHEMA_VERSION,
                serialized_payload, previous_hash, event_hash,
            ),
        )
        return event_id

    def create_client(self, request: ClientCreate) -> dict[str, Any]:
        client_id = str(uuid4())
        now = iso()
        with self._write_lock, self.connect() as connection:
            connection.execute(
                "INSERT INTO clients(id, display_name, private_notes, tags_json, archived, created_at, updated_at) VALUES (?, ?, ?, ?, 0, ?, ?)",
                (client_id, request.display_name, request.private_notes, _json(request.tags), now, now),
            )
        return self.get_client(client_id)

    def get_client(self, client_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = self._require(connection, "clients", client_id)
            count = connection.execute(
                "SELECT COUNT(*) FROM projects WHERE client_id = ?", (row["id"],)
            ).fetchone()
        result = dict(row)
        result["tags"] = _decode_json(result.pop("tags_json"), [])
        result["project_count"] = int(count[0]) if count else 0
        result["archived"] = bool(result["archived"])
        return result

    def list_clients(self, *, search: str, offset: int, limit: int) -> tuple[list[dict[str, Any]], int]:
        pattern = f"%{search.casefold()}%"
        with self.connect() as connection:
            total = int(connection.execute(
                "SELECT COUNT(*) FROM clients WHERE lower(display_name) LIKE ?", (pattern,)
            ).fetchone()[0])
            rows = connection.execute(
                "SELECT id FROM clients WHERE lower(display_name) LIKE ? ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                (pattern, limit, offset),
            ).fetchall()
        return [self.get_client(str(row["id"])) for row in rows], total

    def patch_client(self, client_id: str, patch: ClientPatch) -> dict[str, Any]:
        values = patch.model_dump(exclude_unset=True)
        with self._write_lock, self.connect() as connection:
            row = self._require(connection, "clients", client_id)
            connection.execute(
                """
                UPDATE clients SET display_name = ?, private_notes = ?, tags_json = ?, archived = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    values.get("display_name", row["display_name"]),
                    values.get("private_notes", row["private_notes"]),
                    _json(values.get("tags", _decode_json(row["tags_json"], []))),
                    int(values.get("archived", bool(row["archived"]))), iso(), row["id"],
                ),
            )
        return self.get_client(client_id)

    def create_project(self, request: ProjectCreate) -> dict[str, Any]:
        project_id = str(uuid4())
        now = iso()
        with self._write_lock, self.connect() as connection:
            self._require(connection, "clients", request.client_id)
            connection.execute(
                """
                INSERT INTO projects(id, client_id, name, description, status, retention_policy,
                    retention_until, tags_json, archived_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
                """,
                (
                    project_id, request.client_id, request.name, request.description, request.status,
                    request.retention_policy.value, iso(request.retention_until) if request.retention_until else None,
                    _json(request.tags), now, now,
                ),
            )
            self.append_audit(
                connection, project_id=project_id, batch_id=None, entity_type="project",
                entity_id=project_id, event_type="project.created",
                payload={"retentionPolicy": request.retention_policy.value},
            )
        directory = self.project_dir(project_id)
        directory.mkdir(parents=True, exist_ok=True)
        secure_private_directory(directory)
        return self.get_project(project_id)

    def project_dir(self, project_id: str) -> Path:
        canonical = canonical_id(project_id)
        root = (self.settings.archive_dir / "projects").resolve()
        destination = (root / canonical).resolve()
        if destination.parent != root:
            raise KeyError("Project was not found")
        return destination

    def get_project(self, project_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = self._require(connection, "projects", project_id)
            batch_count = int(connection.execute(
                "SELECT COUNT(*) FROM batches WHERE project_id = ?", (row["id"],)
            ).fetchone()[0])
            storage = int(connection.execute(
                "SELECT COALESCE(SUM(byte_size), 0) FROM source_assets WHERE project_id = ?", (row["id"],)
            ).fetchone()[0])
        result = dict(row)
        result["tags"] = _decode_json(result.pop("tags_json"), [])
        result["batch_count"] = batch_count
        result["storage_bytes"] = storage
        return result

    def list_projects(self, client_id: str, *, offset: int, limit: int) -> tuple[list[dict[str, Any]], int]:
        canonical_id(client_id)
        with self.connect() as connection:
            total = int(connection.execute(
                "SELECT COUNT(*) FROM projects WHERE client_id = ?", (client_id,)
            ).fetchone()[0])
            rows = connection.execute(
                "SELECT id FROM projects WHERE client_id = ? ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                (client_id, limit, offset),
            ).fetchall()
        return [self.get_project(str(row["id"])) for row in rows], total

    def patch_project(self, project_id: str, patch: ProjectPatch) -> dict[str, Any]:
        values = patch.model_dump(exclude_unset=True)
        with self._write_lock, self.connect() as connection:
            row = self._require(connection, "projects", project_id)
            policy = values.get("retention_policy", RetentionPolicy(str(row["retention_policy"])))
            retention_until = values.get("retention_until", row["retention_until"])
            if policy == RetentionPolicy.CUSTOM and retention_until is None:
                raise ValueError("custom retention requires retentionUntil")
            archived_at = row["archived_at"]
            if "archived" in values:
                archived_at = iso() if values["archived"] else None
            connection.execute(
                """
                UPDATE projects SET name = ?, description = ?, status = ?, retention_policy = ?,
                    retention_until = ?, tags_json = ?, archived_at = ?, updated_at = ? WHERE id = ?
                """,
                (
                    values.get("name", row["name"]), values.get("description", row["description"]),
                    values.get("status", row["status"]), policy.value,
                    iso(retention_until) if isinstance(retention_until, datetime) else retention_until,
                    _json(values.get("tags", _decode_json(row["tags_json"], []))), archived_at, iso(), row["id"],
                ),
            )
            self.append_audit(
                connection, project_id=str(row["id"]), batch_id=None, entity_type="project",
                entity_id=str(row["id"]), event_type="project.edited",
                payload={"fields": sorted(values)},
            )
        return self.get_project(project_id)

    def delete_project(self, project_id: str) -> dict[str, Any]:
        """Permanently delete one explicitly selected project and private bytes.

        Shared content-addressed blobs survive while referenced by another
        logical asset. A content-free tombstone remains outside the project
        foreign-key graph so explicit deletion itself is still verifiable.
        """
        project_id = canonical_id(project_id)
        removable_blobs: list[Path] = []
        upload_ids: list[str] = []
        deleted_at = iso()
        with self._write_lock, self.connect() as connection:
            self._require(connection, "projects", project_id)
            running = int(
                connection.execute(
                    "SELECT COUNT(*) FROM queue_items WHERE project_id = ? AND state = 'running'",
                    (project_id,),
                ).fetchone()[0]
            )
            if running:
                raise CatalogueConflict("Cancel active child analyses before deleting this project.")
            asset_rows = connection.execute(
                "SELECT id, content_sha256 FROM source_assets WHERE project_id = ?", (project_id,)
            ).fetchall()
            asset_ids = [str(row["id"]) for row in asset_rows]
            hash_counts = Counter(str(row["content_sha256"]) for row in asset_rows)
            upload_ids = [
                str(row["id"])
                for row in connection.execute(
                    "SELECT id FROM upload_sessions WHERE project_id = ?", (project_id,)
                ).fetchall()
            ]
            counts = {
                "assets": len(asset_rows),
                "batches": int(connection.execute(
                    "SELECT COUNT(*) FROM batches WHERE project_id = ?", (project_id,)
                ).fetchone()[0]),
                "artifacts": int(connection.execute(
                    "SELECT COUNT(*) FROM artifacts WHERE project_id = ?", (project_id,)
                ).fetchone()[0]),
                "auditEvents": int(connection.execute(
                    "SELECT COUNT(*) FROM audit_events WHERE project_id = ?", (project_id,)
                ).fetchone()[0]) + 1,
            }
            deletion_audit_id = self.append_audit(
                connection,
                project_id=project_id,
                batch_id=None,
                entity_type="project",
                entity_id=project_id,
                event_type="project.deletion_requested",
                payload=counts,
            )
            final_audit = connection.execute(
                "SELECT event_hash FROM audit_events WHERE event_id = ?", (deletion_audit_id,)
            ).fetchone()
            tombstone_id = str(uuid4())
            tombstone_payload = {
                "eventId": tombstone_id,
                "projectId": project_id,
                "deletedAt": deleted_at,
                "priorProjectEventHash": str(final_audit["event_hash"]),
                "counts": counts,
            }
            tombstone_hash = hashlib.sha256(_json(tombstone_payload).encode("utf-8")).hexdigest()
            connection.execute(
                """
                INSERT INTO catalogue_deletion_events(
                    event_id, project_id, deleted_at, prior_project_event_hash,
                    counts_json, tombstone_hash
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    tombstone_id,
                    project_id,
                    deleted_at,
                    tombstone_payload["priorProjectEventHash"],
                    _json(counts),
                    tombstone_hash,
                ),
            )
            connection.execute("DELETE FROM queue_items WHERE project_id = ?", (project_id,))
            connection.execute("DELETE FROM segmentation_jobs WHERE project_id = ?", (project_id,))
            if asset_ids:
                placeholders = ",".join("?" for _ in asset_ids)
                connection.execute(
                    f"UPDATE upload_sessions SET duplicate_asset_id = NULL WHERE duplicate_asset_id IN ({placeholders})",
                    asset_ids,
                )
                connection.execute(
                    f"DELETE FROM segment_map_revisions WHERE source_asset_id IN ({placeholders})",
                    asset_ids,
                )
                connection.execute(
                    f"DELETE FROM segments WHERE source_asset_id IN ({placeholders})",
                    asset_ids,
                )
            if upload_ids:
                placeholders = ",".join("?" for _ in upload_ids)
                connection.execute(
                    f"DELETE FROM upload_chunks WHERE upload_id IN ({placeholders})", upload_ids
                )
            artifact_ids = [
                str(row["id"])
                for row in connection.execute(
                    "SELECT id FROM artifacts WHERE project_id = ?", (project_id,)
                ).fetchall()
            ]
            if artifact_ids:
                placeholders = ",".join("?" for _ in artifact_ids)
                connection.execute(
                    f"UPDATE artifacts SET supersedes_id = NULL WHERE supersedes_id IN ({placeholders})",
                    artifact_ids,
                )
            revision_ids = [
                str(row["id"])
                for row in connection.execute(
                    "SELECT id FROM revisions WHERE project_id = ?", (project_id,)
                ).fetchall()
            ]
            if revision_ids:
                placeholders = ",".join("?" for _ in revision_ids)
                connection.execute(
                    f"UPDATE revisions SET parent_revision_id = NULL WHERE parent_revision_id IN ({placeholders})",
                    revision_ids,
                )
            connection.execute("DELETE FROM revisions WHERE project_id = ?", (project_id,))
            connection.execute("DELETE FROM artifacts WHERE project_id = ?", (project_id,))
            connection.execute("DELETE FROM upload_sessions WHERE project_id = ?", (project_id,))
            connection.execute("DELETE FROM source_assets WHERE project_id = ?", (project_id,))
            connection.execute("DELETE FROM batches WHERE project_id = ?", (project_id,))
            connection.execute("DELETE FROM audit_events WHERE project_id = ?", (project_id,))
            connection.execute("DELETE FROM projects WHERE id = ?", (project_id,))
            for digest, count in hash_counts.items():
                blob = connection.execute(
                    "SELECT reference_count, storage_key FROM archive_blobs WHERE content_sha256 = ?",
                    (digest,),
                ).fetchone()
                if blob is None:
                    continue
                remaining = int(blob["reference_count"]) - count
                if remaining <= 0:
                    root = self.settings.archive_dir.resolve()
                    path = (root / str(blob["storage_key"])).resolve()
                    if root in path.parents:
                        removable_blobs.append(path)
                    connection.execute(
                        "DELETE FROM archive_blobs WHERE content_sha256 = ?", (digest,)
                    )
                else:
                    connection.execute(
                        "UPDATE archive_blobs SET reference_count = ? WHERE content_sha256 = ?",
                        (remaining, digest),
                    )
        for upload_id in upload_ids:
            directory = self.upload_dir(upload_id)
            if directory.exists():
                shutil.rmtree(directory)
        project_directory = self.project_dir(project_id)
        if project_directory.exists():
            shutil.rmtree(project_directory)
        for path in removable_blobs:
            path.unlink(missing_ok=True)
        return {
            "event_id": tombstone_id,
            "project_id": project_id,
            "deleted_at": deleted_at,
            "counts": counts,
            "tombstone_hash": tombstone_hash,
        }

    def create_batch(self, project_id: str, request: BatchCreate) -> dict[str, Any]:
        batch_id = str(uuid4())
        now = iso()
        with self._write_lock, self.connect() as connection:
            self._require(connection, "projects", project_id)
            connection.execute(
                """
                INSERT INTO batches(id, project_id, name, sequence_index, default_analysis_mode,
                    enable_genre_analysis, enable_lyrical_analysis, lyrics_consent_confirmed,
                    state, created_at, completed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    batch_id, project_id, request.name, request.sequence, request.default_analysis_mode.value,
                    int(request.enable_genre_analysis), int(request.enable_lyrical_analysis),
                    int(request.lyrics_consent_confirmed), BatchState.DRAFT.value, now,
                ),
            )
            self.append_audit(
                connection, project_id=project_id, batch_id=batch_id, entity_type="batch",
                entity_id=batch_id, event_type="batch.created", payload={"sequence": request.sequence},
            )
        return self.get_batch(batch_id)

    def get_batch(self, batch_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = self._require(connection, "batches", batch_id)
            asset = connection.execute(
                "SELECT COUNT(*) AS total, COALESCE(SUM(duration_seconds), 0) AS duration FROM source_assets WHERE batch_id = ?",
                (batch_id,),
            ).fetchone()
            queue = connection.execute(
                """SELECT
                    SUM(CASE WHEN state = 'completed' THEN 1 ELSE 0 END) AS completed,
                    SUM(CASE WHEN state = 'failed' THEN 1 ELSE 0 END) AS failed,
                    COUNT(*) AS total
                   FROM queue_items WHERE batch_id = ?""",
                (batch_id,),
            ).fetchone()
        result = dict(row)
        result["sequence"] = result.pop("sequence_index")
        result["enable_genre_analysis"] = bool(result["enable_genre_analysis"])
        result["enable_lyrical_analysis"] = bool(result["enable_lyrical_analysis"])
        result["lyrics_consent_confirmed"] = bool(result["lyrics_consent_confirmed"])
        result["item_total"] = int(asset["total"] if asset else 0)
        result["duration_seconds"] = float(asset["duration"] if asset else 0)
        completed = int(queue["completed"] or 0) if queue else 0
        failed = int(queue["failed"] or 0) if queue else 0
        total = int(queue["total"] or 0) if queue else 0
        result["completed_items"] = completed
        result["failed_items"] = failed
        result["progress"] = round((completed + failed) * 100 / total) if total else 0
        return result

    def list_batches(self, project_id: str, *, offset: int, limit: int) -> tuple[list[dict[str, Any]], int]:
        canonical_id(project_id)
        with self.connect() as connection:
            total = int(connection.execute(
                "SELECT COUNT(*) FROM batches WHERE project_id = ?", (project_id,)
            ).fetchone()[0])
            rows = connection.execute(
                "SELECT id FROM batches WHERE project_id = ? ORDER BY sequence_index, created_at LIMIT ? OFFSET ?",
                (project_id, limit, offset),
            ).fetchall()
        return [self.get_batch(str(row["id"])) for row in rows], total

    def patch_batch(self, batch_id: str, patch: BatchPatch) -> dict[str, Any]:
        values = patch.model_dump(exclude_unset=True)
        with self._write_lock, self.connect() as connection:
            row = self._require(connection, "batches", batch_id)
            state = values.get("state", BatchState(str(row["state"])))
            connection.execute(
                "UPDATE batches SET name = ?, sequence_index = ?, state = ?, completed_at = ? WHERE id = ?",
                (
                    values.get("name", row["name"]), values.get("sequence", row["sequence_index"]),
                    state.value, iso() if state == BatchState.COMPLETED else row["completed_at"], row["id"],
                ),
            )
            self.append_audit(
                connection, project_id=str(row["project_id"]), batch_id=str(row["id"]),
                entity_type="batch", entity_id=str(row["id"]), event_type=f"batch.{state.value}",
                payload={"fields": sorted(values)},
            )
        return self.get_batch(batch_id)

    def storage_usage_bytes(self) -> int:
        with self.connect() as connection:
            row = connection.execute("SELECT COALESCE(SUM(byte_size), 0) FROM archive_blobs").fetchone()
        return int(row[0]) if row else 0

    def free_storage_bytes(self) -> int:
        return int(shutil.disk_usage(self.settings.data_dir).free)

    def admit_source(self, total_bytes: int) -> None:
        if total_bytes > self.settings.max_source_upload_bytes:
            raise StorageAdmissionError(
                f"The source exceeds the configured {self.settings.max_source_upload_gb} GiB limit."
            )
        used = self.storage_usage_bytes()
        if self.settings.max_archive_bytes and used + total_bytes > self.settings.max_archive_bytes:
            raise StorageAdmissionError("The configured archive quota cannot admit this source.")
        if self.free_storage_bytes() - total_bytes < self.settings.minimum_free_disk_bytes:
            raise StorageAdmissionError("The configured minimum free-disk reserve would be crossed.")

    def upload_dir(self, upload_id: str) -> Path:
        canonical = canonical_id(upload_id)
        root = self.settings.uploads_dir.resolve()
        destination = (root / canonical).resolve()
        if destination.parent != root:
            raise KeyError("Upload session was not found")
        return destination

    def create_upload_session(self, request: UploadSessionCreate) -> dict[str, Any]:
        self.admit_source(request.total_bytes)
        upload_id = str(uuid4())
        now = utc_now()
        with self._write_lock, self.connect() as connection:
            batch = self._require(connection, "batches", request.batch_id)
            existing = connection.execute(
                "SELECT id FROM upload_sessions WHERE batch_id = ? AND idempotency_key = ?",
                (request.batch_id, request.idempotency_key),
            ).fetchone()
            if existing:
                return self.get_upload_session(str(existing["id"]))
            connection.execute(
                """
                INSERT INTO upload_sessions(id, batch_id, project_id, display_name, total_bytes,
                    received_bytes, expected_sha256, idempotency_key, original_order,
                    permission_confirmed, state,
                    asset_id, duplicate_asset_id, created_at, updated_at, expires_at)
                VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?)
                """,
                (
                    upload_id, request.batch_id, batch["project_id"], request.display_name,
                    request.total_bytes, request.expected_sha256, request.idempotency_key,
                    request.original_order, int(request.permission_confirmed),
                    UploadState.CREATED.value, iso(now), iso(now),
                    iso(now + timedelta(hours=self.settings.abandoned_upload_ttl_hours)),
                ),
            )
            self.append_audit(
                connection, project_id=str(batch["project_id"]), batch_id=request.batch_id,
                entity_type="upload_session", entity_id=upload_id, event_type="upload.created",
                payload={"totalBytes": request.total_bytes, "originalOrder": request.original_order},
            )
        directory = self.upload_dir(upload_id)
        directory.mkdir(parents=False, exist_ok=False)
        secure_private_directory(directory)
        partial = directory / "source.partial"
        partial.touch(exist_ok=False)
        secure_private_file(partial)
        return self.get_upload_session(upload_id)

    def get_upload_session(self, upload_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = self._require(connection, "upload_sessions", upload_id)
        result = dict(row)
        for private_field in (
            "project_id",
            "idempotency_key",
            "original_order",
            "permission_confirmed",
        ):
            result.pop(private_field, None)
        result["chunk_size_bytes"] = self.settings.resumable_upload_chunk_bytes
        return result

    def record_chunk(
        self,
        upload_id: str,
        *,
        offset: int,
        length: int,
        chunk_sha256: str,
    ) -> dict[str, Any]:
        now = iso()
        with self._write_lock, self.connect() as connection:
            row = self._require(connection, "upload_sessions", upload_id)
            if str(row["state"]) in {UploadState.CANCELLED.value, UploadState.COMPLETED.value, UploadState.FAILED.value}:
                raise CatalogueConflict("The upload session is not writable.")
            if offset != int(row["received_bytes"]):
                raise CatalogueConflict(f"Expected byte offset {int(row['received_bytes'])}.")
            if length <= 0 or offset + length > int(row["total_bytes"]):
                raise CatalogueConflict("The chunk range is outside the declared source size.")
            connection.execute(
                "INSERT INTO upload_chunks(upload_id, byte_offset, byte_length, sha256, received_at) VALUES (?, ?, ?, ?, ?)",
                (upload_id, offset, length, chunk_sha256, now),
            )
            connection.execute(
                "UPDATE upload_sessions SET received_bytes = ?, state = ?, updated_at = ? WHERE id = ?",
                (offset + length, UploadState.UPLOADING.value, now, upload_id),
            )
            self.append_audit(
                connection, project_id=str(row["project_id"]), batch_id=str(row["batch_id"]),
                entity_type="upload_session", entity_id=upload_id, event_type="upload.chunk_received",
                payload={"offset": offset, "length": length, "sha256": chunk_sha256},
            )
        return self.get_upload_session(upload_id)

    def mark_upload_failed(self, upload_id: str, reason_code: str) -> None:
        with self._write_lock, self.connect() as connection:
            row = self._require(connection, "upload_sessions", upload_id)
            connection.execute(
                "UPDATE upload_sessions SET state = ?, updated_at = ? WHERE id = ?",
                (UploadState.FAILED.value, iso(), upload_id),
            )
            self.append_audit(
                connection, project_id=str(row["project_id"]), batch_id=str(row["batch_id"]),
                entity_type="upload_session", entity_id=upload_id, event_type="upload.failed",
                payload={"reasonCode": reason_code}, actor_type="system",
            )

    def complete_asset(
        self,
        upload_id: str,
        *,
        content_sha256: str,
        duration_seconds: float,
        codec: str,
        container: str,
        sample_rate: int,
        channels: int,
        storage_key: str,
    ) -> dict[str, Any]:
        asset_id = str(uuid4())
        now = iso()
        with self._write_lock, self.connect() as connection:
            row = self._require(connection, "upload_sessions", upload_id)
            if int(row["received_bytes"]) != int(row["total_bytes"]):
                raise CatalogueConflict("All declared bytes must be received before completion.")
            if str(row["state"]) == UploadState.COMPLETED.value and row["asset_id"]:
                return self.get_asset(str(row["asset_id"]))
            duplicate = connection.execute(
                "SELECT id FROM source_assets WHERE content_sha256 = ? ORDER BY created_at LIMIT 1",
                (content_sha256,),
            ).fetchone()
            blob = connection.execute(
                "SELECT content_sha256 FROM archive_blobs WHERE content_sha256 = ?",
                (content_sha256,),
            ).fetchone()
            if blob:
                connection.execute(
                    "UPDATE archive_blobs SET reference_count = reference_count + 1 WHERE content_sha256 = ?",
                    (content_sha256,),
                )
            else:
                connection.execute(
                    "INSERT INTO archive_blobs(content_sha256, byte_size, storage_key, reference_count, created_at) VALUES (?, ?, ?, 1, ?)",
                    (content_sha256, row["total_bytes"], storage_key, now),
                )
            project = self._require(connection, "projects", str(row["project_id"]))
            connection.execute(
                """
                INSERT INTO source_assets(id, project_id, batch_id, display_name, content_sha256,
                    byte_size, duration_seconds, codec, container, sample_rate, channels, original_order,
                    upload_state, storage_state, archival_state, segmentation_state, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'stored_once', ?, 'not_started', ?)
                """,
                (
                    asset_id, row["project_id"], row["batch_id"], row["display_name"], content_sha256,
                    row["total_bytes"], duration_seconds, codec, container, sample_rate, channels,
                    row["original_order"], UploadState.COMPLETED.value, project["retention_policy"], now,
                ),
            )
            connection.execute(
                "UPDATE upload_sessions SET state = ?, asset_id = ?, duplicate_asset_id = ?, updated_at = ? WHERE id = ?",
                (
                    UploadState.COMPLETED.value, asset_id,
                    str(duplicate["id"]) if duplicate else None, now, upload_id,
                ),
            )
            self.append_audit(
                connection, project_id=str(row["project_id"]), batch_id=str(row["batch_id"]),
                entity_type="asset", entity_id=asset_id, event_type="asset.accepted",
                payload={
                    "sha256": content_sha256, "byteSize": int(row["total_bytes"]),
                    "durationSeconds": duration_seconds, "duplicateDetected": duplicate is not None,
                }, actor_type="system",
            )
        return self.get_asset(asset_id)

    def get_asset(self, asset_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = self._require(connection, "source_assets", asset_id)
        return dict(row)

    def list_assets(self, batch_id: str, *, offset: int, limit: int) -> tuple[list[dict[str, Any]], int]:
        canonical_id(batch_id)
        with self.connect() as connection:
            total = int(connection.execute(
                "SELECT COUNT(*) FROM source_assets WHERE batch_id = ?", (batch_id,)
            ).fetchone()[0])
            rows = connection.execute(
                "SELECT * FROM source_assets WHERE batch_id = ? ORDER BY original_order, created_at LIMIT ? OFFSET ?",
                (batch_id, limit, offset),
            ).fetchall()
        return [dict(row) for row in rows], total

    def asset_source_path(self, asset_id: str) -> Path:
        with self.connect() as connection:
            asset = self._require(connection, "source_assets", asset_id)
            blob = connection.execute(
                "SELECT storage_key FROM archive_blobs WHERE content_sha256 = ?", (asset["content_sha256"],)
            ).fetchone()
        if blob is None:
            raise KeyError("Archived source was not found")
        root = self.settings.archive_dir.resolve()
        path = (root / str(blob["storage_key"])).resolve()
        if root not in path.parents:
            raise KeyError("Archived source was not found")
        return path

    def blob_destination(self, sha256: str) -> tuple[Path, str]:
        if len(sha256) != 64 or any(character not in "0123456789abcdef" for character in sha256):
            raise ValueError("Invalid content hash")
        relative = Path("blobs") / sha256[:2] / f"{sha256}.bin"
        destination = (self.settings.archive_dir / relative).resolve()
        root = self.settings.archive_dir.resolve()
        if root not in destination.parents:
            raise ValueError("Invalid content storage key")
        return destination, relative.as_posix()

    def replace_segments(
        self,
        asset_id: str,
        segments: Iterable[SegmentResponse],
        *,
        reason: str,
        detected: bool,
    ) -> list[dict[str, Any]]:
        segment_list = list(segments)
        if not segment_list:
            raise ValueError("At least one virtual segment is required")
        with self._write_lock, self.connect() as connection:
            asset = self._require(connection, "source_assets", asset_id)
            previous = connection.execute(
                "SELECT COALESCE(MAX(revision), 0) FROM segment_map_revisions WHERE source_asset_id = ?",
                (asset_id,),
            ).fetchone()
            revision = int(previous[0]) + 1 if previous else 1
            payload = [item.model_dump(mode="json", by_alias=True) for item in segment_list]
            payload_hash = hashlib.sha256(_json(payload).encode("utf-8")).hexdigest()
            event_id = self.append_audit(
                connection, project_id=str(asset["project_id"]), batch_id=str(asset["batch_id"]),
                entity_type="segment_map", entity_id=asset_id,
                event_type="segmentation.detected" if detected else "segmentation.edited",
                payload={"revision": revision, "segmentCount": len(payload), "reason": reason},
            )
            connection.execute("DELETE FROM segments WHERE source_asset_id = ?", (asset_id,))
            for item in segment_list:
                dumped = item.model_dump(mode="json")
                connection.execute(
                    """
                    INSERT INTO segments(id, source_asset_id, sequence_index, label, start_seconds, end_seconds,
                        stable_core_start_seconds, stable_core_end_seconds, transition_in_start_seconds,
                        transition_in_end_seconds, transition_out_start_seconds, transition_out_end_seconds,
                        confidence, confidence_score, transition_type, review_state, accepted,
                        child_analysis_job_id, evidence_json, revision)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item.id, asset_id, item.sequence_index, item.label, item.start_seconds, item.end_seconds,
                        item.stable_core_start_seconds, item.stable_core_end_seconds,
                        item.transition_in_start_seconds, item.transition_in_end_seconds,
                        item.transition_out_start_seconds, item.transition_out_end_seconds,
                        item.confidence.value, item.confidence_score, item.transition_type.value,
                        item.review_state.value, int(item.accepted), item.child_analysis_job_id,
                        _json(dumped["evidence"]), revision,
                    ),
                )
            parent = connection.execute(
                "SELECT id FROM segment_map_revisions WHERE source_asset_id = ? ORDER BY revision DESC LIMIT 1",
                (asset_id,),
            ).fetchone()
            revision_id = str(uuid4())
            connection.execute(
                """
                INSERT INTO segment_map_revisions(id, source_asset_id, revision, parent_revision_id,
                    payload_json, payload_sha256, reason, audit_event_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    revision_id, asset_id, revision, str(parent["id"]) if parent else None,
                    _json(payload), payload_hash, reason, event_id, iso(),
                ),
            )
            connection.execute(
                "UPDATE source_assets SET segmentation_state = ? WHERE id = ?",
                ("awaiting_review" if detected else "reviewed", asset_id),
            )
        return self.list_segments(asset_id)

    def list_segments(self, asset_id: str) -> list[dict[str, Any]]:
        canonical_id(asset_id)
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM segments WHERE source_asset_id = ? ORDER BY sequence_index", (asset_id,)
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["source_asset_id"] = item.pop("source_asset_id")
            item["accepted"] = bool(item["accepted"])
            item["evidence"] = _decode_json(item.pop("evidence_json"), {})
            result.append(item)
        return result

    def detected_segment_revision(self, asset_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM segment_map_revisions WHERE source_asset_id = ? ORDER BY revision LIMIT 1",
                (canonical_id(asset_id),),
            ).fetchone()
        if row is None:
            raise KeyError("Detected segment map was not found")
        parsed = _decode_json(row["payload_json"], [])
        return parsed if isinstance(parsed, list) else []

    def enqueue_segments(self, batch_id: str, segment_ids: list[str]) -> list[dict[str, Any]]:
        now = iso()
        created: list[str] = []
        with self._write_lock, self.connect() as connection:
            batch = self._require(connection, "batches", batch_id)
            for segment_id in segment_ids:
                segment = self._require(connection, "segments", segment_id)
                asset = self._require(connection, "source_assets", str(segment["source_asset_id"]))
                if str(asset["batch_id"]) != batch_id:
                    raise CatalogueConflict("Every segment must belong to the requested batch.")
                existing = connection.execute(
                    "SELECT id FROM queue_items WHERE segment_id = ? AND state IN ('stored','queued','running','paused')",
                    (segment_id,),
                ).fetchone()
                if existing:
                    created.append(str(existing["id"]))
                    continue
                queue_id = str(uuid4())
                connection.execute(
                    """
                    INSERT INTO queue_items(id, batch_id, project_id, segment_id, state, attempt,
                        analysis_mode, job_id, failure_reason, lease_owner, lease_expires_at, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, 0, ?, NULL, NULL, NULL, NULL, ?, ?)
                    """,
                    (
                        queue_id, batch_id, batch["project_id"], segment_id, QueueState.QUEUED.value,
                        batch["default_analysis_mode"], now, now,
                    ),
                )
                created.append(queue_id)
            connection.execute("UPDATE batches SET state = ? WHERE id = ?", (BatchState.QUEUED.value, batch_id))
            self.append_audit(
                connection, project_id=str(batch["project_id"]), batch_id=batch_id,
                entity_type="batch", entity_id=batch_id, event_type="child_analysis.queued",
                payload={"itemCount": len(created)},
            )
        return [self.get_queue_item(item_id) for item_id in created]

    def get_queue_item(self, item_id: str) -> dict[str, Any]:
        canonical = canonical_id(item_id)
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM queue_items WHERE id = ?", (canonical,)).fetchone()
        if row is None:
            raise KeyError("Queue item was not found")
        return dict(row)

    def list_queue_items(self, batch_id: str, *, states: tuple[str, ...] = ()) -> list[dict[str, Any]]:
        canonical_id(batch_id)
        with self.connect() as connection:
            if states:
                placeholders = ",".join("?" for _ in states)
                rows = connection.execute(
                    f"SELECT * FROM queue_items WHERE batch_id = ? AND state IN ({placeholders}) ORDER BY created_at",
                    (batch_id, *states),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM queue_items WHERE batch_id = ? ORDER BY created_at", (batch_id,)
                ).fetchall()
        return [dict(row) for row in rows]

    def recover_queue(self) -> int:
        with self._write_lock, self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE queue_items SET state = 'queued', failure_reason = 'Recovered after backend restart',
                    lease_owner = NULL, lease_expires_at = NULL, updated_at = ?
                WHERE state = 'running'
                """,
                (iso(),),
            )
        return int(cursor.rowcount)

    def transition_queue_item(
        self,
        item_id: str,
        state: QueueState,
        *,
        job_id: str | None = None,
        failure_reason: str | None = None,
    ) -> dict[str, Any]:
        with self._write_lock, self.connect() as connection:
            row = connection.execute("SELECT * FROM queue_items WHERE id = ?", (canonical_id(item_id),)).fetchone()
            if row is None:
                raise KeyError("Queue item was not found")
            attempt = int(row["attempt"]) + (1 if state == QueueState.RUNNING else 0)
            connection.execute(
                "UPDATE queue_items SET state = ?, attempt = ?, job_id = COALESCE(?, job_id), failure_reason = ?, updated_at = ? WHERE id = ?",
                (state.value, attempt, job_id, failure_reason, iso(), item_id),
            )
            self.append_audit(
                connection, project_id=str(row["project_id"]), batch_id=str(row["batch_id"]),
                entity_type="queue_item", entity_id=item_id,
                event_type=f"child_analysis.{state.value}",
                payload={"attempt": attempt, "jobId": job_id, "failureReason": failure_reason},
                actor_type="system",
            )
        return self.get_queue_item(item_id)

    def list_audit(self, project_id: str, *, offset: int, limit: int) -> tuple[list[dict[str, Any]], int]:
        canonical_id(project_id)
        with self.connect() as connection:
            total = int(connection.execute(
                "SELECT COUNT(*) FROM audit_events WHERE project_id = ?", (project_id,)
            ).fetchone()[0])
            rows = connection.execute(
                "SELECT * FROM audit_events WHERE project_id = ? ORDER BY sequence LIMIT ? OFFSET ?",
                (project_id, limit, offset),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["payload"] = _decode_json(item.pop("payload_json"), {})
            result.append(item)
        return result, total

    def verify_audit(self, project_id: str) -> dict[str, Any]:
        events, total = self.list_audit(project_id, offset=0, limit=1_000_000)
        previous_hash = "0" * 64
        failure: int | None = None
        expected_sequence = 1
        for event in events:
            canonical = {
                "actorType": event["actor_type"], "batchId": event["batch_id"],
                "correlationId": event["correlation_id"], "entityId": event["entity_id"],
                "entityType": event["entity_type"], "eventId": event["event_id"],
                "eventType": event["event_type"], "payload": event["payload"],
                "previousEventHash": event["previous_event_hash"], "projectId": event["project_id"],
                "requestId": event["request_id"], "schemaVersion": event["schema_version"],
                "sequence": event["sequence"], "timestamp": event["timestamp"],
            }
            actual = hashlib.sha256(_json(canonical).encode("utf-8")).hexdigest()
            if (
                int(event["sequence"]) != expected_sequence
                or event["previous_event_hash"] != previous_hash
                or actual != event["event_hash"]
            ):
                failure = int(event["sequence"])
                break
            previous_hash = str(event["event_hash"])
            expected_sequence += 1
        return {
            "project_id": project_id, "valid": failure is None, "event_count": total,
            "first_sequence": int(events[0]["sequence"]) if events else None,
            "last_sequence": int(events[-1]["sequence"]) if events else None,
            "failure_sequence": failure,
        }

    def artifact_rows(self, project_id: str) -> list[dict[str, Any]]:
        canonical_id(project_id)
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM artifacts WHERE project_id = ? ORDER BY created_at DESC", (project_id,)
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["producer_versions"] = _decode_json(item.pop("producer_versions_json"), {})
            item["current"] = bool(item["current"])
            item.pop("storage_key", None)
            result.append(item)
        return result

    def revision_rows(self, project_id: str) -> list[dict[str, Any]]:
        canonical_id(project_id)
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, entity_type, entity_id, parent_revision_id, revision_number, reason,
                    artifact_sha256, schema_version, audit_event_id, created_at
                FROM revisions WHERE project_id = ? ORDER BY created_at DESC
                """,
                (project_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def queue_context(self, item_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT q.*, b.default_analysis_mode, b.enable_genre_analysis,
                    b.enable_lyrical_analysis, b.lyrics_consent_confirmed, b.state AS batch_state,
                    s.source_asset_id, s.label AS segment_label, s.start_seconds, s.end_seconds,
                    s.stable_core_start_seconds, s.stable_core_end_seconds,
                    s.transition_in_start_seconds, s.transition_in_end_seconds,
                    s.transition_out_start_seconds, s.transition_out_end_seconds,
                    a.display_name AS asset_display_name
                FROM queue_items q
                JOIN batches b ON b.id = q.batch_id
                JOIN segments s ON s.id = q.segment_id
                JOIN source_assets a ON a.id = s.source_asset_id
                WHERE q.id = ?
                """,
                (canonical_id(item_id),),
            ).fetchone()
        if row is None:
            raise KeyError("Queue item was not found")
        return dict(row)

    def queued_batch_ids(self) -> list[str]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT q.batch_id, MIN(q.created_at) AS first_created
                FROM queue_items q JOIN batches b ON b.id = q.batch_id
                WHERE q.state = 'queued' AND b.state IN ('queued', 'running')
                GROUP BY q.batch_id ORDER BY first_created
                """
            ).fetchall()
        return [str(row["batch_id"]) for row in rows]

    def next_queued_item(self, batch_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM queue_items WHERE batch_id = ? AND state = 'queued' ORDER BY created_at LIMIT 1",
                (canonical_id(batch_id),),
            ).fetchone()
        return dict(row) if row is not None else None

    def set_segment_job_id(self, segment_id: str, job_id: str) -> None:
        with self._write_lock, self.connect() as connection:
            segment = self._require(connection, "segments", segment_id)
            asset = self._require(connection, "source_assets", str(segment["source_asset_id"]))
            connection.execute(
                "UPDATE segments SET child_analysis_job_id = ? WHERE id = ?",
                (canonical_id(job_id), segment_id),
            )
            self.append_audit(
                connection, project_id=str(asset["project_id"]), batch_id=str(asset["batch_id"]),
                entity_type="segment", entity_id=segment_id,
                event_type="child_analysis.linked",
                payload={"jobId": job_id}, actor_type="system",
            )

    def register_artifact(
        self,
        *,
        project_id: str,
        batch_id: str | None,
        owner_type: str,
        owner_id: str,
        artifact_type: str,
        schema_version: str,
        media_type: str,
        content: bytes,
        producer_versions: dict[str, str],
        reason: str,
    ) -> dict[str, Any]:
        canonical_id(project_id)
        canonical_id(owner_id)
        artifact_id = str(uuid4())
        sha256 = hashlib.sha256(content).hexdigest()
        relative = Path("projects") / project_id / "artifacts" / f"{artifact_id}.bin"
        destination = (self.settings.archive_dir / relative).resolve()
        archive_root = self.settings.archive_dir.resolve()
        if archive_root not in destination.parents:
            raise ValueError("Invalid artifact storage key")
        destination.parent.mkdir(parents=True, exist_ok=True)
        secure_private_directory(destination.parent)
        temporary = destination.with_suffix(".tmp")
        with temporary.open("xb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        secure_private_file(temporary)
        os.replace(temporary, destination)
        secure_private_file(destination)
        now = iso()
        with self._write_lock, self.connect() as connection:
            previous = connection.execute(
                "SELECT id FROM artifacts WHERE owner_type = ? AND owner_id = ? AND artifact_type = ? AND current = 1",
                (owner_type, owner_id, artifact_type),
            ).fetchone()
            if previous:
                connection.execute("UPDATE artifacts SET current = 0 WHERE id = ?", (previous["id"],))
            event_id = self.append_audit(
                connection, project_id=project_id, batch_id=batch_id,
                entity_type="artifact", entity_id=artifact_id, event_type="artifact.created",
                payload={
                    "artifactType": artifact_type, "sha256": sha256,
                    "byteSize": len(content), "supersedesId": str(previous["id"]) if previous else None,
                }, actor_type="system",
            )
            connection.execute(
                """
                INSERT INTO artifacts(id, project_id, owner_type, owner_id, artifact_type,
                    schema_version, media_type, byte_size, sha256, storage_key, created_at,
                    producer_versions_json, current, supersedes_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (
                    artifact_id, project_id, owner_type, owner_id, artifact_type, schema_version,
                    media_type, len(content), sha256, relative.as_posix(), now,
                    _json(producer_versions), str(previous["id"]) if previous else None,
                ),
            )
            previous_revision = connection.execute(
                "SELECT id, revision_number FROM revisions WHERE entity_type = ? AND entity_id = ? ORDER BY revision_number DESC LIMIT 1",
                (owner_type, owner_id),
            ).fetchone()
            revision_number = int(previous_revision["revision_number"]) + 1 if previous_revision else 1
            connection.execute(
                """
                INSERT INTO revisions(id, project_id, entity_type, entity_id, parent_revision_id,
                    revision_number, reason, artifact_sha256, schema_version, audit_event_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()), project_id, owner_type, owner_id,
                    str(previous_revision["id"]) if previous_revision else None,
                    revision_number, reason, sha256, schema_version, event_id, now,
                ),
            )
        return {
            "id": artifact_id, "owner_type": owner_type, "owner_id": owner_id,
            "artifact_type": artifact_type, "schema_version": schema_version,
            "media_type": media_type, "byte_size": len(content), "sha256": sha256,
            "created_at": now, "producer_versions": producer_versions,
            "current": True, "supersedes_id": str(previous["id"]) if previous else None,
        }

    def report_inputs(self, batch_id: str) -> list[dict[str, Any]]:
        canonical_id(batch_id)
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT s.*, a.id AS artifact_id, a.storage_key, a.byte_size,
                    src.display_name AS source_display_name
                FROM segments s
                JOIN source_assets src ON src.id = s.source_asset_id
                LEFT JOIN artifacts a ON a.owner_type = 'segment' AND a.owner_id = s.id
                    AND a.artifact_type = 'analysis_json' AND a.current = 1
                WHERE src.batch_id = ? AND s.accepted = 1
                ORDER BY s.sequence_index
                """,
                (batch_id,),
            ).fetchall()
        results: list[dict[str, Any]] = []
        archive_root = self.settings.archive_dir.resolve()
        for row in rows:
            item = dict(row)
            item["evidence"] = _decode_json(item.pop("evidence_json"), {})
            item["analysis"] = None
            storage_key = item.pop("storage_key", None)
            byte_size = int(item.pop("byte_size", 0) or 0)
            if isinstance(storage_key, str) and 0 < byte_size <= 20_000_000:
                path = (archive_root / storage_key).resolve()
                if archive_root in path.parents and path.is_file() and path.stat().st_size == byte_size:
                    parsed = _decode_json(path.read_text(encoding="utf-8"), None)
                    item["analysis"] = parsed if isinstance(parsed, dict) else None
            results.append(item)
        return results

    def cancel_upload(self, upload_id: str) -> None:
        with self._write_lock, self.connect() as connection:
            row = self._require(connection, "upload_sessions", upload_id)
            if str(row["state"]) == UploadState.COMPLETED.value:
                raise CatalogueConflict("Completed source assets require explicit asset deletion.")
            connection.execute(
                "UPDATE upload_sessions SET state = ?, updated_at = ? WHERE id = ?",
                (UploadState.CANCELLED.value, iso(), upload_id),
            )
            self.append_audit(
                connection, project_id=str(row["project_id"]), batch_id=str(row["batch_id"]),
                entity_type="upload_session", entity_id=upload_id, event_type="upload.cancelled",
            )
        directory = self.upload_dir(upload_id)
        if directory.exists():
            shutil.rmtree(directory)

    def cleanup_abandoned_uploads(self, now: datetime | None = None) -> int:
        current = iso(now)
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT id FROM upload_sessions WHERE expires_at <= ? AND state NOT IN ('completed','cancelled')",
                (current,),
            ).fetchall()
        cleaned = 0
        for row in rows:
            try:
                self.cancel_upload(str(row["id"]))
                cleaned += 1
            except (CatalogueConflict, OSError):
                continue
        return cleaned

    def create_segmentation_job(self, asset_id: str) -> dict[str, Any]:
        job_id = str(uuid4())
        now = iso()
        with self._write_lock, self.connect() as connection:
            asset = self._require(connection, "source_assets", asset_id)
            existing = connection.execute(
                """
                SELECT id FROM segmentation_jobs
                WHERE asset_id = ? AND state IN ('queued','running')
                ORDER BY created_at DESC LIMIT 1
                """,
                (asset_id,),
            ).fetchone()
            if existing:
                return self.get_segmentation_job(str(existing["id"]))
            connection.execute(
                """
                INSERT INTO segmentation_jobs(
                    id, asset_id, project_id, batch_id, state, stage, progress,
                    observation_count, candidate_count, refined_boundary_count,
                    peak_buffer_bytes, elapsed_seconds, error_code,
                    cancel_requested, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'queued', 'Registering source scan', 0,
                    0, 0, 0, 0, 0, NULL, 0, ?, ?)
                """,
                (job_id, asset_id, asset["project_id"], asset["batch_id"], now, now),
            )
            connection.execute(
                "UPDATE source_assets SET segmentation_state = 'queued' WHERE id = ?", (asset_id,)
            )
            self.append_audit(
                connection,
                project_id=str(asset["project_id"]),
                batch_id=str(asset["batch_id"]),
                entity_type="segmentation_job",
                entity_id=job_id,
                event_type="segmentation.scan_queued",
                payload={"assetId": asset_id},
            )
        return self.get_segmentation_job(job_id)

    def get_segmentation_job(self, job_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM segmentation_jobs WHERE id = ?", (canonical_id(job_id),)
            ).fetchone()
        if row is None:
            raise KeyError("Segmentation job was not found")
        result = dict(row)
        for private_field in ("project_id", "batch_id", "cancel_requested"):
            result.pop(private_field, None)
        return result

    def next_queued_segmentation_job(self) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT id FROM segmentation_jobs WHERE state = 'queued' ORDER BY created_at LIMIT 1"
            ).fetchone()
        return self.get_segmentation_job(str(row["id"])) if row else None

    def recover_segmentation_jobs(self) -> int:
        with self._write_lock, self.connect() as connection:
            asset_ids = connection.execute(
                "SELECT asset_id FROM segmentation_jobs WHERE state = 'running'"
            ).fetchall()
            cursor = connection.execute(
                """
                UPDATE segmentation_jobs
                SET state = 'queued', stage = 'Recovered after backend restart',
                    error_code = NULL, cancel_requested = 0, updated_at = ?
                WHERE state = 'running'
                """,
                (iso(),),
            )
            connection.executemany(
                "UPDATE source_assets SET segmentation_state = 'queued' WHERE id = ?",
                [(row["asset_id"],) for row in asset_ids],
            )
        return int(cursor.rowcount)

    def segmentation_cancel_requested(self, job_id: str) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT cancel_requested FROM segmentation_jobs WHERE id = ?", (canonical_id(job_id),)
            ).fetchone()
        return bool(row[0]) if row else True

    def request_segmentation_cancel(self, job_id: str) -> dict[str, Any]:
        with self._write_lock, self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM segmentation_jobs WHERE id = ?", (canonical_id(job_id),)
            ).fetchone()
            if row is None:
                raise KeyError("Segmentation job was not found")
            if str(row["state"]) in {"completed", "failed", "cancelled"}:
                return self.get_segmentation_job(job_id)
            state = "cancelled" if str(row["state"]) == "queued" else str(row["state"])
            stage = "Cancelled" if state == "cancelled" else "Cancelling source scan"
            connection.execute(
                """
                UPDATE segmentation_jobs SET cancel_requested = 1, state = ?, stage = ?,
                    updated_at = ? WHERE id = ?
                """,
                (state, stage, iso(), job_id),
            )
            if state == "cancelled":
                connection.execute(
                    "UPDATE source_assets SET segmentation_state = 'cancelled' WHERE id = ?",
                    (row["asset_id"],),
                )
                self.append_audit(
                    connection,
                    project_id=str(row["project_id"]),
                    batch_id=str(row["batch_id"]),
                    entity_type="segmentation_job",
                    entity_id=job_id,
                    event_type="segmentation.scan_cancelled",
                    payload={"stage": "queued"},
                    actor_type="system",
                )
        return self.get_segmentation_job(job_id)

    def update_segmentation_job(
        self,
        job_id: str,
        *,
        state: SegmentationJobState | None = None,
        stage: str | None = None,
        progress: int | None = None,
        observation_count: int | None = None,
        candidate_count: int | None = None,
        refined_boundary_count: int | None = None,
        peak_buffer_bytes: int | None = None,
        elapsed_seconds: float | None = None,
        error_code: str | None = None,
        audit_terminal: bool = False,
    ) -> dict[str, Any]:
        with self._write_lock, self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM segmentation_jobs WHERE id = ?", (canonical_id(job_id),)
            ).fetchone()
            if row is None:
                raise KeyError("Segmentation job was not found")
            next_state = state.value if state is not None else str(row["state"])
            next_stage = stage if stage is not None else str(row["stage"])
            next_progress = max(0, min(100, progress if progress is not None else int(row["progress"])))
            connection.execute(
                """
                UPDATE segmentation_jobs SET state = ?, stage = ?, progress = ?,
                    observation_count = ?, candidate_count = ?, refined_boundary_count = ?,
                    peak_buffer_bytes = ?, elapsed_seconds = ?, error_code = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    next_state,
                    next_stage,
                    next_progress,
                    observation_count if observation_count is not None else row["observation_count"],
                    candidate_count if candidate_count is not None else row["candidate_count"],
                    refined_boundary_count if refined_boundary_count is not None else row["refined_boundary_count"],
                    peak_buffer_bytes if peak_buffer_bytes is not None else row["peak_buffer_bytes"],
                    elapsed_seconds if elapsed_seconds is not None else row["elapsed_seconds"],
                    error_code,
                    iso(),
                    job_id,
                ),
            )
            if state is not None:
                asset_state = {
                    SegmentationJobState.QUEUED: "queued",
                    SegmentationJobState.RUNNING: "scanning",
                    SegmentationJobState.COMPLETED: "awaiting_review",
                    SegmentationJobState.FAILED: "failed",
                    SegmentationJobState.CANCELLED: "cancelled",
                }[state]
                connection.execute(
                    "UPDATE source_assets SET segmentation_state = ? WHERE id = ?",
                    (asset_state, row["asset_id"]),
                )
            if audit_terminal and state is not None:
                self.append_audit(
                    connection,
                    project_id=str(row["project_id"]),
                    batch_id=str(row["batch_id"]),
                    entity_type="segmentation_job",
                    entity_id=job_id,
                    event_type=f"segmentation.scan_{state.value}",
                    payload={
                        "observations": observation_count or 0,
                        "candidates": candidate_count or 0,
                        "refinedBoundaries": refined_boundary_count or 0,
                        "errorCode": error_code,
                    },
                    actor_type="system",
                )
        return self.get_segmentation_job(job_id)


_MIGRATION_1 = """
CREATE TABLE IF NOT EXISTS clients (
    id TEXT PRIMARY KEY, display_name TEXT NOT NULL, private_notes TEXT NOT NULL,
    tags_json TEXT NOT NULL, archived INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY, client_id TEXT NOT NULL REFERENCES clients(id), name TEXT NOT NULL,
    description TEXT NOT NULL, status TEXT NOT NULL, retention_policy TEXT NOT NULL,
    retention_until TEXT, tags_json TEXT NOT NULL, archived_at TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS projects_client_idx ON projects(client_id, updated_at);
CREATE TABLE IF NOT EXISTS batches (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), name TEXT NOT NULL,
    sequence_index INTEGER NOT NULL, default_analysis_mode TEXT NOT NULL,
    enable_genre_analysis INTEGER NOT NULL, enable_lyrical_analysis INTEGER NOT NULL,
    lyrics_consent_confirmed INTEGER NOT NULL, state TEXT NOT NULL,
    created_at TEXT NOT NULL, completed_at TEXT
);
CREATE INDEX IF NOT EXISTS batches_project_idx ON batches(project_id, sequence_index);
CREATE TABLE IF NOT EXISTS archive_blobs (
    content_sha256 TEXT PRIMARY KEY, byte_size INTEGER NOT NULL, storage_key TEXT NOT NULL UNIQUE,
    reference_count INTEGER NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS source_assets (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
    batch_id TEXT NOT NULL REFERENCES batches(id), display_name TEXT NOT NULL,
    content_sha256 TEXT NOT NULL REFERENCES archive_blobs(content_sha256), byte_size INTEGER NOT NULL,
    duration_seconds REAL NOT NULL, codec TEXT NOT NULL, container TEXT NOT NULL,
    sample_rate INTEGER NOT NULL, channels INTEGER NOT NULL, original_order INTEGER NOT NULL,
    upload_state TEXT NOT NULL, storage_state TEXT NOT NULL, archival_state TEXT NOT NULL,
    segmentation_state TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS source_assets_batch_idx ON source_assets(batch_id, original_order);
CREATE INDEX IF NOT EXISTS source_assets_hash_idx ON source_assets(content_sha256);
CREATE TABLE IF NOT EXISTS upload_sessions (
    id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES batches(id),
    project_id TEXT NOT NULL REFERENCES projects(id), display_name TEXT NOT NULL,
    total_bytes INTEGER NOT NULL, received_bytes INTEGER NOT NULL, expected_sha256 TEXT,
    idempotency_key TEXT NOT NULL, original_order INTEGER NOT NULL,
    permission_confirmed INTEGER NOT NULL, state TEXT NOT NULL,
    asset_id TEXT REFERENCES source_assets(id), duplicate_asset_id TEXT REFERENCES source_assets(id),
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, expires_at TEXT NOT NULL,
    UNIQUE(batch_id, idempotency_key)
);
CREATE TABLE IF NOT EXISTS upload_chunks (
    upload_id TEXT NOT NULL REFERENCES upload_sessions(id), byte_offset INTEGER NOT NULL,
    byte_length INTEGER NOT NULL, sha256 TEXT NOT NULL, received_at TEXT NOT NULL,
    PRIMARY KEY(upload_id, byte_offset)
);
CREATE TABLE IF NOT EXISTS segments (
    id TEXT PRIMARY KEY, source_asset_id TEXT NOT NULL REFERENCES source_assets(id),
    sequence_index INTEGER NOT NULL, label TEXT NOT NULL, start_seconds REAL NOT NULL,
    end_seconds REAL NOT NULL, stable_core_start_seconds REAL NOT NULL,
    stable_core_end_seconds REAL NOT NULL, transition_in_start_seconds REAL,
    transition_in_end_seconds REAL, transition_out_start_seconds REAL,
    transition_out_end_seconds REAL, confidence TEXT NOT NULL, confidence_score REAL,
    transition_type TEXT NOT NULL, review_state TEXT NOT NULL, accepted INTEGER NOT NULL,
    child_analysis_job_id TEXT, evidence_json TEXT NOT NULL, revision INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS segments_asset_idx ON segments(source_asset_id, sequence_index);
CREATE TABLE IF NOT EXISTS audit_events (
    event_id TEXT PRIMARY KEY, timestamp TEXT NOT NULL, sequence INTEGER NOT NULL,
    project_id TEXT NOT NULL REFERENCES projects(id), batch_id TEXT,
    entity_type TEXT NOT NULL, entity_id TEXT NOT NULL, event_type TEXT NOT NULL,
    actor_type TEXT NOT NULL, request_id TEXT, correlation_id TEXT NOT NULL,
    schema_version TEXT NOT NULL, payload_json TEXT NOT NULL,
    previous_event_hash TEXT NOT NULL, event_hash TEXT NOT NULL,
    UNIQUE(project_id, sequence)
);
CREATE TABLE IF NOT EXISTS segment_map_revisions (
    id TEXT PRIMARY KEY, source_asset_id TEXT NOT NULL REFERENCES source_assets(id),
    revision INTEGER NOT NULL, parent_revision_id TEXT REFERENCES segment_map_revisions(id),
    payload_json TEXT NOT NULL, payload_sha256 TEXT NOT NULL, reason TEXT NOT NULL,
    audit_event_id TEXT NOT NULL REFERENCES audit_events(event_id), created_at TEXT NOT NULL,
    UNIQUE(source_asset_id, revision)
);
CREATE TABLE IF NOT EXISTS queue_items (
    id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES batches(id),
    project_id TEXT NOT NULL REFERENCES projects(id), segment_id TEXT NOT NULL REFERENCES segments(id),
    state TEXT NOT NULL, attempt INTEGER NOT NULL, analysis_mode TEXT NOT NULL, job_id TEXT,
    failure_reason TEXT, lease_owner TEXT, lease_expires_at TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS queue_dispatch_idx ON queue_items(state, created_at);
CREATE TABLE IF NOT EXISTS artifacts (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), owner_type TEXT NOT NULL,
    owner_id TEXT NOT NULL, artifact_type TEXT NOT NULL, schema_version TEXT NOT NULL,
    media_type TEXT NOT NULL, byte_size INTEGER NOT NULL, sha256 TEXT NOT NULL,
    storage_key TEXT NOT NULL, created_at TEXT NOT NULL, producer_versions_json TEXT NOT NULL,
    current INTEGER NOT NULL, supersedes_id TEXT REFERENCES artifacts(id)
);
CREATE TABLE IF NOT EXISTS revisions (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL, parent_revision_id TEXT REFERENCES revisions(id),
    revision_number INTEGER NOT NULL, reason TEXT NOT NULL, artifact_sha256 TEXT NOT NULL,
    schema_version TEXT NOT NULL, audit_event_id TEXT NOT NULL REFERENCES audit_events(event_id),
    created_at TEXT NOT NULL, UNIQUE(entity_type, entity_id, revision_number)
);
"""


_MIGRATION_2 = """
CREATE TABLE IF NOT EXISTS catalogue_deletion_events (
    event_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, deleted_at TEXT NOT NULL,
    prior_project_event_hash TEXT NOT NULL, counts_json TEXT NOT NULL,
    tombstone_hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS catalogue_deletion_project_idx
    ON catalogue_deletion_events(project_id, deleted_at);
"""


_MIGRATION_3 = """
CREATE TABLE IF NOT EXISTS segmentation_jobs (
    id TEXT PRIMARY KEY, asset_id TEXT NOT NULL REFERENCES source_assets(id),
    project_id TEXT NOT NULL REFERENCES projects(id), batch_id TEXT NOT NULL REFERENCES batches(id),
    state TEXT NOT NULL, stage TEXT NOT NULL, progress INTEGER NOT NULL,
    observation_count INTEGER NOT NULL, candidate_count INTEGER NOT NULL,
    refined_boundary_count INTEGER NOT NULL, peak_buffer_bytes INTEGER NOT NULL,
    elapsed_seconds REAL NOT NULL, error_code TEXT, cancel_requested INTEGER NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS segmentation_jobs_dispatch_idx
    ON segmentation_jobs(state, created_at);
"""
