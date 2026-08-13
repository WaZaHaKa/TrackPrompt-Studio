from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .contracts import ContractError
from .jsonio import atomic_write_json, atomic_write_text


@dataclass(frozen=True)
class AssemblyPlan:
    ffmpeg: str
    output_path: str
    segment_directory: str
    commands: tuple[tuple[str, ...], ...]
    concat_list_path: str
    video_only_path: str
    audio_path: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": "1.0.0",
            "ffmpeg": self.ffmpeg,
            "outputPath": self.output_path,
            "segmentDirectory": self.segment_directory,
            "concatListPath": self.concat_list_path,
            "videoOnlyPath": self.video_only_path,
            "audioPath": self.audio_path,
            "commands": [list(command) for command in self.commands],
        }


def build_assembly_plan(
    value: dict[str, Any],
    *,
    output_path: Path,
    work_root: Path,
    ffmpeg: str | None = None,
) -> AssemblyPlan:
    executable = ffmpeg or os.getenv("TRACKPROMPT_MC_FFMPEG_PATH") or shutil.which("ffmpeg")
    if not executable:
        raise ContractError("ffmpeg is not installed or not on PATH")
    timeline = value.get("timeline")
    segments = value.get("segments")
    if not isinstance(timeline, dict) or not isinstance(segments, list):
        raise ContractError("resolved timeline is missing timeline/segments")
    fps = int(timeline["fps"])
    width = int(timeline["width"])
    height = int(timeline["height"])
    audio_path = Path(str(timeline["audioPath"]))
    segment_directory = work_root / "normalized-segments"
    concat_path = work_root / "segments.ffconcat"
    video_only_path = work_root / "assembled-video-only.mp4"
    segment_directory.mkdir(parents=True, exist_ok=True)
    work_root.mkdir(parents=True, exist_ok=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    commands: list[tuple[str, ...]] = []
    concat_lines = ["ffconcat version 1.0"]
    for index, item in enumerate(segments, start=1):
        source_path = Path(str(item["clipPath"]))
        target_path = segment_directory / f"segment-{index:04d}.mp4"
        duration_seconds = int(item["durationFrames"]) / fps
        source_in_seconds = int(item["sourceInFrames"]) / fps
        # -stream_loop guarantees complete coverage even when an editorial segment
        # is longer than one generated clip. Normal projects target <= 8 seconds.
        command = (
            str(executable),
            "-hide_banner",
            "-loglevel",
            "warning",
            "-y",
            "-stream_loop",
            "-1",
            "-ss",
            f"{source_in_seconds:.6f}",
            "-i",
            str(source_path),
            "-t",
            f"{duration_seconds:.6f}",
            "-an",
            "-vf",
            (
                f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,"
                f"fps={fps},format=yuv420p"
            ),
            "-c:v",
            "libx264",
            "-preset",
            "slow",
            "-crf",
            "16",
            "-g",
            str(fps * 2),
            "-movflags",
            "+faststart",
            str(target_path),
        )
        commands.append(command)
        escaped = str(target_path.resolve()).replace("'", "'\\''")
        concat_lines.append(f"file '{escaped}'")

    atomic_write_text(concat_path, "\n".join(concat_lines) + "\n")
    commands.append(
        (
            str(executable),
            "-hide_banner",
            "-loglevel",
            "warning",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_path),
            "-c",
            "copy",
            str(video_only_path),
        )
    )
    commands.append(
        (
            str(executable),
            "-hide_banner",
            "-loglevel",
            "warning",
            "-y",
            "-i",
            str(video_only_path),
            "-i",
            str(audio_path),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "320k",
            "-shortest",
            "-movflags",
            "+faststart",
            str(output_path),
        )
    )
    return AssemblyPlan(
        ffmpeg=str(executable),
        output_path=str(output_path),
        segment_directory=str(segment_directory),
        commands=tuple(commands),
        concat_list_path=str(concat_path),
        video_only_path=str(video_only_path),
        audio_path=str(audio_path),
    )


def execute_assembly(plan: AssemblyPlan) -> None:
    for index, command in enumerate(plan.commands, start=1):
        result = subprocess.run(
            list(command),
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            raise ContractError(f"assembly command {index} failed: {result.stderr[-4000:]}")


def write_assembly_plan(plan: AssemblyPlan, path: Path) -> None:
    atomic_write_json(path, plan.to_dict())


def write_powershell_runner(plan: AssemblyPlan, path: Path) -> None:
    def quote(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    lines = [
        "$ErrorActionPreference = 'Stop'",
        "$ProgressPreference = 'SilentlyContinue'",
        "",
    ]
    for index, command in enumerate(plan.commands, start=1):
        executable, *arguments = command
        lines.append(f"Write-Host 'Assembly step {index}/{len(plan.commands)}'")
        lines.append("& " + quote(executable) + " " + " ".join(quote(item) for item in arguments))
        lines.append("if ($LASTEXITCODE -ne 0) { throw 'FFmpeg assembly failed' }")
        lines.append("")
    lines.append(f"Write-Host 'Created: {plan.output_path}'")
    atomic_write_text(path, "\n".join(lines) + "\n")
