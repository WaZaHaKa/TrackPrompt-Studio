from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Any


class WorkflowContractError(ValueError):
    def __init__(self, code: str, message: str, *, missing_roles: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.code = code
        self.safe_message = message
        self.missing_roles = missing_roles


ROLE_START_IMAGE = "load start image"
ROLE_POSITIVE = "encode positive prompt"
ROLE_NEGATIVE = "encode negative prompt"
ROLE_LATENT = "create Wan image-to-video latent"
ROLE_HIGH_SAMPLER = "high-noise sampling stage"
ROLE_LOW_SAMPLER = "low-noise sampling stage"
ROLE_OUTPUT = "save frame sequence and video"
ROLE_HIGH_MODEL = "load high-noise expert"
ROLE_LOW_MODEL = "load low-noise expert"

ROLE_KEYFRAME_MODEL = "load keyframe model"
ROLE_KEYFRAME_POSITIVE = "encode keyframe positive prompt"
ROLE_KEYFRAME_NEGATIVE = "encode keyframe negative prompt"
ROLE_KEYFRAME_LATENT = "create keyframe latent"
ROLE_KEYFRAME_SAMPLER = "sample keyframe"
ROLE_KEYFRAME_OUTPUT = "save keyframe"

REQUIRED_I2V_ROLES = (
    ROLE_START_IMAGE,
    ROLE_POSITIVE,
    ROLE_NEGATIVE,
    ROLE_LATENT,
    ROLE_HIGH_SAMPLER,
    ROLE_LOW_SAMPLER,
    ROLE_OUTPUT,
)

REQUIRED_KEYFRAME_ROLES = (
    ROLE_KEYFRAME_MODEL,
    ROLE_KEYFRAME_POSITIVE,
    ROLE_KEYFRAME_NEGATIVE,
    ROLE_KEYFRAME_LATENT,
    ROLE_KEYFRAME_SAMPLER,
    ROLE_KEYFRAME_OUTPUT,
)

_SAFE_PREFIX = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_/-]{0,119}$")


def _node_title(node: dict[str, Any]) -> str:
    meta = node.get("_meta")
    title = meta.get("title") if isinstance(meta, dict) else None
    return str(title or "").casefold()


def _class_type(node: dict[str, Any]) -> str:
    value = node.get("class_type")
    return str(value) if isinstance(value, str) else ""


def _inputs(node: dict[str, Any]) -> dict[str, Any]:
    value = node.get("inputs")
    return value if isinstance(value, dict) else {}


def _is_prompt_encoder(node: dict[str, Any]) -> bool:
    class_name = _class_type(node).casefold()
    inputs = _inputs(node)
    return "text" in inputs and ("textencode" in class_name or "text_encoder" in class_name)


def _is_sampler(node: dict[str, Any]) -> bool:
    name = _class_type(node).casefold()
    inputs = _inputs(node)
    has_steps = "steps" in inputs or "end_at_step" in inputs or "start_at_step" in inputs
    return has_steps and ("sampler" in name or "sample" in name)


@dataclass(frozen=True, slots=True)
class WorkflowSemanticMap:
    role_nodes: dict[str, tuple[str, ...]]
    missing_roles: tuple[str, ...]

    @property
    def valid(self) -> bool:
        return not self.missing_roles


def discover_semantic_nodes(workflow: dict[str, Any]) -> WorkflowSemanticMap:
    if not workflow or not all(isinstance(key, str) and isinstance(value, dict) for key, value in workflow.items()):
        raise WorkflowContractError("workflow_invalid", "The ComfyUI API workflow root is invalid.")
    found: dict[str, list[str]] = {role: [] for role in (*REQUIRED_I2V_ROLES, ROLE_HIGH_MODEL, ROLE_LOW_MODEL)}
    unlabelled_prompts: list[str] = []
    unlabelled_samplers: list[str] = []
    unlabelled_models: list[str] = []
    for node_id, raw in workflow.items():
        node = raw
        class_name = _class_type(node).casefold()
        title = _node_title(node)
        inputs = _inputs(node)
        if class_name in {"loadimage", "loadimageoutput"} and "image" in inputs:
            found[ROLE_START_IMAGE].append(node_id)
        if _is_prompt_encoder(node):
            if any(token in title for token in ("negative", "neg prompt", "negative prompt")):
                found[ROLE_NEGATIVE].append(node_id)
            elif any(token in title for token in ("positive", "pos prompt", "positive prompt")):
                found[ROLE_POSITIVE].append(node_id)
            else:
                unlabelled_prompts.append(node_id)
        if (
            ("wan" in class_name and "video" in class_name and "latent" in class_name)
            or {"width", "height", "length"}.issubset(inputs)
            and "wan" in class_name
        ):
            found[ROLE_LATENT].append(node_id)
        if _is_sampler(node):
            if any(token in title for token in ("high noise", "high-noise", "high_noise")):
                found[ROLE_HIGH_SAMPLER].append(node_id)
            elif any(token in title for token in ("low noise", "low-noise", "low_noise")):
                found[ROLE_LOW_SAMPLER].append(node_id)
            else:
                unlabelled_samplers.append(node_id)
        if "filename_prefix" in inputs and any(token in class_name for token in ("save", "video", "combine")):
            found[ROLE_OUTPUT].append(node_id)
        if any(key in inputs for key in ("unet_name", "model_name", "diffusion_model")):
            if any(token in title for token in ("high noise", "high-noise", "high_noise")):
                found[ROLE_HIGH_MODEL].append(node_id)
            elif any(token in title for token in ("low noise", "low-noise", "low_noise")):
                found[ROLE_LOW_MODEL].append(node_id)
            else:
                unlabelled_models.append(node_id)

    # Current official templates may omit custom titles. Two prompt encoders and
    # two samplers still express semantic roles by their ordered graph stages;
    # accept only an unambiguous pair and never depend on numeric node IDs.
    if not found[ROLE_POSITIVE] and not found[ROLE_NEGATIVE] and len(unlabelled_prompts) == 2:
        ordered = tuple(workflow.keys())
        pair = sorted(unlabelled_prompts, key=ordered.index)
        found[ROLE_POSITIVE].append(pair[0])
        found[ROLE_NEGATIVE].append(pair[1])
    if not found[ROLE_HIGH_SAMPLER] and not found[ROLE_LOW_SAMPLER] and len(unlabelled_samplers) == 2:
        first, second = sorted(
            unlabelled_samplers,
            key=lambda item: int(_inputs(workflow[item]).get("start_at_step", 0) or 0),
        )
        found[ROLE_HIGH_SAMPLER].append(first)
        found[ROLE_LOW_SAMPLER].append(second)
    if not found[ROLE_HIGH_MODEL] and not found[ROLE_LOW_MODEL] and len(unlabelled_models) == 2:
        ordered = tuple(workflow.keys())
        pair = sorted(unlabelled_models, key=ordered.index)
        found[ROLE_HIGH_MODEL].append(pair[0])
        found[ROLE_LOW_MODEL].append(pair[1])

    normalized = {role: tuple(ids) for role, ids in found.items() if ids}
    missing = tuple(role for role in REQUIRED_I2V_ROLES if not normalized.get(role))
    return WorkflowSemanticMap(role_nodes=normalized, missing_roles=missing)


def discover_keyframe_semantic_nodes(workflow: dict[str, Any]) -> WorkflowSemanticMap:
    if not workflow or not all(isinstance(key, str) and isinstance(value, dict) for key, value in workflow.items()):
        raise WorkflowContractError("workflow_invalid", "The ComfyUI API workflow root is invalid.")
    found: dict[str, list[str]] = {role: [] for role in REQUIRED_KEYFRAME_ROLES}
    for node_id, node in workflow.items():
        class_name = _class_type(node).casefold()
        title = _node_title(node)
        inputs = _inputs(node)
        if "checkpointloader" in class_name and "ckpt_name" in inputs:
            found[ROLE_KEYFRAME_MODEL].append(node_id)
        if _is_prompt_encoder(node):
            if "negative" in title:
                found[ROLE_KEYFRAME_NEGATIVE].append(node_id)
            elif "positive" in title:
                found[ROLE_KEYFRAME_POSITIVE].append(node_id)
        if {"width", "height", "batch_size"}.issubset(inputs) and "latent" in class_name:
            found[ROLE_KEYFRAME_LATENT].append(node_id)
        if _is_sampler(node) and "latent_image" in inputs:
            found[ROLE_KEYFRAME_SAMPLER].append(node_id)
        if "filename_prefix" in inputs and "save" in class_name:
            found[ROLE_KEYFRAME_OUTPUT].append(node_id)
    normalized = {role: tuple(ids) for role, ids in found.items() if ids}
    missing = tuple(role for role in REQUIRED_KEYFRAME_ROLES if len(normalized.get(role, ())) != 1)
    return WorkflowSemanticMap(role_nodes=normalized, missing_roles=missing)


def _one(mapping: WorkflowSemanticMap, role: str) -> str:
    values = mapping.role_nodes.get(role, ())
    if len(values) != 1:
        raise WorkflowContractError(
            "workflow_role_ambiguous",
            f"The workflow must contain exactly one semantic node for {role}.",
            missing_roles=(role,),
        )
    return values[0]


def _patch_sampler(inputs: dict[str, Any], *, seed: int, steps: int, cfg: float, start: int, end: int) -> None:
    if "seed" in inputs:
        inputs["seed"] = seed
    if "noise_seed" in inputs:
        inputs["noise_seed"] = seed
    if "steps" in inputs:
        inputs["steps"] = steps
    if "cfg" in inputs:
        inputs["cfg"] = cfg
    if "start_at_step" in inputs:
        inputs["start_at_step"] = start
    if "end_at_step" in inputs:
        inputs["end_at_step"] = end


def compile_i2v_workflow(
    workflow: dict[str, Any],
    *,
    uploaded_image_name: str,
    positive_prompt: str,
    negative_prompt: str,
    seed: int,
    width: int,
    height: int,
    length_frames: int,
    steps: int,
    cfg: float,
    expert_boundary: float,
    output_prefix: str,
    high_model_name: str | None = None,
    low_model_name: str | None = None,
) -> tuple[dict[str, Any], WorkflowSemanticMap]:
    if not uploaded_image_name or any(token in uploaded_image_name for token in ("/", "\\", "..", "\x00")):
        raise WorkflowContractError("workflow_image_name_invalid", "The uploaded image identity is invalid.")
    if not positive_prompt.strip() or not negative_prompt.strip():
        raise WorkflowContractError("workflow_prompt_invalid", "The workflow prompts must not be empty.")
    if not _SAFE_PREFIX.fullmatch(output_prefix) or ".." in output_prefix:
        raise WorkflowContractError("workflow_output_prefix_invalid", "The output prefix is invalid.")
    if not (256 <= width <= 4096 and 256 <= height <= 4096 and 1 <= length_frames <= 257):
        raise WorkflowContractError("workflow_dimensions_invalid", "The workflow dimensions are outside safe bounds.")
    if not (1 <= steps <= 200 and 0 < cfg <= 30 and 0 < expert_boundary < 1):
        raise WorkflowContractError("workflow_sampling_invalid", "The workflow sampling values are invalid.")
    mapping = discover_semantic_nodes(workflow)
    if not mapping.valid:
        raise WorkflowContractError(
            "workflow_roles_missing",
            "The installed workflow is missing required semantic node roles.",
            missing_roles=mapping.missing_roles,
        )
    result = copy.deepcopy(workflow)
    _inputs(result[_one(mapping, ROLE_START_IMAGE)])["image"] = uploaded_image_name
    _inputs(result[_one(mapping, ROLE_POSITIVE)])["text"] = positive_prompt.strip()
    _inputs(result[_one(mapping, ROLE_NEGATIVE)])["text"] = negative_prompt.strip()
    latent = _inputs(result[_one(mapping, ROLE_LATENT)])
    for key, value in (("width", width), ("height", height), ("length", length_frames)):
        if key in latent:
            latent[key] = value
    split = max(1, min(steps - 1, round(steps * expert_boundary)))
    high = _inputs(result[_one(mapping, ROLE_HIGH_SAMPLER)])
    low = _inputs(result[_one(mapping, ROLE_LOW_SAMPLER)])
    _patch_sampler(high, seed=seed, steps=steps, cfg=cfg, start=0, end=split)
    _patch_sampler(low, seed=seed, steps=steps, cfg=cfg, start=split, end=steps)
    for node_id in mapping.role_nodes.get(ROLE_OUTPUT, ()):
        _inputs(result[node_id])["filename_prefix"] = output_prefix

    for role, model_name in ((ROLE_HIGH_MODEL, high_model_name), (ROLE_LOW_MODEL, low_model_name)):
        if model_name is None:
            continue
        node_id = _one(mapping, role)
        inputs = _inputs(result[node_id])
        model_input = next(
            (name for name in ("unet_name", "model_name", "diffusion_model") if name in inputs),
            None,
        )
        if model_input is None:
            raise WorkflowContractError("workflow_model_input_missing", "A model loader input is missing.")
        inputs[model_input] = model_name
    return result, mapping


def compile_keyframe_workflow(
    workflow: dict[str, Any],
    *,
    positive_prompt: str,
    negative_prompt: str,
    seed: int,
    width: int,
    height: int,
    output_prefix: str,
) -> tuple[dict[str, Any], WorkflowSemanticMap]:
    if not positive_prompt.strip() or not negative_prompt.strip():
        raise WorkflowContractError("workflow_prompt_invalid", "The workflow prompts must not be empty.")
    if not _SAFE_PREFIX.fullmatch(output_prefix) or ".." in output_prefix:
        raise WorkflowContractError("workflow_output_prefix_invalid", "The output prefix is invalid.")
    if not (256 <= width <= 4096 and 256 <= height <= 4096):
        raise WorkflowContractError("workflow_dimensions_invalid", "The workflow dimensions are outside safe bounds.")
    mapping = discover_keyframe_semantic_nodes(workflow)
    if not mapping.valid:
        raise WorkflowContractError(
            "workflow_roles_missing",
            "The installed keyframe workflow is missing required semantic node roles.",
            missing_roles=mapping.missing_roles,
        )
    result = copy.deepcopy(workflow)
    _inputs(result[_one(mapping, ROLE_KEYFRAME_POSITIVE)])["text"] = positive_prompt.strip()
    _inputs(result[_one(mapping, ROLE_KEYFRAME_NEGATIVE)])["text"] = negative_prompt.strip()
    latent = _inputs(result[_one(mapping, ROLE_KEYFRAME_LATENT)])
    latent["width"] = width
    latent["height"] = height
    sampler = _inputs(result[_one(mapping, ROLE_KEYFRAME_SAMPLER)])
    if "seed" in sampler:
        sampler["seed"] = seed
    elif "noise_seed" in sampler:
        sampler["noise_seed"] = seed
    else:
        raise WorkflowContractError("workflow_seed_input_missing", "The keyframe sampler seed input is missing.")
    _inputs(result[_one(mapping, ROLE_KEYFRAME_OUTPUT)])["filename_prefix"] = output_prefix
    return result, mapping
