from __future__ import annotations

from typing import Any

from .motion import apply_fcurve_interpolation

PROTAGONIST_STATES = (
    "dormant", "signalled", "awakened", "travelling", "damaged",
    "transforming", "transformed", "arrived",
)


def animate_protagonist(hero: Any, shot_plan: dict[str, Any]) -> dict[str, Any]:
    state_indices = {state: index for index, state in enumerate(PROTAGONIST_STATES)}
    hero["trackprompt_protagonist"] = True
    hero["trackprompt_state_names"] = ",".join(PROTAGONIST_STATES)
    for shot in shot_plan["shots"]:
        frame = int(shot["frameStart"])
        state = str(shot["protagonistState"])
        hero["protagonist_state_index"] = state_indices[state]
        hero.keyframe_insert(data_path='["protagonist_state_index"]', frame=frame)
        scale = {
            "signalled": 0.92,
            "awakened": 1.0,
            "travelling": 1.04,
            "damaged": 0.82,
            "transforming": 1.18,
            "transformed": 1.12,
            "arrived": 1.08,
        }.get(state, 0.9)
        hero.scale = (scale, scale * (0.86 if state == "damaged" else 1.0), scale)
        hero.keyframe_insert("scale", frame=frame)
        apply_fcurve_interpolation(hero, "scale", str(shot["motion"]["profile"]))
    return {"object": hero.name, "states": list(PROTAGONIST_STATES)}
