from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

from ..adapters import DeepAdapterError, deep_adapters, run_demucs
from ..config import Settings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke", action="store_true")
    arguments = parser.parse_args()
    settings = Settings.from_env()
    capability = deep_adapters(settings)[0]
    payload: dict[str, object] = capability.model_dump(mode="json", by_alias=True)
    if arguments.smoke and capability.available:
        with tempfile.TemporaryDirectory(prefix="trackprompt-demucs-smoke-") as directory_text:
            directory = Path(directory_text)
            source = directory / "synthetic.wav"
            sample_rate = 44_100
            time = np.arange(sample_rate * 2, dtype=np.float32) / sample_rate
            signal = (0.08 * np.sin(2 * np.pi * 220 * time) + 0.03 * np.sin(2 * np.pi * 440 * time)).astype(np.float32)
            sf.write(source, signal, sample_rate, subtype="PCM_16")
            output = directory / "stems"
            try:
                stems = run_demucs(source, output, settings, device=capability.selected_device)
            except DeepAdapterError as exc:
                payload["tinyInference"] = False
                payload["errorType"] = type(exc).__name__
            else:
                payload["tinyInference"] = all(
                    path.is_file() and path.stat().st_size > 44 for path in stems.values()
                )
            finally:
                shutil.rmtree(output, ignore_errors=True)
                payload["temporaryStemsRemoved"] = not output.exists()
    print(json.dumps(payload, indent=2))
    return 0 if capability.available and (not arguments.smoke or payload.get("tinyInference") is True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
