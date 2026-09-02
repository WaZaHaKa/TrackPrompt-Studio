from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Sequence

from ..manifests import canonical_json_bytes, validate_package_manifest
from ..models import WorkerKind
from ..scheduler import SqliteScheduler
from ..storage import FilesystemStorage
from .blender import BlenderSubprocessRuntime, WorkerCommandRunner
from .core import RenderRuntime, WorkerConfig, WorkerError, WorkerService
from .mock import MockRenderRuntime


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a TrackPrompt cloud worker; mock mode is explicit and offline"
    )
    parser.add_argument("--package-manifest", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--storage-root", required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--worker-id", required=True)
    parser.add_argument("--worker-kind", choices=[item.value for item in WorkerKind], default="CLOUD")
    parser.add_argument("--mock", action="store_true")
    parser.add_argument("--remote-package")
    parser.add_argument("--blender")
    parser.add_argument("--nvidia-smi", default="nvidia-smi")
    parser.add_argument("--render-timeout-seconds", type=float, default=21_600)
    parser.add_argument("--run-until-idle", action="store_true")
    parser.add_argument("--lease-seconds", type=int, default=900)
    parser.add_argument("--heartbeat-seconds", type=int, default=60)
    parser.add_argument("--no-work-timeout-seconds", type=float, default=300)
    return parser


def run(
    argv: Sequence[str] | None = None,
    *,
    runner: WorkerCommandRunner | None = None,
) -> tuple[int, dict[str, object]]:
    try:
        args = _parser().parse_args(argv)
        manifest = json.loads(Path(args.package_manifest).read_text(encoding="utf-8-sig"))
        identities, _ = validate_package_manifest(manifest)
        runtime: RenderRuntime
        if args.mock:
            if args.remote_package is not None or args.blender is not None:
                raise ValueError("mock mode cannot accept production package or Blender paths")
            resolution = manifest["resolution"]
            image = manifest["image"]
            runtime = MockRenderRuntime(
                identities,
                blender_version=str(manifest["blenderVersion"]),
                width=int(resolution["width"]),
                height=int(resolution["height"]),
                bit_depth=int(image["bitDepth"]),
                image_format=str(image["format"]),
                extension=str(image["extension"]),
            )
        else:
            if args.remote_package is None or args.blender is None:
                raise ValueError(
                    "production mode requires --remote-package and --blender"
                )
            runtime = BlenderSubprocessRuntime(
                Path(args.remote_package),
                Path(args.blender),
                worker_id=args.worker_id,
                nvidia_smi_executable=args.nvidia_smi,
                render_timeout_seconds=args.render_timeout_seconds,
                runner=runner,
            )
        with SqliteScheduler(Path(args.database)) as scheduler:
            service = WorkerService(
                WorkerConfig(
                    args.job_id,
                    args.worker_id,
                    WorkerKind(args.worker_kind),
                    args.lease_seconds,
                    args.heartbeat_seconds,
                    no_work_timeout_seconds=args.no_work_timeout_seconds,
                ),
                manifest,
                scheduler,
                FilesystemStorage(Path(args.storage_root)),
                runtime,
            )
            results = service.run_until_idle() if args.run_until_idle else [service.run_once()]
        payload: dict[str, object] = {
            "ok": True,
            "mock": bool(args.mock),
            "results": [asdict(item) for item in results],
        }
        return (
            0 if all(item.outcome.value != "FAILED" for item in results) else 4,
            payload,
        )
    except (OSError, ValueError, KeyError, json.JSONDecodeError, WorkerError) as exc:
        return (
            2,
            {
                "ok": False,
                "error": {"code": type(exc).__name__, "message": str(exc)[:500]},
            },
        )


def main(argv: Sequence[str] | None = None) -> int:
    exit_code, payload = run(argv)
    print(canonical_json_bytes(payload).decode("utf-8"))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
