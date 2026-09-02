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
from uuid import uuid4

from ..privacy import secure_private_directory, secure_private_file
from ..video_generation.audio import AudioEvidence
from .models import LocalVideoDeletePreview, LocalVideoProjectSummary
from .package import LocalVideoProjectPackage


class LocalVideoArchiveError(RuntimeError):
    pass


def _iso() -> str:
    return datetime.now(UTC).isoformat()


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _private_atomic_json(path: Path, value: object) -> str:
    encoded = _canonical(value)
    digest = hashlib.sha256(encoded).hexdigest()
    path.parent.mkdir(parents=True, exist_ok=True)
    secure_private_directory(path.parent)
    temporary = path.with_name(f".{path.name}.partial-{uuid4().hex}")
    try:
        temporary.write_bytes(encoded)
        secure_private_file(temporary)
        os.replace(temporary, path)
        secure_private_file(path)
    finally:
        temporary.unlink(missing_ok=True)
    return digest


def _private_copy(source: Path, destination: Path, expected_sha256: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    secure_private_directory(destination.parent)
    if destination.is_file() and _sha256_file(destination) == expected_sha256:
        return
    temporary = destination.with_name(f".{destination.name}.partial-{uuid4().hex}")
    try:
        with source.open("rb") as input_stream, temporary.open("xb") as output_stream:
            shutil.copyfileobj(input_stream, output_stream, length=4 * 1024 * 1024)
            output_stream.flush()
            os.fsync(output_stream.fileno())
        secure_private_file(temporary)
        if _sha256_file(temporary) != expected_sha256:
            raise LocalVideoArchiveError("The retained project audio failed hash verification")
        os.replace(temporary, destination)
        secure_private_file(destination)
    finally:
        temporary.unlink(missing_ok=True)


def _sanitized_analysis(analysis: dict[str, Any]) -> dict[str, Any]:
    # Analysis measurements stay private, but source names and embedded metadata
    # have no role in a durable project revision.
    raw = json.loads(json.dumps(analysis, ensure_ascii=False))
    if not isinstance(raw, dict):
        raise LocalVideoArchiveError("The analysis snapshot is invalid")
    result: dict[str, Any] = raw
    file_info = result.get("file")
    if isinstance(file_info, dict):
        file_info["displayName"] = "project-audio"
        file_info.pop("privateMetadata", None)
    return result


class LocalVideoProjectArchive:
    def __init__(self, state_root: Path) -> None:
        self.state_root = (state_root / "local-video").resolve()
        self.database_path = self.state_root / "projects.sqlite3"
        self.projects_root = self.state_root / "projects"
        self.temp_root = self.state_root / "temp"
        self._lock = threading.RLock()
        for directory in (self.state_root, self.projects_root, self.temp_root):
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
                CREATE TABLE IF NOT EXISTS local_video_projects (
                    project_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL,
                    current_revision_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    deleted_at TEXT
                );
                CREATE TABLE IF NOT EXISTS local_video_revisions (
                    revision_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES local_video_projects(project_id),
                    revision_number INTEGER NOT NULL,
                    current INTEGER NOT NULL,
                    package_digest TEXT NOT NULL,
                    audio_sha256 TEXT NOT NULL,
                    audio_duration_seconds REAL NOT NULL,
                    analysis_sha256 TEXT NOT NULL,
                    analysis_id TEXT,
                    selected_tier TEXT,
                    status TEXT NOT NULL,
                    storage_key TEXT NOT NULL UNIQUE,
                    manifest_sha256 TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    deleted_at TEXT,
                    UNIQUE(project_id, revision_number)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS local_video_current_revision_idx
                    ON local_video_revisions(project_id) WHERE current = 1 AND deleted_at IS NULL;
                CREATE INDEX IF NOT EXISTS local_video_audio_hash_idx
                    ON local_video_revisions(audio_sha256);
                CREATE TABLE IF NOT EXISTS local_video_dependencies (
                    project_id TEXT NOT NULL,
                    revision_id TEXT NOT NULL REFERENCES local_video_revisions(revision_id),
                    analysis_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(revision_id, analysis_id)
                );
                CREATE TABLE IF NOT EXISTS local_video_audit (
                    event_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    revision_id TEXT,
                    event_type TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                """
            )

    def _revision_root(self, project_id: str, revision_id: str) -> Path:
        target = (self.projects_root / project_id / "revisions" / revision_id).resolve()
        if self.projects_root not in target.parents:
            raise LocalVideoArchiveError("The local video revision identity is invalid")
        return target

    def _audit(
        self,
        connection: sqlite3.Connection,
        project_id: str,
        revision_id: str | None,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        connection.execute(
            """
            INSERT INTO local_video_audit(event_id, project_id, revision_id, event_type, occurred_at, payload_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid4()),
                project_id,
                revision_id,
                event_type,
                _iso(),
                json.dumps(payload, sort_keys=True, separators=(",", ":")),
            ),
        )

    def create_revision(
        self,
        *,
        package: LocalVideoProjectPackage,
        audio: AudioEvidence,
        analysis: dict[str, Any],
        analysis_id: str | None,
        story_plan: dict[str, Any],
        shot_plan: dict[str, Any],
        timeline: list[dict[str, Any]],
        prompts: list[dict[str, Any]],
        selected_tier: str | None = None,
    ) -> str:
        sanitized_analysis = _sanitized_analysis(analysis)
        analysis_sha256 = hashlib.sha256(_canonical(sanitized_analysis)).hexdigest()
        with self._lock, self.connect() as connection:
            current = connection.execute(
                """
                SELECT revision_id, package_digest, audio_sha256, analysis_sha256
                FROM local_video_revisions
                WHERE project_id=? AND current=1 AND deleted_at IS NULL
                """,
                (package.project_id,),
            ).fetchone()
            if (
                current is not None
                and current["package_digest"] == package.package_digest
                and current["audio_sha256"] == audio.sha256
                and current["analysis_sha256"] == analysis_sha256
            ):
                return str(current["revision_id"])
            row = connection.execute(
                "SELECT COALESCE(MAX(revision_number), 0) FROM local_video_revisions WHERE project_id=?",
                (package.project_id,),
            ).fetchone()
            revision_number = int(row[0]) + 1
            revision_id = str(uuid4())
            storage_key = f"projects/{package.project_id}/revisions/{revision_id}"
            now = _iso()
            connection.execute(
                """
                INSERT INTO local_video_projects(project_id, title, status, current_revision_id, created_at, updated_at)
                VALUES (?, ?, 'preparing', ?, ?, ?)
                ON CONFLICT(project_id) DO UPDATE SET
                    title=excluded.title,
                    status='preparing',
                    current_revision_id=excluded.current_revision_id,
                    updated_at=excluded.updated_at,
                    deleted_at=NULL
                """,
                (package.project_id, package.title, revision_id, now, now),
            )
            connection.execute(
                "UPDATE local_video_revisions SET current=0 WHERE project_id=?",
                (package.project_id,),
            )
            # The manifest hash is filled after immutable files are published;
            # reserve the append-only identity inside the same transaction.
            connection.execute(
                """
                INSERT INTO local_video_revisions(
                    revision_id, project_id, revision_number, current, package_digest,
                    audio_sha256, audio_duration_seconds, analysis_sha256, analysis_id,
                    selected_tier, status, storage_key, manifest_sha256, created_at
                ) VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?, 'preparing', ?, ?, ?)
                """,
                (
                    revision_id,
                    package.project_id,
                    revision_number,
                    package.package_digest,
                    audio.sha256,
                    audio.duration_seconds,
                    analysis_sha256,
                    analysis_id,
                    selected_tier,
                    storage_key,
                    "0" * 64,
                    now,
                ),
            )
            if analysis_id:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO local_video_dependencies(project_id, revision_id, analysis_id, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (package.project_id, revision_id, analysis_id, now),
                )

        root = self._revision_root(package.project_id, revision_id)
        root.mkdir(parents=True, exist_ok=False)
        secure_private_directory(root)
        try:
            source_destination = root / "source" / "audio-master.bin"
            _private_copy(audio.path, source_destination, audio.sha256)
            artifact_hashes = {
                "analysis": _private_atomic_json(root / "analysis" / "analysis.json", sanitized_analysis),
                "storyPlan": _private_atomic_json(root / "analysis" / "story-plan.json", story_plan),
                "shotPlan": _private_atomic_json(root / "analysis" / "shot-plan.json", shot_plan),
                "timeline": _private_atomic_json(root / "analysis" / "timeline.json", timeline),
                "prompts": _private_atomic_json(root / "manifests" / "prompt-seed-manifest.json", prompts),
                "package": _private_atomic_json(
                    root / "manifests" / "package-snapshot.json",
                    {
                        "projectConfig": package.project_config,
                        "creativeBible": package.creative_bible,
                        "continuityProfile": package.continuity_profile,
                        "chapterMap": package.chapter_map,
                        "shotBank": package.shot_bank,
                        "editBlueprint": package.edit_blueprint,
                        "renderPlan": package.render_plan,
                        "modelProfile": package.model_profile,
                        "hardwarePolicy": package.hardware_policy,
                        "rights": package.rights,
                        "persistencePolicy": package.persistence_policy,
                        "syncPolicy": package.sync_policy,
                        "workflowContract": package.workflow_contract,
                    },
                ),
            }
            manifest = {
                "schemaVersion": "1.0.0",
                "kind": "trackprompt-local-video-analysis-revision",
                "projectId": package.project_id,
                "revisionId": revision_id,
                "revisionNumber": revision_number,
                "createdAt": now,
                "retentionPolicy": "persistent-until-explicit-project-delete",
                "packageDigest": package.package_digest,
                "audioContentSha256": audio.sha256,
                "audio": {
                    "durationSeconds": audio.duration_seconds,
                    "codec": audio.codec,
                    "container": audio.container,
                    "sampleRateHz": audio.sample_rate_hz,
                    "channels": audio.channels,
                    "byteSize": audio.size_bytes,
                },
                "analysisId": analysis_id,
                "analysisSha256": analysis_sha256,
                "selectedTier": selected_tier,
                "artifactHashes": artifact_hashes,
            }
            manifest_sha256 = _private_atomic_json(root / "revision-manifest.json", manifest)
        except Exception:
            with self._lock, self.connect() as connection:
                connection.execute(
                    "UPDATE local_video_revisions SET status='failed' WHERE revision_id=?",
                    (revision_id,),
                )
                connection.execute(
                    "UPDATE local_video_projects SET status='failed', updated_at=? WHERE project_id=?",
                    (_iso(), package.project_id),
                )
            raise
        with self._lock, self.connect() as connection:
            connection.execute(
                """
                UPDATE local_video_revisions SET status='analysis_archived', manifest_sha256=?
                WHERE revision_id=?
                """,
                (manifest_sha256, revision_id),
            )
            connection.execute(
                """
                UPDATE local_video_projects SET status='analysis_archived', updated_at=?
                WHERE project_id=?
                """,
                (_iso(), package.project_id),
            )
            self._audit(
                connection,
                package.project_id,
                revision_id,
                "analysis_revision_archived",
                {"audioSha256": audio.sha256, "packageDigest": package.package_digest},
            )
        return revision_id

    def set_status(self, project_id: str, status: str, *, selected_tier: str | None = None) -> None:
        now = _iso()
        with self._lock, self.connect() as connection:
            row = connection.execute(
                "SELECT current_revision_id FROM local_video_projects WHERE project_id=? AND deleted_at IS NULL",
                (project_id,),
            ).fetchone()
            if row is None:
                raise KeyError("Local video project not found")
            revision_id = str(row["current_revision_id"])
            connection.execute(
                "UPDATE local_video_projects SET status=?, updated_at=? WHERE project_id=?",
                (status[:80], now, project_id),
            )
            connection.execute(
                """
                UPDATE local_video_revisions SET status=?, selected_tier=COALESCE(?, selected_tier)
                WHERE revision_id=?
                """,
                (status[:80], selected_tier, revision_id),
            )
            self._audit(connection, project_id, revision_id, "status_changed", {"status": status[:80]})

    def current(self, project_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT p.project_id, p.title, p.status, p.updated_at, p.current_revision_id,
                       r.package_digest, r.audio_sha256, r.audio_duration_seconds,
                       r.analysis_sha256, r.analysis_id, r.selected_tier, r.storage_key,
                       r.manifest_sha256
                FROM local_video_projects p
                LEFT JOIN local_video_revisions r ON r.revision_id = p.current_revision_id
                WHERE p.project_id=? AND p.deleted_at IS NULL
                """,
                (project_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def artifact(self, project_id: str, relative: str) -> Path | None:
        current = self.current(project_id)
        if current is None or not isinstance(current.get("storage_key"), str):
            return None
        root = (self.state_root / str(current["storage_key"])).resolve()
        target = (root / relative).resolve()
        if root not in target.parents or not target.is_file():
            return None
        return target

    def list(self, *, query: str | None = None) -> list[LocalVideoProjectSummary]:
        terms: tuple[object, ...] = ()
        where = "WHERE p.deleted_at IS NULL"
        if query and query.strip():
            where += " AND (LOWER(p.project_id) LIKE ? OR LOWER(p.title) LIKE ? OR LOWER(p.status) LIKE ?)"
            pattern = f"%{query.strip().casefold()}%"
            terms = (pattern, pattern, pattern)
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT p.project_id, p.title, p.status, p.current_revision_id, p.updated_at,
                       r.audio_sha256, r.audio_duration_seconds, r.selected_tier
                FROM local_video_projects p
                LEFT JOIN local_video_revisions r ON r.revision_id=p.current_revision_id
                {where}
                ORDER BY p.updated_at DESC
                """,  # noqa: S608 - fixed SQL fragment; values stay parameterized
                terms,
            ).fetchall()
        return [
            LocalVideoProjectSummary(
                project_id=str(row["project_id"]),
                title=str(row["title"]),
                status=str(row["status"]),
                current_revision_id=(
                    str(row["current_revision_id"]) if row["current_revision_id"] else None
                ),
                updated_at=datetime.fromisoformat(str(row["updated_at"])),
                audio_hash_prefix=(str(row["audio_sha256"])[:12] if row["audio_sha256"] else None),
                duration_seconds=(
                    float(row["audio_duration_seconds"])
                    if row["audio_duration_seconds"] is not None
                    else None
                ),
                selected_tier=(str(row["selected_tier"]) if row["selected_tier"] else None),
            )
            for row in rows
        ]

    def is_analysis_referenced(self, analysis_id: str) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM local_video_dependencies d
                JOIN local_video_revisions r ON r.revision_id=d.revision_id
                JOIN local_video_projects p ON p.project_id=d.project_id
                WHERE d.analysis_id=? AND r.deleted_at IS NULL AND p.deleted_at IS NULL LIMIT 1
                """,
                (analysis_id,),
            ).fetchone()
        return row is not None

    def delete_preview(self, project_id: str, revision_id: str | None = None) -> LocalVideoDeletePreview:
        with self.connect() as connection:
            if revision_id:
                rows = connection.execute(
                    """
                    SELECT revision_id, storage_key FROM local_video_revisions
                    WHERE project_id=? AND revision_id=? AND deleted_at IS NULL
                    """,
                    (project_id, revision_id),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT revision_id, storage_key FROM local_video_revisions
                    WHERE project_id=? AND deleted_at IS NULL
                    """,
                    (project_id,),
                ).fetchall()
        if not rows:
            raise KeyError("Local video project not found")
        count = 0
        byte_count = 0
        for row in rows:
            root = (self.state_root / str(row["storage_key"])).resolve()
            if self.projects_root not in root.parents or not root.is_dir():
                continue
            for path in root.rglob("*"):
                if path.is_file():
                    count += 1
                    byte_count += path.stat().st_size
        phrase = f"DELETE LOCAL VIDEO {project_id}" + (f" REVISION {revision_id}" if revision_id else "")
        return LocalVideoDeletePreview(
            project_id=project_id,
            revision_id=revision_id,
            artifact_count=count,
            byte_count=byte_count,
            includes_retained_audio=True,
            confirmation_phrase=phrase,
        )

    def explicit_delete(self, project_id: str, *, revision_id: str | None, confirmation: str) -> None:
        preview = self.delete_preview(project_id, revision_id)
        if confirmation != preview.confirmation_phrase:
            raise LocalVideoArchiveError("The exact deletion confirmation phrase is required")
        with self._lock, self.connect() as connection:
            rows = connection.execute(
                """
                SELECT revision_id, storage_key, current FROM local_video_revisions
                WHERE project_id=? AND deleted_at IS NULL
                """,
                (project_id,),
            ).fetchall()
            targets = [row for row in rows if revision_id is None or row["revision_id"] == revision_id]
            if not targets:
                raise KeyError("Local video project not found")
            now = _iso()
            for row in targets:
                connection.execute(
                    "UPDATE local_video_revisions SET deleted_at=?, current=0 WHERE revision_id=?",
                    (now, row["revision_id"]),
                )
            remaining = connection.execute(
                """
                SELECT revision_id FROM local_video_revisions
                WHERE project_id=? AND deleted_at IS NULL ORDER BY revision_number DESC LIMIT 1
                """,
                (project_id,),
            ).fetchone()
            if remaining is None:
                connection.execute(
                    """
                    UPDATE local_video_projects SET deleted_at=?, current_revision_id=NULL, status='deleted', updated_at=?
                    WHERE project_id=?
                    """,
                    (now, now, project_id),
                )
            else:
                next_revision = str(remaining["revision_id"])
                connection.execute(
                    "UPDATE local_video_revisions SET current=1 WHERE revision_id=?",
                    (next_revision,),
                )
                connection.execute(
                    """
                    UPDATE local_video_projects SET current_revision_id=?, status='analysis_archived', updated_at=?
                    WHERE project_id=?
                    """,
                    (next_revision, now, project_id),
                )
            self._audit(
                connection,
                project_id,
                revision_id,
                "explicit_delete",
                {"revisionId": revision_id, "artifactCount": preview.artifact_count},
            )
        for row in targets:
            root = (self.state_root / str(row["storage_key"])).resolve()
            if self.projects_root in root.parents and root.is_dir():
                shutil.rmtree(root)
