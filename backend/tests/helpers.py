from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

import soundfile as sf

from app.analysis.pipeline import analyze_audio
from app.config import Settings
from app.schemas import AnalysisResult, FileInfo

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPOSITORY_ROOT / "test-fixtures"


def settings_for(data_dir: Path, *, ttl_minutes: int = 60) -> Settings:
    _ = ttl_minutes  # Legacy test-call compatibility; automatic TTL is disabled.
    return Settings(
        data_dir=data_dir,
        max_upload_mb=20,
        max_duration_seconds=120,
        analysis_workers=1,
        max_pending_jobs=2,
        model_cache_dir=data_dir / "models",
        ffmpeg_path=os.getenv("FFMPEG_PATH", shutil.which("ffmpeg") or "ffmpeg"),
        ffprobe_path=os.getenv("FFPROBE_PATH", shutil.which("ffprobe") or "ffprobe"),
        subprocess_timeout_seconds=30,
        analysis_timeout_seconds=120,
        cors_origins=("http://localhost:5173",),
        allowed_hosts=("localhost", "127.0.0.1", "::1", "testserver"),
        log_level="WARNING",
        enable_demucs=False,
        demucs_model_name="htdemucs",
    )


def analysis_for(path: Path, work_dir: Path, *, display_name: str = "source-track.wav") -> AnalysisResult:
    info = sf.info(path)
    file_info = FileInfo(
        display_name=display_name,
        duration_seconds=float(info.duration),
        sample_rate=info.samplerate,
        channels=info.channels,
        codec="pcm_s16le",
        container="wav",
        size_bytes=path.stat().st_size,
        private_metadata={"artist": "Private Artist", "title": "Private Title"},
    )
    work_dir.mkdir(parents=True, exist_ok=True)
    serialized = analyze_audio(
        str(path),
        file_info.model_dump(mode="json", by_alias=True),
        "11111111-1111-4111-8111-111111111111",
        "fast",
        str(work_dir / "progress.json"),
        str(work_dir / "cancel.flag"),
    )
    return AnalysisResult.model_validate_json(serialized)


def feature_payload(value: Any, confidence: str = "high", method: str = "test") -> dict[str, Any]:
    return {
        "value": value,
        "confidence": confidence,
        "method": method,
        "alternatives": [],
        "userEdited": False,
    }
