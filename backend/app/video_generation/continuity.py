from __future__ import annotations

import hashlib
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .contracts import CompiledReferenceImage, ContractError
from .jsonio import canonical_json_bytes, read_json, sha256_file

CONTINUITY_SCHEMA_VERSION = "1.0.0"
SEED_DERIVATION_VERSION = "sha256-v1"


@dataclass(frozen=True)
class ContinuityGroup:
    group_id: str
    label: str
    locked_tokens: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.group_id,
            "label": self.label,
            "lockedTokens": list(self.locked_tokens),
        }


@dataclass(frozen=True)
class ContinuityProfile:
    project_id: str
    master_seed: int
    seed_locked: bool
    character_profiles: tuple[dict[str, Any], ...]
    visual_anchors: dict[str, Any]
    continuity_groups: tuple[ContinuityGroup, ...]

    def group(self, group_id: str) -> ContinuityGroup:
        match = next((item for item in self.continuity_groups if item.group_id == group_id), None)
        if match is None:
            raise ContractError(f"unknown continuity group: {group_id}")
        return match

    def to_plan_dict(
        self,
        *,
        master_seed: int | None = None,
        seed_locked: bool | None = None,
    ) -> dict[str, Any]:
        return {
            "schemaVersion": CONTINUITY_SCHEMA_VERSION,
            "masterSeed": self.master_seed if master_seed is None else master_seed,
            "seedLocked": self.seed_locked if seed_locked is None else seed_locked,
            "seedDerivation": SEED_DERIVATION_VERSION,
            "characterProfiles": list(self.character_profiles),
            "visualAnchors": self.visual_anchors,
            "groups": [item.to_dict() for item in self.continuity_groups],
        }


def load_continuity_profile(path: Path) -> ContinuityProfile:
    value = read_json(path)
    if not isinstance(value, dict) or value.get("schemaVersion") != CONTINUITY_SCHEMA_VERSION:
        raise ContractError(f"continuity profile schemaVersion must be {CONTINUITY_SCHEMA_VERSION}")
    groups = tuple(
        ContinuityGroup(
            group_id=str(item["id"]),
            label=str(item["label"]),
            locked_tokens=tuple(str(token) for token in item.get("lockedTokens", [])),
        )
        for item in value.get("continuityGroups", [])
        if isinstance(item, dict)
    )
    if not groups or len({item.group_id for item in groups}) != len(groups):
        raise ContractError("continuity profile groups must be present and unique")
    master_seed = int(value["masterSeed"])
    if not 0 <= master_seed <= 4_294_967_295:
        raise ContractError("continuity masterSeed is outside uint32")
    characters = value.get("characterProfiles", [])
    anchors = value.get("visualAnchors", {})
    if not isinstance(characters, list) or not isinstance(anchors, dict):
        raise ContractError("continuity characterProfiles or visualAnchors are invalid")
    return ContinuityProfile(
        project_id=str(value["projectId"]),
        master_seed=master_seed,
        seed_locked=bool(value.get("seedLocked", True)),
        character_profiles=tuple(dict(item) for item in characters if isinstance(item, dict)),
        visual_anchors=dict(anchors),
        continuity_groups=groups,
    )


def derive_shot_seed(
    *,
    master_seed: int,
    project_id: str,
    continuity_group_ids: tuple[str, ...],
    shot_id: str,
    variation_index: int,
) -> int:
    if not 0 <= master_seed <= 4_294_967_295:
        raise ContractError("master seed is outside uint32")
    if variation_index < 0:
        raise ContractError("variation index must not be negative")
    material = {
        "version": SEED_DERIVATION_VERSION,
        "masterSeed": master_seed,
        "projectId": project_id,
        "continuityGroupIds": list(continuity_group_ids),
        "shotId": shot_id,
        "variationIndex": variation_index,
    }
    return int.from_bytes(hashlib.sha256(canonical_json_bytes(material)).digest()[:4], "big")


def compile_reference_image(
    *,
    path: Path,
    asset_id: str,
    gcs_bucket: str,
    storage_prefix: str,
    project_id: str,
    source_kind: str,
) -> CompiledReferenceImage:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise ContractError("the selected continuity reference image is unavailable")
    suffix = resolved.suffix.lower()
    mime_type = mimetypes.types_map.get(suffix)
    if mime_type not in {"image/jpeg", "image/png"}:
        raise ContractError("continuity reference images must be JPEG or PNG")
    size = resolved.stat().st_size
    if size <= 0 or size > 20 * 1024 * 1024:
        raise ContractError("continuity reference images must be non-empty and no larger than 20 MB")
    with resolved.open("rb") as handle:
        signature = handle.read(12)
    if mime_type == "image/png" and not signature.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ContractError("the selected PNG reference has invalid file bytes")
    if mime_type == "image/jpeg" and not signature.startswith(b"\xff\xd8\xff"):
        raise ContractError("the selected JPEG reference has invalid file bytes")
    digest = sha256_file(resolved)
    bucket = gcs_bucket.removeprefix("gs://").strip("/")
    extension = ".png" if mime_type == "image/png" else ".jpg"
    return CompiledReferenceImage(
        asset_id=asset_id,
        gcs_uri=f"gs://{bucket}/{storage_prefix}/{project_id}/_references/{digest}{extension}",
        mime_type=mime_type,
        sha256=digest,
        source_kind=source_kind,
    )
