from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import re
import shutil
import sqlite3
import sys
from pathlib import Path
from types import ModuleType
from typing import Any
from uuid import uuid4

PROJECT_ID = "the-riff-that-learned-to-breathe"
REFERENCE_SEEDS = {
    "architect": 24_081_101,
    "listener": 24_081_102,
    "bird": 24_081_103,
    "style": 24_081_104,
}
SOURCE_FRAMES = 81
SOURCE_FPS = 16
DELIVERY_FPS = 24
TRANSITION_SECONDS = 0.25


def load_exact_timeline(repository_root: Path) -> list[dict[str, Any]]:
    archive_root = repository_root / ".trackprompt-data" / "mission-control" / "local-video"
    database_path = archive_root / "projects.sqlite3"
    if not database_path.is_file():
        raise RuntimeError("The Mission Control local-video archive is unavailable")
    uri = f"file:{database_path.as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        row = connection.execute(
            """
            SELECT r.storage_key
            FROM local_video_projects AS p
            JOIN local_video_revisions AS r ON r.revision_id = p.current_revision_id
            WHERE p.project_id = ? AND p.deleted_at IS NULL AND r.deleted_at IS NULL
            """,
            (PROJECT_ID,),
        ).fetchone()
    if row is None:
        raise RuntimeError("The current Mission Control project revision is unavailable")
    timeline_path = (archive_root / str(row[0]) / "analysis" / "timeline.json").resolve()
    if not timeline_path.is_relative_to(archive_root.resolve()) or not timeline_path.is_file():
        raise RuntimeError("The archived exact scene timeline is unavailable")
    timeline = json.loads(timeline_path.read_text(encoding="utf-8"))
    if not isinstance(timeline, list):
        raise RuntimeError("The archived exact scene timeline has an invalid shape")
    if len(timeline) != 16 or abs(float(timeline[-1]["endSeconds"]) - 149.354172) > 0.000001:
        raise RuntimeError("The persistent exact 16-scene timeline prerequisite is unavailable")
    return timeline


def load_qualification_module(repository_root: Path) -> ModuleType:
    path = repository_root / "tools" / "qualify-local-anime-pipeline.py"
    spec = importlib.util.spec_from_file_location("trackprompt_local_qualification", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("The qualified local pipeline module is unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.partial-{uuid4().hex}")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def parse_reference_prompts(path: Path, master_style: str) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    result: dict[str, str] = {}
    for identity, heading in (("architect", "The Architect"), ("listener", "The Listener"), ("bird", "The Riff Bird")):
        match = re.search(rf"(?ms)^## {re.escape(heading)}\s+Prompt:\s+`([^`]+)`", text)
        if match is None:
            raise RuntimeError(f"Reference prompt is missing: {heading}")
        result[identity] = " ".join(match.group(1).split())
    result["style"] = (
        f"{master_style} visual style reference sheet, environment palette swatches rendered as painted "
        "workshop, rain room, quiet night city, floating bridge and pale rose dawn panels, consistent "
        "watercolor, gouache, ink contour and paper grain, no people, no text, no labels, no logos"
    )
    return result


def reference_conditioned_flux_workflow(
    qa: ModuleType,
    *,
    prompt: str,
    negative: str,
    seed: int,
    image_name: str,
    output_prefix: str,
) -> dict[str, Any]:
    return {
        "checkpoint": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": qa.FLUX_MODEL},
            "_meta": {"title": "Load FLUX keyframe model"},
        },
        "positive": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": prompt, "clip": ["checkpoint", 1]},
            "_meta": {"title": "Encode positive prompt"},
        },
        "negative": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": negative, "clip": ["checkpoint", 1]},
            "_meta": {"title": "Encode negative prompt"},
        },
        "reference": {
            "class_type": "LoadImage",
            "inputs": {"image": image_name},
            "_meta": {"title": "Load canonical continuity reference"},
        },
        "encode": {
            "class_type": "VAEEncode",
            "inputs": {"pixels": ["reference", 0], "vae": ["checkpoint", 2]},
            "_meta": {"title": "Condition keyframe latent from canonical reference"},
        },
        "sampler": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["checkpoint", 0],
                "seed": seed,
                "steps": 4,
                "cfg": 1.0,
                "sampler_name": "euler",
                "scheduler": "simple",
                "positive": ["positive", 0],
                "negative": ["negative", 0],
                "latent_image": ["encode", 0],
                "denoise": 0.82,
            },
            "_meta": {"title": "Sample reference-conditioned FLUX keyframe"},
        },
        "decode": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["sampler", 0], "vae": ["checkpoint", 2]},
            "_meta": {"title": "Decode reference-conditioned keyframe"},
        },
        "output": {
            "class_type": "SaveImage",
            "inputs": {"images": ["decode", 0], "filename_prefix": output_prefix},
            "_meta": {"title": "Save reference-conditioned keyframe"},
        },
    }


def choose_reference(scene: dict[str, Any], references: dict[str, Path], composite: Path) -> Path:
    prompt = str(scene["keyframePrompt"]).casefold()
    has_architect = "the architect" in prompt
    has_listener = "the listener" in prompt
    if has_architect and has_listener:
        return composite
    if has_architect:
        return references["architect"]
    if has_listener:
        return references["listener"]
    if "bird" in prompt:
        return references["bird"]
    return references["style"]


async def download_single_image(
    qa: ModuleType,
    api: Any,
    session: Any,
    history: dict[str, Any],
    destination: Path,
) -> None:
    items = qa.output_items(history, {".png", ".jpg", ".jpeg", ".webp"})
    if len(items) != 1:
        raise RuntimeError(f"Expected one image output for {destination.name}; received {len(items)}")
    await api.download_item(session, items[0], destination)
    qa.validate_image(destination, width=qa.WIDTH, height=qa.HEIGHT)


async def upload_and_generate_keyframe(
    qa: ModuleType,
    api: Any,
    session: Any,
    *,
    reference: Path,
    scene: dict[str, Any],
    run_id: str,
    destination: Path,
) -> dict[str, Any]:
    upload_name = await api.upload_image(session, reference)
    workflow = reference_conditioned_flux_workflow(
        qa,
        prompt=str(scene["keyframePrompt"]),
        negative=str(scene["negativePrompt"]),
        seed=int(scene["seed"]),
        image_name=upload_name,
        output_prefix=f"trackprompt/production/{run_id}/keyframes/{scene['shotId']}",
    )
    prompt_id, history, elapsed, measurements = await api.run_workflow(
        session,
        workflow,
        label=f"{scene['shotId']} FLUX",
        timeout_seconds=1_800,
    )
    await download_single_image(qa, api, session, history, destination)
    return {
        "promptId": prompt_id,
        "elapsedSeconds": elapsed,
        "peakVramBytes": measurements.peak_vram_bytes,
        "peakSystemMemoryBytes": measurements.peak_ram_bytes,
        "sha256": qa.sha256_file(destination),
        "referenceSha256": qa.sha256_file(reference),
    }


async def generate_scene_video(
    qa: ModuleType,
    api: Any,
    session: Any,
    *,
    scene: dict[str, Any],
    keyframe: Path,
    run_id: str,
    raw_frames: Path,
    raw_video: Path,
) -> dict[str, Any]:
    upload_name = await api.upload_image(session, keyframe)
    workflow = qa.wan_workflow(
        image_name=upload_name,
        positive=str(scene["wanI2VMotionPrompt"]),
        negative=str(scene["negativePrompt"]),
        output_prefix=f"trackprompt/production/{run_id}/wan/{scene['shotId']}",
    )
    workflow["latent"]["inputs"]["length"] = SOURCE_FRAMES
    workflow["high_sampler"]["inputs"]["noise_seed"] = int(scene["seed"])
    workflow["low_sampler"]["inputs"]["noise_seed"] = int(scene["seed"])
    prompt_id, history, elapsed, measurements = await api.run_workflow(
        session,
        workflow,
        label=f"{scene['shotId']} Wan Q5",
        timeout_seconds=14_400,
    )
    frames = qa.output_items(history, {".png", ".jpg", ".jpeg", ".webp"})
    if len(frames) != SOURCE_FRAMES:
        raise RuntimeError(f"{scene['shotId']} produced {len(frames)} frames; expected {SOURCE_FRAMES}")
    raw_frames.mkdir(parents=True, exist_ok=False)
    for index, item in enumerate(frames):
        await api.download_item(session, item, raw_frames / f"frame_{index:08d}.png")
    videos = qa.output_items(history, {".mp4", ".webm", ".mov", ".mkv"})
    if videos:
        await api.download_item(session, videos[0], raw_video)
    return {
        "promptId": prompt_id,
        "elapsedSeconds": elapsed,
        "peakVramBytes": measurements.peak_vram_bytes,
        "peakSystemMemoryBytes": measurements.peak_ram_bytes,
        "frames": SOURCE_FRAMES,
        "seed": int(scene["seed"]),
        "keyframeSha256": qa.sha256_file(keyframe),
    }


def post_scene(
    qa: ModuleType,
    *,
    raw_frames: Path,
    scene_master: Path,
    working_root: Path,
    duration_seconds: float,
    rife: Path,
    realesrgan: Path,
    ffmpeg: Path,
) -> dict[str, Any]:
    interpolated = working_root / "interpolated"
    upscaled = working_root / "upscaled"
    delivery_frames = round((SOURCE_FRAMES - 1) * DELIVERY_FPS / SOURCE_FPS) + 1
    if len(list(interpolated.glob("*.png"))) != delivery_frames:
        if interpolated.exists():
            raise RuntimeError(f"Incomplete preserved RIFE output requires an explicit new run root: {interpolated}")
        interpolated.mkdir(parents=True)
        qa.run_command(
            [
                str(rife), "-i", str(raw_frames), "-o", str(interpolated), "-n", str(delivery_frames),
                "-m", str(rife.parent / "rife-v4.6"), "-g", "0", "-f", "frame_%08d.png",
            ],
            timeout=7_200,
            cwd=rife.parent,
        )
    if len(list(upscaled.glob("*.png"))) != delivery_frames:
        if upscaled.exists():
            raise RuntimeError(f"Incomplete preserved upscale output requires an explicit new run root: {upscaled}")
        upscaled.mkdir(parents=True)
        qa.run_command(
            [
                str(realesrgan), "-i", str(interpolated), "-o", str(upscaled), "-s", "4",
                "-m", str(realesrgan.parent / "models"), "-n", "realesrgan-x4plus-anime",
                "-g", "0", "-f", "png",
            ],
            timeout=7_200,
            cwd=realesrgan.parent,
        )
    scene_master.parent.mkdir(parents=True, exist_ok=True)
    qa.run_command(
        [
            str(ffmpeg), "-nostdin", "-hide_banner", "-loglevel", "error", "-framerate", "24",
            "-i", str(upscaled / "frame_%08d.png"), "-vf",
            f"scale=1920:1080:flags=lanczos,setsar=1,setpts={duration_seconds / (delivery_frames / 24):.12f}*PTS,fps=24",
            "-t", f"{duration_seconds:.6f}", "-an", "-c:v", "libx264", "-crf", "16",
            "-preset", "slow", "-pix_fmt", "yuv420p", str(scene_master),
        ],
        timeout=1_800,
    )
    return {
        "interpolatedFrames": delivery_frames,
        "durationSeconds": duration_seconds,
        "masterSha256": qa.sha256_file(scene_master),
    }


def assemble_final(
    qa: ModuleType,
    *,
    scene_masters: list[Path],
    durations: list[float],
    audio: Path,
    output: Path,
    ffmpeg: Path,
) -> None:
    adjusted = [duration + TRANSITION_SECONDS * (len(scene_masters) - 1) / len(scene_masters) for duration in durations]
    command = [str(ffmpeg), "-nostdin", "-hide_banner", "-loglevel", "error", "-y"]
    for path in scene_masters:
        command.extend(["-i", str(path)])
    command.extend(["-i", str(audio)])
    filters = [f"[{index}:v]trim=duration={adjusted[index]:.9f},setpts=PTS-STARTPTS[v{index}]" for index in range(len(scene_masters))]
    offset = adjusted[0] - TRANSITION_SECONDS
    previous = "v0"
    for index in range(1, len(scene_masters)):
        output_label = f"x{index}"
        filters.append(
            f"[{previous}][v{index}]xfade=transition=fade:duration={TRANSITION_SECONDS}:offset={offset:.9f}[{output_label}]"
        )
        previous = output_label
        offset += adjusted[index] - TRANSITION_SECONDS
    output.parent.mkdir(parents=True, exist_ok=True)
    command.extend(
        [
            "-filter_complex", ";".join(filters), "-map", f"[{previous}]", "-map", f"{len(scene_masters)}:a:0",
            "-t", "149.354172", "-r", "24", "-c:v", "libx264", "-crf", "16", "-preset", "slow",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "320k", "-movflags", "+faststart", str(output),
        ]
    )
    qa.run_command(command, timeout=7_200)


def plan(arguments: argparse.Namespace, qa: ModuleType) -> dict[str, Any]:
    repository_root = Path(arguments.repository_root).resolve()
    project_root = repository_root / "video-projects" / "local" / PROJECT_ID
    qualification = project_root / "outputs" / "qualification" / "qualification-run.json"
    report = json.loads(qualification.read_text(encoding="utf-8"))
    if report.get("status") != "LOCAL_ANIME_PIPELINE_READY" or report.get("wan", {}).get("tier") != "A14B-Q5_K_M":
        raise RuntimeError("The real Q5 qualification prerequisite has not passed")
    scenes = json.loads((project_root / "prompts" / "scene-prompts.json").read_text(encoding="utf-8"))["scenes"]
    timeline = load_exact_timeline(repository_root)
    if len(scenes) != len(timeline):
        raise RuntimeError("The prompt package does not match the archived exact timeline")
    working_root = Path(arguments.working_root).resolve()
    working_free_gib = shutil.disk_usage(working_root.anchor).free / 2**30
    output_free_gib = shutil.disk_usage(project_root.anchor).free / 2**30
    if working_free_gib < 60:
        raise RuntimeError("The production working drive has less than the required 60 GiB free")
    if output_free_gib < 10:
        raise RuntimeError("The production output drive has less than the required 10 GiB free")
    return {
        "status": "PRODUCTION_LAUNCH_READY",
        "projectId": PROJECT_ID,
        "qualifiedTier": "A14B-Q5_K_M",
        "references": ["architect", "listener", "bird", "style"],
        "referenceConditioning": "FLUX latent img2img from canonical project reference sheets; no text-only substitution",
        "scenes": 16,
        "durationSeconds": 149.354172,
        "workingRoot": str(working_root),
        "workingDriveFreeGiB": round(working_free_gib, 1),
        "outputDriveFreeGiB": round(output_free_gib, 1),
        "finalOutput": str(project_root / "outputs" / "production" / "the-riff-that-learned-to-breathe-1080p24.mp4"),
        "estimatedWanGpuHoursFromQualification": round(16 * float(report["wan"]["elapsedSeconds"]) * SOURCE_FRAMES / 33 / 3600, 1),
        "launchAuthorized": False,
    }


async def render(arguments: argparse.Namespace, qa: ModuleType) -> None:
    repository_root = Path(arguments.repository_root).resolve()
    project_root = repository_root / "video-projects" / "local" / PROJECT_ID
    output_root = project_root / "outputs" / "production"
    working_root = Path(arguments.working_root).resolve()
    run_id = arguments.run_id
    state_path = output_root / f"production-run-{run_id}.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.is_file() else {
        "schemaVersion": "1.0.0", "runId": run_id, "status": "running", "references": {}, "scenes": {},
    }
    scenes = json.loads((project_root / "prompts" / "scene-prompts.json").read_text(encoding="utf-8"))["scenes"]
    timeline = load_exact_timeline(repository_root)
    negative = (project_root / "prompts" / "global-negative.txt").read_text(encoding="utf-8").strip()
    master_style = (project_root / "prompts" / "master-style-prefix.txt").read_text(encoding="utf-8").strip()
    reference_prompts = parse_reference_prompts(project_root / "prompts" / "character-reference-prompts.md", master_style)
    tools_lock = json.loads((Path(arguments.comfyui_root) / "trackprompt-local-generation-tools-lock.json").read_text(encoding="utf-8-sig"))
    rife = Path(tools_lock["rife"]["executable"])
    realesrgan = Path(tools_lock["realesrgan"]["executable"])
    ffmpeg = Path(arguments.ffmpeg)
    ffprobe = Path(arguments.ffprobe)
    api = qa.ComfyApi(arguments.comfyui_url)
    timeout = qa.aiohttp.ClientTimeout(total=None, connect=10, sock_connect=10, sock_read=None)
    references: dict[str, Path] = {}
    async with qa.aiohttp.ClientSession(timeout=timeout) as session:
        await api.preflight(session)
        for identity, prompt in reference_prompts.items():
            destination = output_root / "references" / f"{identity}.png"
            references[identity] = destination
            if not destination.is_file():
                workflow = qa.flux_workflow(prompt, negative, f"trackprompt/production/{run_id}/references/{identity}")
                workflow["sampler"]["inputs"]["seed"] = REFERENCE_SEEDS[identity]
                prompt_id, history, elapsed, measurements = await api.run_workflow(
                    session, workflow, label=f"reference {identity}", timeout_seconds=1_800
                )
                await download_single_image(qa, api, session, history, destination)
                state["references"][identity] = {
                    "promptId": prompt_id, "elapsedSeconds": elapsed, "sha256": qa.sha256_file(destination),
                    "peakVramBytes": measurements.peak_vram_bytes,
                }
                atomic_json(state_path, state)
        composite = output_root / "references" / "continuity-composite.png"
        if not composite.is_file():
            from PIL import Image

            canvas = Image.new("RGB", (qa.WIDTH, qa.HEIGHT), (28, 34, 46))
            for index, identity in enumerate(("architect", "listener", "bird", "style")):
                with Image.open(references[identity]) as source:
                    tile = source.convert("RGB").resize((qa.WIDTH // 2, qa.HEIGHT // 2))
                    canvas.paste(tile, ((index % 2) * qa.WIDTH // 2, (index // 2) * qa.HEIGHT // 2))
            composite.parent.mkdir(parents=True, exist_ok=True)
            canvas.save(composite, format="PNG")
        for scene, timing in zip(scenes, timeline, strict=True):
            shot_id = str(scene["shotId"])
            keyframe = output_root / "keyframes" / f"{shot_id}.png"
            if not keyframe.is_file():
                reference = choose_reference(scene, references, composite)
                record = await upload_and_generate_keyframe(
                    qa, api, session, reference=reference, scene=scene, run_id=run_id, destination=keyframe
                )
                state["scenes"].setdefault(shot_id, {})["keyframe"] = record
                atomic_json(state_path, state)
            raw_frames = working_root / run_id / shot_id / "raw"
            raw_video = working_root / run_id / shot_id / "raw.mp4"
            if len(list(raw_frames.glob("*.png"))) != SOURCE_FRAMES:
                if raw_frames.exists():
                    raise RuntimeError(f"Incomplete preserved Wan output requires a new run ID: {raw_frames}")
                record = await generate_scene_video(
                    qa, api, session, scene=scene, keyframe=keyframe, run_id=run_id,
                    raw_frames=raw_frames, raw_video=raw_video,
                )
                state["scenes"].setdefault(shot_id, {})["wan"] = record
                atomic_json(state_path, state)
            duration = float(timing["endSeconds"]) - float(timing["startSeconds"])
            duration += TRANSITION_SECONDS * 15 / 16
            master = output_root / "scene-masters" / f"{shot_id}.mp4"
            if not master.is_file():
                record = post_scene(
                    qa, raw_frames=raw_frames, scene_master=master,
                    working_root=working_root / run_id / shot_id, duration_seconds=duration,
                    rife=rife, realesrgan=realesrgan, ffmpeg=ffmpeg,
                )
                state["scenes"].setdefault(shot_id, {})["post"] = record
                atomic_json(state_path, state)
    final_output = output_root / "the-riff-that-learned-to-breathe-1080p24.mp4"
    if not final_output.is_file():
        masters = [output_root / "scene-masters" / f"shot-{index:03d}.mp4" for index in range(1, 17)]
        durations = [float(item["endSeconds"]) - float(item["startSeconds"]) for item in timeline]
        assemble_final(
            qa, scene_masters=masters, durations=durations, audio=project_root / "audio" / "track.wav",
            output=final_output, ffmpeg=ffmpeg,
        )
    qc = qa.validate_final_video(ffmpeg, ffprobe, final_output, 0)
    if abs(float(qc["durationSeconds"]) - 149.354172) > 0.05:
        raise RuntimeError("The production master does not preserve the exact source audio clock")
    state.update({"status": "complete", "completedAt": qa.utc_now(), "final": qc})
    atomic_json(state_path, state)


def parse_arguments() -> argparse.Namespace:
    repository_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Launch the resumable 16-scene local anime production render")
    parser.add_argument("--repository-root", default=str(repository_root))
    parser.add_argument("--comfyui-root", default=os.getenv("TRACKPROMPT_COMFYUI_ROOT", r"D:\TrackPrompt-ComfyUI"))
    parser.add_argument("--comfyui-url", default=os.getenv("TRACKPROMPT_COMFYUI_URL", "http://127.0.0.1:8188"))
    parser.add_argument("--working-root", default=r"D:\TrackPrompt-Production\the-riff-that-learned-to-breathe")
    parser.add_argument("--run-id", default="production-001")
    parser.add_argument("--ffmpeg", default=str(repository_root / "tools" / "ffmpeg.exe"))
    parser.add_argument("--ffprobe", default=str(repository_root / "tools" / "ffprobe.exe"))
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--plan", action="store_true")
    action.add_argument("--start", action="store_true")
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    repository_root = Path(arguments.repository_root).resolve()
    qa = load_qualification_module(repository_root)
    if arguments.ffmpeg.endswith("tools\\ffmpeg.exe") or arguments.ffmpeg.endswith("tools/ffmpeg.exe"):
        arguments.ffmpeg = str(qa.FFMPEG_DEFAULT)
        arguments.ffprobe = str(qa.FFMPEG_DEFAULT.with_name("ffprobe.exe"))
    try:
        readiness = plan(arguments, qa)
        if arguments.plan:
            print(json.dumps(readiness, ensure_ascii=False, indent=2))
            return 0
        readiness["launchAuthorized"] = True
        print(json.dumps(readiness, ensure_ascii=False, indent=2), flush=True)
        asyncio.run(render(arguments, qa))
    except (RuntimeError, OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"PRODUCTION_FAILED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
