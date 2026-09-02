from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence

from .schemas import ShotPlan, StoryPlan

_ABSOLUTE_PATH = re.compile(r"(?i)(?:[a-z]:[\\/]|/(?:users|home|data|mnt)/)")
_FORBIDDEN_KEYS = {
    "filename", "displayname", "sourcepath", "audiopath", "lyrics", "transcript",
    "prompt", "credential", "modelpath", "physicalpath", "outputdirectory",
}


def _walk(value: object, path: str = "root") -> list[str]:
    issues: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).replace("_", "").casefold()
            if normalized in _FORBIDDEN_KEYS:
                issues.append(f"forbidden-field:{path}.{key}")
            issues.extend(_walk(item, f"{path}.{key}"))
    elif isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        for index, item in enumerate(value):
            issues.extend(_walk(item, f"{path}[{index}]"))
    elif isinstance(value, str) and _ABSOLUTE_PATH.search(value):
        issues.append(f"absolute-path:{path}")
    return issues


def validate_cinematic_privacy(payload: object) -> None:
    issues = _walk(payload)
    if issues:
        raise ValueError(f"cinematic artifact failed privacy validation: {issues[0]}")


def validate_plan_pair(story_plan: StoryPlan, shot_plan: ShotPlan) -> None:
    if (
        story_plan.input_digest != shot_plan.input_digest
        or story_plan.seed != shot_plan.seed
        or story_plan.frame_start != shot_plan.frame_start
        or story_plan.frame_end != shot_plan.frame_end
        or story_plan.fps != shot_plan.fps
    ):
        raise ValueError("story and shot plan identities do not match")
    act_ids = {act.id for act in story_plan.acts}
    if any(shot.act_id not in act_ids for shot in shot_plan.shots):
        raise ValueError("shot references an unknown story act")
    validate_cinematic_privacy(story_plan.model_dump(mode="json", by_alias=True))
    validate_cinematic_privacy(shot_plan.model_dump(mode="json", by_alias=True))
    json.dumps(story_plan.model_dump(mode="json", by_alias=True), allow_nan=False)
    json.dumps(shot_plan.model_dump(mode="json", by_alias=True), allow_nan=False)
