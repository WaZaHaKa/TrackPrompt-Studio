from __future__ import annotations

from pathlib import Path
from typing import Any


def configure_timeline(cues: dict[str, Any]) -> None:
    import bpy  # type: ignore[import-not-found]

    scene = bpy.context.scene
    timeline = cues["timeline"]
    scene.frame_start = int(timeline["frameStart"])
    scene.frame_end = int(timeline["frameEnd"])
    scene.render.fps = int(timeline["fps"])
    scene.render.fps_base = 1.0
    scene.timeline_markers.clear()
    for section in cues.get("sections", []):
        scene.timeline_markers.new(
            f"TP_SECTION_{section['id']}",
            frame=int(section["startFrame"]),
        )
    for transition in cues.get("transitions", []):
        scene.timeline_markers.new(
            f"TP_TRANSITION_{transition['id']}",
            frame=int(transition["frame"]),
        )


def attach_audio(audio_path: Path, frame_start: int) -> Any:
    import bpy  # type: ignore[import-not-found]

    scene = bpy.context.scene
    editor = scene.sequence_editor_create()
    strips = getattr(editor, "strips", None)
    if strips is None:
        strips = getattr(editor, "sequences", None)
    if strips is None:
        raise RuntimeError("This Blender version exposes no supported sequencer strip API.")
    strip = strips.new_sound("TP_AUDIO", str(audio_path), channel=1, frame_start=frame_start)
    strip.volume = 1.0
    return strip
