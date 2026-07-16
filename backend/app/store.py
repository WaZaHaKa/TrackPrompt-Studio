from __future__ import annotations

import json
import os
import shutil
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from .config import Settings
from .privacy import secure_private_directory, secure_private_file
from .schemas import AnalysisMode, JobStatus


def utc_now() -> datetime:
    return datetime.now(UTC)


def _serialize_time(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class JobRecord:
    job_id: str
    status: JobStatus
    requested_mode: AnalysisMode
    effective_mode: AnalysisMode
    stage: str
    message: str
    progress: int
    created_at: datetime
    updated_at: datetime
    expires_at: datetime
    display_name: str
    error_code: str | None
    error_message: str | None
    permission_confirmed: bool
    enable_lyrical_analysis: bool
    enable_genre_analysis: bool = False
    lyrics_consent_confirmed: bool = False
    derive_lyrical_themes: bool = False
    allow_feature_fallback: bool = False


class DeletionError(RuntimeError):
    pass


class JobStore:
    """SQLite lifecycle metadata plus job-local private payload files.

    Analysis and prompt content intentionally never enters SQLite. This makes the
    metadata-only boundary straightforward to inspect and lets deletion remove a
    job's private payload as one UUID-scoped directory.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.settings.ensure_directories()
        self._write_lock = threading.Lock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.settings.database_path, timeout=10)
        try:
            secure_private_file(self.settings.database_path)
        except OSError:
            connection.close()
            raise
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    requested_mode TEXT NOT NULL,
                    effective_mode TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    message TEXT NOT NULL,
                    progress INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    error_code TEXT,
                    error_message TEXT,
                    permission_confirmed INTEGER NOT NULL,
                    enable_lyrical_analysis INTEGER NOT NULL
                )
                """
            )
            existing_columns = {
                str(row[1]) for row in connection.execute("PRAGMA table_info(jobs)").fetchall()
            }
            migrations = {
                "enable_genre_analysis": "INTEGER NOT NULL DEFAULT 0",
                "lyrics_consent_confirmed": "INTEGER NOT NULL DEFAULT 0",
                "derive_lyrical_themes": "INTEGER NOT NULL DEFAULT 0",
                "allow_feature_fallback": "INTEGER NOT NULL DEFAULT 0",
            }
            for column, declaration in migrations.items():
                if column not in existing_columns:
                    connection.execute(f"ALTER TABLE jobs ADD COLUMN {column} {declaration}")
            connection.execute("CREATE INDEX IF NOT EXISTS jobs_expires_at_idx ON jobs(expires_at)")
        self._secure_database_files()

    def _secure_database_files(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            path = Path(f"{self.settings.database_path}{suffix}")
            if path.exists():
                secure_private_file(path)

    def healthcheck(self) -> bool:
        try:
            with self._connect() as connection:
                row = connection.execute("SELECT 1").fetchone()
            return row is not None and int(row[0]) == 1
        except sqlite3.Error:
            return False

    @staticmethod
    def canonical_job_id(job_id: str) -> str:
        try:
            parsed = UUID(job_id)
        except ValueError as exc:
            raise KeyError("Job not found") from exc
        canonical = str(parsed)
        if canonical != job_id.lower():
            raise KeyError("Job not found")
        return canonical

    def job_dir(self, job_id: str) -> Path:
        canonical = self.canonical_job_id(job_id)
        directory = (self.settings.jobs_dir / canonical).resolve()
        if directory.parent != self.settings.jobs_dir.resolve():
            raise KeyError("Job not found")
        return directory

    def create_job(
        self,
        job_id: str,
        mode: AnalysisMode,
        display_name: str,
        permission_confirmed: bool,
        enable_lyrical_analysis: bool,
        enable_genre_analysis: bool = False,
        lyrics_consent_confirmed: bool = False,
        derive_lyrical_themes: bool = False,
        allow_feature_fallback: bool = False,
    ) -> JobRecord:
        canonical = self.canonical_job_id(job_id)
        directory = self.job_dir(canonical)
        directory.mkdir(parents=False, exist_ok=False)
        secure_private_directory(directory)
        now = utc_now()
        expires = now + timedelta(minutes=self.settings.job_ttl_minutes)
        with self._write_lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO jobs (
                    job_id, status, requested_mode, effective_mode, stage, message, progress,
                    created_at, updated_at, expires_at, display_name, error_code, error_message,
                    permission_confirmed, enable_lyrical_analysis, enable_genre_analysis,
                    lyrics_consent_confirmed, derive_lyrical_themes, allow_feature_fallback
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?, ?, ?)
                """,
                (
                    canonical,
                    JobStatus.QUEUED.value,
                    mode.value,
                    mode.value,
                    "queued",
                    "Waiting for an analysis worker",
                    0,
                    _serialize_time(now),
                    _serialize_time(now),
                    _serialize_time(expires),
                    display_name,
                    int(permission_confirmed),
                    int(enable_lyrical_analysis),
                    int(enable_genre_analysis),
                    int(lyrics_consent_confirmed),
                    int(derive_lyrical_themes),
                    int(allow_feature_fallback),
                ),
            )
        self._secure_database_files()
        return self.require_job(canonical)

    def get_job(self, job_id: str) -> JobRecord | None:
        try:
            canonical = self.canonical_job_id(job_id)
        except KeyError:
            return None
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE job_id = ?", (canonical,)).fetchone()
        if row is None:
            return None
        return JobRecord(
            job_id=str(row["job_id"]),
            status=JobStatus(str(row["status"])),
            requested_mode=AnalysisMode(str(row["requested_mode"])),
            effective_mode=AnalysisMode(str(row["effective_mode"])),
            stage=str(row["stage"]),
            message=str(row["message"]),
            progress=int(row["progress"]),
            created_at=_parse_time(str(row["created_at"])),
            updated_at=_parse_time(str(row["updated_at"])),
            expires_at=_parse_time(str(row["expires_at"])),
            display_name=str(row["display_name"]),
            error_code=str(row["error_code"]) if row["error_code"] is not None else None,
            error_message=str(row["error_message"]) if row["error_message"] is not None else None,
            permission_confirmed=bool(row["permission_confirmed"]),
            enable_lyrical_analysis=bool(row["enable_lyrical_analysis"]),
            enable_genre_analysis=bool(row["enable_genre_analysis"]),
            lyrics_consent_confirmed=bool(row["lyrics_consent_confirmed"]),
            derive_lyrical_themes=bool(row["derive_lyrical_themes"]),
            allow_feature_fallback=bool(row["allow_feature_fallback"]),
        )

    def require_job(self, job_id: str) -> JobRecord:
        record = self.get_job(job_id)
        if record is None:
            raise KeyError("Job not found")
        return record

    def update_job(
        self,
        job_id: str,
        *,
        status: JobStatus | None = None,
        effective_mode: AnalysisMode | None = None,
        stage: str | None = None,
        message: str | None = None,
        progress: int | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> JobRecord:
        record = self.require_job(job_id)
        if record.status in {
            JobStatus.COMPLETED,
            JobStatus.CANCELLED,
            JobStatus.FAILED,
            JobStatus.EXPIRED,
        } and status is not None and status != record.status:
            return record
        next_status = status or record.status
        next_progress = progress if progress is not None else record.progress
        if next_status not in {
            JobStatus.CANCELLED,
            JobStatus.FAILED,
            JobStatus.EXPIRED,
        }:
            next_progress = max(record.progress, next_progress)
        values: dict[str, Any] = {
            "status": next_status.value,
            "effective_mode": (effective_mode or record.effective_mode).value,
            "stage": stage if stage is not None else record.stage,
            "message": message if message is not None else record.message,
            "progress": next_progress,
            "updated_at": _serialize_time(utc_now()),
            "error_code": error_code,
            "error_message": error_message,
            "job_id": record.job_id,
        }
        with self._write_lock, self._connect() as connection:
            connection.execute(
                """
                UPDATE jobs SET status = :status, effective_mode = :effective_mode, stage = :stage,
                    message = :message, progress = :progress, updated_at = :updated_at,
                    error_code = :error_code, error_message = :error_message
                WHERE job_id = :job_id
                """,
                values,
            )
        self._secure_database_files()
        return self.require_job(job_id)

    def expired_job_ids(self, now: datetime | None = None) -> list[str]:
        current = _serialize_time(now or utc_now())
        with self._connect() as connection:
            rows = connection.execute("SELECT job_id FROM jobs WHERE expires_at <= ?", (current,)).fetchall()
        return [str(row["job_id"]) for row in rows]

    def active_job_ids(self) -> list[str]:
        terminal = (
            JobStatus.COMPLETED.value,
            JobStatus.CANCELLED.value,
            JobStatus.FAILED.value,
            JobStatus.EXPIRED.value,
        )
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT job_id FROM jobs WHERE status NOT IN (?, ?, ?, ?)",
                terminal,
            ).fetchall()
        return [str(row["job_id"]) for row in rows]

    def mark_cleanup_pending(self, job_id: str) -> None:
        canonical = self.canonical_job_id(job_id)
        now = _serialize_time(utc_now())
        with self._write_lock, self._connect() as connection:
            connection.execute(
                """
                UPDATE jobs
                SET status = ?, stage = ?, message = ?, progress = 0,
                    updated_at = ?, expires_at = ?, error_code = ?, error_message = ?
                WHERE job_id = ?
                """,
                (
                    JobStatus.FAILED.value,
                    "cleanup_pending",
                    "Rejected upload cleanup is pending and will be retried",
                    now,
                    now,
                    "cleanup_pending",
                    "Private upload cleanup is pending and will be retried automatically.",
                    canonical,
                ),
            )
        self._secure_database_files()

    def delete_job(self, job_id: str) -> bool:
        try:
            canonical = self.canonical_job_id(job_id)
            directory = self.job_dir(canonical)
        except KeyError:
            return False
        existed = self.get_job(canonical) is not None or directory.exists()
        resolved_jobs = self.settings.jobs_dir.resolve()
        resolved_directory = directory.resolve()
        if resolved_directory.parent == resolved_jobs and resolved_directory.name == canonical and resolved_directory.exists():
            try:
                shutil.rmtree(resolved_directory)
            except OSError as exc:
                raise DeletionError("Private job files could not be fully removed; deletion will be retried.") from exc
            if resolved_directory.exists():
                raise DeletionError("Private job files could not be fully removed; deletion will be retried.")
        with self._write_lock, self._connect() as connection:
            connection.execute("DELETE FROM jobs WHERE job_id = ?", (canonical,))
        self._secure_database_files()
        return existed

    def write_json(self, job_id: str, filename: str, payload: dict[str, Any]) -> Path:
        if filename not in {
            "analysis.json", "detected-analysis.json", "prompt.json", "preferences.json",
            "lyrics.json", "detected-lyrics.json", "lyrics-summary.json",
        }:
            raise ValueError("Unsupported job payload filename")
        directory = self.job_dir(job_id)
        if not directory.is_dir():
            raise KeyError("Job not found")
        destination = directory / filename
        temporary = directory / f".{filename}.tmp"
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        temporary.write_text(data, encoding="utf-8")
        secure_private_file(temporary)
        os.replace(temporary, destination)
        secure_private_file(destination)
        return destination

    def delete_json(self, job_id: str, filename: str) -> None:
        if filename not in {"prompt.json", "preferences.json", "lyrics.json", "detected-lyrics.json", "lyrics-summary.json"}:
            raise ValueError("Unsupported job payload filename")
        path = self.job_dir(job_id) / filename
        path.unlink(missing_ok=True)
        if path.exists():
            raise OSError("Private job payload could not be invalidated.")

    def read_json(self, job_id: str, filename: str) -> dict[str, Any] | None:
        if filename not in {
            "analysis.json", "detected-analysis.json", "prompt.json", "preferences.json",
            "lyrics.json", "detected-lyrics.json", "lyrics-summary.json",
        }:
            raise ValueError("Unsupported job payload filename")
        path = self.job_dir(job_id) / filename
        if not path.is_file():
            return None
        if path.stat().st_size > 20_000_000:
            raise ValueError("Stored job payload exceeds the safety limit")
        parsed = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(parsed, dict):
            raise ValueError("Stored job payload is invalid")
        return parsed
