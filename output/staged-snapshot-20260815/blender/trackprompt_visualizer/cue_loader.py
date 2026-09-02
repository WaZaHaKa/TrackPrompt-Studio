from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .validation import MAX_CUE_BYTES, VisualizerValidationError, validate_cue_sheet, validate_input_file


def _reject_nonfinite(token: str) -> None:
    raise VisualizerValidationError(f"Non-finite JSON number {token!r} is not allowed.")


def load_cue_sheet(path: str | Path) -> dict[str, Any]:
    cue_path = validate_input_file(path, label="Cue sheet", suffixes={".json"})
    if cue_path.stat().st_size > MAX_CUE_BYTES:
        raise VisualizerValidationError("Cue sheet exceeds the size limit.")
    try:
        parsed = json.loads(
            cue_path.read_text(encoding="utf-8"),
            parse_constant=_reject_nonfinite,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise VisualizerValidationError("Cue sheet is not valid UTF-8 JSON.") from exc
    return validate_cue_sheet(parsed)
