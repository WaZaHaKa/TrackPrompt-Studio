from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from .config import MissionControlConfig
from .errors import MissionControlError
from .models import (
    AuthorizationResult,
    CalibrationCandidate,
    CalibrationSummary,
    ProfileSummary,
    ProfileValidation,
    ProjectSummary,
    Resolution,
    SceneSummary,
)

_SHA256_RE = re.compile(r"^[0-9A-F]{64}$")
_SAFE_ID_RE = re.compile(r"[^a-z0-9]+")
_PROFILE_AUTHORIZATION_KIND = "trackprompt-render-profile-authorization"
_PROFILE_AUTHORIZATION_REQUEST_KIND = "trackprompt-render-profile-authorization-request"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
    except OSError as exc:
        raise MissionControlError(
            422,
            "identity_file_unreadable",
            "Identity file could not be read",
            "A scene or profile file could not be read for SHA-256 verification.",
            "Restore file access, then retry validation.",
            technical_details=type(exc).__name__,
        ) from exc
    return digest.hexdigest().upper()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest().upper()


def load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MissionControlError(
            422,
            "invalid_json",
            f"{label} is invalid",
            f"{label} is not readable UTF-8 JSON.",
            "Restore the saved file or select another one.",
            technical_details=type(exc).__name__,
        ) from exc
    if not isinstance(payload, dict):
        raise MissionControlError(
            422,
            "invalid_json_shape",
            f"{label} is invalid",
            f"{label} must contain one JSON object.",
            "Restore the saved file or select another one.",
        )
    return cast(dict[str, Any], payload)


def atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{os.getpid()}.{os.urandom(6).hex()}.tmp"
    data = json.dumps(payload, indent=4, sort_keys=True, ensure_ascii=False) + "\n"
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise MissionControlError(
            500,
            "atomic_write_failed",
            "Authorization could not be saved",
            "The local authorization record could not be published atomically.",
            "Check folder permissions and retry authorization.",
            retryable=True,
            technical_details=type(exc).__name__,
        ) from exc


def _mapping(value: object) -> dict[str, Any]:
    return cast(dict[str, Any], value) if isinstance(value, dict) else {}


def _first(mapping: Mapping[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        if name in mapping:
            return mapping[name]
    return default


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if result >= 0 else None


def _integer(value: object, default: int = 0) -> int:
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else default


def _string(value: object, default: str = "") -> str:
    return str(value).strip() if isinstance(value, str) else default


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None


def _slug(value: str, fallback: str) -> str:
    safe = _SAFE_ID_RE.sub("-", value.strip().lower()).strip("-.")
    return safe or fallback


def _profile_scene_path(profile: Mapping[str, Any]) -> Path | None:
    approved_scene = _mapping(profile.get("approvedScene"))
    raw = _first(profile, "approvedScenePath", default=approved_scene.get("path"))
    return Path(raw) if isinstance(raw, str) and raw.strip() else None


def _profile_scene_hash(profile: Mapping[str, Any]) -> str:
    approved_scene = _mapping(profile.get("approvedScene"))
    return _string(
        _first(profile, "approvedSceneSha256", default=approved_scene.get("sha256"))
    ).upper()


def _timeline(profile: Mapping[str, Any]) -> Mapping[str, Any]:
    value = profile.get("timeline")
    return cast(Mapping[str, Any], value) if isinstance(value, dict) else profile


def _authorization_paths(profile_path: Path) -> tuple[Path, Path]:
    base = profile_path.with_suffix("")
    return (
        Path(f"{base}.authorization-request.json"),
        Path(f"{base}.authorization.json"),
    )


def authorization_token(profile: Mapping[str, Any], profile_hash: str, scene_hash: str) -> str:
    authorization = _mapping(profile.get("authorization"))
    project = _string(authorization.get("project")) or _string(profile.get("project"), "TRACKPROMPT")
    preset = _string(authorization.get("preset")) or _string(profile.get("preset"), "CUSTOM")
    profile_label = _string(authorization.get("profile")) or _string(profile.get("profileId"), "PROFILE")
    values = (project.upper(), preset.upper(), profile_label.upper())
    if any(not value or any(character in value for character in "\r\n|") for value in values):
        raise MissionControlError(
            422,
            "unsafe_authorization_identity",
            "Authorization identity is invalid",
            "The saved profile contains an unsafe or empty authorization identity.",
            "Repair and resave the profile through the validated profile tooling.",
        )
    return (
        f"AUTHORIZE FULL RENDER: {values[0]} | {values[1]} | {values[2]} | "
        f"SCENE {scene_hash[:12]} | PROFILE {profile_hash[:12]}"
    )


def validate_authorization_record(
    profile_path: Path,
    scene_path: Path,
    profile: Mapping[str, Any],
) -> tuple[bool, list[str], str]:
    _request_path, record_path = _authorization_paths(profile_path)
    if not record_path.is_file():
        return False, ["No local authorization record exists."], ""
    profile_hash = sha256_file(profile_path)
    scene_hash = sha256_file(scene_path)
    token = authorization_token(profile, profile_hash, scene_hash)
    issues: list[str] = []
    try:
        record = load_json_object(record_path, "Authorization record")
    except MissionControlError:
        return False, ["Authorization record is unreadable or invalid JSON."], ""
    if record.get("kind") != _PROFILE_AUTHORIZATION_KIND:
        issues.append("Authorization record kind is unsupported.")
    if record.get("status") != "authorized":
        issues.append("Authorization record is not authorized.")
    record_profile = _mapping(record.get("profile"))
    record_scene = _mapping(record.get("scene"))
    confirmations = _mapping(record.get("confirmations"))
    if _string(record_profile.get("sha256")).upper() != profile_hash:
        issues.append("Authorization record profile hash does not match the current saved file.")
    if _string(record_scene.get("sha256")).upper() != scene_hash:
        issues.append("Authorization record scene hash does not match the current scene.")
    if confirmations.get("settingsAndHashesReviewed") is not True or confirmations.get(
        "productionRenderAuthorized"
    ) is not True:
        issues.append("Authorization record does not contain both confirmations.")
    if record.get("authorizationToken") != token:
        issues.append("Authorization record token does not match the exact scene and profile.")
    if _string(record.get("tokenSha256")).upper() != sha256_text(token):
        issues.append("Authorization record token hash is invalid.")
    return not issues, issues, token if not issues else ""


class MissionDiscovery:
    def __init__(self, config: MissionControlConfig) -> None:
        self.config = config

    def _profile_files(self) -> Iterable[Path]:
        if not self.config.profile_root.is_dir():
            return ()
        return (
            path
            for path in sorted(self.config.profile_root.rglob("*.json"))
            if ".authorization" not in path.name.lower()
            and path.name.lower() != "recommended-profile.json"
        )

    def _profile_row(self, path: Path) -> tuple[dict[str, Any], str]:
        profile = load_json_object(path, "Render profile")
        profile_id = _string(profile.get("profileId"))
        if not profile_id:
            raise MissionControlError(
                422,
                "not_a_render_profile",
                "Saved file is not a render profile",
                "The JSON file does not contain a profileId.",
                "Select a saved render profile.",
            )
        return profile, sha256_file(path)

    def recommended_profile_id(self) -> str | None:
        profiles = []
        for path in self._profile_files():
            try:
                profile, _hash = self._profile_row(path)
            except MissionControlError:
                continue
            profile_id = _string(profile.get("profileId"))
            profiles.append(profile_id)
            if "720P-HYPER-OPTIMIZED" in profile_id.upper():
                return profile_id
        pointer = self.config.profile_root / "trip-to-andromeda" / "recommended-profile.json"
        if pointer.is_file():
            try:
                pointed = _string(load_json_object(pointer, "Recommended profile pointer").get("profileId"))
                if pointed in profiles:
                    return pointed
            except MissionControlError:
                pass
        return profiles[0] if profiles else None

    def list_scenes(self) -> list[SceneSummary]:
        by_hash: dict[str, SceneSummary] = {}
        for profile_path in self._profile_files():
            try:
                profile, _profile_hash = self._profile_row(profile_path)
                scene_path = _profile_scene_path(profile)
                expected_hash = _profile_scene_hash(profile)
                if scene_path is None or not scene_path.is_file() or not _SHA256_RE.fullmatch(expected_hash):
                    continue
                actual_hash = sha256_file(scene_path)
                if actual_hash in by_hash:
                    continue
                project = _slug(_string(profile.get("project")), "trackprompt")
                preset = _slug(_string(profile.get("preset")), "scene")
                timeline = _timeline(profile)
                manifest_path_raw = _string(profile.get("sceneManifestPath"))
                manifest_path = Path(manifest_path_raw) if manifest_path_raw else None
                manifest_hash = None
                if manifest_path is not None and manifest_path.is_file():
                    manifest_hash = sha256_file(manifest_path)
                preview = self._scene_preview(scene_path)
                by_hash[actual_hash] = SceneSummary(
                    id=preset,
                    project_id=project,
                    display_name="Trip to Andromeda" if project == "trip-to-andromeda" else preset.replace("-", " ").title(),
                    preset=preset,
                    path=str(scene_path.resolve()),
                    sha256=actual_hash,
                    expected_sha256=expected_hash,
                    verified=actual_hash == expected_hash,
                    manifest_path=str(manifest_path.resolve()) if manifest_path is not None and manifest_path.exists() else None,
                    manifest_sha256=manifest_hash,
                    frame_start=_integer(_first(timeline, "frameStart", default=profile.get("frameStart")), 1),
                    frame_end=_integer(_first(timeline, "frameEnd", default=profile.get("frameEnd")), 1),
                    fps=float(_number(_first(timeline, "fps", default=profile.get("fps"))) or 30.0),
                    preview_path=str(preview) if preview is not None else None,
                )
            except MissionControlError:
                continue
        scenes = list(by_hash.values())
        scenes.sort(key=lambda item: (item.project_id, item.id))
        return scenes

    def _scene_preview(self, scene_path: Path) -> Path | None:
        evidence = scene_path.parent / "review-evidence"
        if evidence.is_dir():
            preferred = evidence / "frame_008106.png"
            if preferred.is_file():
                return preferred.resolve()
            candidates = sorted(evidence.glob("frame_*.png"))
            if candidates:
                return candidates[0].resolve()
        return None

    def get_scene(self, scene_id: str) -> SceneSummary:
        for scene in self.list_scenes():
            if scene.id.casefold() == scene_id.casefold():
                return scene
        raise MissionControlError(
            404,
            "scene_not_found",
            "Scene was not found",
            "The selected approved scene is no longer available.",
            "Refresh scene discovery and choose an available scene.",
        )

    def _profile_summary(
        self,
        path: Path,
        profile: dict[str, Any],
        saved_hash: str,
        recommended_id: str | None,
    ) -> ProfileSummary:
        profile_id = _string(profile.get("profileId"))
        scene_path = _profile_scene_path(profile)
        scene_expected_hash = _profile_scene_hash(profile)
        project = _slug(_string(profile.get("project")), "trackprompt")
        preset = _slug(_string(profile.get("preset")), "scene")
        timeline = _timeline(profile)
        resolution = _mapping(profile.get("resolution"))
        frame_start = _integer(_first(timeline, "frameStart", default=profile.get("frameStart")), 1)
        frame_end = _integer(_first(timeline, "frameEnd", default=profile.get("frameEnd")), frame_start)
        chunking = _mapping(profile.get("chunking"))
        production = _mapping(profile.get("production"))
        storage = _mapping(profile.get("storage"))
        calibration = _mapping(profile.get("calibration"))
        authorization = _mapping(profile.get("authorization"))
        authorized = False
        authorization_issues: list[str] = []
        if scene_path is None or not scene_path.is_file():
            authorization_issues = ["Approved scene file does not exist."]
        else:
            authorized, authorization_issues, _token = validate_authorization_record(
                path,
                scene_path,
                profile,
            )
        upper_id = profile_id.upper()
        quality_role = (
            "recommended"
            if profile_id == recommended_id
            else "release"
            if "1080P" in upper_id
            else "balanced"
            if "1440P" in upper_id or "4K-BALANCED" in upper_id
            else "advanced"
        )
        width = _integer(resolution.get("width"))
        height = _integer(resolution.get("height"))
        return ProfileSummary(
            id=profile_id,
            project_id=project,
            scene_id=preset,
            display_name=_string(profile.get("displayName"), profile_id),
            path=str(path.resolve()),
            saved_file_sha256=saved_hash,
            embedded_profile_sha256=_string(profile.get("profileSha256")) or None,
            scene_sha256=scene_expected_hash,
            resolution=Resolution(
                width=width,
                height=height,
                label=_string(resolution.get("label"), f"{width}x{height}"),
            ),
            fps=float(_number(_first(timeline, "fps", default=profile.get("fps"))) or 30.0),
            frame_start=frame_start,
            frame_end=frame_end,
            total_frames=max(0, frame_end - frame_start + 1),
            frames_per_chunk=_integer(
                _first(chunking, "framesPerChunk", default=production.get("framesPerChunk")),
                1,
            ),
            expected_hours=_number(calibration.get("expectedTotalHours")),
            conservative_hours=_number(calibration.get("conservativeTotalHours")),
            planned_frame_sequence_gib=_number(storage.get("plannedFrameSequenceGiB")),
            minimum_launch_free_gib=_number(storage.get("minimumLaunchFreeGiB")),
            quality_role=quality_role,
            quality_verdict=_string(calibration.get("qualityGateResult")) or None,
            calibrated=bool(calibration.get("calibrationId")),
            calibration_id=_string(calibration.get("calibrationId")) or None,
            authorization_status="authorized" if authorized else _string(
                authorization.get("status"),
                "authorization-required",
            ),
            authorized=authorized,
            authorization_issues=authorization_issues,
            recommended=profile_id == recommended_id,
        )

    def list_profiles(self) -> list[ProfileSummary]:
        recommended_id = self.recommended_profile_id()
        profiles: list[ProfileSummary] = []
        for path in self._profile_files():
            try:
                profile, saved_hash = self._profile_row(path)
                profiles.append(self._profile_summary(path, profile, saved_hash, recommended_id))
            except MissionControlError:
                continue
        profiles.sort(
            key=lambda item: (
                0 if item.recommended else 1,
                item.resolution.width * item.resolution.height,
                item.display_name.casefold(),
            )
        )
        return profiles

    def get_profile(self, profile_id: str) -> ProfileSummary:
        for profile in self.list_profiles():
            if profile.id.casefold() == profile_id.casefold():
                return profile
        raise MissionControlError(
            404,
            "profile_not_found",
            "Profile was not found",
            "The selected saved render profile is no longer available.",
            "Refresh profile discovery and choose an available profile.",
        )

    def profile_source(self, profile_id: str) -> tuple[Path, dict[str, Any], str]:
        for path in self._profile_files():
            try:
                profile, saved_hash = self._profile_row(path)
            except MissionControlError:
                continue
            if _string(profile.get("profileId")).casefold() == profile_id.casefold():
                return path.resolve(), profile, saved_hash
        self.get_profile(profile_id)
        raise AssertionError("unreachable")

    def validate_profile(self, profile_id: str) -> ProfileValidation:
        path, profile, saved_hash = self.profile_source(profile_id)
        errors: list[str] = []
        warnings: list[str] = []
        schema = _string(profile.get("schemaVersion"))
        if schema not in {"1.0.0", "1.1.0"}:
            errors.append("schemaVersion must be 1.0.0 or 1.1.0.")
        scene_path = _profile_scene_path(profile)
        expected_scene_hash = _profile_scene_hash(profile)
        if _SHA256_RE.fullmatch(expected_scene_hash) is None:
            errors.append("approvedSceneSha256 must be a 64-character SHA-256 value.")
        actual_scene_hash = ""
        if scene_path is None or not scene_path.is_file():
            errors.append("Approved scene file does not exist.")
        else:
            actual_scene_hash = sha256_file(scene_path)
            if actual_scene_hash != expected_scene_hash:
                errors.append("Approved scene file hash does not match the profile.")
        timeline = _timeline(profile)
        frame_start = _integer(_first(timeline, "frameStart", default=profile.get("frameStart")))
        frame_end = _integer(_first(timeline, "frameEnd", default=profile.get("frameEnd")))
        if frame_start < 1 or frame_end < frame_start:
            errors.append("The saved frame range is invalid.")
        resolution = _mapping(profile.get("resolution"))
        width = _integer(resolution.get("width"))
        height = _integer(resolution.get("height"))
        if width < 16 or height < 16 or width % 2 or height % 2:
            errors.append("Resolution must use valid even dimensions.")
        chunking = _mapping(profile.get("chunking"))
        chunk_size = _integer(chunking.get("framesPerChunk"))
        if chunk_size < 1 or chunk_size > max(1, frame_end - frame_start + 1):
            errors.append("Chunk size must fit the saved frame range.")
        production = _mapping(profile.get("production"))
        if production.get("overwriteValidFrames") is True:
            errors.append("production.overwriteValidFrames must remain false.")
        for required in ("resumeEnabled", "verifyExistingFrames", "atomicChunkCommit"):
            if required in production and production.get(required) is not True:
                errors.append(f"production.{required} must remain true.")
        embedded = _string(profile.get("profileSha256"))
        if embedded and _SHA256_RE.fullmatch(embedded.upper()) is None:
            errors.append("Embedded profileSha256 is malformed.")
        if "4K" in _string(profile.get("profileId")).upper():
            warnings.append("4K profiles require substantially more storage and render time.")
        authorized = False
        authorization_issues: list[str] = []
        if scene_path is not None and scene_path.is_file() and actual_scene_hash == expected_scene_hash:
            authorized, authorization_issues, _token = validate_authorization_record(path, scene_path, profile)
        return ProfileValidation(
            profile_id=profile_id,
            valid=not errors,
            saved_file_sha256=saved_hash,
            scene_sha256=actual_scene_hash or expected_scene_hash,
            errors=errors,
            warnings=warnings,
            authorized=authorized,
            authorization_issues=authorization_issues,
        )

    def authorize_profile(
        self,
        profile_id: str,
        scene_id: str,
        *,
        settings_and_hashes_reviewed: bool,
        production_render_authorized: bool,
    ) -> AuthorizationResult:
        profile_path, profile, profile_hash = self.profile_source(profile_id)
        scene = self.get_scene(scene_id)
        scene_path = Path(scene.path)
        expected_scene_path = _profile_scene_path(profile)
        if expected_scene_path is None or scene_path.resolve() != expected_scene_path.resolve():
            raise MissionControlError(
                409,
                "scene_profile_mismatch",
                "Scene does not match profile",
                "The selected scene is not the exact approved scene saved in this profile.",
                "Return to scene selection and choose the profile's approved scene.",
            )
        scene_hash = sha256_file(scene_path)
        if scene_hash != _profile_scene_hash(profile):
            raise MissionControlError(
                409,
                "scene_hash_mismatch",
                "Scene verification failed",
                "The approved scene content changed after this profile was saved.",
                "Restore the approved scene before authorizing.",
            )
        token = authorization_token(profile, profile_hash, scene_hash)
        requested_at = datetime.now(UTC)
        request_payload: dict[str, Any] = {
            "schemaVersion": "1.1.0",
            "kind": _PROFILE_AUTHORIZATION_REQUEST_KIND,
            "status": "pending-two-confirmations",
            "requestedAt": requested_at.isoformat(),
            "profile": {
                "id": _string(profile.get("id")),
                "profileId": profile_id,
                "displayName": _string(profile.get("displayName"), profile_id),
                "path": str(profile_path),
                "sha256": profile_hash,
            },
            "scene": {"path": str(scene_path), "sha256": scene_hash},
            "tokenSha256": sha256_text(token),
            "tokenPreview": f"SCENE {scene_hash[:12]} | PROFILE {profile_hash[:12]}",
            "confirmations": {
                "settingsAndHashesReviewed": False,
                "productionRenderAuthorized": False,
            },
            "note": "The plaintext authorization token is intentionally absent until both explicit confirmations are recorded.",
        }
        request_path, record_path = _authorization_paths(profile_path)
        atomic_write_json(request_path, request_payload)
        if not settings_and_hashes_reviewed or not production_render_authorized:
            raise MissionControlError(
                422,
                "two_confirmations_required",
                "Both confirmations are required",
                "Authorization requires review of the settings and hashes plus explicit full-render approval.",
                "Complete both confirmation steps, then authorize again.",
                retryable=True,
                context={
                    "settingsAndHashesReviewed": settings_and_hashes_reviewed,
                    "productionRenderAuthorized": production_render_authorized,
                },
            )
        authorized_at = datetime.now(UTC)
        record_payload: dict[str, Any] = {
            "schemaVersion": "1.1.0",
            "kind": _PROFILE_AUTHORIZATION_KIND,
            "status": "authorized",
            "authorizedAt": authorized_at.isoformat(),
            "profile": {
                "id": _string(profile.get("id")),
                "profileId": profile_id,
                "path": str(profile_path),
                "sha256": profile_hash,
            },
            "scene": {"path": str(scene_path), "sha256": scene_hash},
            "confirmations": {
                "settingsAndHashesReviewed": True,
                "productionRenderAuthorized": True,
            },
            "authorizationToken": token,
            "tokenSha256": sha256_text(token),
        }
        atomic_write_json(record_path, record_payload)
        valid, issues, validated_token = validate_authorization_record(
            profile_path,
            scene_path,
            profile,
        )
        if not valid or validated_token != token:
            raise MissionControlError(
                500,
                "authorization_readback_failed",
                "Authorization verification failed",
                "The saved authorization record did not pass exact hash-bound read-back validation.",
                "Inspect folder permissions and retry authorization.",
                technical_details="; ".join(issues),
            )
        return AuthorizationResult(
            authorized=True,
            profile_id=profile_id,
            scene_id=scene_id,
            profile_sha256=profile_hash,
            scene_sha256=scene_hash,
            authorization_token=token,
            token_sha256=sha256_text(token),
            record_path=str(record_path),
            authorized_at=authorized_at,
        )

    def list_projects(self) -> list[ProjectSummary]:
        profiles = self.list_profiles()
        scenes = self.list_scenes()
        projects: dict[str, ProjectSummary] = {}
        recommended = self.recommended_profile_id()
        for profile in profiles:
            project = projects.setdefault(
                profile.project_id,
                ProjectSummary(
                    id=profile.project_id,
                    display_name=(
                        "Trip to Andromeda"
                        if profile.project_id == "trip-to-andromeda"
                        else profile.project_id.replace("-", " ").title()
                    ),
                    scene_ids=[],
                    profile_ids=[],
                    recommended_profile_id=None,
                ),
            )
            if profile.id not in project.profile_ids:
                project.profile_ids.append(profile.id)
            if profile.id == recommended:
                project.recommended_profile_id = profile.id
        for scene in scenes:
            matching_project = projects.get(scene.project_id)
            if matching_project is not None and scene.id not in matching_project.scene_ids:
                matching_project.scene_ids.append(scene.id)
        return sorted(projects.values(), key=lambda item: item.display_name.casefold())

    def list_calibrations(self) -> list[CalibrationSummary]:
        if not self.config.calibration_root.is_dir():
            return []
        summaries: list[CalibrationSummary] = []
        for path in sorted(self.config.calibration_root.rglob("calibration.json")):
            try:
                payload = load_json_object(path, "Calibration result")
                summaries.append(self._calibration_summary(path, payload))
            except MissionControlError:
                continue
        summaries.sort(
            key=lambda item: (
                item.status.casefold() == "complete",
                item.completed_at or item.created_at or datetime.min.replace(tzinfo=UTC),
            ),
            reverse=True,
        )
        return summaries

    def _candidate(self, value: object) -> CalibrationCandidate | None:
        candidate = _mapping(value)
        identifier = _string(_first(candidate, "id", "Id"))
        if not identifier:
            return None
        width = _integer(_first(candidate, "width", "Width"))
        height = _integer(_first(candidate, "height", "Height"))
        warm_median = _number(_first(candidate, "warmMedianSeconds", "WarmMedianSeconds"))
        p90 = _number(_first(candidate, "p90Seconds", "P90Seconds"))
        total_frames = 13_029
        return CalibrationCandidate(
            id=identifier,
            resolution=_string(_first(candidate, "resolution", "Resolution"), f"{width}x{height}"),
            width=width,
            height=height,
            samples=_integer(_first(candidate, "samples", "Samples")),
            status=_string(_first(candidate, "status", "Status"), "unknown"),
            expected_hours=(warm_median * total_frames / 3600.0) if warm_median else None,
            conservative_hours=(p90 * total_frames / 3600.0) if p90 else None,
            projected_storage_bytes=(
                _integer(_first(candidate, "projectedStorageBytes", "ProjectedStorageBytes")) or None
            ),
            quality_result=_string(_first(candidate, "qualityResult", "QualityResult")) or None,
            quality_notes=_string(_first(candidate, "qualityNotes", "QualityNotes")) or None,
        )

    def _calibration_summary(self, path: Path, payload: dict[str, Any]) -> CalibrationSummary:
        machine = _mapping(payload.get("machine"))
        recommendation = self._candidate(payload.get("recommendation"))
        candidates_raw = payload.get("candidates")
        candidates = (
            [candidate for item in candidates_raw if (candidate := self._candidate(item)) is not None]
            if isinstance(candidates_raw, list)
            else []
        )
        finalists = [
            candidate
            for candidate in candidates
            if candidate.status.casefold() in {"passing", "recommended", "complete"}
            or (candidate.quality_result or "").upper().startswith("PASS")
        ][:20]
        return CalibrationSummary(
            id=_string(payload.get("calibrationId"), path.parent.name),
            status=_string(payload.get("status"), "unknown"),
            created_at=_timestamp(payload.get("createdAt")),
            completed_at=_timestamp(payload.get("completedAt")),
            path=str(path.parent.resolve()),
            scene_sha256=_string(payload.get("sceneSha256")) or None,
            machine_id=_string(_first(machine, "MachineId", "machineId")) or None,
            machine_fingerprint=_string(
                _first(machine, "MachineFingerprint", "machineFingerprint", default=payload.get("machineFingerprint"))
            )
            or None,
            cpu_model=_string(_first(machine, "CpuModel", "cpuModel", default=payload.get("cpuModel"))) or None,
            gpu_model=_string(_first(machine, "GpuModel", "gpuModel", default=payload.get("gpuModel"))) or None,
            vram_mib=_integer(_first(machine, "VramMiB", "vramMiB", default=payload.get("vramMiB"))) or None,
            ram_bytes=_integer(_first(machine, "RamBytes", "ramBytes", default=payload.get("ramBytes"))) or None,
            recommended_candidate=recommendation,
            finalists=finalists,
        )

    def get_calibration(self, calibration_id: str) -> CalibrationSummary:
        for calibration in self.list_calibrations():
            if calibration.id.casefold() == calibration_id.casefold():
                return calibration
        raise MissionControlError(
            404,
            "calibration_not_found",
            "Calibration was not found",
            "The selected calibration plan or result no longer exists.",
            "Refresh calibration history and reselect a plan.",
        )
