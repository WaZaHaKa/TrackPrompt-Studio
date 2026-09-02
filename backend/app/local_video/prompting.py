from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from .package import LocalVideoPackageError, LocalVideoProjectPackage

_WORD = re.compile(r"\b[\w'-]+\b", re.UNICODE)


def _normalize(value: str) -> str:
    return " ".join(value.replace("\x00", " ").split()).strip()


def derive_seed(master_seed: int, shot_order: int, *, variation_index: int = 0) -> int:
    if master_seed < 0 or shot_order < 1 or variation_index not in {0, 1}:
        raise LocalVideoPackageError("seed_derivation_invalid", "The deterministic seed input is invalid.")
    # Keep the documented base-seed-plus-order behavior while giving the one
    # allowed hero alternate a stable, non-colliding offset.
    return (master_seed + shot_order + variation_index * 1_000_003) % 4_294_967_296


@dataclass(frozen=True, slots=True)
class CompiledLocalPrompt:
    shot_id: str
    order: int
    variation_index: int
    seed: int
    keyframe_prompt: str
    motion_prompt: str
    negative_prompt: str
    identity_tokens: tuple[str, ...]
    prompt_digest: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "shotId": self.shot_id,
            "order": self.order,
            "variationIndex": self.variation_index,
            "seed": self.seed,
            "keyframePrompt": self.keyframe_prompt,
            "motionPrompt": self.motion_prompt,
            "negativePrompt": self.negative_prompt,
            "identityTokens": list(self.identity_tokens),
            "promptDigest": self.prompt_digest,
        }


def _continuity_tokens(package: LocalVideoProjectPackage, shot: dict[str, Any]) -> tuple[str, ...]:
    groups = package.continuity_profile.get("continuityGroups")
    by_id: dict[str, list[str]] = {}
    if isinstance(groups, list):
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("id"), str):
                continue
            values = group.get("lockedTokens")
            if isinstance(values, list):
                by_id[str(group["id"])] = [
                    _normalize(str(item)) for item in values if _normalize(str(item))
                ]
    requested = shot.get("continuityGroupIds")
    tokens: list[str] = []
    if isinstance(requested, list):
        for group_id in requested:
            tokens.extend(by_id.get(str(group_id), []))
    return tuple(dict.fromkeys(tokens))


def compile_prompts(
    package: LocalVideoProjectPackage,
    *,
    variation_index: int = 0,
) -> tuple[CompiledLocalPrompt, ...]:
    continuity = package.continuity_profile
    master_seed = continuity.get("masterSeed")
    if not isinstance(master_seed, int) or isinstance(master_seed, bool):
        raise LocalVideoPackageError("seed_profile_invalid", "The continuity master seed is invalid.")
    compiled: list[CompiledLocalPrompt] = []
    for order, shot in enumerate(package.shots, start=1):
        shot_id = str(shot["shotId"])
        if variation_index and shot_id not in package.optional_alternate_shots:
            continue
        motion = _normalize(str(shot["prompt"]))
        if len(_WORD.findall(motion)) > 100:
            raise LocalVideoPackageError(
                "package_motion_prompt_too_long", "A shot motion prompt exceeds 100 words."
            )
        keyframe = _normalize(str(shot["keyframePrompt"]))
        # Static appearance and global visual language belong in the keyframe.
        # Wan receives only the package's concise motion/camera prompt.
        keyframe_parts = [package.style_prefix, *_continuity_tokens(package, shot), keyframe]
        compiled_keyframe = ", ".join(dict.fromkeys(part for part in keyframe_parts if part))
        negative_parts = [package.global_negative, _normalize(str(shot["negativePrompt"]))]
        negative = ", ".join(dict.fromkeys(part for part in negative_parts if part))
        seed = derive_seed(master_seed, order, variation_index=variation_index)
        digest_source = "\n".join(
            (shot_id, str(variation_index), str(seed), compiled_keyframe, motion, negative)
        ).encode("utf-8")
        compiled.append(
            CompiledLocalPrompt(
                shot_id=shot_id,
                order=order,
                variation_index=variation_index,
                seed=seed,
                keyframe_prompt=compiled_keyframe,
                motion_prompt=motion,
                negative_prompt=negative,
                identity_tokens=_continuity_tokens(package, shot),
                prompt_digest=hashlib.sha256(digest_source).hexdigest(),
            )
        )
    return tuple(compiled)
