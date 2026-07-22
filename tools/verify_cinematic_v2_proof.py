from __future__ import annotations

import argparse
import hashlib
import json
import os
import struct
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.cinematic.schemas import ArtDirectionReview, ArtDirectionReviewCollection  # noqa: E402
from app.cinematic.validation import validate_cinematic_privacy  # noqa: E402

EXPECTED_FRAMES = (15, 48, 67, 87, 109, 132)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _png_dimensions(path: Path) -> tuple[int, int]:
    with path.open("rb") as stream:
        header = stream.read(24)
    if len(header) != 24 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
        raise ValueError(f"{path.name} is not a structurally valid PNG")
    return struct.unpack(">II", header[16:24])


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    validate_cinematic_privacy(payload)
    temporary = path.parent / f".{path.name}.{os.getpid()}.tmp"
    data = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n"
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _ffprobe(path: Path, executable: Path) -> dict[str, Any]:
    result = subprocess.run(
        [
            str(executable),
            "-v",
            "error",
            "-show_entries",
            "format=duration,size:stream=codec_name,codec_type,width,height,pix_fmt,sample_rate,channels",
            "-of",
            "json",
            str(path),
        ],
        shell=False,
        check=False,
        capture_output=True,
        timeout=30,
    )
    if result.returncode != 0 or len(result.stdout) > 1_000_000:
        raise RuntimeError("ffprobe did not verify the bounded preview")
    payload = json.loads(result.stdout.decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("ffprobe returned an invalid payload")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the bounded Cinematic Visualizer V2 proof.")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--ffprobe", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    ffprobe = args.ffprobe.resolve(strict=True)
    preview = root / "preview-signal-to-gate"
    stills: list[dict[str, Any]] = []
    for frame in EXPECTED_FRAMES:
        path = preview / f"frame_{frame:06d}.png"
        width, height = _png_dimensions(path)
        if (width, height) != (640, 360) or path.stat().st_size <= 0:
            raise RuntimeError("A representative still failed the bounded image contract.")
        stills.append(
            {
                "frame": frame,
                "file": path.name,
                "width": width,
                "height": height,
                "sizeBytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
        )

    clip = preview / "signal-to-first-gate-preview.mp4"
    probe = _ffprobe(clip, ffprobe)
    streams = probe.get("streams")
    format_info = probe.get("format")
    if not isinstance(streams, list) or not isinstance(format_info, dict):
        raise RuntimeError("The bounded preview has no verified streams.")
    video = next((item for item in streams if isinstance(item, dict) and item.get("codec_type") == "video"), None)
    audio = next((item for item in streams if isinstance(item, dict) and item.get("codec_type") == "audio"), None)
    duration = float(format_info.get("duration", 0.0))
    if (
        not isinstance(video, dict)
        or not isinstance(audio, dict)
        or video.get("codec_name") != "h264"
        or video.get("pix_fmt") != "yuv420p"
        or (video.get("width"), video.get("height")) != (640, 360)
        or audio.get("codec_name") != "aac"
        or not 0.0 < duration <= 10.0
    ):
        raise RuntimeError("The bounded preview failed its H.264/AAC media contract.")

    review_path = root / "art-direction-reviews.json"
    reviews = ArtDirectionReviewCollection.model_validate_json(review_path.read_text(encoding="utf-8"))
    revised = ArtDirectionReviewCollection(
        reviews=[
            ArtDirectionReview.model_validate(
                {
                    **review.model_dump(mode="json"),
                    "focal_readability": "clear",
                    "depth": "acceptable",
                    "silhouette": "clear",
                    "color_hierarchy": "acceptable",
                    "visual_density": "acceptable",
                    "story_clarity": "needs-revision",
                    "mobile_readability": "clear",
                    "findings": [
                        "The protagonist reads clearly, but narrative landmarks need stronger shot-to-shot differentiation."
                    ],
                    "decision": "revise",
                    "revision_metadata": {
                        "revision": review.revision_metadata.revision + 1,
                        "reviewer": "codex-assisted",
                        "note": "Reviewed across six representative 640x360 stills; no artist approval claimed.",
                    },
                }
            )
            for review in reviews.reviews
        ]
    )
    _atomic_json(review_path, revised.model_dump(mode="json", by_alias=True))
    preview_manifest = {
        "schemaVersion": "1.0.0",
        "kind": "trackprompt-cinematic-v2-preview-manifest",
        "verifiedAt": datetime.now(UTC).isoformat(),
        "preset": "space-journey-story",
        "previewOnly": True,
        "boundedStory": ["Signal", "Awakening", "Departure", "First Gate"],
        "stills": stills,
        "clip": {
            "file": clip.name,
            "sha256": _sha256(clip),
            "sizeBytes": clip.stat().st_size,
            "durationSeconds": duration,
            "videoCodec": "h264",
            "audioCodec": "aac",
            "width": 640,
            "height": 360,
            "pixelFormat": "yuv420p",
        },
        "review": {
            "file": review_path.name,
            "sha256": _sha256(review_path),
            "decision": "revise",
            "artistApproved": False,
            "finding": "Narrative landmarks need stronger shot-to-shot differentiation.",
        },
        "production": {
            "fullTimelineRendered": False,
            "authorizationUsed": False,
            "calibratedForV2": False,
        },
    }
    manifest_path = preview / "preview-manifest.json"
    _atomic_json(manifest_path, preview_manifest)
    print(json.dumps({"ok": True, "manifest": str(manifest_path), "durationSeconds": duration}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
