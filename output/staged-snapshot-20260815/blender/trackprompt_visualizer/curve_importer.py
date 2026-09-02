from __future__ import annotations

from typing import Any

CONTROL_CURVES = {
    "master_energy": ("masterEnergy",),
    "drum_energy": ("drumEnergy", "transientActivity"),
    "bass_energy": ("bassEnergy", "lowBandEnergy"),
    "vocal_energy": ("vocalEnergy",),
    "other_energy": ("otherEnergy", "masterEnergy"),
    "low_band": ("lowBandEnergy",),
    "mid_band": ("midBandEnergy",),
    "high_band": ("highBandEnergy",),
    "brightness": ("brightness",),
    "transient_activity": ("transientActivity",),
}


def resolve_curve_sources(cues: dict[str, Any]) -> tuple[dict[str, str | None], list[dict[str, str]]]:
    curves = cues.get("curves", {})
    if not isinstance(curves, dict) or "masterEnergy" not in curves:
        raise ValueError("masterEnergy is required")
    resolved: dict[str, str | None] = {}
    fallbacks: list[dict[str, str]] = []
    for control, candidates in CONTROL_CURVES.items():
        source = next((candidate for candidate in candidates if candidate in curves), None)
        resolved[control] = source
        requested = candidates[0]
        if source is None:
            fallbacks.append({"control": control, "requested": requested, "used": "constantZero"})
        elif source != requested:
            fallbacks.append({"control": control, "requested": requested, "used": source})
    return resolved, fallbacks


def iter_action_fcurves(action: Any) -> list[Any]:
    legacy = getattr(action, "fcurves", None)
    if legacy is not None:
        return list(legacy)
    result: list[Any] = []
    for layer in getattr(action, "layers", []):
        for strip in getattr(layer, "strips", []):
            for channelbag in getattr(strip, "channelbags", []):
                result.extend(list(getattr(channelbag, "fcurves", [])))
    return result


def create_audio_bus(cues: dict[str, Any]) -> tuple[Any, list[dict[str, str]]]:
    import bpy  # type: ignore[import-not-found]

    resolved, fallbacks = resolve_curve_sources(cues)
    bus = bpy.data.objects.new("TP_AUDIO_BUS", None)
    bpy.context.scene.collection.objects.link(bus)
    bus.empty_display_type = "PLAIN_AXES"
    bus.hide_render = True
    for control in CONTROL_CURVES:
        bus[control] = 0.0
        bus.id_properties_ui(control).update(min=0.0, max=1.0, soft_min=0.0, soft_max=1.0)
    previous_interpolation = bpy.context.preferences.edit.keyframe_new_interpolation_type
    bpy.context.preferences.edit.keyframe_new_interpolation_type = "LINEAR"
    try:
        curves = cues["curves"]
        frame_start = int(cues["timeline"]["frameStart"])
        frame_end = int(cues["timeline"]["frameEnd"])
        for control, source in resolved.items():
            points = (
                curves[source]["points"]
                if source is not None
                else [[frame_start, 0.0], [frame_end, 0.0]]
            )
            for frame, value in points:
                bus[control] = min(1.0, max(0.0, float(value)))
                bus.keyframe_insert(data_path=f'["{control}"]', frame=int(frame), group="TrackPrompt Audio")
    finally:
        bpy.context.preferences.edit.keyframe_new_interpolation_type = previous_interpolation
    action = bus.animation_data.action if bus.animation_data else None
    if action is not None:
        for fcurve in iter_action_fcurves(action):
            for keyframe in fcurve.keyframe_points:
                keyframe.interpolation = "LINEAR"
    return bus, fallbacks
