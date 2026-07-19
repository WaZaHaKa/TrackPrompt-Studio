from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from .schemas import TrackPromptVisualCueSheet

_PRIVATE_KEYS = {
    "displayname",
    "filename",
    "privateMetadata".casefold(),
    "waveformPeaks".casefold(),
    "lyrics",
    "transcript",
    "promptpackage",
    "promptcandidates",
    "sourceaudiopath",
    "uploadpath",
    "stempath",
    "modelcachepath",
}
_WINDOWS_ABSOLUTE = re.compile(r"(?i)\b[a-z]:[\\/]")


def _walk(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if str(key).casefold() in _PRIVATE_KEYS:
                raise ValueError("visual cue sheet contains a private field")
            _walk(nested)
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for nested in value:
            _walk(nested)
        return
    if isinstance(value, str):
        normalized = value.replace("\\", "/").casefold()
        if _WINDOWS_ABSOLUTE.search(value) or normalized.startswith(("/data/", "/home/", "/users/")):
            raise ValueError("visual cue sheet contains a private filesystem path")


def validate_public_cue_sheet(cue_sheet: TrackPromptVisualCueSheet) -> TrackPromptVisualCueSheet:
    payload = cue_sheet.model_dump(mode="json", by_alias=True)
    _walk(payload)
    return cue_sheet
