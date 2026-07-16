from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from ..config import Settings
from ..privacy import secure_private_file
from .pipeline import AnalysisCancelled, analyze_audio


def _settings_from_payload(payload: dict[str, Any]) -> Settings:
    values = dict(payload)
    for name in ("data_dir", "model_cache_dir"):
        raw = values.get(name)
        if not isinstance(raw, str):
            raise ValueError("Worker settings contain an invalid path.")
        values[name] = Path(raw)
    for name in ("cors_origins", "allowed_hosts"):
        raw = values.get(name)
        if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
            raise ValueError("Worker settings contain an invalid local-boundary list.")
        values[name] = tuple(raw)
    return Settings(**values)


def _load_input(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.stat().st_size > 1_000_000:
        raise ValueError("Worker input is unavailable or too large.")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Worker input is invalid.")
    return payload


def _write_result(path: Path, result: str) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(result, encoding="utf-8")
    secure_private_file(temporary)
    os.replace(temporary, path)
    secure_private_file(path)


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--decoded", required=True)
    parser.add_argument("--progress", required=True)
    parser.add_argument("--cancel", required=True)
    arguments = parser.parse_args()
    try:
        payload = _load_input(Path(arguments.input))
        file_data = payload.get("file")
        settings_data = payload.get("settings")
        job_id = payload.get("jobId")
        requested_mode = payload.get("requestedMode")
        if (
            not isinstance(file_data, dict)
            or not isinstance(settings_data, dict)
            or not isinstance(job_id, str)
            or not isinstance(requested_mode, str)
        ):
            raise ValueError("Worker input fields are invalid.")
        result = analyze_audio(
            arguments.decoded,
            file_data,
            job_id,
            requested_mode,
            arguments.progress,
            arguments.cancel,
            _settings_from_payload(settings_data),
            bool(payload.get("enableGenreAnalysis", False)),
            bool(payload.get("enableLyricsAnalysis", False)),
            bool(payload.get("lyricsConsentConfirmed", False)),
            bool(payload.get("deriveLyricalThemes", False)),
        )
        _write_result(Path(arguments.output), result)
    except AnalysisCancelled:
        return 2
    except Exception as exc:
        # Only the exception class crosses the process boundary; no paths,
        # metadata, or derived content are written to stderr.
        sys.stderr.write(f"worker_error={type(exc).__name__}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
