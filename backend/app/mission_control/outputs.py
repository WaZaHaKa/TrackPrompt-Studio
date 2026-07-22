from __future__ import annotations

import os
import re
import shutil
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from .discovery import MissionDiscovery, load_json_object
from .errors import MissionControlError
from .models import (
    OutputClassification,
    OutputCreateChildResult,
    OutputEntry,
    OutputInspection,
    RenderIdentity,
)

_SAFE_SEGMENT = re.compile(r"[^a-z0-9]+")
_RENDER_MANIFEST_KIND = "trackprompt-final-render-manifest"


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left.resolve())) == os.path.normcase(str(right.resolve()))


def _contains(parent: Path, child: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
    except (OSError, ValueError):
        return False
    return True


def _nearest_existing(path: Path) -> Path | None:
    candidate = path
    while not candidate.exists():
        if candidate.parent == candidate:
            return None
        candidate = candidate.parent
    return candidate


def _entry(path: Path) -> OutputEntry:
    try:
        details = path.lstat()
        attributes = int(getattr(details, "st_file_attributes", 0))
    except OSError:
        attributes = 0
    is_directory = path.is_dir()
    is_file = path.is_file()
    return OutputEntry(
        name=path.name,
        type="directory" if is_directory else "file" if is_file else "other",
        hidden=path.name.startswith(".")
        or bool(attributes & int(getattr(stat, "FILE_ATTRIBUTE_HIDDEN", 0))),
        system=bool(attributes & int(getattr(stat, "FILE_ATTRIBUTE_SYSTEM", 0))),
        reparse_point=path.is_symlink()
        or bool(attributes & int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))),
    )


def _safe_child_name(value: str) -> str:
    safe = _SAFE_SEGMENT.sub("-", value.strip().lower()).strip("-.")
    if not safe or safe in {".", ".."}:
        safe = "trackprompt-render"
    return safe[:96].rstrip("-.")


class OutputManager:
    def __init__(self, discovery: MissionDiscovery) -> None:
        self.discovery = discovery

    def validated_path(self, raw_path: str, *, must_exist: bool = False) -> Path:
        if not raw_path.strip():
            raise self._unsafe("Output path is empty.")
        candidate = Path(raw_path).expanduser()
        if not candidate.is_absolute():
            raise self._unsafe("Output path must be absolute.")
        try:
            resolved = candidate.resolve(strict=must_exist)
        except OSError as exc:
            raise self._unsafe("Output path could not be resolved.", type(exc).__name__) from exc
        anchor = Path(resolved.anchor)
        try:
            relative_parts = resolved.relative_to(anchor).parts
        except ValueError as exc:
            raise self._unsafe("Output path has an invalid filesystem anchor.") from exc
        if len(relative_parts) < 2:
            raise self._unsafe(
                "Output directory is too broad; choose an isolated directory at least two levels below the drive root."
            )
        return resolved

    def _unsafe(self, detail: str, technical: str | None = None) -> MissionControlError:
        return MissionControlError(
            422,
            "unsafe_output_path",
            "Output folder is not safe",
            detail,
            "Choose a dedicated render folder or create a new render folder inside a suitable parent.",
            technical_details=technical,
        )

    def _identity(
        self,
        path: Path,
        profile_id: str,
        scene_id: str,
    ) -> RenderIdentity:
        profile = self.discovery.get_profile(profile_id)
        scene = self.discovery.get_scene(scene_id)
        if profile.scene_id.casefold() != scene.id.casefold() or profile.scene_sha256 != scene.sha256:
            raise MissionControlError(
                409,
                "scene_profile_mismatch",
                "Scene and profile do not match",
                "The selected saved profile is bound to another exact scene identity.",
                "Choose the approved scene saved in the profile.",
            )
        return RenderIdentity(
            project_id=profile.project_id,
            scene_id=scene.id,
            scene_sha256=scene.sha256,
            profile_id=profile.id,
            profile_sha256=profile.saved_file_sha256,
            output_directory=str(path),
        )

    def inspect(
        self,
        raw_path: str,
        *,
        profile_id: str | None = None,
        scene_id: str | None = None,
    ) -> OutputInspection:
        try:
            path = self.validated_path(raw_path)
        except MissionControlError:
            candidate = Path(raw_path).expanduser()
            return OutputInspection(
                path=str(candidate),
                exists=candidate.exists(),
                usable=False,
                classification=OutputClassification.UNSAFE_PATH,
                issues=["Output path is unsafe or too broad."],
            )
        expected: RenderIdentity | None = None
        if (profile_id is None) != (scene_id is None):
            raise MissionControlError(
                422,
                "incomplete_output_identity",
                "Output identity is incomplete",
                "Profile and scene must be supplied together for resume compatibility inspection.",
                "Reselect the scene and profile, then inspect the folder again.",
            )
        if profile_id is not None and scene_id is not None:
            expected = self._identity(path, profile_id, scene_id)
            profile_path = Path(self.discovery.get_profile(profile_id).path)
            scene_path = Path(self.discovery.get_scene(scene_id).path)
            if _contains(path, profile_path) or _contains(path, scene_path):
                return OutputInspection(
                    path=str(path),
                    exists=path.exists(),
                    usable=False,
                    classification=OutputClassification.UNSAFE_PATH,
                    expected_identity=expected,
                    issues=["The approved scene and saved profile must not be stored inside the production output directory."],
                )
        existing_root = _nearest_existing(path)
        free_bytes = None
        if existing_root is not None:
            try:
                free_bytes = shutil.disk_usage(existing_root).free
            except OSError:
                pass
        if not path.exists():
            parent = _nearest_existing(path.parent)
            usable = parent is not None and parent.is_dir()
            return OutputInspection(
                path=str(path),
                exists=False,
                usable=usable,
                classification=OutputClassification.NEW_OUTPUT,
                expected_identity=expected,
                issues=[] if usable else ["The output parent directory does not exist."],
                free_bytes=free_bytes,
            )
        if not path.is_dir():
            return OutputInspection(
                path=str(path),
                exists=True,
                usable=False,
                classification=OutputClassification.NOT_A_DIRECTORY,
                conflicting_entries=[path.name],
                expected_identity=expected,
                issues=["The selected path exists but is not a directory."],
                free_bytes=free_bytes,
            )
        try:
            entries = [_entry(item) for item in sorted(path.iterdir(), key=lambda item: item.name.casefold())]
        except OSError as exc:
            raise MissionControlError(
                422,
                "output_unreadable",
                "Output folder cannot be inspected",
                "The selected folder cannot be enumerated safely.",
                "Check folder permissions and retry.",
                technical_details=type(exc).__name__,
            ) from exc
        linked_entries = [item for item in entries if item.reparse_point]
        if linked_entries:
            conflicts = [
                f"{item.name} [{', '.join(self._flags(item))}]"
                for item in linked_entries
            ]
            return OutputInspection(
                path=str(path),
                exists=True,
                usable=False,
                classification=OutputClassification.CONTAINS_HIDDEN_SYSTEM_ENTRIES,
                entries=entries,
                conflicting_entries=conflicts,
                expected_identity=expected,
                issues=[
                    "The output contains a linked or reparse-point top-level entry; renderer writes could escape the selected folder."
                ],
                free_bytes=free_bytes,
                create_child_available=True,
            )
        if not entries:
            return OutputInspection(
                path=str(path),
                exists=True,
                usable=True,
                classification=OutputClassification.EMPTY_DIRECTORY,
                entries=[],
                expected_identity=expected,
                free_bytes=free_bytes,
            )
        manifest_path = path / "manifests" / "render-manifest.json"
        if manifest_path.is_file():
            return self._inspect_manifest(
                path,
                manifest_path,
                entries,
                expected,
                free_bytes,
            )
        conflicts = [
            f"{item.name} [{', '.join(self._flags(item))}]" for item in entries
        ]
        hidden_or_system = any(item.hidden or item.system or item.reparse_point for item in entries)
        only_directories = all(item.type == "directory" for item in entries)
        classification = (
            OutputClassification.CONTAINS_HIDDEN_SYSTEM_ENTRIES
            if hidden_or_system
            else OutputClassification.PARENT_SUITABLE
            if only_directories
            else OutputClassification.CONTAINS_UNRELATED_FILES
        )
        issue = (
            "The folder contains hidden, system, or linked entries and cannot be initialized directly."
            if hidden_or_system
            else "The folder already contains child folders; create a new render folder here."
            if only_directories
            else "The folder contains unrelated files and cannot be initialized directly."
        )
        return OutputInspection(
            path=str(path),
            exists=True,
            usable=False,
            classification=classification,
            entries=entries,
            conflicting_entries=conflicts,
            expected_identity=expected,
            issues=[issue],
            free_bytes=free_bytes,
            create_child_available=True,
        )

    def _flags(self, item: OutputEntry) -> list[str]:
        flags: list[str] = [item.type]
        if item.hidden:
            flags.append("hidden")
        if item.system:
            flags.append("system")
        if item.reparse_point:
            flags.append("reparse-point")
        return flags

    def _inspect_manifest(
        self,
        output: Path,
        manifest_path: Path,
        entries: list[OutputEntry],
        expected: RenderIdentity | None,
        free_bytes: int | None,
    ) -> OutputInspection:
        issues: list[str] = []
        try:
            manifest = load_json_object(manifest_path, "Render manifest")
        except MissionControlError:
            return OutputInspection(
                path=str(output),
                exists=True,
                usable=False,
                classification=OutputClassification.INCOMPATIBLE_RENDER,
                entries=entries,
                conflicting_entries=["manifests/render-manifest.json [invalid JSON]"],
                expected_identity=expected,
                issues=["Existing render manifest is invalid JSON."],
                free_bytes=free_bytes,
                create_child_available=True,
            )
        scene = cast(dict[str, Any], manifest.get("scene")) if isinstance(manifest.get("scene"), dict) else {}
        profile = (
            cast(dict[str, Any], manifest.get("renderProfile"))
            if isinstance(manifest.get("renderProfile"), dict)
            else {}
        )
        existing_output = str(manifest.get("outputDirectory", ""))
        existing = RenderIdentity(
            project_id=str(manifest.get("projectId") or (expected.project_id if expected else "unknown")),
            scene_id=str(manifest.get("sceneId") or (expected.scene_id if expected else "unknown")),
            scene_sha256=str(scene.get("sha256", "")).upper(),
            profile_id=str(profile.get("profileId") or profile.get("id") or "unknown"),
            profile_sha256=str(profile.get("sha256", "")).upper(),
            output_directory=existing_output or str(output),
        )
        if manifest.get("kind") != _RENDER_MANIFEST_KIND:
            issues.append("Render manifest kind is unsupported.")
        if existing_output:
            try:
                if not _same_path(Path(existing_output), output):
                    issues.append("Render manifest belongs to another output directory.")
            except OSError:
                issues.append("Render manifest contains an invalid output directory.")
        else:
            issues.append("Render manifest does not contain its exact output directory.")
        if expected is not None:
            if existing.scene_sha256 != expected.scene_sha256:
                issues.append("Output belongs to another scene.")
            if existing.profile_sha256 != expected.profile_sha256:
                issues.append("Output belongs to another exact saved profile.")
            self._frame_contract_issues(manifest, expected.profile_id, issues)
        return OutputInspection(
            path=str(output),
            exists=True,
            usable=not issues and expected is not None,
            classification=(
                OutputClassification.COMPATIBLE_RESUMABLE
                if not issues and expected is not None
                else OutputClassification.INCOMPATIBLE_RENDER
            ),
            entries=entries,
            conflicting_entries=[] if not issues else ["manifests/render-manifest.json [identity conflict]"],
            existing_identity=existing,
            expected_identity=expected,
            issues=issues,
            free_bytes=free_bytes,
            create_child_available=bool(issues),
        )

    def _frame_contract_issues(
        self,
        manifest: dict[str, Any],
        profile_id: str,
        issues: list[str],
    ) -> None:
        profile_path, profile_payload, _saved_hash = self.discovery.profile_source(profile_id)
        _ = profile_path
        frame_contract = (
            cast(dict[str, Any], manifest.get("frameContract"))
            if isinstance(manifest.get("frameContract"), dict)
            else {}
        )
        timeline = (
            cast(dict[str, Any], profile_payload.get("timeline"))
            if isinstance(profile_payload.get("timeline"), dict)
            else profile_payload
        )
        resolution = (
            cast(dict[str, Any], profile_payload.get("resolution"))
            if isinstance(profile_payload.get("resolution"), dict)
            else {}
        )
        sequence = (
            cast(dict[str, Any], profile_payload.get("imageSequence"))
            if isinstance(profile_payload.get("imageSequence"), dict)
            else {}
        )
        expected_values: dict[str, object] = {
            "frameStart": timeline.get("frameStart", profile_payload.get("frameStart")),
            "frameEnd": timeline.get("frameEnd", profile_payload.get("frameEnd")),
            "fps": timeline.get("fps", profile_payload.get("fps")),
            "width": resolution.get("width"),
            "height": resolution.get("height"),
            "filenamePattern": sequence.get("filenamePattern"),
            "format": sequence.get("format"),
            "bitDepth": sequence.get("bitDepth"),
            "colorMode": sequence.get("colorMode"),
        }
        start = expected_values["frameStart"]
        end = expected_values["frameEnd"]
        if isinstance(start, int) and isinstance(end, int):
            expected_values["frameCount"] = end - start + 1
        for field, value in expected_values.items():
            actual = frame_contract.get(field)
            both_numeric = (
                isinstance(actual, (int, float))
                and not isinstance(actual, bool)
                and isinstance(value, (int, float))
                and not isinstance(value, bool)
            )
            matches = actual == value if both_numeric else str(actual) == str(value)
            if not matches:
                issues.append(f"Output {field} does not match the profile.")

    def create_child(
        self,
        parent_directory: str,
        *,
        project_id: str,
        profile_id: str,
        base_name: str | None = None,
    ) -> OutputCreateChildResult:
        parent = self.validated_path(parent_directory, must_exist=True)
        if not parent.is_dir():
            raise self._unsafe("Parent output path must be an existing directory.")
        profile = self.discovery.get_profile(profile_id)
        if profile.project_id != project_id:
            raise MissionControlError(
                409,
                "project_profile_mismatch",
                "Project and profile do not match",
                "The selected profile belongs to another project.",
                "Reselect the project and profile.",
            )
        base = _safe_child_name(base_name or f"{project_id}-{profile.resolution.width}x{profile.resolution.height}")
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        child: Path | None = None
        for attempt in range(1000):
            suffix = "" if attempt == 0 else f"-{attempt:03d}"
            candidate = parent / f"{base}-{stamp}{suffix}"
            if not _contains(parent, candidate):
                raise self._unsafe("Generated child path escaped the selected parent directory.")
            try:
                candidate.mkdir()
                child = candidate.resolve()
                break
            except FileExistsError:
                continue
            except OSError as exc:
                raise MissionControlError(
                    422,
                    "output_child_creation_failed",
                    "Render folder could not be created",
                    "A unique child folder could not be created in the selected parent.",
                    "Check folder permissions or choose another parent folder.",
                    retryable=True,
                    technical_details=type(exc).__name__,
                ) from exc
        if child is None:
            raise MissionControlError(
                409,
                "output_child_collision_limit",
                "Render folder could not be allocated",
                "Too many folders already use the generated render name.",
                "Choose a different parent or render folder name.",
            )
        inspection = self.inspect(str(child))
        return OutputCreateChildResult(path=str(child), inspection=inspection)
