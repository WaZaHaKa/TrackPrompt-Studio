from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..analysis.pipeline import analyze_audio
from ..analysis_archive import AnalysisArchiveRepository
from ..config import Settings
from ..media import decode_for_analysis, probe_media
from ..privacy import secure_private_directory, secure_private_file


@dataclass(frozen=True, slots=True)
class ProjectAnalysis:
    analysis_id: str
    value: dict[str, Any]
    reused: bool


def _write_private(path: Path, data: bytes) -> None:
    temporary = path.with_name(f".{path.name}.partial-{uuid4().hex}")
    try:
        temporary.write_bytes(data)
        secure_private_file(temporary)
        os.replace(temporary, path)
        secure_private_file(path)
    finally:
        temporary.unlink(missing_ok=True)


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Archived analysis is invalid")
    return value


def find_archived_analysis(
    archive: AnalysisArchiveRepository,
    *,
    audio_sha256: str,
) -> ProjectAnalysis | None:
    with archive.connect() as connection:
        row = connection.execute(
            """
            SELECT analysis_id FROM analysis_catalogue
            WHERE source_sha256=? AND deleted_at IS NULL AND archive_health='healthy'
            ORDER BY updated_at DESC LIMIT 1
            """,
            (audio_sha256,),
        ).fetchone()
    if row is None:
        return None
    analysis_id = str(row["analysis_id"])
    path = archive.resolve_artifact(analysis_id, "analysis")
    if path is None:
        return None
    return ProjectAnalysis(analysis_id=analysis_id, value=_load_json(path), reused=True)


def load_archived_analysis(
    archive: AnalysisArchiveRepository,
    analysis_id: str,
    *,
    expected_audio_sha256: str,
) -> ProjectAnalysis:
    record = archive.get(analysis_id)
    if record is None:
        raise KeyError("Analysis not found")
    with archive.connect() as connection:
        row = connection.execute(
            "SELECT source_sha256 FROM analysis_catalogue WHERE analysis_id=? AND deleted_at IS NULL",
            (analysis_id,),
        ).fetchone()
    if row is None or row["source_sha256"] != expected_audio_sha256:
        raise ValueError("The selected analysis does not match the project audio content")
    path = archive.resolve_artifact(analysis_id, "analysis")
    if path is None:
        raise ValueError("The selected analysis snapshot is unavailable")
    return ProjectAnalysis(analysis_id=analysis_id, value=_load_json(path), reused=True)


def run_and_archive_analysis(
    *,
    source_audio: Path,
    audio_sha256: str,
    settings: Settings,
    archive: AnalysisArchiveRepository,
) -> ProjectAnalysis:
    analysis_id = str(uuid4())
    created_at = datetime.now(UTC)
    workspace = (settings.data_dir / "local-video-analysis-work" / analysis_id).resolve()
    expected_parent = (settings.data_dir / "local-video-analysis-work").resolve()
    if workspace.parent != expected_parent:
        raise ValueError("Analysis workspace identity is invalid")
    workspace.mkdir(parents=True, exist_ok=False)
    secure_private_directory(workspace)
    try:
        source = workspace / "source.bin"
        with source_audio.open("rb") as input_stream, source.open("xb") as output_stream:
            shutil.copyfileobj(input_stream, output_stream, length=4 * 1024 * 1024)
            output_stream.flush()
            os.fsync(output_stream.fileno())
        secure_private_file(source)
        import hashlib

        digest = hashlib.sha256()
        with source.open("rb") as stream:
            while chunk := stream.read(4 * 1024 * 1024):
                digest.update(chunk)
        if digest.hexdigest() != audio_sha256:
            raise ValueError("The project audio changed during analysis staging")
        probe = probe_media(
            source,
            "project-audio.wav",
            settings,
            max_bytes=max(settings.max_upload_bytes, source.stat().st_size),
            max_duration_seconds=settings.max_single_track_analysis_seconds,
            source_kind="project track",
        )
        decoded = decode_for_analysis(probe, workspace / "decoded.wav", settings)
        progress = workspace / "progress.json"
        cancel = workspace / "cancel.requested"
        result = analyze_audio(
            str(decoded),
            probe.file.model_dump(mode="json", by_alias=True),
            analysis_id,
            "fast",
            str(progress),
            str(cancel),
            settings,
            False,
            False,
            False,
            False,
        )
        value = json.loads(result)
        if not isinstance(value, dict):
            raise ValueError("The local analyzer returned an invalid result")
        _write_private(workspace / "analysis.json", result.encode("utf-8"))
        _write_private(workspace / "detected-analysis.json", result.encode("utf-8"))
        archive.archive_completed(
            analysis_id=analysis_id,
            display_name="Local video project analysis",
            created_at=created_at,
            updated_at=datetime.now(UTC),
            job_directory=workspace,
        )
        return ProjectAnalysis(analysis_id=analysis_id, value=value, reused=False)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
