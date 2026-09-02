from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from .controller import LocalVideoController
from .models import LocalVideoPrepareRequest, LocalVideoWorkflowRequest


def _controller(arguments: argparse.Namespace) -> LocalVideoController:
    repository_root = Path(arguments.repository_root).resolve()
    data_root = Path(arguments.data_root).resolve()
    return LocalVideoController(
        repository_root=repository_root,
        state_root=data_root / "mission-control",
        analysis_data_root=data_root,
        ffmpeg_path=lambda: Path(arguments.ffmpeg).resolve(),
        ffprobe_path=lambda: Path(arguments.ffprobe).resolve(),
    )


async def _prepare(arguments: argparse.Namespace) -> int:
    controller = _controller(arguments)
    project = await controller.prepare(LocalVideoPrepareRequest(project_id=arguments.project_id))
    print(
        json.dumps(
            project.model_dump(mode="json", by_alias=True),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


def _load_workflow(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("The API workflow is invalid")
    return value


async def _sync_qualification(arguments: argparse.Namespace) -> int:
    controller = _controller(arguments)
    repository_root = Path(arguments.repository_root).resolve()
    output_root = (
        repository_root
        / "video-projects"
        / "local"
        / arguments.project_id
        / "outputs"
        / "qualification"
    )
    flux_path = output_root / "flux-workflow.actual.json"
    wan_candidates = sorted(output_root.glob("wan-q5-workflow*.json"))
    wan_path = next(
        (
            path
            for path in reversed(wan_candidates)
            if _load_workflow(path).get("vae", {}).get("inputs", {}).get("vae_name")
            == "wan_2.1_vae.safetensors"
        ),
        None,
    )
    if wan_path is None:
        raise ValueError("No successful A14B qualification workflow is available")
    installed = [
        controller.install_workflow(
            LocalVideoWorkflowRequest(
                workflow_id="flux1-schnell-fp8-anime-keyframe-v1",
                capability="keyframe-flux",
                workflow=_load_workflow(flux_path),
                source_url="https://docs.comfy.org/tutorials/flux/flux-1-text-to-image",
                source_revision="ComfyUI-v0.30.0",
            )
        ),
        controller.install_workflow(
            LocalVideoWorkflowRequest(
                workflow_id="wan22-i2v-a14b-q5-v1",
                capability="wan22-i2v",
                workflow=_load_workflow(wan_path),
                source_url="https://docs.comfy.org/tutorials/video/wan/wan2_2",
                source_revision="ComfyUI-v0.30.0+GGUF-6ea2651",
            )
        ),
    ]
    qualification = controller.record_qualification(arguments.project_id)
    readiness = await controller.readiness()
    print(
        json.dumps(
            {
                "workflows": [item.model_dump(mode="json", by_alias=True) for item in installed],
                "qualification": qualification.model_dump(mode="json", by_alias=True),
                "readiness": readiness.model_dump(mode="json", by_alias=True),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage a private local video project revision")
    parser.add_argument("command", choices=("prepare", "sync-qualification"))
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--repository-root", required=True)
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--ffmpeg", required=True)
    parser.add_argument("--ffprobe", required=True)
    arguments = parser.parse_args()
    if arguments.command == "prepare":
        return asyncio.run(_prepare(arguments))
    if arguments.command == "sync-qualification":
        return asyncio.run(_sync_qualification(arguments))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
