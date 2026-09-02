from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
from pathlib import Path
from uuid import uuid4


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def frame_exposure_metrics(rgb: bytes, width: int, height: int) -> dict[str, float | int]:
    pixel_count = width * height
    if width < 1 or height < 1 or len(rgb) != pixel_count * 3:
        raise ValueError("RGB frame byte count does not match its dimensions.")
    histogram = [0] * 256
    near_white = bytearray(pixel_count)
    near_white_count = 0
    clipped_count = 0
    for pixel in range(pixel_count):
        offset = pixel * 3
        red, green, blue = rgb[offset], rgb[offset + 1], rgb[offset + 2]
        luminance = (54 * red + 183 * green + 19 * blue) // 256
        histogram[luminance] += 1
        if red >= 250 and green >= 250 and blue >= 250:
            near_white[pixel] = 1
            near_white_count += 1
        if red == 255 and green == 255 and blue == 255:
            clipped_count += 1

    largest_component = 0
    for seed in range(pixel_count):
        if near_white[seed] == 0:
            continue
        near_white[seed] = 0
        stack = [seed]
        component = 0
        while stack:
            current = stack.pop()
            component += 1
            x = current % width
            if x > 0 and near_white[current - 1]:
                near_white[current - 1] = 0
                stack.append(current - 1)
            if x + 1 < width and near_white[current + 1]:
                near_white[current + 1] = 0
                stack.append(current + 1)
            if current >= width and near_white[current - width]:
                near_white[current - width] = 0
                stack.append(current - width)
            if current + width < pixel_count and near_white[current + width]:
                near_white[current + width] = 0
                stack.append(current + width)
        largest_component = max(largest_component, component)

    target = math.ceil(pixel_count * 0.99)
    cumulative = 0
    percentile_99 = 255
    for value, count in enumerate(histogram):
        cumulative += count
        if cumulative >= target:
            percentile_99 = value
            break
    return {
        "p99Luminance": percentile_99 / 255.0,
        "nearWhiteFraction": near_white_count / pixel_count,
        "clippedWhiteFraction": clipped_count / pixel_count,
        "largestNearWhiteComponentFraction": largest_component / pixel_count,
    }


def _decode_phone_frames(
    ffmpeg: Path,
    media: Path,
    width: int,
    height: int,
    *,
    maximum_frames: int,
) -> list[bytes]:
    command = [
        str(ffmpeg),
        "-nostdin",
        "-v",
        "error",
        "-i",
        str(media),
        "-vf",
        f"scale={width}:{height}:flags=lanczos",
        "-pix_fmt",
        "rgb24",
        "-f",
        "rawvideo",
        "-",
    ]
    completed = subprocess.run(
        command,
        shell=False,
        check=False,
        capture_output=True,
        timeout=600,
    )
    frame_bytes = width * height * 3
    if (
        completed.returncode != 0
        or len(completed.stderr) > 1_000_000
        or len(completed.stdout) == 0
        or len(completed.stdout) % frame_bytes != 0
    ):
        raise RuntimeError("FFmpeg could not decode bounded phone-size review frames.")
    frame_count = len(completed.stdout) // frame_bytes
    if frame_count > maximum_frames:
        raise RuntimeError("Decoded media exceeds the bounded R12 frame count.")
    return [
        completed.stdout[offset : offset + frame_bytes]
        for offset in range(0, len(completed.stdout), frame_bytes)
    ]


def analyze_media(
    *,
    ffmpeg: Path,
    clip: Path,
    stills: list[Path],
    layout: str,
    phone_width: int,
    phone_height: int,
    phone_output_directory: Path | None = None,
) -> dict[str, object]:
    clip_frames = _decode_phone_frames(
        ffmpeg,
        clip,
        phone_width,
        phone_height,
        maximum_frames=600,
    )
    frame_metrics = [
        frame_exposure_metrics(frame, phone_width, phone_height)
        for frame in clip_frames
    ]
    still_metrics: list[dict[str, object]] = []
    for still in stills:
        decoded = _decode_phone_frames(
            ffmpeg,
            still,
            phone_width,
            phone_height,
            maximum_frames=1,
        )
        if len(decoded) != 1:
            raise RuntimeError("A representative still did not decode as one frame.")
        phone_file: dict[str, object] = {}
        if phone_output_directory is not None:
            phone_output_directory.mkdir(parents=False, exist_ok=True)
            phone_still = phone_output_directory / still.name
            completed = subprocess.run(
                [
                    str(ffmpeg),
                    "-y",
                    "-nostdin",
                    "-v",
                    "error",
                    "-i",
                    str(still),
                    "-vf",
                    f"scale={phone_width}:{phone_height}:flags=lanczos",
                    "-frames:v",
                    "1",
                    str(phone_still),
                ],
                shell=False,
                check=False,
                capture_output=True,
                timeout=60,
            )
            if completed.returncode != 0 or not phone_still.is_file():
                raise RuntimeError("FFmpeg could not publish a phone-size representative still.")
            phone_file = {
                "phoneFile": f"phone/{phone_still.name}",
                "phoneSha256": _sha256_file(phone_still),
            }
        still_metrics.append(
            {
                "file": still.name,
                "sha256": _sha256_file(still),
                **phone_file,
                **frame_exposure_metrics(decoded[0], phone_width, phone_height),
            }
        )
    near_white_limit = 0.02
    component_limit = 0.01
    worst_near_white = max(
        [float(item["nearWhiteFraction"]) for item in frame_metrics]
        + [float(item["nearWhiteFraction"]) for item in still_metrics]
    )
    worst_component = max(
        [float(item["largestNearWhiteComponentFraction"]) for item in frame_metrics]
        + [float(item["largestNearWhiteComponentFraction"]) for item in still_metrics]
    )
    worst_clip_frame = max(
        range(len(frame_metrics)),
        key=lambda index: float(frame_metrics[index]["nearWhiteFraction"]),
    )
    return {
        "schemaVersion": "1.0.0",
        "kind": "trackprompt-r12-exposure-clipping-report",
        "revisionId": "andromeda-r12-continuous-slice",
        "layout": layout,
        "phoneReview": {"width": phone_width, "height": phone_height, "crop": False},
        "clip": {
            "file": clip.name,
            "sha256": _sha256_file(clip),
            "decodedFrameCount": len(frame_metrics),
            "meanP99Luminance": sum(float(item["p99Luminance"]) for item in frame_metrics)
            / len(frame_metrics),
            "maximumNearWhiteFraction": max(
                float(item["nearWhiteFraction"]) for item in frame_metrics
            ),
            "maximumClippedWhiteFraction": max(
                float(item["clippedWhiteFraction"]) for item in frame_metrics
            ),
            "maximumLargestNearWhiteComponentFraction": max(
                float(item["largestNearWhiteComponentFraction"])
                for item in frame_metrics
            ),
            "worstNearWhiteOutputFrame": worst_clip_frame + 1,
        },
        "representativeStills": still_metrics,
        "thresholds": {
            "maximumNearWhiteFraction": near_white_limit,
            "maximumLargestNearWhiteComponentFraction": component_limit,
        },
        "measuredWorst": {
            "nearWhiteFraction": worst_near_white,
            "largestNearWhiteComponentFraction": worst_component,
        },
        "technicalPass": (
            worst_near_white < near_white_limit and worst_component < component_limit
        ),
        "artisticApproval": False,
    }


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Measure R12 phone-size exposure and clipping.")
    parser.add_argument("--ffmpeg", required=True, type=Path)
    parser.add_argument("--clip", required=True, type=Path)
    parser.add_argument("--stills-directory", required=True, type=Path)
    parser.add_argument("--layout", required=True, choices=("landscape", "vertical"))
    parser.add_argument("--phone-width", required=True, type=int)
    parser.add_argument("--phone-height", required=True, type=int)
    parser.add_argument("--phone-output-directory", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    ffmpeg = args.ffmpeg.expanduser().resolve(strict=True)
    clip = args.clip.expanduser().resolve(strict=True)
    stills_directory = args.stills_directory.expanduser().resolve(strict=True)
    output = args.output.expanduser().resolve()
    phone_output_directory = (
        args.phone_output_directory.expanduser().resolve()
        if args.phone_output_directory is not None
        else None
    )
    if (
        not ffmpeg.is_file()
        or not clip.is_file()
        or not stills_directory.is_dir()
        or not output.parent.is_dir()
        or not 120 <= args.phone_width <= 640
        or not 120 <= args.phone_height <= 640
    ):
        raise SystemExit("R12 exposure inputs are invalid.")
    stills = sorted(stills_directory.glob("frame_*.png"))
    if len(stills) != 8:
        raise SystemExit("R12 exposure review requires exactly eight representative stills.")
    report = analyze_media(
        ffmpeg=ffmpeg,
        clip=clip,
        stills=stills,
        layout=args.layout,
        phone_width=args.phone_width,
        phone_height=args.phone_height,
        phone_output_directory=phone_output_directory,
    )
    temporary = output.parent / f".{output.name}.{uuid4().hex}.tmp"
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(report, stream, indent=2, sort_keys=True, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    print(
        json.dumps(
            {
                "ok": True,
                "technicalPass": report["technicalPass"],
                "layout": args.layout,
                "decodedFrameCount": report["clip"]["decodedFrameCount"],
                "output": output.name,
            },
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
