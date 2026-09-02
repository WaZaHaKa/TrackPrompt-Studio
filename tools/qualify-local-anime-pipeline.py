from __future__ import annotations

import argparse
import asyncio
import ctypes
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import aiohttp
from PIL import Image, ImageStat

PROJECT_ID = "the-riff-that-learned-to-breathe"
FLUX_MODEL = "flux1-schnell-fp8.safetensors"
WAN_HIGH_Q5 = "wan2.2_i2v_high_noise_14B_Q5_K_M.gguf"
WAN_LOW_Q5 = "wan2.2_i2v_low_noise_14B_Q5_K_M.gguf"
WAN_TEXT_ENCODER = "umt5_xxl_fp8_e4m3fn_scaled.safetensors"
WAN_VAE = "wan_2.1_vae.safetensors"
SEED = 24_081_001
WIDTH = 1024
HEIGHT = 576
WAN_FRAMES = 33
WAN_FPS = 16
DELIVERY_FPS = 24
WAN_STEPS = 20
WAN_SPLIT_STEP = 10
FFMPEG_DEFAULT = Path(
    r"C:\Users\theon\AppData\Local\Microsoft\WinGet\Packages"
    r"\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe"
    r"\ffmpeg-8.1.1-full_build\bin\ffmpeg.exe"
)


class QualificationError(RuntimeError):
    pass


class MemoryStatusEx(ctypes.Structure):
    _fields_ = [
        ("length", ctypes.c_ulong),
        ("memory_load", ctypes.c_ulong),
        ("total_physical", ctypes.c_ulonglong),
        ("available_physical", ctypes.c_ulonglong),
        ("total_page_file", ctypes.c_ulonglong),
        ("available_page_file", ctypes.c_ulonglong),
        ("total_virtual", ctypes.c_ulonglong),
        ("available_virtual", ctypes.c_ulonglong),
        ("available_extended_virtual", ctypes.c_ulonglong),
    ]


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.partial-{uuid4().hex}")
    try:
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def extract_section(text: str, heading: str, next_heading: str) -> str:
    match = re.search(
        rf"(?ms)^{re.escape(heading)}\s*$\n(.*?)\n^{re.escape(next_heading)}\s*$",
        text,
    )
    if match is None:
        raise QualificationError(f"Project prompt section is missing: {heading}")
    result = " ".join(line.strip() for line in match.group(1).splitlines() if line.strip())
    if not result:
        raise QualificationError(f"Project prompt section is empty: {heading}")
    return result


def current_system_ram_used() -> int:
    status = MemoryStatusEx()
    status.length = ctypes.sizeof(MemoryStatusEx)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return 0
    return int(status.total_physical - status.available_physical)


def current_vram_used() -> int:
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.used",
                "--format=csv,noheader,nounits",
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            shell=False,
        )
        if result.returncode == 0:
            return int(result.stdout.strip().splitlines()[0]) * 1024 * 1024
    except (OSError, ValueError, subprocess.TimeoutExpired, IndexError):
        pass
    return 0


@dataclass(slots=True)
class Measurements:
    peak_vram_bytes: int = 0
    peak_ram_bytes: int = 0


async def monitor_resources(stop: asyncio.Event, measurements: Measurements) -> None:
    while not stop.is_set():
        measurements.peak_vram_bytes = max(measurements.peak_vram_bytes, current_vram_used())
        measurements.peak_ram_bytes = max(measurements.peak_ram_bytes, current_system_ram_used())
        try:
            await asyncio.wait_for(stop.wait(), timeout=1.0)
        except TimeoutError:
            continue


def run_command(command: list[str], *, timeout: float, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    print(f"RUN {Path(command[0]).name} {' '.join(command[1:])}", flush=True)
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=str(cwd) if cwd else None,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise QualificationError(f"Command failed to run: {Path(command[0]).name}") from exc
    if result.returncode != 0:
        diagnostics = (result.stderr or result.stdout)[-8_000:]
        raise QualificationError(
            f"Command failed ({Path(command[0]).name}, exit {result.returncode}): {diagnostics}"
        )
    return result


def flux_workflow(prompt: str, negative: str, output_prefix: str) -> dict[str, Any]:
    return {
        "checkpoint": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": FLUX_MODEL},
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
        "latent": {
            "class_type": "EmptySD3LatentImage",
            "inputs": {"width": WIDTH, "height": HEIGHT, "batch_size": 1},
            "_meta": {"title": "Create keyframe latent"},
        },
        "sampler": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["checkpoint", 0],
                "seed": SEED,
                "steps": 4,
                "cfg": 1.0,
                "sampler_name": "euler",
                "scheduler": "simple",
                "positive": ["positive", 0],
                "negative": ["negative", 0],
                "latent_image": ["latent", 0],
                "denoise": 1.0,
            },
            "_meta": {"title": "Sample FLUX keyframe"},
        },
        "decode": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["sampler", 0], "vae": ["checkpoint", 2]},
            "_meta": {"title": "Decode FLUX keyframe"},
        },
        "output": {
            "class_type": "SaveImage",
            "inputs": {"images": ["decode", 0], "filename_prefix": output_prefix},
            "_meta": {"title": "Save FLUX keyframe"},
        },
    }


def wan_workflow(
    *,
    image_name: str,
    positive: str,
    negative: str,
    output_prefix: str,
    high_model: str = WAN_HIGH_Q5,
    low_model: str = WAN_LOW_Q5,
) -> dict[str, Any]:
    return {
        "high_model": {
            "class_type": "UnetLoaderGGUF",
            "inputs": {"unet_name": high_model},
            "_meta": {"title": "Load high-noise expert"},
        },
        "low_model": {
            "class_type": "UnetLoaderGGUF",
            "inputs": {"unet_name": low_model},
            "_meta": {"title": "Load low-noise expert"},
        },
        "high_sampling": {
            "class_type": "ModelSamplingSD3",
            "inputs": {"model": ["high_model", 0], "shift": 5.0},
            "_meta": {"title": "Configure high-noise expert"},
        },
        "low_sampling": {
            "class_type": "ModelSamplingSD3",
            "inputs": {"model": ["low_model", 0], "shift": 5.0},
            "_meta": {"title": "Configure low-noise expert"},
        },
        "text_encoder": {
            "class_type": "CLIPLoader",
            "inputs": {"clip_name": WAN_TEXT_ENCODER, "type": "wan", "device": "cpu"},
            "_meta": {"title": "Load Wan text encoder"},
        },
        "vae": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": WAN_VAE},
            "_meta": {"title": "Load Wan VAE"},
        },
        "positive": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": positive, "clip": ["text_encoder", 0]},
            "_meta": {"title": "Encode positive prompt"},
        },
        "negative": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": negative, "clip": ["text_encoder", 0]},
            "_meta": {"title": "Encode negative prompt"},
        },
        "start_image": {
            "class_type": "LoadImage",
            "inputs": {"image": image_name},
            "_meta": {"title": "Load start image"},
        },
        "latent": {
            "class_type": "WanImageToVideo",
            "inputs": {
                "positive": ["positive", 0],
                "negative": ["negative", 0],
                "vae": ["vae", 0],
                "width": WIDTH,
                "height": HEIGHT,
                "length": WAN_FRAMES,
                "batch_size": 1,
                "start_image": ["start_image", 0],
            },
            "_meta": {"title": "Create Wan image-to-video latent"},
        },
        "high_sampler": {
            "class_type": "KSamplerAdvanced",
            "inputs": {
                "model": ["high_sampling", 0],
                "add_noise": "enable",
                "noise_seed": SEED,
                "steps": WAN_STEPS,
                "cfg": 3.5,
                "sampler_name": "euler",
                "scheduler": "simple",
                "positive": ["latent", 0],
                "negative": ["latent", 1],
                "latent_image": ["latent", 2],
                "start_at_step": 0,
                "end_at_step": WAN_SPLIT_STEP,
                "return_with_leftover_noise": "enable",
            },
            "_meta": {"title": "High-noise sampling stage"},
        },
        "low_sampler": {
            "class_type": "KSamplerAdvanced",
            "inputs": {
                "model": ["low_sampling", 0],
                "add_noise": "disable",
                "noise_seed": SEED,
                "steps": WAN_STEPS,
                "cfg": 3.5,
                "sampler_name": "euler",
                "scheduler": "simple",
                "positive": ["latent", 0],
                "negative": ["latent", 1],
                "latent_image": ["high_sampler", 0],
                "start_at_step": WAN_SPLIT_STEP,
                "end_at_step": WAN_STEPS,
                "return_with_leftover_noise": "disable",
            },
            "_meta": {"title": "Low-noise sampling stage"},
        },
        "decode": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["low_sampler", 0], "vae": ["vae", 0]},
            "_meta": {"title": "Decode Wan frames"},
        },
        "save_frames": {
            "class_type": "SaveImage",
            "inputs": {"images": ["decode", 0], "filename_prefix": f"{output_prefix}/frame"},
            "_meta": {"title": "Save frame sequence and video"},
        },
        "create_video": {
            "class_type": "CreateVideo",
            "inputs": {"images": ["decode", 0], "fps": float(WAN_FPS), "bit_depth": 8},
            "_meta": {"title": "Create raw Wan video"},
        },
        "save_video": {
            "class_type": "SaveVideo",
            "inputs": {
                "video": ["create_video", 0],
                "filename_prefix": f"{output_prefix}/raw",
                "format": "mp4",
                "codec": "h264",
            },
            "_meta": {"title": "Save frame sequence and video"},
        },
    }


class ComfyApi:
    def __init__(self, base_url: str) -> None:
        parsed = urllib.parse.urlsplit(base_url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
            raise QualificationError("ComfyUI qualification requires a loopback HTTP endpoint")
        self.base_url = base_url.rstrip("/")

    async def get_json(self, session: aiohttp.ClientSession, path: str) -> dict[str, Any]:
        async with session.get(f"{self.base_url}/{path.lstrip('/')}") as response:
            payload = await response.json(content_type=None)
            if response.status >= 400 or not isinstance(payload, dict):
                raise QualificationError(f"ComfyUI GET {path} failed with HTTP {response.status}")
            return payload

    async def preflight(self, session: aiohttp.ClientSession) -> dict[str, Any]:
        stats, objects, queue = await asyncio.gather(
            self.get_json(session, "/system_stats"),
            self.get_json(session, "/object_info"),
            self.get_json(session, "/queue"),
        )
        required_nodes = {
            "CheckpointLoaderSimple",
            "CLIPTextEncode",
            "EmptySD3LatentImage",
            "KSampler",
            "VAEDecode",
            "SaveImage",
            "UnetLoaderGGUF",
            "CLIPLoader",
            "VAELoader",
            "LoadImage",
            "WanImageToVideo",
            "KSamplerAdvanced",
            "ModelSamplingSD3",
            "CreateVideo",
            "SaveVideo",
        }
        missing = sorted(required_nodes - set(objects))
        if missing:
            raise QualificationError(f"ComfyUI node preflight failed: {', '.join(missing)}")
        serialized = json.dumps(objects, ensure_ascii=False)
        required_models = {FLUX_MODEL, WAN_HIGH_Q5, WAN_LOW_Q5, WAN_TEXT_ENCODER, WAN_VAE}
        missing_models = sorted(name for name in required_models if name not in serialized)
        if missing_models:
            raise QualificationError(f"ComfyUI model preflight failed: {', '.join(missing_models)}")
        if "queue_running" not in queue or "queue_pending" not in queue:
            raise QualificationError("ComfyUI queue endpoint returned an incompatible response")
        return stats

    async def upload_image(self, session: aiohttp.ClientSession, path: Path) -> str:
        form = aiohttp.FormData()
        form.add_field("image", path.read_bytes(), filename=path.name, content_type="image/png")
        form.add_field("type", "input")
        form.add_field("overwrite", "false")
        async with session.post(f"{self.base_url}/upload/image", data=form) as response:
            payload = await response.json(content_type=None)
            if response.status >= 400 or not isinstance(payload, dict):
                raise QualificationError(f"ComfyUI image upload failed with HTTP {response.status}")
            name = payload.get("name")
            if not isinstance(name, str) or not name:
                raise QualificationError("ComfyUI image upload returned no safe identity")
            return name

    async def run_workflow(
        self,
        session: aiohttp.ClientSession,
        workflow: dict[str, Any],
        *,
        label: str,
        timeout_seconds: float,
    ) -> tuple[str, dict[str, Any], float, Measurements]:
        client_id = str(uuid4())
        started = time.monotonic()
        measurements = Measurements()
        stop = asyncio.Event()
        monitor = asyncio.create_task(monitor_resources(stop, measurements))
        try:
            async with session.ws_connect(
                f"{self.base_url.replace('http://', 'ws://')}/ws?clientId={client_id}",
                heartbeat=30,
                receive_timeout=60,
            ) as socket:
                async with session.post(
                    f"{self.base_url}/prompt",
                    json={"prompt": workflow, "client_id": client_id},
                ) as response:
                    payload = await response.json(content_type=None)
                    if response.status >= 400 or not isinstance(payload, dict):
                        raise QualificationError(
                            f"ComfyUI rejected {label} workflow (HTTP {response.status}): "
                            f"{json.dumps(payload, ensure_ascii=False)[:8_000]}"
                        )
                    prompt_id = payload.get("prompt_id")
                    if not isinstance(prompt_id, str) or not prompt_id:
                        raise QualificationError(f"ComfyUI returned no prompt ID for {label}")
                print(f"{label}: queued as {prompt_id}", flush=True)
                deadline = time.monotonic() + timeout_seconds
                last_report = 0.0
                while time.monotonic() < deadline:
                    remaining = max(1.0, min(30.0, deadline - time.monotonic()))
                    try:
                        message = await asyncio.wait_for(socket.receive(), timeout=remaining)
                    except TimeoutError:
                        message = None
                    now = time.monotonic()
                    if now - last_report >= 30:
                        elapsed = now - started
                        print(
                            f"{label}: {elapsed:.0f}s elapsed, peak VRAM "
                            f"{measurements.peak_vram_bytes / 2**30:.2f} GiB, peak RAM "
                            f"{measurements.peak_ram_bytes / 2**30:.2f} GiB",
                            flush=True,
                        )
                        last_report = now
                    if message is None or message.type == aiohttp.WSMsgType.BINARY:
                        continue
                    if message.type in {aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR}:
                        raise QualificationError(f"ComfyUI WebSocket closed during {label}")
                    if message.type != aiohttp.WSMsgType.TEXT:
                        continue
                    event = json.loads(message.data)
                    if not isinstance(event, dict):
                        continue
                    data = event.get("data") if isinstance(event.get("data"), dict) else {}
                    if data.get("prompt_id") != prompt_id:
                        continue
                    event_type = event.get("type")
                    if event_type in {"execution_error", "execution_interrupted"}:
                        raise QualificationError(
                            f"ComfyUI {label} execution failed: {json.dumps(data, ensure_ascii=False)[:8_000]}"
                        )
                    if event_type == "progress":
                        value, maximum = data.get("value"), data.get("max")
                        if isinstance(value, int) and isinstance(maximum, int):
                            print(f"{label}: sampler {value}/{maximum}", flush=True)
                    if event_type == "execution_success" or (
                        event_type == "executing" and data.get("node") is None
                    ):
                        break
                else:
                    raise QualificationError(f"ComfyUI {label} exceeded {timeout_seconds:.0f}s")
            record: dict[str, Any] | None = None
            history_deadline = time.monotonic() + 60
            while time.monotonic() < history_deadline:
                history = await self.get_json(session, f"/history/{prompt_id}")
                candidate = history.get(prompt_id)
                if isinstance(candidate, dict):
                    record = candidate
                    break
                await asyncio.sleep(1)
            if not isinstance(record, dict):
                raise QualificationError(f"ComfyUI history is missing {label} output")
            status_text = json.dumps(record.get("status"), ensure_ascii=False).casefold()
            if any(token in status_text for token in ("error", "failed", "interrupted")):
                raise QualificationError(f"ComfyUI history reports {label} failure: {status_text[:8_000]}")
            elapsed = time.monotonic() - started
            return prompt_id, record, elapsed, measurements
        finally:
            stop.set()
            await monitor

    async def download_item(
        self,
        session: aiohttp.ClientSession,
        item: dict[str, Any],
        destination: Path,
    ) -> None:
        filename = item.get("filename")
        subfolder = item.get("subfolder", "")
        kind = item.get("type", "output")
        if not isinstance(filename, str) or not isinstance(subfolder, str) or not isinstance(kind, str):
            raise QualificationError("ComfyUI output identity is invalid")
        destination.parent.mkdir(parents=True, exist_ok=True)
        async with session.get(
            f"{self.base_url}/view",
            params={"filename": filename, "subfolder": subfolder, "type": kind},
        ) as response:
            if response.status >= 400:
                raise QualificationError(f"ComfyUI output download failed with HTTP {response.status}")
            payload = await response.read()
        if not payload:
            raise QualificationError("ComfyUI returned an empty output")
        temporary = destination.with_name(f".{destination.name}.partial-{uuid4().hex}")
        try:
            temporary.write_bytes(payload)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)


def output_items(history: dict[str, Any], extensions: set[str]) -> list[dict[str, Any]]:
    outputs = history.get("outputs")
    result: list[dict[str, Any]] = []
    if not isinstance(outputs, dict):
        return result
    for node in outputs.values():
        if not isinstance(node, dict):
            continue
        for value in node.values():
            if not isinstance(value, list):
                continue
            for item in value:
                if not isinstance(item, dict):
                    continue
                filename = item.get("filename")
                if isinstance(filename, str) and Path(filename).suffix.casefold() in extensions:
                    result.append(item)
    return result


def validate_image(path: Path, *, width: int, height: int) -> dict[str, Any]:
    if not path.is_file() or path.stat().st_size <= 0:
        raise QualificationError("FLUX keyframe is missing or empty")
    with Image.open(path) as image:
        image.load()
        actual = image.size
        rgb = image.convert("RGB")
        stats = ImageStat.Stat(rgb.resize((128, 72)))
        means = [float(value) for value in stats.mean]
        extrema = rgb.getextrema()
    if actual != (width, height):
        raise QualificationError(f"FLUX keyframe has wrong dimensions: {actual}")
    dynamic_range = max(high for _low, high in extrema) - min(low for low, _high in extrema)
    if max(means) < 2.0 or dynamic_range < 4:
        raise QualificationError("FLUX keyframe appears black or empty")
    return {
        "width": actual[0],
        "height": actual[1],
        "channelMeans": means,
        "dynamicRange": dynamic_range,
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
    }


def ffprobe_json(ffprobe: Path, path: Path) -> dict[str, Any]:
    result = run_command(
        [
            str(ffprobe),
            "-v",
            "error",
            "-show_entries",
            "format=duration,size:stream=index,codec_type,codec_name,width,height,r_frame_rate,avg_frame_rate,nb_frames",
            "-of",
            "json",
            str(path),
        ],
        timeout=120,
    )
    value = json.loads(result.stdout)
    if not isinstance(value, dict):
        raise QualificationError(f"ffprobe returned invalid output for {path.name}")
    return value


def video_stream(probe: dict[str, Any]) -> dict[str, Any]:
    streams = probe.get("streams")
    if not isinstance(streams, list):
        raise QualificationError("Media probe has no streams")
    stream = next(
        (item for item in streams if isinstance(item, dict) and item.get("codec_type") == "video"),
        None,
    )
    if stream is None:
        raise QualificationError("Media probe has no video stream")
    return stream


def exact_rate(value: object) -> float:
    if not isinstance(value, str) or "/" not in value:
        return 0.0
    numerator, denominator = value.split("/", 1)
    try:
        return float(numerator) / float(denominator)
    except (ValueError, ZeroDivisionError):
        return 0.0


def validate_final_video(ffmpeg: Path, ffprobe: Path, path: Path, started_ns: int) -> dict[str, Any]:
    if not path.is_file() or path.stat().st_size <= 0 or path.stat().st_mtime_ns < started_ns:
        raise QualificationError("Final qualification video is missing, empty, or stale")
    probe = ffprobe_json(ffprobe, path)
    stream = video_stream(probe)
    rate = exact_rate(stream.get("avg_frame_rate") or stream.get("r_frame_rate"))
    if stream.get("width") != 1920 or stream.get("height") != 1080 or abs(rate - 24.0) > 0.001:
        raise QualificationError("Final qualification video is not exact 1920x1080 at 24 fps")
    decode = subprocess.run(
        [
            str(ffmpeg),
            "-nostdin",
            "-hide_banner",
            "-v",
            "error",
            "-i",
            str(path),
            "-map",
            "0:v:0",
            "-f",
            "null",
            "-",
        ],
        check=False,
        capture_output=True,
        timeout=300,
        shell=False,
    )
    if decode.returncode != 0:
        raise QualificationError("Final qualification video failed a complete decode")
    return {
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
        "codec": stream.get("codec_name"),
        "width": stream.get("width"),
        "height": stream.get("height"),
        "fps": rate,
        "frames": stream.get("nb_frames"),
        "durationSeconds": float(probe.get("format", {}).get("duration", 0.0)),
        "decodePassed": True,
    }


def required_new_paths(paths: list[Path]) -> None:
    existing = [str(path) for path in paths if path.exists()]
    if existing:
        raise QualificationError(
            "Qualification refuses to overwrite existing artifacts; preserve them and use a fresh output tree: "
            + ", ".join(existing)
        )


def versioned_path(path: Path) -> Path:
    if not path.exists():
        return path
    for attempt in range(1, 1000):
        candidate = path.with_name(f"{path.stem}.retry-{attempt:03d}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise QualificationError(f"No free retry identity is available for {path.name}")


def versioned_directory(path: Path) -> Path:
    if not path.exists():
        return path
    for attempt in range(1, 1000):
        candidate = path.with_name(f"{path.name}-retry-{attempt:03d}")
        if not candidate.exists():
            return candidate
    raise QualificationError(f"No free retry identity is available for {path.name}")


def resumable_sequence_directory(path: Path, expected_count: int) -> tuple[Path, bool]:
    candidates = [path, *sorted(path.parent.glob(f"{path.name}-retry-*"))]
    complete = [
        candidate
        for candidate in candidates
        if candidate.is_dir() and len(list(candidate.glob("*.png"))) == expected_count
    ]
    if complete:
        return complete[-1], True
    return versioned_directory(path), False


async def qualify(arguments: argparse.Namespace) -> dict[str, Any]:
    repository_root = Path(arguments.repository_root).resolve()
    project_root = repository_root / "video-projects" / "local" / PROJECT_ID
    output_root = project_root / "outputs" / "qualification"
    raw_frames = output_root / "wan-q5-raw-frames"
    delivery_frames = round((WAN_FRAMES - 1) * DELIVERY_FPS / WAN_FPS) + 1
    interpolated_frames, interpolated_frames_complete = resumable_sequence_directory(
        output_root / "wan-q5-interpolated-frames", delivery_frames
    )
    upscaled_frames, upscaled_frames_complete = resumable_sequence_directory(
        output_root / "wan-q5-upscaled-frames", delivery_frames
    )
    flux_path = output_root / "flux-keyframe.png"
    raw_video = output_root / "wan-q5-raw.mp4"
    interpolated_video = output_root / "wan-q5-interpolated.mp4"
    upscaled_video = output_root / "wan-q5-upscaled.mp4"
    final_video = output_root / "qualification-1080p24.mp4"
    report_path = output_root / "qualification-run.json"
    actual_flux_workflow = output_root / "flux-workflow.actual.json"
    if arguments.resume_wan_prompt_id:
        if arguments.resume_wan_workflow:
            actual_wan_workflow = Path(arguments.resume_wan_workflow).resolve()
        else:
            candidates = sorted(output_root.glob("wan-q5-workflow*.json"))
            if not candidates:
                raise QualificationError("No preserved Wan workflow exists for the requested resume")
            successful_candidates = []
            for candidate in candidates:
                try:
                    value = json.loads(candidate.read_text(encoding="utf-8"))
                except (OSError, UnicodeError, json.JSONDecodeError):
                    continue
                if value.get("vae", {}).get("inputs", {}).get("vae_name") == WAN_VAE:
                    successful_candidates.append(candidate)
            if not successful_candidates:
                raise QualificationError("No A14B-compatible preserved Wan workflow exists for resume")
            actual_wan_workflow = successful_candidates[-1]
    else:
        actual_wan_workflow = versioned_path(output_root / "wan-q5-workflow.actual.json")
    resume_flux = flux_path.is_file()
    resume_wan_outputs = raw_video.is_file() and len(list(raw_frames.glob("*.png"))) == WAN_FRAMES
    resume_interpolated = interpolated_frames_complete and interpolated_video.is_file()
    resume_upscaled = upscaled_frames_complete and upscaled_video.is_file()
    required_new_paths(
        [
            *([] if resume_wan_outputs else [raw_frames]),
            *([] if resume_interpolated else [interpolated_frames]),
            *([] if resume_upscaled else [upscaled_frames]),
            *([] if resume_wan_outputs else [raw_video]),
            *([] if resume_interpolated else [interpolated_video]),
            *([] if resume_upscaled else [upscaled_video]),
            final_video,
            report_path,
            *([] if arguments.resume_wan_prompt_id else [actual_wan_workflow]),
        ]
    )
    output_root.mkdir(parents=True, exist_ok=True)
    started_ns = time.time_ns()
    started_at = utc_now()

    prompt_text = (project_root / "prompts" / "individual" / "shot-001.txt").read_text(encoding="utf-8")
    keyframe_prompt = extract_section(prompt_text, "KEYFRAME PROMPT", "WAN2.2 I2V MOTION PROMPT")
    motion_prompt = extract_section(prompt_text, "WAN2.2 I2V MOTION PROMPT", "NEGATIVE PROMPT")
    negative_prompt = (project_root / "prompts" / "global-negative.txt").read_text(encoding="utf-8").strip()
    if not negative_prompt:
        raise QualificationError("Project negative prompt is empty")

    tools_lock = json.loads(
        (Path(arguments.comfyui_root).resolve() / "trackprompt-local-generation-tools-lock.json").read_text(
            encoding="utf-8-sig"
        )
    )
    rife = Path(tools_lock["rife"]["executable"]).resolve()
    realesrgan = Path(tools_lock["realesrgan"]["executable"]).resolve()
    rife_model = rife.parent / "rife-v4.6"
    realesrgan_models = realesrgan.parent / "models"
    ffmpeg = Path(arguments.ffmpeg).resolve()
    ffprobe = Path(arguments.ffprobe).resolve()
    for required in (rife, realesrgan, ffmpeg, ffprobe):
        if not required.is_file():
            raise QualificationError(f"Required executable is unavailable: {required.name}")
    if not rife_model.is_dir() or not realesrgan_models.is_dir():
        raise QualificationError("Pinned post-processing model directory is unavailable")

    timeout = aiohttp.ClientTimeout(total=None, connect=10, sock_connect=10, sock_read=None)
    api = ComfyApi(arguments.comfyui_url)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        stats = await api.preflight(session)
        if resume_flux:
            flux_prompt_id = "preserved-successful-flux"
            flux_seconds = float(arguments.resume_flux_seconds)
            flux_measurements = Measurements(
                peak_vram_bytes=round(float(arguments.resume_flux_peak_vram_gib) * 2**30),
                peak_ram_bytes=round(float(arguments.resume_flux_peak_ram_gib) * 2**30),
            )
            flux_qc = validate_image(flux_path, width=WIDTH, height=HEIGHT)
            print(f"FLUX: reusing validated successful keyframe -> {flux_path}", flush=True)
        else:
            flux = flux_workflow(keyframe_prompt, negative_prompt, "trackprompt/qualification/flux-keyframe")
            atomic_json(actual_flux_workflow, flux)
            flux_prompt_id, flux_history, flux_seconds, flux_measurements = await api.run_workflow(
                session,
                flux,
                label="FLUX",
                timeout_seconds=1_800,
            )
            flux_items = output_items(flux_history, {".png", ".jpg", ".jpeg", ".webp"})
            if len(flux_items) != 1:
                raise QualificationError(f"FLUX produced {len(flux_items)} image outputs; expected one")
            await api.download_item(session, flux_items[0], flux_path)
            flux_qc = validate_image(flux_path, width=WIDTH, height=HEIGHT)
            print(f"FLUX: qualified in {flux_seconds:.1f}s -> {flux_path}", flush=True)

        if arguments.resume_wan_prompt_id:
            history = await api.get_json(session, f"/history/{arguments.resume_wan_prompt_id}")
            wan_history = history.get(arguments.resume_wan_prompt_id)
            if not isinstance(wan_history, dict):
                raise QualificationError("The requested successful Wan history record is unavailable")
            status = wan_history.get("status")
            if not isinstance(status, dict) or status.get("completed") is not True or status.get("status_str") != "success":
                raise QualificationError("The requested Wan history record is not a successful completed run")
            wan_prompt_id = arguments.resume_wan_prompt_id
            wan_seconds = float(arguments.resume_wan_seconds)
            wan_measurements = Measurements(
                peak_vram_bytes=round(float(arguments.resume_wan_peak_vram_gib) * 2**30),
                peak_ram_bytes=round(float(arguments.resume_wan_peak_ram_gib) * 2**30),
            )
            print(f"Wan2.2 Q5: reusing successful prompt {wan_prompt_id}", flush=True)
        else:
            upload_name = await api.upload_image(session, flux_path)
            wan = wan_workflow(
                image_name=upload_name,
                positive=motion_prompt,
                negative=negative_prompt,
                output_prefix="trackprompt/qualification/wan-q5",
            )
            atomic_json(actual_wan_workflow, wan)
            wan_prompt_id, wan_history, wan_seconds, wan_measurements = await api.run_workflow(
                session,
                wan,
                label="Wan2.2 Q5",
                timeout_seconds=7_200,
            )
        if resume_wan_outputs:
            print(f"Wan2.2 Q5: reusing {WAN_FRAMES} downloaded frames and raw video", flush=True)
        else:
            frame_items = output_items(wan_history, {".png", ".jpg", ".jpeg", ".webp"})
            if len(frame_items) != WAN_FRAMES:
                raise QualificationError(f"Wan produced {len(frame_items)} frames; expected {WAN_FRAMES}")
            raw_frames.mkdir(parents=True, exist_ok=False)
            for index, item in enumerate(frame_items):
                await api.download_item(session, item, raw_frames / f"frame_{index:08d}.png")
            video_items = output_items(wan_history, {".mp4", ".webm", ".mov", ".mkv"})
            if video_items:
                await api.download_item(session, video_items[0], raw_video)

    if not raw_video.is_file():
        run_command(
            [
                str(ffmpeg),
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-framerate",
                str(WAN_FPS),
                "-i",
                str(raw_frames / "frame_%08d.png"),
                "-an",
                "-c:v",
                "libx264",
                "-crf",
                "16",
                "-preset",
                "slow",
                "-pix_fmt",
                "yuv420p",
                str(raw_video),
            ],
            timeout=900,
        )
    raw_probe = ffprobe_json(ffprobe, raw_video)
    raw_stream = video_stream(raw_probe)
    if raw_stream.get("width") != WIDTH or raw_stream.get("height") != HEIGHT:
        raise QualificationError("Raw Wan video has wrong dimensions")
    print(f"Wan2.2 Q5: qualified in {wan_seconds:.1f}s -> {raw_video}", flush=True)

    if resume_interpolated:
        print(f"RIFE: reusing validated {delivery_frames}-frame interpolation", flush=True)
    else:
        interpolated_frames.mkdir(parents=True, exist_ok=False)
        run_command(
            [
                str(rife),
                "-i",
                str(raw_frames),
                "-o",
                str(interpolated_frames),
                "-n",
                str(delivery_frames),
                "-m",
                str(rife_model),
                "-g",
                "0",
                "-f",
                "frame_%08d.png",
            ],
            timeout=3_600,
            cwd=rife.parent,
        )
        interpolated_count = len(list(interpolated_frames.glob("*.png")))
        if interpolated_count != delivery_frames:
            raise QualificationError(
                f"RIFE produced {interpolated_count} frames; expected {delivery_frames}"
            )
        run_command(
            [
                str(ffmpeg),
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-framerate",
                str(DELIVERY_FPS),
                "-i",
                str(interpolated_frames / "frame_%08d.png"),
                "-an",
                "-c:v",
                "libx264",
                "-crf",
                "16",
                "-preset",
                "slow",
                "-pix_fmt",
                "yuv420p",
                str(interpolated_video),
            ],
            timeout=900,
        )

    if resume_upscaled:
        print(f"Real-ESRGAN: reusing validated {delivery_frames}-frame upscale", flush=True)
    else:
        upscaled_frames.mkdir(parents=True, exist_ok=False)
        run_command(
            [
                str(realesrgan),
                "-i",
                str(interpolated_frames),
                "-o",
                str(upscaled_frames),
                "-s",
                "4",
                "-m",
                str(realesrgan_models),
                "-n",
                "realesrgan-x4plus-anime",
                "-g",
                "0",
                "-f",
                "png",
            ],
            timeout=3_600,
            cwd=realesrgan.parent,
        )
        upscaled_count = len(list(upscaled_frames.glob("*.png")))
        if upscaled_count != delivery_frames:
            raise QualificationError(
                f"Real-ESRGAN produced {upscaled_count} frames; expected {delivery_frames}"
            )
        run_command(
            [
                str(ffmpeg),
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-framerate",
                str(DELIVERY_FPS),
                "-i",
                str(upscaled_frames / "frame_%08d.png"),
                "-an",
                "-c:v",
                "libx264",
                "-crf",
                "16",
                "-preset",
                "slow",
                "-pix_fmt",
                "yuv420p",
                str(upscaled_video),
            ],
            timeout=900,
        )
    run_command(
        [
            str(ffmpeg),
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-framerate",
            str(DELIVERY_FPS),
            "-i",
            str(upscaled_frames / "frame_%08d.png"),
            "-vf",
            "scale=1920:1080:flags=lanczos,setsar=1",
            "-an",
            "-c:v",
            "libx264",
            "-crf",
            "16",
            "-preset",
            "slow",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(final_video),
        ],
        timeout=900,
    )
    final_qc = validate_final_video(ffmpeg, ffprobe, final_video, started_ns)

    report = {
        "schemaVersion": "1.0.0",
        "status": "LOCAL_ANIME_PIPELINE_READY",
        "startedAt": started_at,
        "completedAt": utc_now(),
        "provider": {
            "endpoint": arguments.comfyui_url,
            "version": stats.get("system", {}).get("comfyui_version"),
            "device": tools_lock.get("pytorch", {}).get("device"),
            "pytorch": tools_lock.get("pytorch", {}).get("torch"),
            "cuda": tools_lock.get("pytorch", {}).get("cuda"),
        },
        "flux": {
            "model": FLUX_MODEL,
            "seed": SEED,
            "width": WIDTH,
            "height": HEIGHT,
            "steps": 4,
            "promptId": flux_prompt_id,
            "elapsedSeconds": flux_seconds,
            "peakVramBytes": flux_measurements.peak_vram_bytes,
            "peakSystemMemoryBytes": flux_measurements.peak_ram_bytes,
            "qc": flux_qc,
        },
        "wan": {
            "tier": "A14B-Q5_K_M",
            "highModel": WAN_HIGH_Q5,
            "lowModel": WAN_LOW_Q5,
            "seed": SEED,
            "width": WIDTH,
            "height": HEIGHT,
            "frameCount": WAN_FRAMES,
            "fps": WAN_FPS,
            "steps": WAN_STEPS,
            "expertSplitStep": WAN_SPLIT_STEP,
            "cfg": 3.5,
            "sampler": "euler",
            "scheduler": "simple",
            "modelSamplingShift": 5.0,
            "promptId": wan_prompt_id,
            "elapsedSeconds": wan_seconds,
            "peakVramBytes": wan_measurements.peak_vram_bytes,
            "peakSystemMemoryBytes": wan_measurements.peak_ram_bytes,
            "offloading": "ComfyUI dynamic VRAM with async weight offloading (2 streams)",
            "oomRetry": False,
        },
        "post": {
            "rife": tools_lock["rife"],
            "rifeModel": "rife-v4.6",
            "interpolatedFrames": delivery_frames,
            "realesrgan": tools_lock["realesrgan"],
            "realesrganModel": "realesrgan-x4plus-anime",
            "final": final_qc,
        },
        "artifacts": {
            "fluxKeyframe": str(flux_path),
            "rawWanVideo": str(raw_video),
            "interpolatedVideo": str(interpolated_video),
            "upscaledVideo": str(upscaled_video),
            "finalVideo": str(final_video),
            "rawFrames": str(raw_frames),
            "interpolatedFrames": str(interpolated_frames),
            "upscaledFrames": str(upscaled_frames),
            "fluxWorkflow": str(actual_flux_workflow),
            "wanWorkflow": str(actual_wan_workflow),
        },
    }
    atomic_json(report_path, report)
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return report


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the real local FLUX -> Wan -> RIFE -> Real-ESRGAN qualification")
    parser.add_argument("--repository-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--comfyui-root", default=os.getenv("TRACKPROMPT_COMFYUI_ROOT", r"D:\TrackPrompt-ComfyUI"))
    parser.add_argument("--comfyui-url", default=os.getenv("TRACKPROMPT_COMFYUI_URL", "http://127.0.0.1:8188"))
    parser.add_argument("--ffmpeg", default=str(FFMPEG_DEFAULT))
    parser.add_argument("--ffprobe", default=str(FFMPEG_DEFAULT.with_name("ffprobe.exe")))
    parser.add_argument("--resume-flux-seconds", type=float, default=0.0)
    parser.add_argument("--resume-flux-peak-vram-gib", type=float, default=0.0)
    parser.add_argument("--resume-flux-peak-ram-gib", type=float, default=0.0)
    parser.add_argument("--resume-wan-prompt-id", default="")
    parser.add_argument("--resume-wan-workflow", default="")
    parser.add_argument("--resume-wan-seconds", type=float, default=0.0)
    parser.add_argument("--resume-wan-peak-vram-gib", type=float, default=0.0)
    parser.add_argument("--resume-wan-peak-ram-gib", type=float, default=0.0)
    return parser.parse_args()


def main() -> int:
    try:
        asyncio.run(qualify(parse_arguments()))
    except (QualificationError, OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"QUALIFICATION_FAILED: {exc}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
