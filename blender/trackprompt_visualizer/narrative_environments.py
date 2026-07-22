from __future__ import annotations

from typing import Any

ENVIRONMENTS = (
    "dead_moon", "signal_ruins", "launch_structure", "gate_corridor",
    "broken_void", "transformation_megastructure", "andromeda_arrival",
)


def build_narrative_environments(
    shot_plan: dict[str, Any],
    collection: Any,
) -> dict[str, Any]:
    import bpy  # type: ignore[import-not-found]

    anchors: dict[str, Any] = {}
    for index, name in enumerate(ENVIRONMENTS):
        anchor = bpy.data.objects.new(f"TP_ENV_{name.upper()}", None)
        collection.objects.link(anchor)
        anchor["trackprompt_environment"] = name
        anchor["trackprompt_environment_index"] = index
        anchors[name] = anchor
    for shot in shot_plan["shots"]:
        active = str(shot["environment"]["environment"])
        frame = int(shot["frameStart"])
        for name, anchor in anchors.items():
            anchor.hide_viewport = name != active
            anchor.hide_render = name != active
            anchor.keyframe_insert("hide_viewport", frame=frame)
            anchor.keyframe_insert("hide_render", frame=frame)
    return {"environmentCount": len(anchors), "anchors": [anchor.name for anchor in anchors.values()]}
