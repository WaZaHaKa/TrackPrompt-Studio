from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class LocalVideoPackageError(ValueError):
    """A safe package validation error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.safe_message = message


PACKAGE_FILES = (
    "project-config.json",
    "creative-bible.json",
    "continuity-profile.json",
    "chapter-map.json",
    "shot-bank.json",
    "edit-blueprint.json",
    "render-plan.json",
    "model-profile.json",
    "hardware-policy.json",
    "rights-and-credits.json",
    "persistent-analysis-policy.json",
    "prompts/global-negative.txt",
    "prompts/master-style-prefix.txt",
    "timing/sync-policy.json",
    "workflows/comfyui-wan22-i2v-a14b-workflow-contract.json",
)

_PROJECT_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,79}$")
_SHOT_ID = re.compile(r"^shot-[0-9]{3}$")
_AUDIO_SUFFIXES = {".wav", ".flac", ".mp3", ".m4a", ".aac", ".ogg"}
_WORD = re.compile(r"\b[\w'-]+\b", re.UNICODE)


def _json_object(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise LocalVideoPackageError("package_file_missing", "The project package is incomplete.")
    if path.stat().st_size > 20_000_000:
        raise LocalVideoPackageError("package_file_too_large", "A project package file is unexpectedly large.")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LocalVideoPackageError("package_json_invalid", "A project package JSON file is invalid.") from exc
    if not isinstance(value, dict):
        raise LocalVideoPackageError("package_json_invalid", "A project package JSON root must be an object.")
    return value


def _text(path: Path) -> str:
    if not path.is_file() or path.stat().st_size > 1_000_000:
        raise LocalVideoPackageError("package_file_missing", "The project package is incomplete.")
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as exc:
        raise LocalVideoPackageError("package_text_invalid", "A project package text file is invalid.") from exc


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


@dataclass(frozen=True, slots=True)
class LocalVideoProjectPackage:
    root: Path
    project_id: str
    title: str
    project_config: dict[str, Any]
    creative_bible: dict[str, Any]
    continuity_profile: dict[str, Any]
    chapter_map: dict[str, Any]
    shot_bank: dict[str, Any]
    edit_blueprint: dict[str, Any]
    render_plan: dict[str, Any]
    model_profile: dict[str, Any]
    hardware_policy: dict[str, Any]
    rights: dict[str, Any]
    persistence_policy: dict[str, Any]
    sync_policy: dict[str, Any]
    workflow_contract: dict[str, Any]
    global_negative: str
    style_prefix: str
    package_digest: str

    @property
    def shots(self) -> tuple[dict[str, Any], ...]:
        raw = self.shot_bank.get("shots")
        if not isinstance(raw, list):
            return ()
        return tuple(item for item in raw if isinstance(item, dict))

    @property
    def provisional_duration_seconds(self) -> float:
        timeline = self.project_config.get("timeline")
        if isinstance(timeline, dict):
            value = timeline.get("provisionalDurationSeconds")
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return float(value)
        raise LocalVideoPackageError("package_timeline_invalid", "The package timeline is incomplete.")

    @property
    def optional_alternate_shots(self) -> frozenset[str]:
        value = self.project_config.get("optionalHeroVariantShotIds")
        return frozenset(str(item) for item in value) if isinstance(value, list) else frozenset()

    def audio_files(self) -> tuple[Path, ...]:
        directory = self.root / "audio"
        if not directory.is_dir():
            return ()
        return tuple(
            sorted(
                (
                    path
                    for path in directory.iterdir()
                    if path.is_file() and path.suffix.casefold() in _AUDIO_SUFFIXES
                ),
                key=lambda item: item.name.casefold(),
            )
        )

    def require_audio(self) -> Path:
        files = self.audio_files()
        if len(files) != 1:
            raise LocalVideoPackageError(
                "package_audio_count_invalid",
                "The local project must contain exactly one supported audio track.",
            )
        return files[0]


def _validate_project_ids(project_id: str, values: list[dict[str, Any]], directory_name: str) -> None:
    if not _PROJECT_ID.fullmatch(project_id) or project_id != directory_name:
        raise LocalVideoPackageError("package_identity_invalid", "The project package identity is invalid.")
    for value in values:
        candidate = value.get("projectId")
        if candidate is not None and candidate != project_id:
            raise LocalVideoPackageError(
                "package_identity_mismatch",
                "Project package files do not share one stable project identity.",
            )


def _validate_shots(package: LocalVideoProjectPackage) -> None:
    shots = package.shots
    timeline = package.project_config.get("timeline")
    expected_count = int(timeline.get("baseSceneCount", 0)) if isinstance(timeline, dict) else 0
    if expected_count <= 0 or len(shots) != expected_count:
        raise LocalVideoPackageError(
            "package_shot_count_invalid",
            "The package shot count does not match its timeline contract.",
        )
    declared_count = package.shot_bank.get("shotCount")
    if declared_count != expected_count:
        raise LocalVideoPackageError("package_shot_count_invalid", "The shot bank count is inconsistent.")
    required = package.project_config.get("requiredShotIds")
    required_ids = tuple(str(item) for item in required) if isinstance(required, list) else ()
    ids: list[str] = []
    expected_start = 0.0
    orders: list[int] = []
    for index, shot in enumerate(shots, start=1):
        shot_id = str(shot.get("shotId", ""))
        if not _SHOT_ID.fullmatch(shot_id) or shot_id != f"shot-{index:03d}":
            raise LocalVideoPackageError("package_shot_identity_invalid", "Shot identities are invalid.")
        try:
            order = int(shot["order"])
            start = float(shot["provisionalStartSeconds"])
            end = float(shot["provisionalEndSeconds"])
        except (KeyError, TypeError, ValueError) as exc:
            raise LocalVideoPackageError("package_shot_timing_invalid", "A shot timing is invalid.") from exc
        if order != index or abs(start - expected_start) > 0.000001 or end <= start:
            raise LocalVideoPackageError("package_shot_timing_invalid", "Shot timings must be ordered and contiguous.")
        motion = shot.get("prompt")
        keyframe = shot.get("keyframePrompt")
        negative = shot.get("negativePrompt")
        if not all(isinstance(item, str) and item.strip() for item in (motion, keyframe, negative)):
            raise LocalVideoPackageError("package_prompt_missing", "Every shot needs keyframe, motion, and negative prompts.")
        assert isinstance(motion, str)
        if len(_WORD.findall(motion)) > 100:
            raise LocalVideoPackageError("package_motion_prompt_too_long", "A shot motion prompt exceeds 100 words.")
        ids.append(shot_id)
        orders.append(order)
        expected_start = end
    if tuple(ids) != required_ids or len(orders) != len(set(orders)):
        raise LocalVideoPackageError("package_required_shots_invalid", "Required shots are inconsistent.")
    if abs(expected_start - package.provisional_duration_seconds) > 0.000001:
        raise LocalVideoPackageError("package_timeline_invalid", "The provisional timeline duration is inconsistent.")
    alternates = package.optional_alternate_shots
    if not alternates.issubset(ids):
        raise LocalVideoPackageError("package_alternate_shots_invalid", "Optional alternate shots are invalid.")
    maximum = package.project_config.get("maxGeneratedVideoClips")
    if maximum != expected_count + len(alternates):
        raise LocalVideoPackageError("package_reroll_policy_invalid", "The bounded clip limit is inconsistent.")


def load_project_package(projects_root: Path, project_id: str) -> LocalVideoProjectPackage:
    if not _PROJECT_ID.fullmatch(project_id):
        raise LocalVideoPackageError("package_identity_invalid", "The project package identity is invalid.")
    resolved_root = projects_root.resolve()
    package_root = (resolved_root / project_id).resolve()
    if package_root.parent != resolved_root or not package_root.is_dir():
        raise LocalVideoPackageError("package_not_found", "The local video project package was not found.")
    for relative in PACKAGE_FILES:
        path = (package_root / relative).resolve()
        if package_root not in path.parents or not path.is_file():
            raise LocalVideoPackageError("package_file_missing", "The project package is incomplete.")

    values = {
        relative: _json_object(package_root / relative)
        for relative in PACKAGE_FILES
        if relative.endswith(".json")
    }
    config = values["project-config.json"]
    title = config.get("title")
    if config.get("schemaVersion") != "1.0.0" or not isinstance(title, str) or not title.strip():
        raise LocalVideoPackageError("package_config_invalid", "The project configuration is invalid.")
    _validate_project_ids(project_id, list(values.values()), package_root.name)

    digest_payload: dict[str, object] = dict(sorted(values.items()))
    global_negative = _text(package_root / "prompts/global-negative.txt")
    style_prefix = _text(package_root / "prompts/master-style-prefix.txt")
    digest_payload["prompts/global-negative.txt"] = global_negative
    digest_payload["prompts/master-style-prefix.txt"] = style_prefix
    package = LocalVideoProjectPackage(
        root=package_root,
        project_id=project_id,
        title=title.strip(),
        project_config=config,
        creative_bible=values["creative-bible.json"],
        continuity_profile=values["continuity-profile.json"],
        chapter_map=values["chapter-map.json"],
        shot_bank=values["shot-bank.json"],
        edit_blueprint=values["edit-blueprint.json"],
        render_plan=values["render-plan.json"],
        model_profile=values["model-profile.json"],
        hardware_policy=values["hardware-policy.json"],
        rights=values["rights-and-credits.json"],
        persistence_policy=values["persistent-analysis-policy.json"],
        sync_policy=values["timing/sync-policy.json"],
        workflow_contract=values["workflows/comfyui-wan22-i2v-a14b-workflow-contract.json"],
        global_negative=global_negative,
        style_prefix=style_prefix,
        package_digest=hashlib.sha256(_canonical_json(digest_payload)).hexdigest(),
    )
    _validate_shots(package)
    spend = config.get("spendPolicy")
    if not isinstance(spend, dict) or spend.get("networkInferenceAllowed") is not False:
        raise LocalVideoPackageError("package_privacy_invalid", "Local inference privacy is not locked.")
    profiles = config.get("generationProfiles")
    if not isinstance(profiles, list) or not profiles:
        raise LocalVideoPackageError("package_profile_missing", "No local generation profile is declared.")
    if any(not isinstance(item, dict) or item.get("providerId") != "local-comfyui" for item in profiles):
        raise LocalVideoPackageError("package_provider_invalid", "The package contains a non-local provider.")
    workflow_policy = package.rights.get("workflowPolicy")
    if (
        package.rights.get("rightsStatus") != "confirmed-collaboration-full-creative-permission"
        or not isinstance(workflow_policy, dict)
        or not str(workflow_policy.get("copyrightApprovalGate", "")).startswith("satisfied")
    ):
        raise LocalVideoPackageError("package_rights_unsatisfied", "The project rights record is not satisfied.")
    return package


def discover_project_packages(projects_root: Path) -> tuple[LocalVideoProjectPackage, ...]:
    if not projects_root.is_dir():
        return ()
    packages: list[LocalVideoProjectPackage] = []
    for directory in sorted(projects_root.iterdir(), key=lambda item: item.name.casefold()):
        if not directory.is_dir() or not _PROJECT_ID.fullmatch(directory.name):
            continue
        try:
            packages.append(load_project_package(projects_root, directory.name))
        except LocalVideoPackageError:
            continue
    return tuple(packages)
