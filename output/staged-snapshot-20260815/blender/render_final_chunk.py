from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import math
import os
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

RENDER_EVENT_PREFIX = "WZHK_RENDER_EVENT "
_EVENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from tools.final_render_tooling import (  # noqa: E402
    RENDER_MANIFEST_KIND,
    TOOL_SCHEMA_VERSION,
    ToolingError,
    load_render_profile,
    sha256_file,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render one authorized TrackPrompt final image-sequence chunk.")
    parser.add_argument("--profile", required=True)
    parser.add_argument("--render-manifest", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--start", type=int, required=True)
    parser.add_argument("--end", type=int, required=True)
    parser.add_argument("--job-id", default="standalone")
    parser.add_argument("--worker-id", default=f"blender-{os.getpid()}")
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    return parser.parse_args(arguments)


class RenderEventEmitter:
    """Emit bounded renderer facts with an exact, relative artifact identity."""

    def __init__(
        self,
        scene: Any,
        *,
        job_id: str,
        worker_id: str,
        project_id: str,
        scene_sha256: str,
        profile_sha256: str,
        output_variant_id: str,
        width: int,
        height: int,
        composition_profile_id: str,
        artifact_directory: str,
        artifact_filename_pattern: str,
        start: int,
        end: int,
    ) -> None:
        identities = (
            job_id,
            worker_id,
            project_id,
            output_variant_id,
            composition_profile_id,
        )
        if any(_EVENT_ID.fullmatch(value) is None for value in identities):
            raise ToolingError("invalid-telemetry-identity", "Telemetry job and worker IDs must be safe identifiers.")
        self.scene = scene
        self.job_id = job_id
        self.worker_id = worker_id
        self.project_id = project_id
        self.scene_sha256 = scene_sha256
        self.profile_sha256 = profile_sha256
        self.output_variant_id = output_variant_id
        self.width = width
        self.height = height
        self.composition_profile_id = composition_profile_id
        self.artifact_directory = artifact_directory
        self.artifact_filename_pattern = artifact_filename_pattern
        self.start = start
        self.end = end
        self.chunk_id = f"{start:06d}-{end:06d}"
        self.sequence = 0
        self.frame_started_monotonic: float | None = None
        self.last_stats_monotonic = 0.0
        self.shots = self._load_shots(scene)

    @staticmethod
    def _load_shots(scene: Any) -> tuple[dict[str, Any], ...]:
        raw = scene.get("trackprompt_shot_plan", "")
        if not raw:
            raw = scene.get("trackprompt_shot_plan_json", "")
        if not isinstance(raw, str) or len(raw) > 2_000_000:
            return ()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return ()
        shots = payload.get("shots") if isinstance(payload, dict) else None
        if not isinstance(shots, list):
            return ()
        return tuple(shot for shot in shots if isinstance(shot, dict))

    def _story_context(self, frame: int) -> dict[str, object]:
        for shot in self.shots:
            start = shot.get("frameStart")
            end = shot.get("frameEnd")
            if isinstance(start, int) and isinstance(end, int) and start <= frame <= end:
                act_id = str(shot.get("actId", ""))[:128]
                shot_id = str(shot.get("id", ""))[:128]
                return {
                    "actId": act_id or None,
                    "actName": act_id.replace("-", " ").title() or None,
                    "shotId": shot_id or None,
                    "shotName": str(shot.get("name", ""))[:160] or None,
                    "complexityClass": (
                        str(shot.get("complexityClass", ""))[:128] or None
                    ),
                }
        return {
            "actId": None,
            "actName": None,
            "shotId": None,
            "shotName": None,
            "complexityClass": None,
        }

    def emit(self, event_type: str, *, frame: int | None = None, **facts: object) -> None:
        self.sequence += 1
        payload: dict[str, object] = {
            "schemaVersion": "2.0.0",
            "eventType": event_type,
            "sequence": self.sequence,
            "jobId": self.job_id,
            "workerId": self.worker_id,
            "processId": os.getpid(),
            "chunkId": self.chunk_id,
            "chunkStart": self.start,
            "chunkEnd": self.end,
            "frame": frame,
            "rendererStatus": {
                "frame_started": "rendering",
                "frame_written": "awaiting_chunk_validation",
                "render_stats": "rendering",
                "chunk_complete": "chunk_rendered",
                "render_cancelled": "cancelled",
            }.get(event_type, "unknown"),
            "projectId": self.project_id,
            "sceneSha256": self.scene_sha256,
            "profileSha256": self.profile_sha256,
            "outputVariantId": self.output_variant_id,
            "width": self.width,
            "height": self.height,
            "compositionProfileId": self.composition_profile_id,
            "emittedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            **(self._story_context(frame) if frame is not None else {}),
            **facts,
        }
        print(RENDER_EVENT_PREFIX + json.dumps(payload, ensure_ascii=True, separators=(",", ":")), flush=True)

    def render_pre(self, *_: object) -> None:
        frame = int(self.scene.frame_current)
        self.frame_started_monotonic = time.monotonic()
        self.emit("frame_started", frame=frame)

    def render_write(self, *_: object) -> None:
        frame = int(self.scene.frame_current)
        elapsed = None
        if self.frame_started_monotonic is not None:
            elapsed = max(0.0, time.monotonic() - self.frame_started_monotonic)
        self.emit(
            "frame_written",
            frame=frame,
            elapsedSeconds=elapsed,
            outputIdentity=f"frame-{frame:06d}",
            artifactRelativePath=(
                f"{self.artifact_directory}/"
                f"{self.artifact_filename_pattern % frame}"
            ),
        )

    def render_stats(self, *_: object) -> None:
        now = time.monotonic()
        if now - self.last_stats_monotonic < 1.0:
            return
        self.last_stats_monotonic = now
        elapsed = None if self.frame_started_monotonic is None else max(0.0, now - self.frame_started_monotonic)
        self.emit("render_stats", frame=int(self.scene.frame_current), elapsedSeconds=elapsed)

    def render_cancel(self, *_: object) -> None:
        self.emit("render_cancelled", frame=int(self.scene.frame_current))


def _install_event_handlers(bpy: Any, emitter: RenderEventEmitter) -> tuple[tuple[Any, Any], ...]:
    handlers = (
        (bpy.app.handlers.render_pre, emitter.render_pre),
        (bpy.app.handlers.render_write, emitter.render_write),
        (bpy.app.handlers.render_stats, emitter.render_stats),
        (bpy.app.handlers.render_cancel, emitter.render_cancel),
    )
    for collection, handler in handlers:
        collection.append(handler)
    return handlers


def _remove_event_handlers(handlers: tuple[tuple[Any, Any], ...]) -> None:
    for collection, handler in handlers:
        if handler in collection:
            collection.remove(handler)


def _absolute_existing_file(value: str, label: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ToolingError("path-not-absolute", f"{label} must be absolute.")
    resolved = path.resolve(strict=True)
    if not resolved.is_file():
        raise ToolingError("missing-input", f"{label} must be an existing file.")
    return resolved


def _load_manifest(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ToolingError("invalid-render-manifest", "Render manifest is unreadable or invalid.") from exc
    if not isinstance(payload, dict):
        raise ToolingError("invalid-render-manifest", "Render manifest must contain an object.")
    return payload


def _validate_authorized_chunk(
    bpy: Any,
    profile_path: Path,
    manifest_path: Path,
    output_path: Path,
    start: int,
    end: int,
) -> tuple[Any, Path]:
    profile = load_render_profile(profile_path)
    if bpy.app.version_string != profile.blender_version:
        raise ToolingError("blender-version-mismatch", "Running Blender version differs from the reviewed profile.")
    manifest = _load_manifest(manifest_path)
    if manifest.get("schemaVersion") != TOOL_SCHEMA_VERSION or manifest.get("kind") != RENDER_MANIFEST_KIND:
        raise ToolingError("invalid-render-manifest", "Render manifest has the wrong kind or schema.")
    authorization = manifest.get("authorization")
    expected_token_hash = hashlib.sha256(profile.authorization_token.encode()).hexdigest().upper()
    if (
        not isinstance(authorization, dict)
        or authorization.get("status") != "operator-token-accepted"
        or not hmac.compare_digest(str(authorization.get("expectedTokenSha256", "")), expected_token_hash)
        or not hmac.compare_digest(str(authorization.get("acceptedTokenSha256", "")), expected_token_hash)
    ):
        raise ToolingError("authorization-not-recorded", "Render manifest does not record an accepted operator token.")
    manifest_profile = manifest.get("renderProfile")
    if not isinstance(manifest_profile, dict) or not hmac.compare_digest(
        str(manifest_profile.get("sha256", "")), profile.source_sha256
    ):
        raise ToolingError("render-manifest-mismatch", "Render profile hash does not match the render manifest.")
    scene_path = Path(bpy.data.filepath).resolve(strict=True)
    scene_hash = sha256_file(scene_path)
    manifest_scene = manifest.get("scene")
    if (
        not isinstance(manifest_scene, dict)
        or not hmac.compare_digest(scene_hash, profile.approved_scene_sha256)
        or not hmac.compare_digest(str(manifest_scene.get("sha256", "")), scene_hash)
    ):
        raise ToolingError("scene-hash-mismatch", "Loaded scene is not the authorized frozen candidate.")
    output_root_value = manifest.get("outputDirectory")
    if not isinstance(output_root_value, str):
        raise ToolingError("invalid-render-manifest", "Render manifest has no output directory.")
    output_root = Path(output_root_value).resolve(strict=True)
    expected_manifest = (output_root / "manifests" / "render-manifest.json").resolve(strict=True)
    if expected_manifest != manifest_path:
        raise ToolingError("path-containment-failed", "Render manifest is not inside its declared output root.")
    output = output_path.resolve(strict=True)
    checkpoints = (output_root / "checkpoints").resolve(strict=True)
    try:
        output.relative_to(checkpoints)
    except ValueError as exc:
        raise ToolingError("path-containment-failed", "Temporary chunk output must be under checkpoints.") from exc
    if output == checkpoints or any(output.iterdir()):
        raise ToolingError("unsafe-chunk-output", "Temporary chunk output must be a new empty directory.")
    if start < profile.frame_start or end > profile.frame_end or end < start:
        raise ToolingError("invalid-frame-range", "Chunk is outside the render profile frame range.")
    manifest_chunking = manifest.get("chunking")
    manifest_chunk_size = manifest_chunking.get("framesPerChunk") if isinstance(manifest_chunking, dict) else None
    if (
        isinstance(manifest_chunk_size, bool)
        or not isinstance(manifest_chunk_size, int)
        or manifest_chunk_size < 1
        or manifest_chunk_size > profile.chunk_size
    ):
        raise ToolingError("render-manifest-mismatch", "Render manifest chunk size is outside the reviewed profile.")
    if end - start + 1 > manifest_chunk_size:
        raise ToolingError("invalid-frame-range", "Chunk exceeds the reviewed profile chunk size.")
    return profile, output


def _apply_color_management(scene: Any, settings: dict[str, Any]) -> None:
    assignments = (
        (scene.display_settings, "display_device", settings["displayDevice"], "displayDevice"),
        (scene.view_settings, "view_transform", settings["viewTransform"], "viewTransform"),
        (scene.view_settings, "look", settings["look"], "look"),
        (scene.view_settings, "exposure", float(settings["exposure"]), "exposure"),
        (scene.view_settings, "gamma", float(settings["gamma"]), "gamma"),
        (
            scene.sequencer_colorspace_settings,
            "name",
            settings["sequencerColorSpace"],
            "sequencerColorSpace",
        ),
    )
    for owner, attribute, value, profile_field in assignments:
        try:
            setattr(owner, attribute, value)
        except (AttributeError, TypeError, ValueError) as exc:
            raise ToolingError(
                "unsupported-color-management-setting",
                f"Blender cannot apply colorManagement.{profile_field} from the approved profile.",
            ) from exc


def _compositor_tree(scene: Any) -> Any | None:
    tree = getattr(scene, "node_tree", None)
    if tree is None:
        tree = getattr(scene, "compositing_node_group", None)
    return tree


def _iter_compositor_nodes(tree: Any, seen: set[int] | None = None) -> Any:
    visited = set() if seen is None else seen
    identity = id(tree)
    if identity in visited:
        return
    visited.add(identity)
    nodes = getattr(tree, "nodes", None)
    if nodes is None:
        return
    for node in nodes:
        yield node
        child = getattr(node, "node_tree", None)
        if child is not None:
            yield from _iter_compositor_nodes(child, visited)


def _node_input(node: Any, name: str) -> Any | None:
    inputs = getattr(node, "inputs", None)
    if inputs is None:
        return None
    getter = getattr(inputs, "get", None)
    if callable(getter):
        return getter(name)
    try:
        return inputs[name]
    except (KeyError, TypeError):
        return None


def _set_compositor_value(
    node: Any,
    *,
    profile_field: str,
    input_name: str,
    value: Any,
    legacy_attribute: str | None = None,
    legacy_value: Any | None = None,
) -> None:
    socket = _node_input(node, input_name)
    try:
        if socket is not None and hasattr(socket, "default_value"):
            socket.default_value = value
            return
        if legacy_attribute is not None and hasattr(node, legacy_attribute):
            setattr(node, legacy_attribute, value if legacy_value is None else legacy_value)
            return
    except (AttributeError, TypeError, ValueError) as exc:
        raise ToolingError(
            "unsupported-compositor-setting",
            f"Blender rejected compositor.{profile_field} on the approved Fog Glow node.",
        ) from exc
    raise ToolingError(
        "unsupported-compositor-setting",
        f"Blender cannot apply compositor.{profile_field} on the approved Fog Glow node.",
    )


def _apply_compositor_profile(scene: Any, render_settings: dict[str, Any], raw_profile: dict[str, Any]) -> None:
    compositor_value = raw_profile.get("compositor", render_settings.get("compositor"))
    if compositor_value is None:
        return
    if not isinstance(compositor_value, dict):
        raise ToolingError("invalid-profile", "compositor must be a JSON object.")
    enabled = compositor_value.get("enabled")
    if enabled is None and str(raw_profile.get("schemaVersion", "")) == "1.0.0":
        enabled = render_settings.get("useCompositing")
    if not isinstance(enabled, bool):
        raise ToolingError("invalid-profile", "compositor.enabled must be boolean.")
    try:
        scene.render.use_compositing = enabled
    except (AttributeError, TypeError, ValueError) as exc:
        raise ToolingError(
            "unsupported-compositor-setting",
            "Blender cannot apply the approved compositor enable state.",
        ) from exc
    if not enabled:
        return

    tree = _compositor_tree(scene)
    if tree is None:
        raise ToolingError("unsupported-compositor-setting", "The approved compositor node tree is unavailable.")
    expected_name = compositor_value.get("name")
    if isinstance(expected_name, str) and expected_name and str(getattr(tree, "name", "")) != expected_name:
        raise ToolingError(
            "scene-contract-mismatch",
            "Loaded scene compositor node-tree name differs from the approved profile.",
        )
    glare_nodes = [
        node
        for node in _iter_compositor_nodes(tree)
        if str(getattr(node, "type", "")).upper() == "GLARE"
        or str(getattr(node, "bl_idname", "")) == "CompositorNodeGlare"
    ]
    preferred = [node for node in glare_nodes if str(getattr(node, "name", "")) == "TP_CONTROLLED_GLOW"]
    glow = preferred[0] if preferred else (glare_nodes[0] if len(glare_nodes) == 1 else None)
    fog_enabled = compositor_value.get("fogGlowEnabled", compositor_value.get("fogGlow"))
    if fog_enabled is None and str(raw_profile.get("schemaVersion", "")) == "1.0.0":
        fog_enabled = enabled
    if not isinstance(fog_enabled, bool):
        raise ToolingError("invalid-profile", "compositor.fogGlowEnabled must be boolean.")
    if glow is None:
        if fog_enabled:
            raise ToolingError(
                "unsupported-compositor-setting",
                "The approved compositor has no unambiguous Fog Glow node.",
            )
        return
    if not hasattr(glow, "mute"):
        raise ToolingError("unsupported-compositor-setting", "Blender cannot apply the Fog Glow enable state.")
    try:
        glow.mute = not fog_enabled
    except (AttributeError, TypeError, ValueError) as exc:
        raise ToolingError("unsupported-compositor-setting", "Blender rejected the Fog Glow enable state.") from exc
    if not fog_enabled:
        return

    _set_compositor_value(
        glow,
        profile_field="fogGlow",
        input_name="Type",
        value="Fog Glow",
        legacy_attribute="glare_type",
        legacy_value="FOG_GLOW",
    )
    quality = compositor_value.get("fogGlowQuality")
    if quality is not None:
        normalized_quality = str(quality).upper()
        socket_quality = normalized_quality.title()
        _set_compositor_value(
            glow,
            profile_field="fogGlowQuality",
            input_name="Quality",
            value=socket_quality if _node_input(glow, "Quality") is not None else normalized_quality,
            legacy_attribute="quality",
        )
    for field, input_name, legacy_attribute in (
        ("fogGlowThreshold", "Threshold", "threshold"),
        ("fogGlowStrength", "Strength", None),
        ("fogGlowSize", "Size", None),
        ("fogGlowIterations", "Iterations", "iterations"),
    ):
        if field in compositor_value:
            _set_compositor_value(
                glow,
                profile_field=field,
                input_name=input_name,
                value=compositor_value[field],
                legacy_attribute=legacy_attribute,
            )


def _apply_render_profile(scene: Any, profile: Any, output: Path, start: int, end: int) -> None:
    source_fps = scene.render.fps / scene.render.fps_base
    schema_version = str(profile.raw.get("schemaVersion", ""))
    if schema_version == "1.0.0":
        if scene.frame_start != profile.frame_start or scene.frame_end != profile.frame_end:
            raise ToolingError("scene-contract-mismatch", "Loaded scene frame range differs from the legacy render profile.")
        if not math.isclose(source_fps, profile.fps, rel_tol=0.0, abs_tol=1e-9):
            raise ToolingError("scene-contract-mismatch", "Loaded scene FPS differs from the legacy render profile.")
    else:
        if profile.frame_start < scene.frame_start or profile.frame_end > scene.frame_end:
            raise ToolingError(
                "scene-contract-mismatch",
                "Builder profile frame range must remain inside the approved scene timeline.",
            )
        source_timeline = profile.raw.get("sourceTimeline")
        if isinstance(source_timeline, dict):
            expected_start = source_timeline.get("frameStart")
            expected_end = source_timeline.get("frameEnd")
            expected_fps = source_timeline.get("fps")
            if expected_start is not None and int(expected_start) != scene.frame_start:
                raise ToolingError("scene-contract-mismatch", "Approved scene start frame drifted after profile creation.")
            if expected_end is not None and int(expected_end) != scene.frame_end:
                raise ToolingError("scene-contract-mismatch", "Approved scene end frame drifted after profile creation.")
            if expected_fps is not None and not math.isclose(
                float(expected_fps), source_fps, rel_tol=0.0, abs_tol=1e-9
            ):
                raise ToolingError("scene-contract-mismatch", "Approved scene native FPS drifted after profile creation.")
    if str(scene.get("trackprompt_preset", "")) != profile.preset:
        raise ToolingError("scene-contract-mismatch", "Loaded scene preset differs from the render profile.")
    render_settings = profile.raw.get("render")
    if not isinstance(render_settings, dict):
        render_settings = {}
    expected_engine = render_settings.get("engine")
    if isinstance(expected_engine, str) and scene.render.engine != expected_engine:
        raise ToolingError("scene-contract-mismatch", "Loaded scene render engine differs from the profile.")
    _apply_color_management(scene, profile.color_management)

    scene.render.resolution_x = profile.width
    scene.render.resolution_y = profile.height
    scene.render.resolution_percentage = profile.resolution_percentage
    scene.render.pixel_aspect_x = profile.pixel_aspect_x
    scene.render.pixel_aspect_y = profile.pixel_aspect_y
    scene.render.fps = int(round(profile.fps))
    scene.render.fps_base = scene.render.fps / profile.fps
    scene.render.image_settings.file_format = profile.image.format
    scene.render.image_settings.color_mode = profile.image.color_mode
    scene.render.image_settings.color_depth = str(profile.image.bit_depth)
    if hasattr(scene.render.image_settings, "color_management"):
        scene.render.image_settings.color_management = "FOLLOW_SCENE"
    if profile.image.format == "PNG" and profile.image.compression is not None:
        compression = profile.image.compression
        if isinstance(compression, bool) or not isinstance(compression, int) or not 0 <= compression <= 100:
            raise ToolingError("invalid-profile", "PNG compression must be an integer from 0 through 100.")
        scene.render.image_settings.compression = compression
    if profile.image.format == "OPEN_EXR" and profile.image.compression is not None:
        codec = str(profile.image.compression).upper()
        if codec not in {"ZIP", "PIZ"}:
            raise ToolingError("invalid-profile", "Final OpenEXR compression must be ZIP or PIZ.")
        scene.render.image_settings.exr_codec = codec

    samples = render_settings.get("samples")
    eevee = getattr(scene, "eevee", None)
    if samples is not None:
        if isinstance(samples, bool) or not isinstance(samples, int) or not 1 <= samples <= 4096:
            raise ToolingError("invalid-profile", "Render samples must be an integer from 1 through 4096.")
        if eevee is None or not hasattr(eevee, "taa_render_samples"):
            raise ToolingError("unsupported-render-setting", "This Blender build cannot apply EEVEE final samples.")
        eevee.taa_render_samples = samples
    shadow_pool_size = render_settings.get("shadowPoolSize")
    if shadow_pool_size is not None:
        shadow_pool_value = str(shadow_pool_size)
        if shadow_pool_value not in {"128", "256", "512", "1024", "2048"}:
            raise ToolingError("invalid-profile", "render.shadowPoolSize is not an approved EEVEE enum value.")
        if eevee is None or not hasattr(eevee, "shadow_pool_size"):
            raise ToolingError("unsupported-render-setting", "This Blender build cannot apply the EEVEE shadow pool size.")
        eevee.shadow_pool_size = shadow_pool_value

    def apply_eevee_integer(profile_key: str, attribute: str, minimum: int, maximum: int) -> None:
        value = render_settings.get(profile_key)
        if value is None:
            return
        if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
            raise ToolingError(
                "invalid-profile",
                f"render.{profile_key} must be an integer from {minimum} through {maximum}.",
            )
        if eevee is None or not hasattr(eevee, attribute):
            raise ToolingError("unsupported-render-setting", f"Blender cannot apply render.{profile_key}.")
        setattr(eevee, attribute, value)

    def apply_eevee_number(profile_key: str, attribute: str, minimum: float, maximum: float) -> None:
        value = render_settings.get(profile_key)
        if value is None:
            return
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or not minimum <= float(value) <= maximum
        ):
            raise ToolingError(
                "invalid-profile",
                f"render.{profile_key} must be a finite number from {minimum} through {maximum}.",
            )
        if eevee is None or not hasattr(eevee, attribute):
            raise ToolingError("unsupported-render-setting", f"Blender cannot apply render.{profile_key}.")
        setattr(eevee, attribute, float(value))

    def apply_eevee_boolean(profile_key: str, attribute: str) -> None:
        value = render_settings.get(profile_key)
        if value is None:
            return
        if not isinstance(value, bool):
            raise ToolingError("invalid-profile", f"render.{profile_key} must be boolean.")
        if eevee is None or not hasattr(eevee, attribute):
            raise ToolingError("unsupported-render-setting", f"Blender cannot apply render.{profile_key}.")
        setattr(eevee, attribute, value)

    apply_eevee_integer("shadowRayCount", "shadow_ray_count", 1, 4)
    apply_eevee_number("shadowResolutionScale", "shadow_resolution_scale", 0.0, 1.0)
    apply_eevee_boolean("rayTracing", "use_raytracing")
    apply_eevee_integer("volumetricSamples", "volumetric_samples", 1, 256)
    apply_eevee_integer("volumetricShadowSamples", "volumetric_shadow_samples", 1, 128)
    apply_eevee_integer("volumetricRayDepth", "volumetric_ray_depth", 1, 16)
    apply_eevee_boolean("volumetricShadows", "use_volumetric_shadows")

    ray_tracing_method = render_settings.get("rayTracingMethod")
    if ray_tracing_method is not None:
        normalized_method = str(ray_tracing_method).upper()
        if normalized_method not in {"PROBE", "SCREEN"}:
            raise ToolingError("invalid-profile", "render.rayTracingMethod must be PROBE or SCREEN.")
        if eevee is None or not hasattr(eevee, "ray_tracing_method"):
            raise ToolingError("unsupported-render-setting", "Blender cannot apply render.rayTracingMethod.")
        eevee.ray_tracing_method = normalized_method

    volumetric_tile_size = render_settings.get("volumetricTileSize")
    if volumetric_tile_size is not None:
        normalized_tile_size = str(volumetric_tile_size)
        if normalized_tile_size not in {"1", "2", "4", "8", "16"}:
            raise ToolingError("invalid-profile", "render.volumetricTileSize must be 1, 2, 4, 8, or 16.")
        if eevee is None or not hasattr(eevee, "volumetric_tile_size"):
            raise ToolingError("unsupported-render-setting", "Blender cannot apply render.volumetricTileSize.")
        eevee.volumetric_tile_size = normalized_tile_size

    high_quality_normals = render_settings.get("highQualityNormals")
    if high_quality_normals not in (None, False):
        raise ToolingError(
            "unsupported-render-setting",
            "render.highQualityNormals is not supported by the reviewed Blender 5.2 EEVEE API.",
        )
    for profile_key, attribute in (
        ("motionBlur", "use_motion_blur"),
        ("useCompositing", "use_compositing"),
        ("filmTransparent", "film_transparent"),
    ):
        value = render_settings.get(profile_key)
        if value is not None:
            if not isinstance(value, bool):
                raise ToolingError("invalid-profile", f"render.{profile_key} must be boolean.")
            setattr(scene.render, attribute, value)
    _apply_compositor_profile(scene, render_settings, profile.raw)
    dither = render_settings.get("ditherIntensity")
    if dither is not None:
        if isinstance(dither, bool) or not isinstance(dither, (int, float)) or not math.isfinite(float(dither)):
            raise ToolingError("invalid-profile", "render.ditherIntensity must be finite.")
        scene.render.dither_intensity = float(dither)

    scene.frame_start = start
    scene.frame_end = end
    scene.render.use_file_extension = True
    scene.render.use_overwrite = False
    scene.render.use_placeholder = False
    blender_pattern = profile.image.filename_pattern.replace("%06d", "######")
    suffix = f".{profile.image.extension}"
    if blender_pattern.endswith(suffix):
        blender_pattern = blender_pattern[: -len(suffix)]
    scene.render.filepath = str(output / blender_pattern)


def main() -> int:
    import bpy  # type: ignore[import-not-found]

    handlers: tuple[tuple[Any, Any], ...] = ()
    try:
        args = _arguments()
        profile_path = _absolute_existing_file(args.profile, "Render profile")
        manifest_path = _absolute_existing_file(args.render_manifest, "Render manifest")
        output_path = Path(args.output).expanduser()
        if not output_path.is_absolute() or not output_path.is_dir():
            raise ToolingError("invalid-output", "Chunk output must be an existing absolute directory.")
        profile, output = _validate_authorized_chunk(
            bpy,
            profile_path,
            manifest_path,
            output_path,
            args.start,
            args.end,
        )
        _apply_render_profile(bpy.context.scene, profile, output, args.start, args.end)
        output_variant = profile.raw.get("outputVariant")
        output_variant_settings = (
            output_variant if isinstance(output_variant, dict) else {}
        )
        composition_profile = profile.raw.get("compositionProfile")
        composition_settings = (
            composition_profile if isinstance(composition_profile, dict) else {}
        )
        output_variant_id = str(
            output_variant_settings.get(
                "id",
                profile.raw.get("outputVariantId", "primary"),
            )
        )
        composition_profile_id = str(
            composition_settings.get(
                "id",
                profile.raw.get("compositionProfileId", "primary"),
            )
        )
        artifact_root = output.parents[2]
        artifact_directory = output.relative_to(artifact_root).as_posix()
        emitter = RenderEventEmitter(
            bpy.context.scene,
            job_id=args.job_id,
            worker_id=args.worker_id,
            project_id=profile.project,
            scene_sha256=profile.approved_scene_sha256,
            profile_sha256=profile.source_sha256,
            output_variant_id=output_variant_id,
            width=profile.width,
            height=profile.height,
            composition_profile_id=composition_profile_id,
            artifact_directory=artifact_directory,
            artifact_filename_pattern=profile.image.filename_pattern,
            start=args.start,
            end=args.end,
        )
        handlers = _install_event_handlers(bpy, emitter)
        bpy.ops.render.render(animation=True)
        expected = [output / profile.image.filename(frame) for frame in range(args.start, args.end + 1)]
        missing = [path.name for path in expected if not path.is_file() or path.stat().st_size <= 0]
        if missing:
            raise ToolingError("chunk-render-incomplete", f"Blender did not produce {len(missing)} expected frame(s).")
        emitter.emit(
            "chunk_complete",
            frame=args.end,
            renderedFrameCount=args.end - args.start + 1,
        )
        result = {
            "ok": True,
            "startFrame": args.start,
            "endFrame": args.end,
            "frameCount": args.end - args.start + 1,
            "output": str(output),
            "sceneSha256": profile.approved_scene_sha256,
            "renderProfileSha256": profile.source_sha256,
        }
    except (OSError, ToolingError) as exc:
        code = exc.code if isinstance(exc, ToolingError) else "chunk-render-filesystem-error"
        print(json.dumps({"ok": False, "error": {"code": code, "message": str(exc)[:500]}}))
        return 2
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": {
                        "code": "chunk-render-unhandled-error",
                        "errorType": type(exc).__name__,
                        "message": str(exc)[:500],
                    },
                }
            )
        )
        return 3
    finally:
        _remove_event_handlers(handlers)
    print(json.dumps(result, ensure_ascii=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
