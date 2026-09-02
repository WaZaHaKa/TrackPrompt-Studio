from __future__ import annotations

import json
import re
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    required = [
        "project-config.json",
        "creative-bible.json",
        "continuity-profile.json",
        "chapter-map.json",
        "shot-bank.json",
        "edit-blueprint.json",
        "render-plan.json",
        "model-profile.json",
        "hardware-policy.json",
        "rights-and-credits.json",
        "persistent-analysis-policy.json",
        "CODEX_IMPLEMENTATION_PROMPT.md",
    ]
    missing = [name for name in required if not (root / name).is_file()]
    if missing:
        raise RuntimeError(f"Missing required files: {missing}")

    for path in root.rglob("*.json"):
        with path.open("r", encoding="utf-8") as handle:
            json.load(handle)

    shot_bank = json.loads((root / "shot-bank.json").read_text(encoding="utf-8"))
    shots = shot_bank["shots"]
    if shot_bank["shotCount"] != 16 or len(shots) != 16:
        raise RuntimeError("Expected exactly 16 shots")

    expected_time = 0.0
    for index, shot in enumerate(shots, start=1):
        expected_id = f"shot-{index:03d}"
        if shot["shotId"] != expected_id:
            raise RuntimeError(f"Expected {expected_id}, got {shot['shotId']}")
        start = float(shot["provisionalStartSeconds"])
        end = float(shot["provisionalEndSeconds"])
        if abs(start - expected_time) > 1e-6 or end <= start:
            raise RuntimeError(f"Invalid timing at {expected_id}: {start}–{end}")
        words = re.findall(r"\b[\w'-]+\b", shot["prompt"])
        if len(words) > 100:
            raise RuntimeError(f"{expected_id} motion prompt has {len(words)} words")
        expected_time = end

    if abs(expected_time - 149.0) > 1e-6:
        raise RuntimeError(f"Expected 149.0 seconds, got {expected_time}")

    print("PACKAGE_VALIDATION_PASS")
    print(f"root={root}")
    print("shots=16")
    print("duration=149.000")
    return 0


if __name__ == "__main__":
    sys.exit(main())
