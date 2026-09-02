from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from pydantic import BaseModel

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from backend.app.cinematic.release_finalization import (  # noqa: E402
    ReleaseFinalizationError,
    finalize_horizontal_release,
    load_profile_preparation_request,
    load_release_finalization_request,
    prepare_versioned_profiles,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare versioned Andromeda V2 render profiles or finalize a fresh "
            "horizontal-only, operator-gated release from exact reviewed evidence. "
            "This tool never invokes Blender, a renderer, or an encoder."
        )
    )
    commands = parser.add_subparsers(dest="command", required=True)

    profiles = commands.add_parser(
        "prepare-profiles",
        help=(
            "Create new scene-bound horizontal and disabled-vertical profiles "
            "from explicit immutable base profiles."
        ),
    )
    profiles.add_argument("--repository-root", type=Path, required=True)
    profiles.add_argument("--request", type=Path, required=True)
    profiles.add_argument("--output-directory", type=Path, required=True)
    profiles.add_argument(
        "--overwrite",
        action="store_true",
        help=(
            "Deprecated compatibility flag. Rejected: profile publication "
            "requires a fresh output directory."
        ),
    )

    finalize = commands.add_parser(
        "finalize",
        help=(
            "Validate exact human-reviewed evidence and write a fresh calibration, "
            "technical authorization, release report, and package manifest."
        ),
    )
    finalize.add_argument("--repository-root", type=Path, required=True)
    finalize.add_argument("--request", type=Path, required=True)
    finalize.add_argument("--output-directory", type=Path, required=True)
    finalize.add_argument(
        "--overwrite",
        action="store_true",
        help=(
            "Deprecated compatibility flag. Rejected: release publication "
            "requires a fresh output directory."
        ),
    )
    return parser


def _print_json(payload: object, *, stream: TextIO = sys.stdout) -> None:
    if isinstance(payload, BaseModel):
        payload = payload.model_dump(mode="json", by_alias=True)
    print(
        json.dumps(
            payload,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ),
        file=stream,
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "prepare-profiles":
            request = load_profile_preparation_request(args.request)
            result = prepare_versioned_profiles(
                args.repository_root,
                request,
                args.output_directory,
                overwrite=args.overwrite,
            )
            _print_json(result)
            return 0

        request = load_release_finalization_request(args.request)
        result = finalize_horizontal_release(
            args.repository_root,
            request,
            args.output_directory,
            overwrite=args.overwrite,
        )
        _print_json(result)
        return 0
    except (OSError, ValueError, ReleaseFinalizationError) as exc:
        _print_json(
            {
                "ok": False,
                "error": {
                    "code": "andromeda-release-finalization-rejected",
                    "message": str(exc),
                },
                "productionStartAllowed": False,
                "productionRenderStarted": False,
                "externalProcessesStarted": False,
            },
            stream=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
