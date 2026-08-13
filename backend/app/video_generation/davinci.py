from __future__ import annotations

import csv
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path, PureWindowsPath
from typing import Any

from .contracts import ContractError
from .jsonio import atomic_write_text


def _file_uri(value: str) -> str:
    # Detect a Windows absolute path before Path.resolve(). On POSIX, resolving
    # ``C:\\...`` succeeds but incorrectly treats it as a relative filename.
    windows = PureWindowsPath(value)
    if windows.is_absolute() or (len(windows.drive) == 2 and windows.drive[1] == ":"):
        normalized = str(windows).replace("\\", "/")
        return "file:///" + urllib.parse.quote(normalized, safe="/:._-")

    path = Path(value)
    try:
        return path.resolve().as_uri()
    except ValueError:
        normalized = str(windows).replace("\\", "/")
        return "file://" + urllib.parse.quote(normalized, safe="/:._-")


def _frames(value: int, fps: int) -> str:
    return f"{value}/{fps}s"


def _require_timeline(
    value: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    timeline = value.get("timeline")
    segments = value.get("segments")
    markers = value.get("markers")
    if not isinstance(timeline, dict) or not isinstance(segments, list) or not isinstance(markers, list):
        raise ContractError("resolved timeline is missing timeline/segments/markers")
    return timeline, segments, markers


def export_fcpxml(value: dict[str, Any], output_path: Path) -> None:
    timeline, segments, markers = _require_timeline(value)
    fps = int(timeline["fps"])
    width = int(timeline["width"])
    height = int(timeline["height"])
    duration_frames = int(timeline["durationFrames"])
    title = str(value.get("title", "TrackPrompt Video"))

    root = ET.Element("fcpxml", {"version": "1.11"})
    resources = ET.SubElement(root, "resources")
    format_id = "r-format"
    ET.SubElement(
        resources,
        "format",
        {
            "id": format_id,
            "name": f"TrackPrompt {width}x{height}p{fps}",
            "frameDuration": f"1/{fps}s",
            "width": str(width),
            "height": str(height),
            "colorSpace": "1-1-1 (Rec. 709)",
        },
    )

    asset_by_path: dict[str, str] = {}
    for index, path in enumerate(dict.fromkeys(str(item["clipPath"]) for item in segments), start=1):
        asset_id = f"r-video-{index:03d}"
        asset_by_path[path] = asset_id
        asset = ET.SubElement(
            resources,
            "asset",
            {
                "id": asset_id,
                "name": Path(path).name,
                "start": "0s",
                "duration": _frames(int(timeline["generatedClipDurationSeconds"]) * fps, fps),
                "hasVideo": "1",
                "hasAudio": "0",
                "format": format_id,
            },
        )
        ET.SubElement(
            asset,
            "media-rep",
            {"kind": "original-media", "src": _file_uri(path)},
        )

    audio_id = "r-audio"
    audio_path = str(timeline["audioPath"])
    audio_asset = ET.SubElement(
        resources,
        "asset",
        {
            "id": audio_id,
            "name": Path(audio_path).name,
            "start": "0s",
            "duration": _frames(duration_frames, fps),
            "hasVideo": "0",
            "hasAudio": "1",
            "audioSources": "1",
            "audioChannels": "2",
            "audioRate": "48k",
        },
    )
    ET.SubElement(
        audio_asset,
        "media-rep",
        {"kind": "original-media", "src": _file_uri(audio_path)},
    )

    library = ET.SubElement(root, "library")
    event = ET.SubElement(library, "event", {"name": "TrackPrompt Fast Lane"})
    project = ET.SubElement(event, "project", {"name": title})
    sequence = ET.SubElement(
        project,
        "sequence",
        {
            "format": format_id,
            "duration": _frames(duration_frames, fps),
            "tcStart": "0s",
            "tcFormat": "NDF",
            "audioLayout": "stereo",
            "audioRate": "48k",
        },
    )
    spine = ET.SubElement(sequence, "spine")
    gap = ET.SubElement(
        spine,
        "gap",
        {
            "name": "TrackPrompt Generated Video",
            "offset": "0s",
            "start": "0s",
            "duration": _frames(duration_frames, fps),
        },
    )
    ET.SubElement(
        gap,
        "asset-clip",
        {
            "name": Path(audio_path).name,
            "ref": audio_id,
            "lane": "-1",
            "offset": "0s",
            "start": "0s",
            "duration": _frames(duration_frames, fps),
            "audioRole": "music",
        },
    )

    for item in segments:
        clip = ET.SubElement(
            gap,
            "asset-clip",
            {
                "name": f"{item['segmentId']} {item['shotId']}",
                "ref": asset_by_path[str(item["clipPath"])],
                "lane": "1",
                "offset": _frames(int(item["timelineStartFrames"]), fps),
                "start": _frames(int(item["sourceInFrames"]), fps),
                "duration": _frames(int(item["durationFrames"]), fps),
            },
        )
        ET.SubElement(
            clip,
            "note",
        ).text = str(item.get("editorialNote", ""))

    for marker in markers:
        ET.SubElement(
            gap,
            "marker",
            {
                "start": _frames(int(marker["startFrames"]), fps),
                "duration": "1/24s",
                "value": str(marker["title"]),
                "note": str(marker["chapterId"]),
            },
        )

    ET.indent(root, space="  ")
    xml = '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n'
    xml += ET.tostring(root, encoding="unicode") + "\n"
    atomic_write_text(output_path, xml)


def export_fcp7_xml(value: dict[str, Any], output_path: Path) -> None:
    timeline, segments, markers = _require_timeline(value)
    fps = int(timeline["fps"])
    width = int(timeline["width"])
    height = int(timeline["height"])
    duration_frames = int(timeline["durationFrames"])
    title = str(value.get("title", "TrackPrompt Video"))

    root = ET.Element("xmeml", {"version": "5"})
    sequence = ET.SubElement(root, "sequence", {"id": "sequence-1"})
    ET.SubElement(sequence, "name").text = title
    ET.SubElement(sequence, "duration").text = str(duration_frames)
    rate = ET.SubElement(sequence, "rate")
    ET.SubElement(rate, "timebase").text = str(fps)
    ET.SubElement(rate, "ntsc").text = "FALSE"
    timecode = ET.SubElement(sequence, "timecode")
    tc_rate = ET.SubElement(timecode, "rate")
    ET.SubElement(tc_rate, "timebase").text = str(fps)
    ET.SubElement(tc_rate, "ntsc").text = "FALSE"
    ET.SubElement(timecode, "string").text = "00:00:00:00"
    ET.SubElement(timecode, "frame").text = "0"
    ET.SubElement(timecode, "displayformat").text = "NDF"

    media = ET.SubElement(sequence, "media")
    video = ET.SubElement(media, "video")
    fmt = ET.SubElement(video, "format")
    sample = ET.SubElement(fmt, "samplecharacteristics")
    sample_rate = ET.SubElement(sample, "rate")
    ET.SubElement(sample_rate, "timebase").text = str(fps)
    ET.SubElement(sample_rate, "ntsc").text = "FALSE"
    ET.SubElement(sample, "width").text = str(width)
    ET.SubElement(sample, "height").text = str(height)
    ET.SubElement(sample, "anamorphic").text = "FALSE"
    ET.SubElement(sample, "pixelaspectratio").text = "square"
    ET.SubElement(sample, "fielddominance").text = "none"
    track = ET.SubElement(video, "track")

    file_ids: dict[str, str] = {}
    for index, item in enumerate(segments, start=1):
        path = str(item["clipPath"])
        file_id = file_ids.setdefault(path, f"file-video-{len(file_ids) + 1:03d}")
        clipitem = ET.SubElement(track, "clipitem", {"id": f"clipitem-{index:04d}"})
        ET.SubElement(clipitem, "name").text = f"{item['segmentId']} {item['shotId']}"
        ET.SubElement(clipitem, "duration").text = str(int(timeline["generatedClipDurationSeconds"]) * fps)
        clip_rate = ET.SubElement(clipitem, "rate")
        ET.SubElement(clip_rate, "timebase").text = str(fps)
        ET.SubElement(clip_rate, "ntsc").text = "FALSE"
        ET.SubElement(clipitem, "start").text = str(item["timelineStartFrames"])
        ET.SubElement(clipitem, "end").text = str(
            int(item["timelineStartFrames"]) + int(item["durationFrames"])
        )
        ET.SubElement(clipitem, "in").text = str(item["sourceInFrames"])
        ET.SubElement(clipitem, "out").text = str(int(item["sourceInFrames"]) + int(item["durationFrames"]))
        file_element = ET.SubElement(clipitem, "file", {"id": file_id})
        if (
            list(file_ids.values()).index(file_id) == len(file_ids) - 1
            and sum(1 for existing in segments[:index] if str(existing["clipPath"]) == path) == 1
        ):
            ET.SubElement(file_element, "name").text = Path(path).name
            ET.SubElement(file_element, "pathurl").text = _file_uri(path)
            file_rate = ET.SubElement(file_element, "rate")
            ET.SubElement(file_rate, "timebase").text = str(fps)
            ET.SubElement(file_rate, "ntsc").text = "FALSE"
            ET.SubElement(file_element, "duration").text = str(
                int(timeline["generatedClipDurationSeconds"]) * fps
            )
            file_media = ET.SubElement(file_element, "media")
            ET.SubElement(file_media, "video")
        labels = ET.SubElement(clipitem, "labels")
        ET.SubElement(labels, "label2").text = str(item["chapterId"])
        ET.SubElement(clipitem, "comments").text = str(item.get("editorialNote", ""))

    audio = ET.SubElement(media, "audio")
    audio_track = ET.SubElement(audio, "track")
    audio_clip = ET.SubElement(audio_track, "clipitem", {"id": "audio-master-1"})
    audio_path = str(timeline["audioPath"])
    ET.SubElement(audio_clip, "name").text = Path(audio_path).name
    ET.SubElement(audio_clip, "duration").text = str(duration_frames)
    audio_rate = ET.SubElement(audio_clip, "rate")
    ET.SubElement(audio_rate, "timebase").text = str(fps)
    ET.SubElement(audio_rate, "ntsc").text = "FALSE"
    ET.SubElement(audio_clip, "start").text = "0"
    ET.SubElement(audio_clip, "end").text = str(duration_frames)
    ET.SubElement(audio_clip, "in").text = "0"
    ET.SubElement(audio_clip, "out").text = str(duration_frames)
    audio_file = ET.SubElement(audio_clip, "file", {"id": "file-audio-master"})
    ET.SubElement(audio_file, "name").text = Path(audio_path).name
    ET.SubElement(audio_file, "pathurl").text = _file_uri(audio_path)
    ET.SubElement(audio_file, "duration").text = str(duration_frames)
    audio_file_media = ET.SubElement(audio_file, "media")
    audio_media = ET.SubElement(audio_file_media, "audio")
    sample_characteristics = ET.SubElement(audio_media, "samplecharacteristics")
    ET.SubElement(sample_characteristics, "depth").text = "16"
    ET.SubElement(sample_characteristics, "samplerate").text = "48000"
    ET.SubElement(audio_media, "channelcount").text = "2"

    for marker in markers:
        marker_element = ET.SubElement(sequence, "marker")
        ET.SubElement(marker_element, "name").text = str(marker["title"])
        ET.SubElement(marker_element, "comment").text = str(marker["chapterId"])
        ET.SubElement(marker_element, "in").text = str(marker["startFrames"])
        ET.SubElement(marker_element, "out").text = str(marker["startFrames"])

    ET.indent(root, space="  ")
    xml = '<?xml version="1.0" encoding="UTF-8"?>\n'
    xml += ET.tostring(root, encoding="unicode") + "\n"
    atomic_write_text(output_path, xml)


def export_edl(value: dict[str, Any], output_path: Path) -> None:
    timeline, segments, _ = _require_timeline(value)
    fps = int(timeline["fps"])

    def timecode(frames: int) -> str:
        hours, rest = divmod(frames, fps * 3600)
        minutes, rest = divmod(rest, fps * 60)
        seconds, frame = divmod(rest, fps)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}:{frame:02d}"

    lines = [f"TITLE: {value.get('title', 'TrackPrompt Video')}", "FCM: NON-DROP FRAME", ""]
    for index, item in enumerate(segments, start=1):
        source_in = int(item["sourceInFrames"])
        source_out = source_in + int(item["durationFrames"])
        record_in = int(item["timelineStartFrames"])
        record_out = record_in + int(item["durationFrames"])
        reel = str(item["shotId"]).replace("-", "")[:8].upper()
        lines.append(
            f"{index:03d}  {reel:<8} V     C        "
            f"{timecode(source_in)} {timecode(source_out)} "
            f"{timecode(record_in)} {timecode(record_out)}"
        )
        lines.append(f"* FROM CLIP NAME: {Path(str(item['clipPath'])).name}")
        lines.append(f"* CHAPTER: {item['chapterId']}")
        lines.append("")
    atomic_write_text(output_path, "\n".join(lines) + "\n")


def export_edit_sheet_csv(value: dict[str, Any], output_path: Path) -> None:
    timeline, segments, _ = _require_timeline(value)
    fps = int(timeline["fps"])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "segment_id",
                "chapter_id",
                "shot_id",
                "clip_path",
                "timeline_start_seconds",
                "duration_seconds",
                "source_in_seconds",
                "transition_in",
                "transition_out",
                "editorial_note",
            ]
        )
        for item in segments:
            writer.writerow(
                [
                    item["segmentId"],
                    item["chapterId"],
                    item["shotId"],
                    item["clipPath"],
                    round(int(item["timelineStartFrames"]) / fps, 3),
                    round(int(item["durationFrames"]) / fps, 3),
                    round(int(item["sourceInFrames"]) / fps, 3),
                    item["transitionIn"],
                    item["transitionOut"],
                    item["editorialNote"],
                ]
            )


def export_marker_csv(value: dict[str, Any], output_path: Path) -> None:
    timeline, _, markers = _require_timeline(value)
    fps = int(timeline["fps"])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["name", "start_seconds", "duration_seconds", "note"])
        for marker in markers:
            writer.writerow(
                [
                    marker["title"],
                    round(int(marker["startFrames"]) / fps, 3),
                    round(int(marker["durationFrames"]) / fps, 3),
                    marker["chapterId"],
                ]
            )
