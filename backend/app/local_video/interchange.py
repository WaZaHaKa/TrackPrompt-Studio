from __future__ import annotations

import csv
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from .models import LocalVideoTimelineScene


def _frames(seconds: float, fps: int = 24) -> int:
    return round(seconds * fps)


def _timecode(frame: int, fps: int = 24) -> str:
    hours, remainder = divmod(frame, fps * 3600)
    minutes, remainder = divmod(remainder, fps * 60)
    seconds, frames = divmod(remainder, fps)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}:{frames:02d}"


def _uri(path: Path) -> str:
    return path.resolve().as_uri()


def _require_scenes(scenes: tuple[LocalVideoTimelineScene, ...]) -> None:
    if len(scenes) != 16 or scenes[0].start_seconds != 0:
        raise ValueError("Resolve interchange requires the exact 16-scene timeline")
    for previous, current in zip(scenes, scenes[1:], strict=False):
        if abs(previous.end_seconds - current.start_seconds) > 0.000001:
            raise ValueError("Resolve interchange timeline is not contiguous")


def export_resolve_interchange(
    *,
    scenes: tuple[LocalVideoTimelineScene, ...],
    clip_paths: dict[str, Path],
    transitions: list[dict[str, Any]],
    output_root: Path,
) -> dict[str, Path]:
    _require_scenes(scenes)
    missing = [scene.shot_id for scene in scenes if not clip_paths.get(scene.shot_id, Path()).is_file()]
    if missing:
        raise ValueError("Resolve interchange is missing verified scene masters")
    if len(transitions) != 15:
        raise ValueError("Resolve interchange requires exactly 15 transition records")
    output_root.mkdir(parents=True, exist_ok=True)
    files = {
        "fcpxml": output_root / "resolve.fcpxml",
        "fcp7": output_root / "resolve-fcp7.xml",
        "edl": output_root / "timeline.edl",
        "markers": output_root / "markers.csv",
        "editSheet": output_root / "edit-sheet.csv",
    }

    resources = ET.Element("resources")
    ET.SubElement(resources, "format", id="r0", name="FFVideoFormat1080p24", frameDuration="1/24s")
    for index, scene in enumerate(scenes, start=1):
        ET.SubElement(
            resources,
            "asset",
            id=f"r{index}",
            name=scene.shot_id,
            src=_uri(clip_paths[scene.shot_id]),
            start="0s",
            duration=f"{_frames(scene.duration_seconds)}/24s",
            hasVideo="1",
            hasAudio="0",
        )
    fcpxml = ET.Element("fcpxml", version="1.11")
    fcpxml.append(resources)
    library = ET.SubElement(fcpxml, "library")
    event = ET.SubElement(library, "event", name="TrackPrompt local video")
    project = ET.SubElement(event, "project", name="The Riff That Learned to Breathe")
    sequence = ET.SubElement(
        project,
        "sequence",
        format="r0",
        duration=f"{_frames(scenes[-1].end_seconds)}/24s",
        tcStart="0s",
        tcFormat="NDF",
    )
    spine = ET.SubElement(sequence, "spine")
    for index, scene in enumerate(scenes, start=1):
        ET.SubElement(
            spine,
            "asset-clip",
            ref=f"r{index}",
            name=scene.shot_id,
            offset=f"{_frames(scene.start_seconds)}/24s",
            start="0s",
            duration=f"{_frames(scene.duration_seconds)}/24s",
        )
    ET.ElementTree(fcpxml).write(files["fcpxml"], encoding="utf-8", xml_declaration=True)

    xmeml = ET.Element("xmeml", version="5")
    sequence7 = ET.SubElement(xmeml, "sequence")
    ET.SubElement(sequence7, "name").text = "The Riff That Learned to Breathe"
    ET.SubElement(sequence7, "duration").text = str(_frames(scenes[-1].end_seconds))
    rate = ET.SubElement(sequence7, "rate")
    ET.SubElement(rate, "timebase").text = "24"
    ET.SubElement(rate, "ntsc").text = "FALSE"
    media = ET.SubElement(sequence7, "media")
    track = ET.SubElement(ET.SubElement(media, "video"), "track")
    for index, scene in enumerate(scenes, start=1):
        clip = ET.SubElement(track, "clipitem", id=f"clipitem-{index}")
        ET.SubElement(clip, "name").text = scene.shot_id
        ET.SubElement(clip, "start").text = str(_frames(scene.start_seconds))
        ET.SubElement(clip, "end").text = str(_frames(scene.end_seconds))
        ET.SubElement(clip, "in").text = "0"
        ET.SubElement(clip, "out").text = str(_frames(scene.duration_seconds))
        file_element = ET.SubElement(clip, "file", id=f"file-{index}")
        ET.SubElement(file_element, "name").text = clip_paths[scene.shot_id].name
        ET.SubElement(file_element, "pathurl").text = _uri(clip_paths[scene.shot_id])
    ET.ElementTree(xmeml).write(files["fcp7"], encoding="utf-8", xml_declaration=True)

    edl_lines = ["TITLE: THE RIFF THAT LEARNED TO BREATHE", "FCM: NON-DROP FRAME"]
    for index, scene in enumerate(scenes, start=1):
        start_frame = _frames(scene.start_seconds)
        end_frame = _frames(scene.end_seconds)
        duration_frames = end_frame - start_frame
        edl_lines.append(
            f"{index:03d}  AX       V     C        00:00:00:00 {_timecode(duration_frames)} "
            f"{_timecode(start_frame)} {_timecode(end_frame)}"
        )
        edl_lines.append(f"* FROM CLIP NAME: {scene.shot_id}")
    files["edl"].write_text("\n".join(edl_lines) + "\n", encoding="utf-8")

    with files["markers"].open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["marker_name", "seconds", "timecode", "kind"])
        for scene in scenes:
            writer.writerow(
                [scene.shot_id, f"{scene.start_seconds:.6f}", _timecode(_frames(scene.start_seconds)), "scene"]
            )
    transition_by_shot = {str(item.get("fromShotId")): item for item in transitions}
    with files["editSheet"].open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            [
                "shot_id",
                "order",
                "start_seconds",
                "end_seconds",
                "duration_seconds",
                "boundary_source",
                "transition_out",
                "clip_sha256_pending_manifest",
            ]
        )
        for scene in scenes:
            transition = transition_by_shot.get(scene.shot_id, {})
            writer.writerow(
                [
                    scene.shot_id,
                    scene.order,
                    f"{scene.start_seconds:.6f}",
                    f"{scene.end_seconds:.6f}",
                    f"{scene.duration_seconds:.6f}",
                    scene.boundary_source,
                    transition.get("transitionId", ""),
                    "",
                ]
            )
    return files
