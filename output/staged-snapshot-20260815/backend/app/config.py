from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from .privacy import secure_private_directory


def _positive_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value


def _origins() -> tuple[str, ...]:
    raw = os.getenv(
        "CORS_ORIGINS",
        (
            "http://localhost:5173,http://127.0.0.1:5173,"
            "http://localhost:4173,http://127.0.0.1:4173"
        ),
    )
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def _hosts() -> tuple[str, ...]:
    raw = os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1,::1,testserver")
    return tuple(item.strip().lower() for item in raw.split(",") if item.strip())


def _boolean(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")


def _device(name: str, default: str = "auto") -> str:
    value = os.getenv(name, default).strip().lower()
    if value not in {"auto", "cpu", "cuda"}:
        raise ValueError(f"{name} must be auto, cpu, or cuda")
    return value


def _safe_identifier(name: str, default: str, *, maximum: int = 160) -> str:
    value = os.getenv(name, default).strip() or default
    if len(value) > maximum or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]*", value) is None or ".." in value:
        raise ValueError(f"{name} must be a safe model identifier")
    return value


def _endpoint(name: str, default: str) -> str:
    value = os.getenv(name, default).strip().rstrip("/")
    if not re.fullmatch(r"http://(?:localhost|127\.0\.0\.1|[a-z][a-z0-9-]{0,62})(?::[0-9]{1,5})?", value):
        raise ValueError(f"{name} must be an HTTP endpoint on localhost or the private service network")
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    data_dir: Path
    max_upload_mb: int
    max_duration_seconds: int
    job_ttl_minutes: int
    analysis_workers: int
    max_pending_jobs: int
    model_cache_dir: Path
    ffmpeg_path: str
    ffprobe_path: str
    subprocess_timeout_seconds: int
    analysis_timeout_seconds: int
    cors_origins: tuple[str, ...]
    allowed_hosts: tuple[str, ...]
    log_level: str
    enable_demucs: bool
    demucs_model_name: str
    demucs_device: str = "auto"
    gpu_task_workers: int = 1
    enable_genre_tagger: bool = False
    genre_model_id: str = "laion/clap-htsat-unfused"
    genre_model_revision: str = "8fa0f1c6d0433df6e97c127f64b2a1d6c0dcda8a"
    genre_device: str = "auto"
    enable_lyrics_adapter: bool = False
    lyrics_model_name: str = "Systran/faster-whisper-small"
    lyrics_model_revision: str = "536b0662742c02347bc0e980a01041f333bce120"
    lyrics_device: str = "auto"
    lyrics_compute_type: str = "float16"
    lyrics_cpu_fallback: bool = False
    enable_local_prompt_writer: bool = False
    local_llm_model: str = "qwen2.5:7b-instruct-q4_K_M"
    local_llm_model_digest: str = "845dbda0ea48"
    local_llm_endpoint: str = "http://prompt-writer:11434"
    local_llm_timeout_seconds: int = 90
    local_llm_keep_loaded: bool = False
    prompt_writer_device: str = "cuda"
    upload_chunk_bytes: int = 1024 * 1024
    decoded_sample_rate: int = 16_000

    @classmethod
    def from_env(cls) -> Settings:
        repository_root = Path(__file__).resolve().parents[2]
        data_dir = Path(
            os.getenv("TRACKPROMPT_DATA_DIR", str(repository_root / ".trackprompt-data"))
        ).resolve()
        # A max-duration stereo analysis can use several hundred MiB even with
        # float32/coarse STFT features, so one worker is the safe default.
        workers = _positive_int("ANALYSIS_WORKERS", 1)
        demucs_model_name = os.getenv("DEMUCS_MODEL_NAME", "htdemucs").strip() or "htdemucs"
        if (
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", demucs_model_name) is None
            or ".." in demucs_model_name
        ):
            raise ValueError("DEMUCS_MODEL_NAME must be a safe local model identifier")
        demucs_device = _device("DEMUCS_DEVICE")
        lyrics_compute_type = os.getenv("LYRICS_COMPUTE_TYPE", "float16").strip().lower()
        if lyrics_compute_type not in {"float16", "int8_float16", "int8", "float32"}:
            raise ValueError("LYRICS_COMPUTE_TYPE is not supported")
        return cls(
            data_dir=data_dir,
            max_upload_mb=_positive_int("MAX_UPLOAD_MB", 200),
            max_duration_seconds=_positive_int("MAX_DURATION_SECONDS", 1200),
            job_ttl_minutes=_positive_int("JOB_TTL_MINUTES", 60),
            analysis_workers=workers,
            max_pending_jobs=_positive_int("MAX_PENDING_JOBS", workers * 2),
            model_cache_dir=Path(os.getenv("MODEL_CACHE_DIR", str(data_dir / "models"))).resolve(),
            ffmpeg_path=os.getenv("FFMPEG_PATH", "ffmpeg"),
            ffprobe_path=os.getenv("FFPROBE_PATH", "ffprobe"),
            subprocess_timeout_seconds=_positive_int("SUBPROCESS_TIMEOUT_SECONDS", 120),
            analysis_timeout_seconds=_positive_int("ANALYSIS_TIMEOUT_SECONDS", 600),
            cors_origins=_origins(),
            allowed_hosts=_hosts(),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            enable_demucs=_boolean("ENABLE_DEMUCS", False),
            demucs_model_name=demucs_model_name,
            demucs_device=demucs_device,
            gpu_task_workers=_positive_int("GPU_TASK_WORKERS", 1),
            enable_genre_tagger=_boolean("ENABLE_GENRE_TAGGER", False),
            genre_model_id=_safe_identifier("GENRE_MODEL_ID", "laion/clap-htsat-unfused"),
            genre_model_revision=_safe_identifier(
                "GENRE_MODEL_REVISION", "8fa0f1c6d0433df6e97c127f64b2a1d6c0dcda8a"
            ),
            genre_device=_device("GENRE_DEVICE"),
            enable_lyrics_adapter=_boolean("ENABLE_LYRICS_ADAPTER", False),
            lyrics_model_name=_safe_identifier("LYRICS_MODEL_NAME", "Systran/faster-whisper-small"),
            lyrics_model_revision=_safe_identifier(
                "LYRICS_MODEL_REVISION", "536b0662742c02347bc0e980a01041f333bce120"
            ),
            lyrics_device=_device("LYRICS_DEVICE"),
            lyrics_compute_type=lyrics_compute_type,
            lyrics_cpu_fallback=_boolean("LYRICS_CPU_FALLBACK", False),
            enable_local_prompt_writer=_boolean("ENABLE_LOCAL_PROMPT_WRITER", False),
            local_llm_model=_safe_identifier("LOCAL_LLM_MODEL", "qwen2.5:7b-instruct-q4_K_M"),
            local_llm_model_digest=_safe_identifier("LOCAL_LLM_MODEL_DIGEST", "845dbda0ea48"),
            local_llm_endpoint=_endpoint("LOCAL_LLM_ENDPOINT", "http://prompt-writer:11434"),
            local_llm_timeout_seconds=_positive_int("LOCAL_LLM_TIMEOUT_SECONDS", 90),
            local_llm_keep_loaded=_boolean("LOCAL_LLM_KEEP_LOADED", False),
            prompt_writer_device=_device("PROMPT_WRITER_DEVICE", "cuda"),
        )

    @property
    def genre_model_dir(self) -> Path:
        return self.model_cache_dir / "genre" / self.genre_model_id.replace("/", "--")

    @property
    def demucs_model_dir(self) -> Path:
        scoped = self.model_cache_dir / "demucs"
        return scoped if (scoped / "demucs-models.json").is_file() else self.model_cache_dir

    @property
    def lyrics_model_dir(self) -> Path:
        return self.model_cache_dir / "lyrics" / self.lyrics_model_name.replace("/", "--")

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"

    @property
    def database_path(self) -> Path:
        return self.data_dir / "trackprompt.sqlite3"

    @property
    def cancellations_dir(self) -> Path:
        return self.data_dir / ".cancellations"

    def ensure_directories(self) -> None:
        for directory in (
            self.data_dir,
            self.jobs_dir,
            self.model_cache_dir,
            self.cancellations_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)
            secure_private_directory(directory)
