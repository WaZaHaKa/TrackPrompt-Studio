from __future__ import annotations

import re

from .contracts import CreativeBible, ShotSpec

_SPACE = re.compile(r"\s+")


def _join(values: tuple[str, ...]) -> str:
    return ", ".join(item.strip() for item in values if item.strip())


def compile_prompt(bible: CreativeBible, shot: ShotSpec) -> tuple[str, str]:
    """Compile a project shot into one self-contained Veo prompt.

    The generated prompt intentionally excludes source audio, transcript text,
    lyrics, local paths, account IDs, and credentials.
    """

    parts = [
        "Cinematic music-video shot with no dialogue and no visible text.",
        f"Persistent visual identity: {bible.visual_identity}",
        f"Persistent protagonist: {bible.protagonist}",
        f"Narrative intent: {shot.narrative_intent}.",
        f"Shot action and environment: {shot.prompt}",
        f"Palette: {_join(bible.palette)}.",
        f"Camera language: {_join(bible.camera_language)}.",
        f"Lighting: {_join(bible.lighting_language)}.",
        f"Texture and atmosphere: {_join(bible.texture_language)}.",
        "Single continuous shot, coherent anatomy, controlled motion, "
        "cinematic depth, no cuts inside the generated clip.",
    ]
    if shot.continuity_tokens:
        parts.append(f"Continuity anchors: {_join(shot.continuity_tokens)}.")

    prompt = _SPACE.sub(" ", " ".join(parts)).strip()
    negative = _SPACE.sub(
        " ",
        ", ".join(
            part.strip(" ,") for part in (bible.global_negative_prompt, shot.negative_prompt) if part.strip()
        ),
    ).strip()
    return prompt, negative
