from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from tools.analyze_cinematic_v2_r13_lookdev import (
    _decode_rgb,
    luminance_metrics,
    sha256_file,
)


NEAR_BLACK_REVIEW_FRACTION = 0.85
CLIPPED_HIGHLIGHT_REVIEW_FRACTION = 0.01


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a JSON object in {path.name}.")
    return payload


def _artifact(root: Path, reference: object, label: str) -> Path:
    if not isinstance(reference, dict):
        raise ValueError(f"Missing {label} reference.")
    relative = reference.get("file")
    expected = reference.get("sha256")
    if not isinstance(relative, str) or not isinstance(expected, str):
        raise ValueError(f"Invalid {label} reference.")
    path = (root / relative).resolve(strict=True)
    try:
        path.relative_to(root.resolve(strict=True))
    except ValueError as exc:
        raise ValueError(f"{label} escapes the R13.1 media root.") from exc
    if sha256_file(path) != expected:
        raise ValueError(f"{label} hash does not match the rendered artifact.")
    return path


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        raise ValueError("Percentile calculation requires values.")
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * fraction))]


def contrast_metrics(rgb: bytes) -> dict[str, float]:
    luminances = [
        (0.2126 * rgb[index] + 0.7152 * rgb[index + 1] + 0.0722 * rgb[index + 2])
        / 255.0
        for index in range(0, len(rgb), 3)
    ]
    p10 = _percentile(luminances, 0.10)
    p90 = _percentile(luminances, 0.90)
    return {"p10": p10, "p90": p90, "p90MinusP10": p90 - p10}


def neighbor_luminance_delta(rgb: bytes, width: int, height: int) -> float:
    if len(rgb) != width * height * 3:
        raise ValueError("RGB byte count does not match the declared dimensions.")
    total = 0.0
    count = 0
    for y_value in range(height):
        row = y_value * width
        for x_value in range(width - 1):
            left = (row + x_value) * 3
            right = left + 3
            left_luma = (
                0.2126 * rgb[left] + 0.7152 * rgb[left + 1] + 0.0722 * rgb[left + 2]
            ) / 255.0
            right_luma = (
                0.2126 * rgb[right]
                + 0.7152 * rgb[right + 1]
                + 0.0722 * rgb[right + 2]
            ) / 255.0
            total += abs(right_luma - left_luma)
            count += 1
    return total / count


def _probe(ffprobe: Path, media: Path) -> dict[str, Any]:
    result = subprocess.run(
        [
            str(ffprobe),
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(media),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
        shell=False,
    )
    payload = json.loads(result.stdout)
    streams = payload.get("streams", [])
    video = next((item for item in streams if item.get("codec_type") == "video"), None)
    audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
    if not isinstance(video, dict) or not isinstance(audio, dict):
        raise ValueError("R13.1 preview must contain both video and audio streams.")
    return {
        "width": int(video["width"]),
        "height": int(video["height"]),
        "codec": video.get("codec_name"),
        "pixelFormat": video.get("pix_fmt"),
        "fps": video.get("avg_frame_rate"),
        "frameCount": int(video.get("nb_frames", 0)),
        "durationSeconds": float(video.get("duration", payload["format"]["duration"])),
        "audioCodec": audio.get("codec_name"),
        "audioSampleRate": int(audio.get("sample_rate", 0)),
        "audioChannels": int(audio.get("channels", 0)),
    }


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        "w",
        encoding="utf-8",
        newline="\n",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=True, sort_keys=True)
        handle.write("\n")
        temporary = Path(handle.name)
    os.replace(temporary, path)


def analyze_r131_media(
    media_root: Path,
    ffmpeg: Path,
    ffprobe: Path,
    motion_report: Path,
    output: Path,
) -> dict[str, Any]:
    root = media_root.resolve(strict=True)
    ffmpeg_path = ffmpeg.resolve(strict=True)
    ffprobe_path = ffprobe.resolve(strict=True)
    manifest_path = root / "r13.1-render-manifest.json"
    manifest = _read_json(manifest_path)
    if (
        manifest.get("kind") != "trackprompt-cinematic-v2-r13.1-render-manifest"
        or manifest.get("revisionId") != "andromeda-r13.1-selected-refinement"
        or manifest.get("frameRange", {}).get("count") != 120
    ):
        raise ValueError("The R13.1 render manifest is invalid.")
    states = manifest.get("reviewStates")
    if not isinstance(states, list) or len(states) != 8:
        raise ValueError("R13.1 diagnostics require all eight review states.")
    reviews: list[dict[str, Any]] = []
    for state in states:
        if not isinstance(state, dict):
            raise ValueError("R13.1 review state is invalid.")
        native = _artifact(root, state.get("native"), "native review still")
        phone = _artifact(root, state.get("phone"), "phone review still")
        native_rgb = _decode_rgb(ffmpeg_path, native, 1080, 1920)
        phone_rgb = _decode_rgb(ffmpeg_path, phone, 180, 320)
        native_luminance = luminance_metrics(native_rgb, 1080, 1920)
        phone_luminance = luminance_metrics(phone_rgb, 180, 320)
        native_contrast = contrast_metrics(native_rgb)
        phone_contrast = contrast_metrics(phone_rgb)
        findings: list[str] = []
        if max(
            native_luminance["nearBlackFraction"],
            phone_luminance["nearBlackFraction"],
        ) > NEAR_BLACK_REVIEW_FRACTION:
            findings.append("near-black-area-exceeds-cosmic-review-threshold")
        if max(
            native_luminance["clippedHighlightFraction"],
            phone_luminance["clippedHighlightFraction"],
        ) > CLIPPED_HIGHLIGHT_REVIEW_FRACTION:
            findings.append("clipped-highlight-area-exceeds-one-percent")
        if min(native_contrast["p90MinusP10"], phone_contrast["p90MinusP10"]) < 0.12:
            findings.append("mobile-value-separation-is-weak")
        reviews.append(
            {
                "id": state.get("id"),
                "frame": state.get("frame"),
                "criteria": state.get("criteria"),
                "nativeLuminance": native_luminance,
                "phoneLuminance": phone_luminance,
                "nativeContrast": native_contrast,
                "phoneContrast": phone_contrast,
                "findings": findings,
            }
        )
    comparison = manifest.get("qualityComparison")
    if not isinstance(comparison, dict):
        raise ValueError("R13.1 quality comparison is missing.")
    before_phone = _artifact(root, comparison.get("beforePhone"), "before quality phone still")
    after_phone = _artifact(root, comparison.get("afterPhone"), "after quality phone still")
    before_rgb = _decode_rgb(ffmpeg_path, before_phone, 180, 320)
    after_rgb = _decode_rgb(ffmpeg_path, after_phone, 180, 320)
    preview = _artifact(root, manifest.get("motionPreview"), "motion preview")
    media = _probe(ffprobe_path, preview)
    if (
        media["width"] != 1080
        or media["height"] != 1920
        or media["codec"] != "h264"
        or media["pixelFormat"] != "yuv420p"
        or media["frameCount"] != 120
        or abs(media["durationSeconds"] - 4.0) > 0.05
        or media["audioCodec"] != "aac"
    ):
        raise ValueError("R13.1 encoded preview metadata does not match the bounded contract.")
    motion = _read_json(motion_report.resolve(strict=True))
    if motion.get("technicalPass") is not True or motion.get("frameCount") != 120:
        raise ValueError("R13.1 motion diagnostics did not pass the exact rendered range.")
    payload = {
        "schemaVersion": "1.0.0",
        "kind": "trackprompt-cinematic-v2-r13.1-media-diagnostics",
        "revisionId": "andromeda-r13.1-selected-refinement",
        "renderManifest": {
            "file": manifest_path.name,
            "sha256": sha256_file(manifest_path),
        },
        "motionReport": {
            "file": motion_report.resolve(strict=True).name,
            "sha256": sha256_file(motion_report.resolve(strict=True)),
        },
        "preview": {"metadata": media, "sha256": sha256_file(preview)},
        "reviewStates": reviews,
        "qualityComparison": {
            "beforeTemporalSamples": 8,
            "afterTemporalSamples": 64,
            "beforePhoneNeighborLuminanceDelta": neighbor_luminance_delta(
                before_rgb, 180, 320
            ),
            "afterPhoneNeighborLuminanceDelta": neighbor_luminance_delta(
                after_rgb, 180, 320
            ),
            "interpretation": "Reference metrics only; visual review determines whether stochastic noise improved.",
        },
        "summary": {
            "reviewStateCount": len(reviews),
            "nearBlackReviewCount": sum(
                "near-black-area-exceeds-cosmic-review-threshold" in item["findings"]
                for item in reviews
            ),
            "clippedHighlightMaximum": max(
                max(
                    item["nativeLuminance"]["clippedHighlightFraction"],
                    item["phoneLuminance"]["clippedHighlightFraction"],
                )
                for item in reviews
            ),
            "technicalPass": all(not item["findings"] for item in reviews),
        },
        "automaticArtisticApproval": False,
        "humanArtistApproval": "pending",
    }
    _atomic_json(output, payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze bounded R13.1 media and motion evidence.")
    parser.add_argument("--media-root", type=Path, required=True)
    parser.add_argument("--ffmpeg", type=Path, required=True)
    parser.add_argument("--ffprobe", type=Path, required=True)
    parser.add_argument("--motion-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = analyze_r131_media(
        args.media_root,
        args.ffmpeg,
        args.ffprobe,
        args.motion_report,
        args.output,
    )
    print(json.dumps({"ok": True, "summary": payload["summary"]}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
