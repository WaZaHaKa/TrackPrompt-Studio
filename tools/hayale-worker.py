"""Stable local Hayale controller entrypoint; never creates a different run."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
PROJECT = REPO / "video-projects/hayale-flowers-promo/v2.1"


def bootstrap() -> None:
    sys.path.insert(0, str(REPO / "backend"))
    for path in (
        REPO / "backend/.venv/Lib/site-packages",
        Path("D:/TrackPrompt-ComfyUI/.venv/Lib/site-packages"),
    ):
        if str(path) not in sys.path:
            sys.path.append(str(path))


def module(name: str) -> Any:
    key = f"hayale_v21_{name}"
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, PROJECT / f"{name}.py")
        assert spec and spec.loader
        value = importlib.util.module_from_spec(spec)
        sys.modules[key] = value
        spec.loader.exec_module(value)
    return sys.modules[key]


def inspect(run_id: str) -> dict[str, Any]:
    prep, recovery = module("prepare"), module("recovery")
    run_root = prep.contained(prep.ROOT / "runs" / run_id)
    binding = prep.read(run_root / "run.json")
    if binding["profile"] != "quality":
        raise ValueError(
            "This incident continuation preserves the original quality profile"
        )
    manifest = prep.verify_prepared()
    runtime = module("runtime")
    if runtime.existing_server_available(run_root):
        with recovery.existing_server(run_root) as client:
            view = recovery.inspect_boundary(
                run_root, client, prep.PROFILES[binding["profile"]], manifest=manifest
            )
    else:
        source = module("durable").CompletedSnapshot(run_root)
        view = recovery.inspect_boundary(
            run_root, source, prep.PROFILES[binding["profile"]], manifest=manifest
        )
        if any(
            row["state"] not in {"raw_verified", "frames_complete", "not_submitted"}
            for row in view["scenes"].values()
        ):
            raise ValueError(
                "Stopped provider has an unresolved earlier request; no continuation"
            )
        view["providerSource"] = (
            "positive-retained-completion; no assumption about missing history"
        )
    held = recovery.lease_is_held(prep.ROOT)
    for row in view["scenes"].values():
        row.pop("history", None)
    blocked = held or any(
        row["state"] in {"ambiguous", "missing", "failed"}
        for row in view["scenes"].values()
    )
    return {
        **view,
        "ownerHeld": held,
        "gate": "HOLD_EXISTING_OWNER"
        if held
        else "HOLD_RECONCILIATION"
        if blocked
        else "READY_FOR_EXCLUSIVE_CONTINUATION",
        "canContinue": not blocked,
        "newRun": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", choices=["344ab3d2444c"], default="344ab3d2444c")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--check-only", action="store_true")
    actions.add_argument("--launch", action="store_true")
    actions.add_argument("--continue-existing", action="store_true")
    args = parser.parse_args()
    bootstrap()
    try:
        view = inspect(args.run_id)
        print(json.dumps(view, indent=2), flush=True)
        if not view["canContinue"]:
            return 3
        if not args.launch and not args.continue_existing:
            return 0
        if ".cache" in str(Path(sys.executable)).lower():
            raise ValueError(
                "Use the installed stable Python, not an agent-cache interpreter"
            )
        if args.launch:
            log_root = (
                REPO
                / ".trackprompt-data/hayale-v21/runs"
                / args.run_id
                / "controller-logs"
            )
            log_root.mkdir(exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            # Shared native-tested process primitive; importing it does not
            # initialize WNP or make execution depend on monitoring availability.
            spec = importlib.util.spec_from_file_location(
                "hayale_detached_launch", REPO / "tools/hayale-wnp.py"
            )
            assert spec and spec.loader
            launch = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = launch
            spec.loader.exec_module(launch)
            process = launch.spawn_detached_python(
                [
                    str(Path(__file__).resolve()),
                    "--run-id",
                    args.run_id,
                    "--continue-existing",
                ],
                log_root / f"{stamp}.stdout.log",
                log_root / f"{stamp}.stderr.log",
                cwd=REPO,
            )
            print(
                json.dumps(
                    {
                        "launchedPid": process.pid,
                        "run": args.run_id,
                        "acceptedByWorker": False,
                        "newRun": False,
                    }
                )
            )
            return 0
        # Reconciliation precedes any new submission; the controller itself then
        # acquires the same OS lock as the legacy worker before touching execution.
        prep = module("prepare")
        latest = prep.read(prep.ROOT / "latest-run.json")
        if Path(latest["runRoot"]).name != args.run_id:
            raise ValueError(
                "Latest-run pointer does not identify the explicitly requested run"
            )
        original = prep.read(Path(latest["runRoot"]) / "run.json")
        # Original binding is preserved; only reviewed orchestration modules may
        # change. Assets, finishing and generation settings stay exact.
        from app.video_generation.jsonio import (
            sha256_file,
            sha256_json,
            atomic_write_json,
        )

        for name in ("prepare.py", "finish.py"):
            if sha256_file(PROJECT / name) != original["code"][name]:
                raise ValueError(
                    "Preparation/finishing changed; this continuation cannot silently rebind them"
                )
        identity = {
            "preparation": original["preparation"],
            "profile": original["profile"],
            "silent": original["silent"],
            "code": {p.name: sha256_file(p) for p in sorted(PROJECT.glob("*.py"))},
        }
        atomic_write_json(
            Path(latest["runRoot"]) / "recovery/compatible-code.json",
            {
                "originalRunDigest": original["digest"],
                "approvedIdentityDigest": sha256_json(identity),
                "scope": "explicit same-run continuation after read-only reconciliation",
            },
        )
        result = module("run").create(
            profile=original["profile"],
            new_run=False,
            silent=original["silent"],
            existing_run_id=args.run_id,
        )
        print(json.dumps(result, indent=2))
        return 0
    except Exception as error:
        print(
            json.dumps(
                {
                    "gate": "HOLD",
                    "error": type(error).__name__,
                    "message": str(error)
                    if isinstance(error, ValueError)
                    else "Local continuation could not be proven safe",
                    "newRun": False,
                }
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
