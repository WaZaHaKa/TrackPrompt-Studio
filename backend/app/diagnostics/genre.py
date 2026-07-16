from __future__ import annotations

import argparse
import json

import numpy as np

from ..config import Settings
from ..tagging.music import TransformersClapMusicTagger


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke", action="store_true")
    arguments = parser.parse_args()
    adapter = TransformersClapMusicTagger(Settings.from_env())
    capability = adapter.capability()
    payload: dict[str, object] = capability.model_dump(mode="json", by_alias=True)
    if arguments.smoke and capability.available:
        sample_rate = 16_000
        time = np.arange(sample_rate * 2, dtype=np.float32) / sample_rate
        signal = (0.08 * np.sin(2 * np.pi * 220 * time)).astype(np.float32)
        scores = adapter._similarities(
            signal,
            [("electronic", "electronic music with synthesized sound"), ("acoustic", "acoustic folk performance")],
        )
        payload["tinyInference"] = all(np.isfinite(value) for value in scores.values())
        payload["similarities"] = scores
    adapter.cleanup()
    print(json.dumps(payload, indent=2))
    return 0 if capability.available and (not arguments.smoke or payload.get("tinyInference") is True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
