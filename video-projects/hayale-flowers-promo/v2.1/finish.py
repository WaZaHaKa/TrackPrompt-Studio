"""Local frame finishing and existing TrackPrompt Resolve package export."""

from __future__ import annotations

import copy
import json
import struct
import sys
import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "backend"))
from app.subprocess_utils import run_process_bounded  # noqa: E402
from app.video_generation.exporter import export_davinci_package  # noqa: E402
from app.video_generation.jsonio import atomic_write_json, sha256_file, sha256_json  # noqa: E402


def read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def run(command: list[str], *, timeout: int = 3600, log: Path | None = None) -> bytes:
    result = run_process_bounded(
        command, timeout_seconds=timeout, stdout_limit=2_000_000, stderr_limit=2_000_000
    )
    if log is not None:
        log.write_bytes(result.stderr)
    if result.returncode or result.stdout_exceeded:
        raise ValueError(
            "Local finishing failed. Retained command and stderr logs explain the native error."
        )
    return result.stdout


def post_commands(tools: dict[str, str], raw: Path, output: Path) -> list[list[str]]:
    interpolated, upscaled = output / "interpolated", output / "upscaled"
    return [
        [
            tools["rife"],
            "-i",
            str(raw),
            "-o",
            str(interpolated),
            "-m",
            tools["rifeModel"],
            "-n",
            "193",
            "-f",
            "frame_%08d.png",
            "-g",
            "0",
            "-j",
            "1:1:1",
        ],
        [
            tools["upscaler"],
            "-i",
            str(interpolated),
            "-o",
            str(upscaled),
            "-m",
            tools["upscaleModels"],
            "-n",
            "realesrgan-x4plus",
            "-s",
            "4",
            "-t",
            "256",
            "-g",
            "0",
            "-j",
            "1:1:1",
            "-f",
            "png",
        ],
        [
            tools["ffmpeg"],
            "-nostdin",
            "-v",
            "error",
            "-n",
            "-framerate",
            "24",
            "-start_number",
            "1",
            "-i",
            str(upscaled / "frame_%08d.png"),
            "-vf",
            "scale=1920:1080:force_original_aspect_ratio=increase:flags=lanczos:out_color_matrix=bt709:out_range=tv,crop=1920:1080,setsar=1,format=yuv422p10le,setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709",
            "-frames:v",
            "192",
            "-an",
            "-c:v",
            "prores_ks",
            "-profile:v",
            "3",
            "-color_primaries",
            "bt709",
            "-color_trc",
            "bt709",
            "-colorspace",
            "bt709",
            "-color_range",
            "tv",
            "-map_metadata",
            "-1",
            str(output / "master.mov"),
        ],
    ]


def verify_png_sequence(root: Path, size: tuple[int, int], count: int) -> None:
    expected = [f"frame_{n:08d}.png" for n in range(1, count + 1)]
    if sorted(path.name for path in root.iterdir()) != expected:
        raise ValueError(
            "Postprocessing frame sequence is missing, duplicated or not contiguous"
        )
    for name in expected:
        with (root / name).open("rb") as stream:
            header = stream.read(24)
        if (
            len(header) != 24
            or header[:8] != b"\x89PNG\r\n\x1a\n"
            or struct.unpack(">II", header[16:24]) != size
        ):
            raise ValueError(
                "Postprocessing returned an invalid image or unexpected dimensions"
            )


def verify_video(
    tools: dict[str, str], path: Path, frames: int, audio: bool = False
) -> dict[str, Any]:
    evidence = json.loads(
        run(
            [
                tools["ffprobe"],
                "-v",
                "error",
                "-count_frames",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                str(path),
            ]
        )
    )
    video = [s for s in evidence["streams"] if s["codec_type"] == "video"]
    sound = [s for s in evidence["streams"] if s["codec_type"] == "audio"]
    if len(video) != 1 or (
        video[0]["width"],
        video[0]["height"],
        int(video[0]["nb_read_frames"]),
        video[0]["avg_frame_rate"],
    ) != (1920, 1080, frames, "24/1"):
        raise ValueError(
            "Video dimensions, decoded frame count or FPS failed verification"
        )
    if len(sound) != int(audio) or (
        sound and (sound[0]["sample_rate"], sound[0]["channels"]) != ("48000", 2)
    ):
        raise ValueError("Local audio stream failed verification")
    if abs(float(evidence["format"]["duration"]) - frames / 24) > 0.1:
        raise ValueError("Video duration failed verification")
    run(
        [
            tools["ffmpeg"],
            "-nostdin",
            "-v",
            "error",
            "-xerror",
            "-i",
            str(path),
            "-f",
            "null",
            "-",
        ]
    )
    return evidence  # type: ignore[no-any-return]


def finish_scene(
    tools: dict[str, str], scene_root: Path, size: tuple[int, int]
) -> Path:
    raw_digest = sha256_file(scene_root / "raw-verified.json")
    marker = scene_root / "master-verified.json"
    if marker.exists():
        receipt = read(marker)
        path = Path(receipt["path"])
        if (
            not path.resolve().is_relative_to(scene_root.resolve())
            or receipt["rawDigest"] != raw_digest
            or sha256_file(path) != receipt["sha256"]
        ):
            raise ValueError(
                "A completed scene master changed; refusing to overwrite it"
            )
        return path
    output = scene_root / "post" / uuid4().hex[:8]
    (output / "interpolated").mkdir(parents=True)
    (output / "upscaled").mkdir()
    commands = post_commands(tools, scene_root / "raw", output)
    atomic_write_json(output / "commands.json", {"commands": commands})
    for index, command in enumerate(commands):
        print(f"Local finishing stage {index + 1}/3", flush=True)
        run(command, timeout=6 * 3600, log=output / f"stage-{index + 1}.stderr.log")
        if index < 2:
            verify_png_sequence(
                output / ("interpolated" if index == 0 else "upscaled"),
                size if index == 0 else (size[0] * 4, size[1] * 4),
                193,
            )
    master = output / "master.mov"
    verify_video(tools, master, 192)
    atomic_write_json(
        marker,
        {"path": str(master), "sha256": sha256_file(master), "rawDigest": raw_digest},
    )
    return master


def verify_interchange(output: Path, edit: dict[str, Any]) -> None:
    fcpxml = ET.parse(output / "hayale-promo-v21.fcpxml").getroot()
    xml = ET.parse(output / "hayale-promo-v21.xml").getroot()
    sequence = fcpxml.find(".//sequence")
    if (
        sequence is None
        or Fraction(sequence.attrib["duration"].removesuffix("s")) != 60
        or xml.findtext(".//sequence/duration") != "1440"
    ):
        raise ValueError("Interchange duration failed")
    clips = fcpxml.findall(".//asset-clip[@lane='1']")
    if len(clips) != 8:
        raise ValueError("Interchange must have eight events")
    for clip, event in zip(clips, edit["segments"], strict=True):
        if (
            Fraction(clip.attrib["offset"].removesuffix("s")) * 24
            != event["timelineStartFrames"]
            or Fraction(clip.attrib["duration"].removesuffix("s")) * 24
            != event["durationFrames"]
        ):
            raise ValueError("Interchange event timing failed")
    for tree in (fcpxml, xml):
        uris = [e.attrib["src"] for e in tree.findall(".//media-rep")] + [
            e.text for e in tree.findall(".//pathurl")
        ]
        for uri in uris:
            raw = unquote(urlparse(uri).path)
            path = Path(raw[1:] if len(raw) > 2 and raw[2] == ":" else raw)
            if not path.is_file() or not path.resolve().is_relative_to(
                output.resolve()
            ):
                raise ValueError("Interchange has missing or external derived media")
    events = [
        line
        for line in (output / "hayale-promo-v21.edl").read_text().splitlines()
        if line[:3].isdigit()
    ]
    if len(events) != 8 or not events[-1].endswith("00:01:00:00"):
        raise ValueError("EDL event count or end timecode failed")


def finish_package(
    tools: dict[str, str],
    clips: dict[str, Path],
    prepared: Path,
    output: Path,
    plan_digest: str,
    *,
    silent: bool = False,
) -> dict[str, Any]:
    if output.exists() or set(clips) != {f"shot-{n:03d}" for n in range(1, 8)}:
        raise ValueError(
            "Finishing requires seven distinct clip identities and a new output directory"
        )
    output.mkdir(parents=True)
    edit = copy.deepcopy(read(prepared / "edit-plan.json"))
    cues = read(prepared / "music-cues.json")
    raw_hashes = {key: sha256_file(path) for key, path in clips.items()}
    plate = prepared / "references/scene-07.png"
    plate_hash = sha256_file(plate)
    logo = output / "logo-bookend-locked.mov"
    ffmpeg = tools["ffmpeg"]
    run(
        [
            ffmpeg,
            "-nostdin",
            "-v",
            "error",
            "-n",
            "-i",
            str(clips["shot-007"]),
            "-loop",
            "1",
            "-framerate",
            "24",
            "-t",
            "8",
            "-i",
            str(plate),
            "-filter_complex",
            "[0:v]fps=24,format=yuv444p,setsar=1[base];[1:v]format=rgba,fade=t=in:st=3.5:d=0.5:alpha=1,scale=1920:1080:out_color_matrix=bt709:out_range=tv,format=yuva444p[lock];[base][lock]overlay=shortest=1:format=yuv444,format=yuv422p10le,setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709[v]",
            "-map",
            "[v]",
            "-frames:v",
            "192",
            "-an",
            "-c:v",
            "prores_ks",
            "-profile:v",
            "3",
            "-color_primaries",
            "bt709",
            "-color_trc",
            "bt709",
            "-colorspace",
            "bt709",
            "-color_range",
            "tv",
            str(logo),
        ]
    )
    audio = Path(cues["audioPath"]) if cues.get("audioPath") and not silent else None
    if audio:
        if sha256_file(audio) != cues["sourceSha256"]:
            raise ValueError("Local audio changed since preparation")
        master_audio = output / "promo-audio-60s.wav"
        run(
            [
                ffmpeg,
                "-nostdin",
                "-v",
                "error",
                "-n",
                "-i",
                str(audio),
                "-map",
                "0:a:0",
                "-vn",
                "-af",
                "apad,atrim=duration=60",
                "-ar",
                "48000",
                "-ac",
                "2",
                "-c:a",
                "pcm_s24le",
                "-map_metadata",
                "-1",
                str(master_audio),
            ]
        )
        edit["timeline"]["audioPath"] = str(master_audio)
    else:
        edit["timeline"]["audioPath"] = None
    for event in edit["segments"]:
        event["clipPath"] = str(
            logo if event["shotId"] == "shot-007" else clips[event["shotId"]]
        )
    edit["localEditDigest"] = sha256_json(
        {"edit": edit, "localPlan": plan_digest, "clips": raw_hashes}
    )
    exports = export_davinci_package(edit, output_root=output, ffmpeg=ffmpeg)
    for index, command in enumerate(read(Path(exports["assemblyPlan"]))["commands"], 1):
        print(f"Assembling local 60-second edit: stage {index}", flush=True)
        run(command, log=output / f"assembly-{index}.stderr.log")
    preview = Path(exports["previewOutput"])
    evidence = verify_video(tools, preview, 1440, bool(audio))
    verify_interchange(output, edit)
    coverage = read(Path(exports["coverageReport"]))
    if not coverage["continuous"] or not coverage["allRequiredShotsUsed"]:
        raise ValueError("Timeline coverage failed")
    if (
        any(sha256_file(clips[key]) != digest for key, digest in raw_hashes.items())
        or sha256_file(plate) != plate_hash
    ):
        raise ValueError("Immutable source clips or logo changed during finishing")
    if audio and sha256_file(audio) != cues["sourceSha256"]:
        raise ValueError("Original audio changed during finishing")
    report = {
        "status": "technical-pass-operator-review-required",
        "frames": 1440,
        "fps": 24,
        "durationSeconds": 60,
        "size": [1920, 1080],
        "preview": str(preview),
        "previewSha256": sha256_file(preview),
        "audioPresent": bool(audio),
        "uniqueGenerations": 7,
        "logoUses": 2,
        "planDigest": plan_digest,
        "rawClipsUnchanged": True,
        "sourceAudioUnchanged": True,
        "creativeReview": "pending",
        "resolveImport": "not-run",
    }
    atomic_write_json(output / "verification-report.json", report)
    atomic_write_json(output / "ffprobe-evidence.json", evidence)
    atomic_write_json(
        output / "source-provenance.json",
        {
            "clipPaths": {key: str(p) for key, p in clips.items()},
            "clipSha256": raw_hashes,
            "musicCues": cues,
            "logoPlateSha256": plate_hash,
        },
    )
    atomic_write_json(
        output / "grade-plan.json",
        {
            "status": "operator-review-required",
            "look": "warm neutral daylight, clean ivory whites, believable greens, burgundy and blush",
            "checks": "match white balance and exposure; inspect store geometry, sign lettering, floral identity, interpolation and upscaler shimmer",
            "bakedFinalGrade": False,
        },
    )
    manifest = read(output / "render-manifest.json")
    manifest["status"] = report["status"]
    atomic_write_json(output / "render-manifest.json", manifest)
    return report
