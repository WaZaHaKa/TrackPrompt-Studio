from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class PostProductionError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.safe_message = message


@dataclass(frozen=True, slots=True)
class PostToolchain:
    ffmpeg: Path
    ffprobe: Path
    rife: Path
    realesrgan: Path

    @classmethod
    def discover(
        cls,
        *,
        ffmpeg: str | Path | None = None,
        ffprobe: str | Path | None = None,
    ) -> PostToolchain:
        def executable(value: str | Path | None, environment: str, command: str) -> Path:
            raw = str(value) if value else os.getenv(environment, "") or shutil.which(command)
            if not raw:
                raise PostProductionError(
                    "post_tool_missing",
                    f"The configured local {command} executable is unavailable.",
                )
            path = Path(raw).resolve()
            if not path.is_file():
                raise PostProductionError(
                    "post_tool_missing",
                    f"The configured local {command} executable is unavailable.",
                )
            return path

        return cls(
            ffmpeg=executable(ffmpeg, "FFMPEG_PATH", "ffmpeg"),
            ffprobe=executable(ffprobe, "FFPROBE_PATH", "ffprobe"),
            rife=executable(None, "TRACKPROMPT_RIFE_PATH", "rife-ncnn-vulkan"),
            realesrgan=executable(
                None,
                "TRACKPROMPT_REALESRGAN_PATH",
                "realesrgan-ncnn-vulkan",
            ),
        )


@dataclass(frozen=True, slots=True)
class ScenePostPlan:
    shot_id: str
    raw_frames: Path
    interpolated_frames: Path
    upscaled_frames: Path
    output_master: Path
    expected_source_frames: int
    expected_delivery_frames: int
    commands: tuple[tuple[str, ...], ...]


def build_scene_post_plan(
    *,
    toolchain: PostToolchain,
    shot_id: str,
    raw_frames: Path,
    output_root: Path,
    source_frame_count: int = 81,
) -> ScenePostPlan:
    if not re.fullmatch(r"shot-[0-9]{3}", shot_id):
        raise PostProductionError("post_shot_invalid", "The scene identity is invalid.")
    if source_frame_count != 81:
        raise PostProductionError("post_frame_count_invalid", "A base scene must contain exactly 81 frames.")
    interpolated = output_root / "interpolated" / shot_id
    upscaled = output_root / "upscaled" / shot_id
    master = output_root / "scene-masters" / f"{shot_id}-1080p24.mov"
    delivery_frames = round(source_frame_count * 24 / 16)
    commands = (
        (
            str(toolchain.rife),
            "-i",
            str(raw_frames),
            "-o",
            str(interpolated),
            "-n",
            str(delivery_frames),
            "-f",
            "frame_%08d.png",
        ),
        (
            str(toolchain.realesrgan),
            "-i",
            str(interpolated),
            "-o",
            str(upscaled),
            "-n",
            "realesr-animevideov3",
            "-s",
            "2",
            "-f",
            "png",
        ),
        (
            str(toolchain.ffmpeg),
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-framerate",
            "24",
            "-i",
            str(upscaled / "frame_%08d.png"),
            "-vf",
            "scale=1920:1080:flags=lanczos,format=yuv422p10le",
            "-an",
            "-c:v",
            "prores_ks",
            "-profile:v",
            "1",
            str(master),
        ),
    )
    return ScenePostPlan(
        shot_id=shot_id,
        raw_frames=raw_frames,
        interpolated_frames=interpolated,
        upscaled_frames=upscaled,
        output_master=master,
        expected_source_frames=source_frame_count,
        expected_delivery_frames=delivery_frames,
        commands=commands,
    )


def execute_scene_post_plan(plan: ScenePostPlan, *, timeout_seconds: float = 7_200) -> None:
    source_frames = [path for path in plan.raw_frames.glob("*.png") if path.is_file()]
    if len(source_frames) != plan.expected_source_frames:
        raise PostProductionError(
            "post_source_frames_incomplete",
            "The generated scene frame sequence is incomplete.",
        )
    for directory in (plan.interpolated_frames, plan.upscaled_frames, plan.output_master.parent):
        directory.mkdir(parents=True, exist_ok=True)
    for command in plan.commands:
        try:
            result = subprocess.run(
                list(command),
                check=False,
                capture_output=True,
                timeout=timeout_seconds,
                shell=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise PostProductionError(
                "post_process_failed",
                "A local post-production command failed or timed out.",
            ) from exc
        if result.returncode != 0 or len(result.stderr) > 2_000_000:
            raise PostProductionError(
                "post_process_failed",
                "A local post-production command failed or returned excessive diagnostics.",
            )
    if not plan.output_master.is_file() or plan.output_master.stat().st_size <= 0:
        raise PostProductionError("post_master_missing", "The scene master was not created.")


def inspect_scene_quality(toolchain: PostToolchain, path: Path) -> dict[str, Any]:
    try:
        result = subprocess.run(
            [
                str(toolchain.ffmpeg),
                "-nostdin",
                "-hide_banner",
                "-i",
                str(path),
                "-vf",
                "blackdetect=d=0.08:pix_th=0.02,freezedetect=n=-45dB:d=0.5",
                "-an",
                "-f",
                "null",
                "-",
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=900,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PostProductionError("post_qc_failed", "Scene quality inspection failed.") from exc
    diagnostics = result.stderr[-2_000_000:]
    black = len(re.findall(r"black_start:", diagnostics))
    frozen = len(re.findall(r"freeze_start:", diagnostics))
    return {
        "decodePassed": result.returncode == 0,
        "blackRuns": black,
        "frozenRuns": frozen,
        "passed": result.returncode == 0 and black == 0 and frozen == 0,
    }
