import json
import os
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest

from app.subprocess_utils import run_process_bounded
from app.video_generation.assembly import build_assembly_plan
from app.video_generation.davinci import export_fcp7_xml, export_fcpxml


def silent_timeline(tmp_path: Path) -> dict[str, Any]:
    return {
        "title": "Recorded media",
        "timeline": {"fps": 30, "width": 1080, "height": 1920, "durationFrames": 90, "audioPath": None},
        "segments": [
            {
                "segmentId": "event-001",
                "shotId": "shot-001",
                "chapterId": "flowers",
                "clipPath": str(tmp_path / "source.mp4"),
                "derivedMediaPath": str(tmp_path / "event.mp4"),
                "sourceInFrames": 60,
                "timelineStartFrames": 0,
                "durationFrames": 90,
                "editorialNote": "Original footage; source audio omitted",
            }
        ],
        "markers": [{"startFrames": 0, "durationFrames": 90, "title": "Flowers", "chapterId": "flowers"}],
    }


@pytest.mark.parametrize("exporter,extension", [(export_fcpxml, "fcpxml"), (export_fcp7_xml, "xml")])
def test_silent_timeline_has_no_phantom_audio_resource(tmp_path: Path, exporter: Any, extension: str) -> None:
    output = tmp_path / ("silent." + extension)
    exporter(silent_timeline(tmp_path), output)
    root = ET.parse(output).getroot()
    assert root.findall('.//asset[@hasAudio="1"]') == []
    assert root.findall(".//audio") == []
    assert "None" not in output.read_text()
    if extension == "fcpxml":
        assert root.find(".//marker").get("duration") == "1/30s"


def test_silent_assembly_does_not_mux_a_nonexistent_audio_file(tmp_path: Path) -> None:
    plan = build_assembly_plan(
        silent_timeline(tmp_path),
        output_path=tmp_path / "preview.mp4",
        work_root=tmp_path / "work",
        ffmpeg="ffmpeg",
    )
    assert plan.audio_path is None
    assert len(plan.commands) == 2
    assert plan.commands[-1][-1] == str(tmp_path / "preview.mp4")
    assert all("None" not in command for command in plan.commands)


def test_still_input_loops_at_timeline_rate(tmp_path: Path) -> None:
    value = silent_timeline(tmp_path)
    value["segments"][0].update(inputKind="still", sourceInFrames=0, clipPath=str(tmp_path / "photo.jpg"))
    command = build_assembly_plan(
        value, output_path=tmp_path / "preview.mp4", work_root=tmp_path / "work", ffmpeg="ffmpeg"
    ).commands[0]
    assert command[command.index("-loop") + 1] == "1"
    assert command[command.index("-framerate") + 1] == "30"
    # Input duration must precede -i so reverse cannot buffer an infinite still.
    assert command.index("-t") < command.index("-i")


def test_center_framing_uses_scaled_image_center(tmp_path: Path) -> None:
    value = silent_timeline(tmp_path)
    value["segments"][0]["sourceFraming"] = "center"
    command = build_assembly_plan(
        value, output_path=tmp_path / "preview.mp4", work_root=tmp_path / "work", ffmpeg="ffmpeg"
    ).commands[0]
    filters = command[command.index("-vf") + 1].split(",")
    assert "crop=1080:1920:(iw-ow)/2:(ih-oh)/2" in filters


def test_synthetic_still_renders_exact_silent_frame_count(tmp_path: Path) -> None:
    ffmpeg = os.environ.get("FFMPEG_PATH") or shutil.which("ffmpeg")
    ffprobe = os.environ.get("FFPROBE_PATH") or shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        pytest.skip("Local FFmpeg/ffprobe required")
    picture = tmp_path / "synthetic.ppm"
    picture.write_bytes(b"P6\n32 32\n255\n" + bytes([30, 150, 50]) * 32 * 32)
    value = silent_timeline(tmp_path)
    value["timeline"].update(width=64, height=64, durationFrames=9, normalizeSdr=True)
    value["segments"][0].update(
        clipPath=str(picture), inputKind="still", sourceInFrames=0, durationFrames=9, reverse=True
    )
    output = tmp_path / "preview.mp4"
    plan = build_assembly_plan(value, output_path=output, work_root=tmp_path / "work", ffmpeg=ffmpeg)
    for command in plan.commands:
        assert run_process_bounded(command, timeout_seconds=30).returncode == 0
    result = run_process_bounded(
        [ffprobe, "-v", "error", "-count_frames", "-show_streams", "-of", "json", str(output)],
        timeout_seconds=30,
    )
    assert result.returncode == 0
    streams = json.loads(result.stdout)["streams"]
    assert len(streams) == 1
    assert streams[0]["codec_type"] == "video"
    assert streams[0]["nb_read_frames"] == "9"
    assert streams[0]["avg_frame_rate"] == "30/1"
    assert (streams[0]["width"], streams[0]["height"]) == (64, 64)
    assert streams[0]["pix_fmt"] == "yuv420p"
    assert streams[0]["color_range"] == "tv"
    assert streams[0]["color_space"] == "bt709"
    assert streams[0]["color_transfer"] == "bt709"
    assert streams[0]["color_primaries"] == "bt709"
