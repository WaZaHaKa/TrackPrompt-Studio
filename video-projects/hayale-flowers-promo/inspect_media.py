"""Project-local, read-only source inspection; all outputs stay in TrackPrompt.

Requires Pillow for photographic contact sheets. No model/provider/network use.
Run with --ffmpeg and --ffprobe pointing to installed local executables.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))
from app.subprocess_utils import run_process_bounded  # noqa: E402
from app.video_generation.jsonio import atomic_write_json, sha256_file  # noqa: E402

SOURCE = Path("D:/HayaleFlowers/promo-2026-09-15")
RUNTIME = REPO / ".trackprompt-data/video-generation/hayale-flowers-promo"


def run(arguments: list[str]) -> bytes:
    result = run_process_bounded(
        arguments, timeout_seconds=120, stdout_limit=1048576, stderr_limit=65536
    )
    if result.returncode or result.stdout_exceeded or result.stderr_exceeded:
        raise RuntimeError(
            "Bounded media inspection failed; no completion claim is valid"
        )
    return result.stdout


def sheet(
    items: list[tuple[Path, str]], target: Path, *, portrait: bool = False
) -> None:
    columns = 6
    width, height = (160, 285) if portrait else (240, 160)
    canvas = Image.new(
        "RGB",
        (columns * width, ((len(items) + columns - 1) // columns) * (height + 25)),
        "#202020",
    )
    draw = ImageDraw.Draw(canvas)
    for index, (path, label) in enumerate(items):
        with Image.open(path) as source:
            picture = ImageOps.exif_transpose(source).convert("RGB")
            picture.thumbnail((width, height))
        x, y = index % columns * width, index // columns * (height + 25)
        canvas.paste(picture, (x + (width - picture.width) // 2, y))
        draw.text((x + 4, y + height + 3), label, fill="white")
    canvas.save(target, quality=88)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ffmpeg", required=True)
    parser.add_argument("--ffprobe", required=True)
    args = parser.parse_args()
    if not SOURCE.is_dir() or SOURCE.is_symlink() or SOURCE.is_junction():
        raise RuntimeError(
            "Expected immutable source root is unavailable or redirected"
        )
    destination = RUNTIME / "inspection"
    # Never overwrite an earlier inspection, especially its immutable baseline.
    if destination.exists():
        raise RuntimeError(
            "Inspection already exists; retain it and use the saved inventory"
        )
    for ancestor in (REPO / ".trackprompt-data", RUNTIME.parent, RUNTIME):
        if ancestor.is_symlink() or ancestor.is_junction():
            raise RuntimeError("Redirected runtime root refused")
    destination.mkdir(parents=True)
    assets = []
    photographs = []
    counters: Counter[str] = Counter()
    files = sorted(SOURCE.rglob("*"))
    for path in files:
        if path.is_symlink() or path.is_junction():
            raise RuntimeError("Redirected source refused")
        if not path.is_file():
            continue
        relative = path.relative_to(SOURCE).as_posix()
        suffix = path.suffix.lower()
        kind = (
            "video"
            if suffix == ".mp4"
            else "still"
            if suffix in (".jpg", ".jpeg")
            else "raw"
            if suffix == ".nef"
            else "note"
        )
        counters[kind] += 1
        label = f"{kind}-{counters[kind]:03d}"
        stat = path.stat()
        digest = sha256_file(path)
        if (
            path.stat().st_mtime_ns != stat.st_mtime_ns
            or path.stat().st_size != stat.st_size
        ):
            raise RuntimeError("Source changed during inspection")
        asset = {
            "assetId": "asset-" + hashlib.sha256(relative.encode()).hexdigest()[:16],
            "label": label,
            "relativePath": relative,
            "sourcePath": str(path),
            "kind": kind,
            "bytes": stat.st_size,
            "mtimeNs": stat.st_mtime_ns,
            "sha256": digest,
            "v1Eligible": kind in ("video", "still")
            and relative.split("/")[0] in ("video", "stills"),
        }
        if kind == "video":
            metadata = json.loads(
                run(
                    [
                        args.ffprobe,
                        "-v",
                        "error",
                        "-show_entries",
                        "stream=index,codec_type,codec_name,width,height,avg_frame_rate,r_frame_rate,duration,color_range,color_space,color_transfer,color_primaries:stream_side_data=rotation:format=duration",
                        "-of",
                        "json",
                        str(path),
                    ]
                )
            )
            asset["probe"] = metadata
            frames = destination / label
            frames.mkdir()
            run(
                [
                    args.ffmpeg,
                    "-nostdin",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-n",
                    "-i",
                    str(path),
                    "-an",
                    "-vf",
                    "fps=1/2,scale=160:285:force_original_aspect_ratio=decrease",
                    "-q:v",
                    "3",
                    str(frames / "%03d.jpg"),
                ]
            )
            samples = sorted(frames.glob("*.jpg"))
            sheet(
                [(p, f"{label} ~{i * 2 + 1}s") for i, p in enumerate(samples)],
                destination / f"{label}.jpg",
                portrait=True,
            )
        elif kind == "still":
            with Image.open(path) as picture:
                exif = picture.getexif()
                asset["image"] = {
                    "width": picture.width,
                    "height": picture.height,
                    "orientation": exif.get(274, 1),
                    "exifColorSpace": exif.get_ifd(34665).get(40961),
                    "iccProfilePresent": bool(picture.info.get("icc_profile")),
                    "format": picture.format,
                }
                picture.verify()
            photographs.append((path, label))
        elif kind == "raw":
            companion = next(
                (
                    p
                    for p in path.parent.glob(path.stem + ".*")
                    if p.suffix.lower() in (".jpg", ".jpeg")
                ),
                None,
            )
            asset["rawDecodeStatus"] = "not-run; JPEG companion used for V1 review"
            asset["jpegCompanion"] = (
                companion.relative_to(SOURCE).as_posix() if companion else None
            )
        assets.append(asset)
    for index in range(0, len(photographs), 36):
        sheet(
            photographs[index : index + 36],
            destination / f"stills-{index // 36 + 1:02d}.jpg",
        )
    groups: dict[str, list[str]] = {}
    for asset in assets:
        groups.setdefault(asset["sha256"], []).append(asset["assetId"])
    inventory = {
        "sourceRoot": str(SOURCE),
        "counts": dict(counters),
        "assets": assets,
        "exactDuplicateGroups": [g for g in groups.values() if len(g) > 1],
        "technicalQuality": "video metadata and JPEG decode inspected; RAW decode not run",
        "sourceWrites": 0,
        "enhancedStillsUsed": False,
        "audioSelected": False,
    }
    atomic_write_json(destination / "media-inventory.json", inventory)
    print(
        json.dumps(
            {
                "counts": dict(counters),
                "exactDuplicateGroups": len(inventory["exactDuplicateGroups"]),
            }
        )
    )


if __name__ == "__main__":
    main()
