"""The recorded-media adapter must reject changed sources and invalid edits."""

import copy
import importlib.util
import json
import os
import shutil
from pathlib import Path

import pytest

from app.subprocess_utils import run_process_bounded
from app.video_generation.exporter import export_davinci_package

SCRIPT = Path(__file__).resolve().parents[3] / "video-projects/hayale-flowers-promo/build_v1.py"
spec = importlib.util.spec_from_file_location("hayale_build", SCRIPT)
assert spec and spec.loader
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def valid_edit():
    assets = {
        "asset-a": {
            "v1Eligible": True,
            "kind": "video",
            "sha256": "a" * 64,
            "probe": {"format": {"duration": "5.0"}},
        }
    }
    cut = {
        "name": "master",
        "width": 1080,
        "height": 1920,
        "fps": 30,
        "durationFrames": 90,
        "segments": [
            {
                "assetId": "asset-a",
                "sourceSha256": "a" * 64,
                "inputKind": "video",
                "sourceInFrames": 60,
                "timelineStartFrames": 0,
                "durationFrames": 90,
            }
        ],
    }
    return cut, assets


def test_edit_can_end_exactly_at_original_media_end():
    cut, assets = valid_edit()
    builder.validate_cut(cut, assets)


@pytest.mark.parametrize(
    "key,value",
    [
        ("sourceSha256", "b" * 64),
        ("sourceInFrames", -1),
        ("sourceInFrames", 61),
        ("timelineStartFrames", 1),
        ("durationFrames", 0),
        ("durationFrames", 89),
        ("inputKind", "still"),
    ],
)
def test_rejects_invalid_selects(key, value):
    cut, assets = valid_edit()
    cut["segments"][0][key] = value
    with pytest.raises(ValueError):
        builder.validate_cut(cut, assets)


def test_rejects_v2_only_assets_even_if_other_metadata_matches():
    cut, assets = valid_edit()
    assets["asset-a"]["v1Eligible"] = False
    with pytest.raises(ValueError):
        builder.validate_cut(cut, assets)


def test_rejects_overlapping_events():
    cut, assets = valid_edit()
    cut["segments"].append(copy.deepcopy(cut["segments"][0]))
    cut["durationFrames"] = 180
    with pytest.raises(ValueError):
        builder.validate_cut(cut, assets)


@pytest.mark.parametrize("name", ["../escape", "C:/escape", "master/extra", "", "unknown"])
def test_rejects_cut_names_that_could_escape_output_root(name):
    cut, assets = valid_edit()
    cut["name"] = name
    with pytest.raises(ValueError):
        builder.validate_cut(cut, assets)


def test_mixed_jpeg_video_package_has_matching_decoded_preview(tmp_path):
    ffmpeg = os.environ.get("FFMPEG_PATH") or shutil.which("ffmpeg")
    ffprobe = os.environ.get("FFPROBE_PATH") or shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        pytest.skip("Local media tools required")
    picture = tmp_path / "synthetic.jpg"
    clip = tmp_path / "synthetic.mp4"
    commands = [
        [ffmpeg, "-v", "error", "-f", "lavfi", "-i", "color=red:s=96x64", "-frames:v", "1", str(picture)],
        [
            ffmpeg,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=s=64x64:r=30",
            "-t",
            "0.3",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-color_range",
            "tv",
            "-colorspace",
            "bt709",
            str(clip),
        ],
    ]
    for command in commands:
        assert run_process_bounded(command, timeout_seconds=30).returncode == 0
    value = {
        "title": "Synthetic mixed range",
        "timeline": {
            "fps": 30,
            "width": 64,
            "height": 64,
            "durationFrames": 18,
            "audioPath": None,
            "normalizeSdr": True,
        },
        "segments": [],
        "markers": [],
        "editorial": {"coverage": {"requiredShotIds": ["shot-001", "shot-002"]}},
    }
    for index, path in enumerate((picture, clip)):
        value["segments"].append(
            {
                "segmentId": f"event-{index + 1:03d}",
                "shotId": f"shot-{index + 1:03d}",
                "chapterId": "test",
                "clipPath": str(path),
                "inputKind": "still" if index == 0 else "video",
                "sourceInFrames": 0,
                "timelineStartFrames": index * 9,
                "durationFrames": 9,
                "sourceFraming": "center",
                "transitionIn": "cut",
                "transitionOut": "cut",
                "editorialNote": "Synthetic only",
            }
        )
    directory = tmp_path / "package"
    exports = export_davinci_package(value, output_root=directory, ffmpeg=ffmpeg)
    assembly = json.loads(Path(exports["assemblyPlan"]).read_text())
    # The project adapter uses explicit export filenames; emulate that contract.
    edit_path = Path(exports["editPlan"])
    edit = json.loads(edit_path.read_text())
    edit["editorial"]["exportFiles"] = {"preview1080p": Path(exports["previewOutput"]).name}
    edit_path.write_text(json.dumps(edit))
    for command in assembly["commands"]:
        assert run_process_bounded(command, timeout_seconds=30).returncode == 0
    report = builder.verify_package(directory, ffmpeg, ffprobe)
    assert report["decodedFrames"] == 18
    assert report["previewMatchesEventDecodedFrames"] == "pass"
    assert report["previewColorMetadata"]["color_range"] == "tv"
    assert report["previewColorMetadata"]["color_space"] == "bt709"
