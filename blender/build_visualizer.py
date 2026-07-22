from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BLENDER_ROOT = Path(__file__).resolve().parent
if str(BLENDER_ROOT) not in sys.path:
    sys.path.insert(0, str(BLENDER_ROOT))

from trackprompt_visualizer.mcp_entrypoints import build_scene  # noqa: E402


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a TrackPrompt Blender visualizer preset.")
    parser.add_argument("--cues", required=True)
    parser.add_argument("--audio", required=True)
    parser.add_argument("--preset", default="abstract-geometry")
    parser.add_argument("--seed", type=int, default=84291)
    parser.add_argument("--config", help="Optional absolute visualizer configuration JSON path")
    parser.add_argument("--shot-plan", help="Required absolute V2 shot-plan JSON path")
    parser.add_argument("--output", required=True)
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    return parser.parse_args(arguments)


def main() -> int:
    args = _arguments()
    result = build_scene(
        args.cues,
        args.audio,
        args.output,
        args.preset,
        args.seed,
        config_path=args.config,
        shot_plan_path=args.shot_plan,
    )
    print(json.dumps(result, ensure_ascii=True, separators=(",", ":")))
    return 0 if result.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
