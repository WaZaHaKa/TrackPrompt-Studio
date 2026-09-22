"""Offline preparation for the temporary, isolated Hayale local run."""

from __future__ import annotations

import copy
import json
import shutil
import struct
import sys
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parent
REPO = PROJECT.parents[2]
sys.path.insert(0, str(REPO / "backend"))
from app.video_generation.jsonio import atomic_write_json, sha256_file, sha256_json  # noqa: E402

ROOT = REPO / ".trackprompt-data/hayale-v21"
V2 = REPO / ".trackprompt-data/video-generation/hayale-flowers-promo/v2-preparation"
COMFY = Path("D:/TrackPrompt-ComfyUI")
TOOLS = Path("D:/TrackPrompt-Tools")
PROFILES = {"quality": (832, 480), "low-memory": (640, 368)}
NEGATIVE = (
    "people, faces, hands, new architecture, extra doors, enlarged rooms, invented flowers, "
    "duplicated petals, morphing products, warped lettering, plastic CGI, aggressive HDR, "
    "flicker, texture crawling, whip pans, internal cuts, watermarks, subtitles, invented text, "
    "blurred details, oversharpening, oversaturation, cartoon, anime, unstable geometry"
)


def read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def contained(path: Path, root: Path | None = None) -> Path:
    root = ROOT if root is None else root
    candidate = path.absolute()
    if not candidate.resolve().is_relative_to(root.resolve()):
        raise ValueError("V2.1 output escapes its isolated runtime")
    cursor = candidate
    while cursor != REPO:
        if cursor.is_symlink() or cursor.is_junction():
            raise ValueError("Redirected output directory refused")
        if cursor == cursor.parent:
            raise ValueError("Output must stay inside TrackPrompt-Studio")
        cursor = cursor.parent
    return candidate


def png_size(path: Path) -> tuple[int, int]:
    with path.open("rb") as stream:
        header = stream.read(24)
    if (
        len(header) != 24
        or header[:8] != b"\x89PNG\r\n\x1a\n"
        or header[12:16] != b"IHDR"
    ):
        raise ValueError("Expected a valid PNG image")
    return struct.unpack(">II", header[16:24])  # type: ignore[no-any-return]


def file_record(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError(f"Required local asset is missing: {path.name}")
    before = path.stat()
    digest = sha256_file(path)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("An input changed while being hashed")
    return {
        "path": str(path.resolve()),
        "bytes": after.st_size,
        "mtimeNs": after.st_mtime_ns,
        "sha256": digest,
    }


def verify_record(record: dict[str, Any], *, full_hash: bool = True) -> Path:
    path = Path(record["path"])
    stat = path.stat()
    if stat.st_size != record["bytes"]:
        raise ValueError(f"Prepared input changed: {path.name}")
    # Large installed weights were hashed once. Rehash after any timestamp change.
    if full_hash or stat.st_mtime_ns != record["mtimeNs"]:
        if sha256_file(path) != record["sha256"]:
            raise ValueError(f"Prepared input changed: {path.name}")
    return path


def local_edit(source: dict[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(source)
    value["title"] = "Hayale Flowers V2.1 - local 60-second promo"
    value["projectId"] = "hayale-flowers-promo-v21"
    value["editorial"]["exportFiles"] = {
        "fcpxml": "hayale-promo-v21.fcpxml",
        "fcp7": "hayale-promo-v21.xml",
        "edl": "hayale-promo-v21.edl",
        "preview1080p": "hayale-promo-v21-60s.mp4",
    }
    value["editorial"]["handoff"] = {
        "title": value["title"],
        "summary": "Seven local Wan scenes; one logo reused twice; fixed 60-second edit. Operator visual review required.",
    }
    return value


def scene_workflow(
    scene: dict[str, Any], profile: str, image: str, prefix: str
) -> dict[str, Any]:
    graph = read(PROJECT / "wan-workflow.json")
    width, height = PROFILES[profile]
    graph["latent"]["inputs"].update(
        width=width, height=height, length=129, batch_size=1
    )
    graph["start_image"]["inputs"]["image"] = image
    style = "Warm neutral daylight, clean ivory whites, believable greens and flower colors, authentic photographic detail. "
    if scene["shotId"] == "shot-007":
        style = "Elegant illustrated floral brand signature on an ivory background. "
    graph["positive"]["inputs"]["text"] = style + scene["prompt"]
    graph["negative"]["inputs"]["text"] = NEGATIVE
    for node in ("high_sampler", "low_sampler"):
        graph[node]["inputs"].update(steps=40, cfg=3.5, noise_seed=scene["seed"])
    graph["high_sampler"]["inputs"].update(start_at_step=0, end_at_step=20)
    graph["low_sampler"]["inputs"].update(start_at_step=20, end_at_step=40)
    graph["decode"] = {
        "class_type": "VAEDecodeTiled",
        "inputs": {
            "samples": ["low_sampler", 0],
            "vae": ["vae", 0],
            "tile_size": 256,
            "overlap": 64,
            "temporal_size": 16,
            "temporal_overlap": 8,
        },
    }
    graph["save_frames"]["inputs"]["filename_prefix"] = prefix
    return graph


def installed_tools() -> dict[str, str]:
    ffmpeg = sorted(
        (Path.home() / "AppData/Local/Microsoft/WinGet/Packages").glob(
            "Gyan.FFmpeg_*/ffmpeg-*/bin/ffmpeg.exe"
        )
    )
    if not ffmpeg:
        raise ValueError(
            "Existing FFmpeg installation was not found; no download was attempted"
        )
    rife = TOOLS / "rife-20221029/rife-ncnn-vulkan-20221029-windows"
    upscale = TOOLS / "realesrgan-0.2.2.4"
    return {
        "ffmpeg": str(ffmpeg[-1]),
        "ffprobe": str(ffmpeg[-1].with_name("ffprobe.exe")),
        "rife": str(rife / "rife-ncnn-vulkan.exe"),
        "rifeModel": str(rife / "rife-v4.6"),
        "upscaler": str(upscale / "realesrgan-ncnn-vulkan.exe"),
        "upscaleModels": str(upscale / "models"),
    }


def prepare() -> dict[str, Any]:
    root = contained(ROOT / "prepared")
    manifest_path = root / "manifest.json"
    if manifest_path.exists():
        return verify_prepared()
    if root.exists() and any(root.iterdir()):
        raise ValueError(
            "Incomplete preparation retained; inspect it before explicitly choosing a new preparation directory"
        )
    references = read(V2 / "references-manifest.json")["references"]
    scenes = read(PROJECT.parent / "v2/shot-bank.json")["shots"]
    if [r["shotId"] for r in references] != [f"shot-{n:03d}" for n in range(1, 8)]:
        raise ValueError("Exactly seven ordered V2 references are required")
    edit = local_edit(read(V2 / "edit-plan.json"))
    cues = read(V2 / "music-cues.json")
    tools = installed_tools()
    model_paths = [
        COMFY / "models/unet" / f"wan2.2_i2v_{stage}_noise_14B_Q5_K_M.gguf"
        for stage in ("high", "low")
    ]
    model_paths += [
        COMFY / "models/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors",
        COMFY / "models/vae/wan_2.1_vae.safetensors",
    ]
    model_paths += list(Path(tools["rifeModel"]).glob("flownet.*"))
    model_paths += [
        Path(tools["upscaleModels"]) / f"realesrgan-x4plus.{suffix}"
        for suffix in ("bin", "param")
    ]
    dependencies = model_paths + [
        Path(tools[k]) for k in ("ffmpeg", "ffprobe", "rife", "upscaler")
    ]
    dependencies += [
        COMFY / ".venv/Scripts/python.exe",
        COMFY / "main.py",
        COMFY / "nodes.py",
        COMFY / "comfy/cli_args.py",
        COMFY / "custom_nodes/ComfyUI-GGUF/nodes.py",
    ]
    print(
        "Verifying the existing local models and tools (hashing weights once).",
        flush=True,
    )
    installed = [file_record(path) for path in dependencies]
    source_records = [file_record(Path(r["sourcePath"])) for r in references]
    for reference, record in zip(references, source_records, strict=True):
        if record["sha256"] != reference["sourceSha256"]:
            raise ValueError(
                "An original source changed since V2 reference preparation"
            )
    if cues.get("audioPath"):
        audio_record = file_record(Path(cues["audioPath"]))
        if audio_record["sha256"] != cues["sourceSha256"]:
            raise ValueError("Audio changed since the prepared local onset analysis")
        source_records.append(audio_record)
    root.mkdir(parents=True, exist_ok=True)
    local_refs = []
    for reference in references:
        source = (REPO / reference["preparedPath"]).resolve()
        if (
            not source.is_relative_to(V2 / "references")
            or sha256_file(source) != reference["sha256"]
        ):
            raise ValueError("Prepared V2 reference identity is invalid")
        destination = root / "references" / source.name
        destination.parent.mkdir(exist_ok=True)
        shutil.copyfile(source, destination)
        local_refs.append(
            {
                **reference,
                **file_record(destination),
                "dimensions": list(png_size(destination)),
            }
        )
    atomic_write_json(root / "edit-plan.json", edit)
    atomic_write_json(root / "music-cues.json", cues)
    atomic_write_json(root / "scenes.json", {"scenes": scenes})
    for profile in PROFILES:
        for scene, reference in zip(scenes, local_refs, strict=True):
            graph = scene_workflow(
                scene, profile, Path(reference["path"]).name, f"{scene['shotId']}/frame"
            )
            atomic_write_json(
                root / "workflows" / profile / f"{scene['shotId']}.json", graph
            )
    prepared_files = [file_record(path) for path in sorted(root.rglob("*.json"))]
    manifest = {
        "schemaVersion": 1,
        "projectId": "hayale-flowers-promo-v21",
        "status": "prepared-not-generated",
        "comfyRoot": str(COMFY),
        "tools": tools,
        "installed": installed,
        "sources": source_records,
        "references": local_refs,
        "files": prepared_files,
        "profiles": PROFILES,
        "inferenceQualification": "not-run",
        "generationRequests": 7,
        "timelineEvents": 8,
        "frames": 1440,
        "fps": 24,
        "deliverySize": [1920, 1080],
        "audioUpload": False,
    }
    manifest["digest"] = sha256_json(manifest)
    atomic_write_json(manifest_path, manifest)
    return verify_prepared()


def verify_prepared() -> dict[str, Any]:
    manifest = read(contained(ROOT / "prepared/manifest.json"))
    unsigned = {key: value for key, value in manifest.items() if key != "digest"}
    if sha256_json(unsigned) != manifest["digest"]:
        raise ValueError("V2.1 preparation manifest changed")
    for row in manifest["installed"]:
        verify_record(row, full_hash=False)
    for row in manifest["references"] + manifest["files"]:
        contained(Path(row["path"]), ROOT / "prepared")
        verify_record(row)
    for row in manifest["sources"]:
        verify_record(row)
    return manifest
