from __future__ import annotations

import math
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
    total_frames: int
    fps: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": "1.0.0",
            "ffmpeg": self.ffmpeg,
            "outputPath": self.output_path,
            "segmentDirectory": self.segment_directory,
            "concatListPath": self.concat_list_path,
            "videoOnlyPath": self.video_only_path,
            "audioPath": self.audio_path,
            "totalFrames": self.total_frames,
            "fps": self.fps,
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
    segment_directory = output_path.parent / "derived-media"
    concat_path = work_root / "segments.ffconcat"
    video_only_path = work_root / "assembled-video-only.mp4"
    segment_directory.mkdir(parents=True, exist_ok=True)
    work_root.mkdir(parents=True, exist_ok=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    commands: list[tuple[str, ...]] = []
    concat_lines = ["ffconcat version 1.0"]
    for index, item in enumerate(segments, start=1):
        source_path = Path(str(item["clipPath"]))
        configured_target = item.get("derivedMediaPath")
        target_path = (
            Path(str(configured_target))
            if isinstance(configured_target, str)
            else segment_directory / f"event-{index:04d}.mp4"
        )
        duration_seconds = int(item["durationFrames"]) / fps
        source_in_seconds = int(item["sourceInFrames"]) / fps
        playback_rate = float(item.get("playbackRate", 1.0))
        crop_scale = max(1.0, min(1.4, float(item.get("cropScale", 1.0))))
        crop_x = max(0.0, min(1.0, float(item.get("cropX", 0.5))))
        crop_y = max(0.0, min(1.0, float(item.get("cropY", 0.5))))
        scaled_width = math.ceil(width * crop_scale / 2) * 2
        scaled_height = math.ceil(height * crop_scale / 2) * 2
        x_offset = round((scaled_width - width) * crop_x)
        y_offset = round((scaled_height - height) * crop_y)
        reverse = bool(item.get("reverse", False))
        ping_pong = bool(item.get("pingPong", False))
        freeze_frames = max(0, min(int(item.get("freezeFrames", 0)), int(item["durationFrames"]) - 1))
        freeze_seconds = freeze_frames / fps
        fade_in = min(duration_seconds, int(item.get("fadeInFrames", 0)) / fps)
        fade_out = min(duration_seconds, int(item.get("fadeOutFrames", 0)) / fps)
        transition_path: Path | None = None
        transition_seconds = 0.0
        if index > 1 and "dissolve" in str(item.get("transitionIn", "")):
            previous = segments[index - 2]
            transition_path = Path(str(previous["clipPath"]))
            transition_seconds = max(1 / fps, min(duration_seconds / 2, fade_in or 8 / fps))
            fade_in = 0.0
        base_filters = [
            f"scale={scaled_width}:{scaled_height}:force_original_aspect_ratio=increase",
            f"crop={width}:{height}:{x_offset}:{y_offset}",
            f"fps={fps}",
            f"setpts=(PTS-STARTPTS)/{playback_rate:.6f}",
        ]
        if reverse and not ping_pong:
            base_filters.append("reverse")
        if freeze_frames:
            base_filters.append(
                f"trim=duration={max(1 / fps, duration_seconds - freeze_seconds):.6f}"
            )
            base_filters.append(f"tpad=stop_mode=clone:stop_duration={freeze_seconds:.6f}")
            base_filters.append(
                f"zoompan=z='min(zoom+0.0008,1.08)':x='iw/2-(iw/zoom/2)':"
                f"y='ih/2-(ih/zoom/2)':d=1:s={width}x{height}:fps={fps}"
            )
        base_filters.extend(
            [
                f"tpad=stop_mode=clone:stop_duration={duration_seconds + 1:.6f}",
                f"trim=duration={duration_seconds:.6f}",
                "setpts=PTS-STARTPTS",
            ]
        )
        if fade_in > 0:
            base_filters.append(f"fade=t=in:st=0:d={fade_in:.6f}")
        if fade_out > 0:
            base_filters.append(
                f"fade=t=out:st={max(0.0, duration_seconds - fade_out):.6f}:d={fade_out:.6f}"
            )
        base_filters.append("format=yuv420p")
        command_parts = [
            str(executable),
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-y",
            "-stream_loop",
            "1",
            "-ss",
            f"{source_in_seconds:.6f}",
            "-i",
            str(source_path),
        ]
        overlay_shot_id = item.get("overlayShotId")
        overlay_opacity = float(item.get("overlayOpacity", 0.0))
        overlay_path: Path | None = None
        if isinstance(overlay_shot_id, str) and overlay_opacity > 0:
            overlay = next(
                (candidate for candidate in segments if candidate.get("shotId") == overlay_shot_id),
                None,
            )
            if isinstance(overlay, dict):
                overlay_path = Path(str(overlay["clipPath"]))
        if ping_pong:
            half = max(1 / fps, duration_seconds / 2)
            pre = ",".join(base_filters[:4])
            post = [
                f"tpad=stop_mode=clone:stop_duration={duration_seconds + 1:.6f}",
                f"trim=duration={duration_seconds:.6f}",
                "setpts=PTS-STARTPTS",
            ]
            if fade_out > 0:
                post.append(
                    f"fade=t=out:st={max(0.0, duration_seconds - fade_out):.6f}:d={fade_out:.6f}"
                )
            post.append("format=yuv420p")
            filter_graph = (
                f"[0:v]{pre},trim=duration={half:.6f},setpts=PTS-STARTPTS,split=2[forward][reversein];"
                f"[reversein]reverse,setpts=PTS-STARTPTS[backward];"
                f"[forward][backward]concat=n=2:v=1:a=0,{','.join(post)}[outv]"
            )
            command_parts.extend(["-filter_complex", filter_graph, "-map", "[outv]"])
        elif transition_path is not None:
            command_parts.extend(["-stream_loop", "-1", "-i", str(transition_path)])
            filter_graph = (
                f"[0:v]{','.join(base_filters[:-1])}[base];"
                f"[1:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height},fps={fps},trim=duration={transition_seconds:.6f},"
                f"setpts=PTS-STARTPTS,format=rgba,"
                f"fade=t=out:st=0:d={transition_seconds:.6f}:alpha=1[prior];"
                f"[base][prior]overlay=eof_action=pass:shortest=0,format=yuv420p[outv]"
            )
            command_parts.extend(["-filter_complex", filter_graph, "-map", "[outv]"])
        elif overlay_path is not None:
            command_parts.extend(["-stream_loop", "-1", "-i", str(overlay_path)])
            filter_graph = (
                f"[0:v]{','.join(base_filters[:-1])}[base];"
                f"[1:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height},fps={fps},format=rgba,colorchannelmixer=aa={overlay_opacity:.3f},"
                f"tpad=stop_mode=clone:stop_duration={duration_seconds + 1:.6f},"
                f"trim=duration={duration_seconds:.6f},setpts=PTS-STARTPTS[veil];"
                f"[base][veil]overlay=shortest=1,format=yuv420p[outv]"
            )
            command_parts.extend(["-filter_complex", filter_graph, "-map", "[outv]"])
        else:
            command_parts.extend(["-vf", ",".join(base_filters)])
        command_parts.extend([
            "-t",
            f"{duration_seconds:.6f}",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "18",
            "-g",
            str(fps * 2),
            "-movflags",
            "+faststart",
            str(target_path),
        ])
        command = tuple(command_parts)
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
            "-t",
            f"{int(timeline['durationFrames']) / fps:.6f}",
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
        total_frames=int(timeline["durationFrames"]),
        fps=fps,
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
            timeout=3_600,
            shell=False,
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
