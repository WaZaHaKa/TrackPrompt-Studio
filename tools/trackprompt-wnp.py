"""Check/select the existing TrackPrompt WNP node. Never starts rendering or services."""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
from dataclasses import asdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

from app.wnp import WNPProjection  # noqa: E402
from app.wnp.check import local_check, probe  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("check", "select", "drain", "probe"), nargs="?", default="check")
    parser.add_argument("--root", type=Path, default=REPO)
    parser.add_argument("--run")
    parser.add_argument("--origin", default="http://127.0.0.1:9761")
    parser.add_argument("--project", default="trackprompt-studio")
    parser.add_argument("--node", default="render-rtx3060")
    args = parser.parse_args()
    try:
        if args.mode == "probe":
            result = probe(args.root, args.origin, expected_project=args.project, expected_node=args.node)
        elif args.mode in {"select", "drain"}:
            with WNPProjection(args.root) as projection:
                if args.mode == "select":
                    if not args.run:
                        parser.error("select requires --run")
                    projection.select_run(args.run)
                result = asdict(projection.drain())
        else:
            result = local_check(args.root)
        print(json.dumps(result, sort_keys=True))
        return 0 if result["ok"] else 2
    except Exception as exc:
        # Never print provider/transport exception strings or credential paths.
        code = "reader_or_store_permission_denied" if isinstance(exc, PermissionError) else "reader_or_store_missing" if isinstance(exc, FileNotFoundError) else "wnp_transport_unreachable" if isinstance(exc, (urllib.error.URLError, TimeoutError)) else "wnp_check_unavailable"
        if isinstance(exc, urllib.error.HTTPError):
            code = "reader_unauthorized" if exc.code in {401, 403} else "wnp_http_failure"
        if isinstance(getattr(exc, "code", None), str):
            code = exc.code
        print(json.dumps({"ok": False, "error": code,
                          "companion_ui": "unverified"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
