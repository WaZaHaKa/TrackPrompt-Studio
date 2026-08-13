from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from ..video_generation.mission_models import VideoGenerationEvent, VideoJobRecord
from .eta import EtaPersistentState
from .models import JobRecord, LogEntry, RenderEvent
from .render_contracts import MediaRenderJob


class MissionControlStore:
    def __init__(self, database_path: Path, *, event_retention: int = 50_000) -> None:
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self.database_path = database_path
        self.event_retention = event_retention
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(database_path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._migrate()

    def _migrate(self) -> None:
        with self._lock, self._connection:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS mission_control_jobs (
                    id TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    renderer TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS mission_control_jobs_updated
                ON mission_control_jobs(updated_at DESC);

                CREATE TABLE IF NOT EXISTS mission_control_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    FOREIGN KEY(job_id) REFERENCES mission_control_jobs(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS mission_control_events_job_sequence
                ON mission_control_events(job_id, sequence);

                CREATE TABLE IF NOT EXISTS mission_control_logs (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    level TEXT NOT NULL,
                    message TEXT NOT NULL,
                    FOREIGN KEY(job_id) REFERENCES mission_control_jobs(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS mission_control_logs_job_sequence
                ON mission_control_logs(job_id, sequence);

                CREATE TABLE IF NOT EXISTS mission_control_settings (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS mission_control_media_render_jobs (
                    id TEXT PRIMARY KEY,
                    updated_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS mission_control_eta_states (
                    job_id TEXT PRIMARY KEY,
                    updated_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS mission_control_video_generation_jobs (
                    id TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS mission_control_video_generation_jobs_updated
                ON mission_control_video_generation_jobs(updated_at DESC);

                CREATE TABLE IF NOT EXISTS mission_control_video_generation_events (
                    sequence INTEGER PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    FOREIGN KEY(job_id) REFERENCES mission_control_video_generation_jobs(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS mission_control_video_generation_events_job_sequence
                ON mission_control_video_generation_events(job_id, sequence);

                CREATE TABLE IF NOT EXISTS mission_control_event_sequence (
                    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                    next_sequence INTEGER NOT NULL
                );
                """
            )
            highest = self._connection.execute(
                """
                SELECT MAX(sequence) AS sequence FROM (
                    SELECT COALESCE(MAX(sequence), 0) AS sequence FROM mission_control_events
                    UNION ALL
                    SELECT COALESCE(MAX(sequence), 0) AS sequence
                    FROM mission_control_video_generation_events
                )
                """
            ).fetchone()
            next_sequence = (int(highest["sequence"]) if highest is not None else 0) + 1
            self._connection.execute(
                """
                INSERT INTO mission_control_event_sequence(singleton, next_sequence)
                VALUES (1, ?)
                ON CONFLICT(singleton) DO UPDATE SET
                    next_sequence = MAX(next_sequence, excluded.next_sequence)
                """,
                (next_sequence,),
            )

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def healthcheck(self) -> bool:
        with self._lock:
            row = self._connection.execute("SELECT 1 AS ok").fetchone()
        return row is not None and int(row["ok"]) == 1

    def put_job(self, job: JobRecord) -> None:
        payload = job.model_dump_json(by_alias=False)
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO mission_control_jobs(
                    id, state, renderer, created_at, updated_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    state = excluded.state,
                    renderer = excluded.renderer,
                    updated_at = excluded.updated_at,
                    payload_json = excluded.payload_json
                """,
                (
                    job.id,
                    job.state.value,
                    job.renderer.value,
                    job.created_at.isoformat(),
                    job.updated_at.isoformat(),
                    payload,
                ),
            )

    def get_job(self, job_id: str) -> JobRecord | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload_json FROM mission_control_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
        if row is None:
            return None
        return JobRecord.model_validate_json(str(row["payload_json"]))

    def list_jobs(self, *, limit: int = 100) -> list[JobRecord]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT payload_json FROM mission_control_jobs
                ORDER BY updated_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [JobRecord.model_validate_json(str(row["payload_json"])) for row in rows]

    def put_media_render_job(self, job: MediaRenderJob) -> None:
        """Persist the reusable V2 job contract in Mission Control's canonical DB."""
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO mission_control_media_render_jobs(id, updated_at, payload_json)
                VALUES (?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    updated_at = excluded.updated_at,
                    payload_json = excluded.payload_json
                """,
                (
                    job.id,
                    job.updated_at.isoformat(),
                    job.model_dump_json(by_alias=False),
                ),
            )

    def get_media_render_job(self, job_id: str) -> MediaRenderJob | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload_json FROM mission_control_media_render_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
        if row is None:
            return None
        return MediaRenderJob.model_validate_json(str(row["payload_json"]))

    def put_eta_state(self, job_id: str, state: EtaPersistentState) -> None:
        """Persist robust ETA observations so restarts retain measured history."""
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO mission_control_eta_states(job_id, updated_at, payload_json)
                VALUES (?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    updated_at = excluded.updated_at,
                    payload_json = excluded.payload_json
                """,
                (
                    job_id,
                    state.updated_at.isoformat(),
                    state.model_dump_json(by_alias=False),
                ),
            )

    def get_eta_state(self, job_id: str) -> EtaPersistentState | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload_json FROM mission_control_eta_states WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        if row is None:
            return None
        return EtaPersistentState.model_validate_json(str(row["payload_json"]))

    def put_video_job(self, job: VideoJobRecord) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO mission_control_video_generation_jobs(
                    id, state, created_at, updated_at, payload_json
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    state = excluded.state,
                    updated_at = excluded.updated_at,
                    payload_json = excluded.payload_json
                """,
                (
                    job.id,
                    job.state.value,
                    job.created_at.isoformat(),
                    job.updated_at.isoformat(),
                    job.model_dump_json(by_alias=False),
                ),
            )

    def get_video_job(self, job_id: str) -> VideoJobRecord | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload_json FROM mission_control_video_generation_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
        if row is None:
            return None
        return VideoJobRecord.model_validate_json(str(row["payload_json"]))

    def list_video_jobs(self, *, limit: int = 100) -> list[VideoJobRecord]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT payload_json FROM mission_control_video_generation_jobs
                ORDER BY updated_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [VideoJobRecord.model_validate_json(str(row["payload_json"])) for row in rows]

    def _allocate_event_sequence_locked(self) -> int:
        row = self._connection.execute(
            "SELECT next_sequence FROM mission_control_event_sequence WHERE singleton = 1"
        ).fetchone()
        if row is None:
            raise RuntimeError("Mission Control event sequence is unavailable")
        sequence = int(row["next_sequence"])
        self._connection.execute(
            "UPDATE mission_control_event_sequence SET next_sequence = ? WHERE singleton = 1",
            (sequence + 1,),
        )
        return sequence

    def append_event(self, event: RenderEvent) -> RenderEvent:
        timestamp = event.timestamp.isoformat()
        with self._lock, self._connection:
            sequence = self._allocate_event_sequence_locked()
            stored = event.model_copy(update={"sequence": sequence})
            self._connection.execute(
                """
                INSERT INTO mission_control_events(sequence, job_id, timestamp, payload_json)
                VALUES (?, ?, ?, ?)
                """,
                (sequence, event.job_id, timestamp, stored.model_dump_json(by_alias=False)),
            )
            cutoff = sequence - self.event_retention
            if cutoff > 0:
                self._connection.execute(
                    "DELETE FROM mission_control_events WHERE sequence <= ?",
                    (cutoff,),
                )
        return stored

    def append_video_event(self, event: VideoGenerationEvent) -> VideoGenerationEvent:
        with self._lock, self._connection:
            sequence = self._allocate_event_sequence_locked()
            stored = event.model_copy(update={"sequence": sequence})
            self._connection.execute(
                """
                INSERT INTO mission_control_video_generation_events(
                    sequence, job_id, timestamp, payload_json
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    sequence,
                    event.job_id,
                    event.timestamp.isoformat(),
                    stored.model_dump_json(by_alias=False),
                ),
            )
            cutoff = sequence - self.event_retention
            if cutoff > 0:
                self._connection.execute(
                    "DELETE FROM mission_control_video_generation_events WHERE sequence <= ?",
                    (cutoff,),
                )
        return stored

    def events_after(
        self,
        after_sequence: int,
        *,
        job_id: str | None = None,
        limit: int = 1_000,
    ) -> list[RenderEvent | VideoGenerationEvent]:
        job_filter = "" if job_id is None else "AND job_id = ?"
        query = f"""
            SELECT payload_json, event_kind FROM (
                SELECT sequence, job_id, payload_json, 'render' AS event_kind
                FROM mission_control_events WHERE sequence > ? {job_filter}
                UNION ALL
                SELECT sequence, job_id, payload_json, 'video_generation' AS event_kind
                FROM mission_control_video_generation_events WHERE sequence > ? {job_filter}
            ) ORDER BY sequence LIMIT ?
        """
        parameters: tuple[object, ...]
        if job_id is None:
            parameters = (after_sequence, after_sequence, limit)
        else:
            parameters = (after_sequence, job_id, after_sequence, job_id, limit)
        with self._lock:
            rows = self._connection.execute(query, parameters).fetchall()
        return [
            VideoGenerationEvent.model_validate_json(str(row["payload_json"]))
            if str(row["event_kind"]) == "video_generation"
            else RenderEvent.model_validate_json(str(row["payload_json"]))
            for row in rows
        ]

    def latest_event_sequence(self) -> int:
        with self._lock:
            row = self._connection.execute(
                "SELECT next_sequence - 1 AS sequence FROM mission_control_event_sequence WHERE singleton = 1"
            ).fetchone()
        return int(row["sequence"]) if row is not None else 0

    def append_log(
        self,
        job_id: str,
        level: str,
        message: str,
        *,
        timestamp: datetime | None = None,
    ) -> LogEntry:
        recorded_at = timestamp or datetime.now(UTC)
        bounded_message = message.replace("\x00", "")[:8_192]
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                INSERT INTO mission_control_logs(job_id, timestamp, level, message)
                VALUES (?, ?, ?, ?)
                """,
                (job_id, recorded_at.isoformat(), level, bounded_message),
            )
            if cursor.lastrowid is None:
                raise RuntimeError("SQLite did not return a log sequence")
            sequence = int(cursor.lastrowid)
        return LogEntry(
            sequence=sequence,
            timestamp=recorded_at,
            job_id=job_id,
            level=cast(Any, level),
            message=bounded_message,
        )

    def logs(
        self,
        job_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 200,
    ) -> list[LogEntry]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT sequence, timestamp, job_id, level, message
                FROM mission_control_logs
                WHERE job_id = ? AND sequence > ?
                ORDER BY sequence LIMIT ?
                """,
                (job_id, after_sequence, limit),
            ).fetchall()
        return [
            LogEntry(
                sequence=int(row["sequence"]),
                timestamp=datetime.fromisoformat(str(row["timestamp"])),
                job_id=str(row["job_id"]),
                level=cast(Any, str(row["level"])),
                message=str(row["message"]),
            )
            for row in rows
        ]

    def get_setting(self, key: str, default: Any = None) -> Any:
        with self._lock:
            row = self._connection.execute(
                "SELECT value_json FROM mission_control_settings WHERE key = ?",
                (key,),
            ).fetchone()
        if row is None:
            return default
        try:
            return json.loads(str(row["value_json"]))
        except json.JSONDecodeError:
            return default

    def put_setting(self, key: str, value: Any) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO mission_control_settings(key, value_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value_json = excluded.value_json,
                    updated_at = excluded.updated_at
                """,
                (
                    key,
                    json.dumps(value, ensure_ascii=False, sort_keys=True),
                    datetime.now(UTC).isoformat(),
                ),
            )
