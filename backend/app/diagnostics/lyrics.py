from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

from ..config import Settings
from ..lyrics.transcriber import FasterWhisperLyricsAdapter


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke", action="store_true")
    arguments = parser.parse_args()
    adapter = FasterWhisperLyricsAdapter(Settings.from_env())
    capability = adapter.capability()
    payload: dict[str, object] = capability.model_dump(mode="json", by_alias=True)
    if arguments.smoke and capability.available:
        with tempfile.TemporaryDirectory(prefix="trackprompt-lyrics-smoke-") as directory:
            path = Path(directory) / "silence.wav"
            sf.write(path, np.zeros(16_000, dtype=np.float32), 16_000, subtype="PCM_16")
            model = adapter._load()
            segments, info = model.transcribe(str(path), beam_size=1, vad_filter=True)
            list(segments)
            payload["tinyInference"] = bool(getattr(info, "duration", 0.0) >= 0.0)
    adapter.cleanup()
    print(json.dumps(payload, indent=2))
    return 0 if capability.available and (not arguments.smoke or payload.get("tinyInference") is True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
