from __future__ import annotations

import argparse
import json
import re
import struct
from pathlib import Path
from typing import Any

from ....config import Settings
from ....subprocess_utils import ProcessTimedOut, run_process_bounded
from ..preflight import sha256_file
from ..production import SpectrumProductionError

EXPECTED_FRAMES = (
    "intro-0010.png",
    "intro-end-0103.png",
    "main-0105.png",
    "main-mid-0200.png",
    "main-end-0255.png",
    "outro-0257.png",
    "grid-end-0311.png",
    "post-grid-tail-0313.png",
    "near-eof.png",
)
_STAT = re.compile(r"^lavfi\.signalstats\.([A-Z]+)=([0-9.]+)$", re.MULTILINE)


def _png_dimensions(path: Path) -> tuple[int, int]:
    try:
        header = path.read_bytes()[:24]
    except OSError as exc:
        raise SpectrumProductionError("A visual sanity frame is unavailable.") from exc
    if len(header) != 24 or header[:8] != b"\x89PNG\r\n\x1a\n":
        raise SpectrumProductionError("A visual sanity frame is not a readable PNG.")
    return struct.unpack(">II", header[16:24])


def _signal_stats(
    ffmpeg_path: str,
    frame: Path,
    *,
    crop: tuple[int, int, int, int] | None = None,
) -> dict[str, float]:
    filters: list[str] = []
    if crop is not None:
        width, height, x, y = crop
        filters.append(f"crop={width}:{height}:{x}:{y}")
    filters.extend(("signalstats", "metadata=print:file=-"))
    try:
        result = run_process_bounded(
            [
                ffmpeg_path,
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(frame),
                "-vf",
                ",".join(filters),
                "-frames:v",
                "1",
                "-f",
                "null",
                "-",
            ],
            timeout_seconds=30,
            stdout_limit=64_000,
            stderr_limit=32_000,
        )
    except (FileNotFoundError, ProcessTimedOut) as exc:
        raise SpectrumProductionError("FFmpeg could not measure a visual sanity frame.") from exc
    if result.returncode != 0 or result.stdout_exceeded or result.stderr_exceeded:
        raise SpectrumProductionError("FFmpeg rejected a visual sanity frame.")
    text = (result.stdout + result.stderr).decode("utf-8", errors="replace")
    return {name: float(value) for name, value in _STAT.findall(text)}


def build_visual_sanity_report(
    ffmpeg_path: str,
    review_root: Path,
) -> dict[str, Any]:
    frames = {name: review_root / name for name in EXPECTED_FRAMES}
    if any(not path.is_file() or path.stat().st_size <= 0 for path in frames.values()):
        raise SpectrumProductionError("The visual sanity review-frame set is incomplete.")
    dimensions = {name: _png_dimensions(path) for name, path in frames.items()}
    full = {name: _signal_stats(ffmpeg_path, path) for name, path in frames.items()}
    branding = {
        name: _signal_stats(ffmpeg_path, path, crop=(300, 280, 20, 20))
        for name, path in frames.items()
    }
    geometry = {
        name: _signal_stats(ffmpeg_path, path, crop=(600, 350, 325, 10))
        for name, path in frames.items()
    }
    spectrum = {
        name: _signal_stats(ffmpeg_path, path, crop=(700, 180, 60, 270))
        for name, path in frames.items()
    }
    hashes = {name: sha256_file(path) for name, path in frames.items()}
    active_names = EXPECTED_FRAMES[:-1]
    checks = [
        {
            "id": "review-resolution",
            "passed": all(value == (960, 540) for value in dimensions.values()),
            "measured": {name: f"{value[0]}x{value[1]}" for name, value in dimensions.items()},
            "expected": "all review frames are 960x540",
        },
        {
            "id": "frames-not-black",
            "passed": all(stats.get("YAVG", 0) > 8 and stats.get("YMAX", 0) > 120 for stats in full.values()),
            "measured": {name: stats.get("YAVG") for name, stats in full.items()},
            "expected": "YAVG > 8 and YMAX > 120",
        },
        {
            "id": "foreground-present",
            "passed": all(branding[name].get("YMAX", 0) > 180 for name in EXPECTED_FRAMES),
            "measured": {name: branding[name].get("YMAX") for name in EXPECTED_FRAMES},
            "expected": "branding crop YMAX > 180 in every section",
        },
        {
            "id": "geometry-present",
            "passed": all(geometry[name].get("YMAX", 0) > 70 for name in active_names),
            "measured": {name: geometry[name].get("YMAX") for name in EXPECTED_FRAMES},
            "expected": "geometry crop YMAX > 70 through the active tail",
        },
        {
            "id": "spectrum-present",
            "passed": all(spectrum[name].get("YMAX", 0) > 70 for name in active_names),
            "measured": {name: spectrum[name].get("YMAX") for name in EXPECTED_FRAMES},
            "expected": "spectrum crop YMAX > 70 before near-EOF decay",
        },
        {
            "id": "timeline-changes",
            "passed": len(set(hashes.values())) == len(hashes),
            "measured": len(set(hashes.values())),
            "expected": f"{len(hashes)} distinct review-frame hashes",
        },
        {
            "id": "tail-decays",
            "passed": (
                geometry["near-eof.png"].get("YAVG", 999)
                < geometry["post-grid-tail-0313.png"].get("YAVG", 0)
            ),
            "measured": {
                "tailYavg": geometry["post-grid-tail-0313.png"].get("YAVG"),
                "nearEofYavg": geometry["near-eof.png"].get("YAVG"),
            },
            "expected": "near-EOF geometry luminance is lower than the 03:13 tail",
        },
    ]
    return {
        "schemaVersion": "1.0.0",
        "valid": all(bool(check["passed"]) for check in checks),
        "checks": checks,
        "frameSha256": hashes,
        "note": "Statistical sanity checks are not human aesthetic approval.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure non-brittle WZHK geometry frame sanity.")
    parser.add_argument("--review-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    report = build_visual_sanity_report(
        Settings.from_env().ffmpeg_path,
        arguments.review_root.resolve(strict=True),
    )
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps(report, indent=2))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
