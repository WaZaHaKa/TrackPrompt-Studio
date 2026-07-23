from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import wave
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a three-frame TrackPrompt final-tooling vertical smoke test.")
    parser.add_argument("--blender", required=True)
    parser.add_argument("--ffmpeg")
    parser.add_argument("--ffprobe")
    parser.add_argument("--work-root", required=True)
    return parser.parse_args()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _absolute_command_path(value: str) -> Path:
    # Some WinGet package directories permit execution but deny Python's stat
    # and realpath traversal. The production PowerShell wrapper performs the
    # authoritative Test-Path/command validation before invoking either tool.
    return Path(value).absolute()


def _run(arguments: list[str], label: str) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        arguments,
        shell=False,
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"{label} failed with {completed.returncode}.\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}"
        )
    return completed


def _json_output(text: str) -> dict[str, object]:
    for index, character in enumerate(text):
        if character != "{":
            continue
        try:
            payload = json.loads(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise RuntimeError(f"Command output did not end in a JSON object:\n{text}")


def _write_audio(path: Path) -> None:
    sample_rate = 44_100
    frame_count = round(sample_rate * 0.1)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(b"\0\0\0\0" * frame_count)


def main() -> int:
    args = _arguments()
    repository = Path(__file__).resolve().parents[2]
    work_parent = Path(args.work_root).resolve(strict=True)
    run_root = work_parent / f"trackprompt-final-tooling-smoke-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
    source_root = run_root / "source"
    scene = source_root / "synthetic.blend"
    profile = source_root / "render-profile.final.json"
    audio = source_root / "synthetic.wav"
    output = run_root / "production"
    blender = Path(args.blender).resolve(strict=True)
    powershell = shutil.which("powershell.exe")
    if powershell is None:
        raise RuntimeError("Windows PowerShell is unavailable.")

    creator = repository / "blender" / "tests" / "create_final_render_smoke_scene.py"
    _run(
        [
            str(blender),
            "--background",
            "--python-exit-code",
            "1",
            "--python",
            str(creator),
            "--",
            "--output",
            str(scene),
        ],
        "Synthetic Blender-scene creation",
    )
    _write_audio(audio)
    scene_hash = _sha(scene)
    audio_hash = _sha(audio)
    payload = {
        "schemaVersion": "1.0.0",
        "project": "trip-to-andromeda",
        "preset": "space-journey",
        "profileId": "SYNTHETIC-SMOKE-30-SDR",
        "blenderVersion": "5.2.0 LTS",
        "frameStart": 1,
        "frameEnd": 3,
        "fps": 30,
        "resolution": {"width": 16, "height": 16, "percentage": 100},
        "imageSequence": {
            "format": "PNG",
            "extension": "png",
            "bitDepth": 16,
            "colorMode": "RGB",
            "compression": 15,
            "filenamePattern": "frame_######.png",
            "colorManagement": {"displayTransformBaked": True},
        },
        "approvedSceneSha256": scene_hash,
        "chunking": {"framesPerChunk": 2, "rationale": "Three-frame bounded integration fixture."},
        "storage": {
            "plannedFrameSequenceGiB": 0.001,
            "projectedMasterGiB": 0.001,
            "projectedDeliveryGiB": 0.001,
            "supportReserveGiB": 0.001,
            "contingencyMultiplier": 1.5,
            "minimumLaunchFreeGiB": 0.01,
        },
        "render": {
            "engine": "BLENDER_EEVEE",
            "samples": 1,
            "shadowPoolSize": "128",
            "motionBlur": False,
            "useCompositing": False,
            "filmTransparent": False,
            "ditherIntensity": 1.0,
        },
        "colorManagement": {
            "displayDevice": "sRGB",
            "viewTransform": "AgX",
            "look": "AgX - Medium High Contrast",
            "exposure": 0.0,
            "gamma": 1.0,
            "sequencerColorSpace": "sRGB",
        },
        "audio": {"sha256": audio_hash, "sampleRate": 44100, "channels": 2, "durationSeconds": 0.1},
        "encoding": {
            "delivery": {
                "container": "mp4",
                "fileExtension": ".mp4",
                "videoCodec": "libx264",
                "expectedVideoCodec": "h264",
                "profile": "high",
                "displayToDeliveryFilter": "colorspace=ispace=gbr:iprimaries=bt709:itrc=iec61966-2-1:irange=pc:space=bt709:primaries=bt709:trc=bt709:range=tv:format=yuv420p",
                "preset": "medium",
                "crf": 18,
                "pixelFormat": "yuv420p",
                "audioCodec": "aac",
                "audioBitrate": "192k",
                "color": {"primaries": "bt709", "transfer": "bt709", "space": "bt709", "range": "tv"},
            },
            "master": {
                "container": "mov",
                "fileExtension": ".mov",
                "videoCodec": "prores_ks",
                "expectedVideoCodec": "prores",
                "profile": "3",
                "displayToDeliveryFilter": "colorspace=ispace=gbr:iprimaries=bt709:itrc=iec61966-2-1:irange=pc:space=bt709:primaries=bt709:trc=bt709:range=tv:format=yuv422p10,format=yuv422p10le",
                "pixelFormat": "yuv422p10le",
                "audioCodec": "pcm_s24le",
                "color": {"primaries": "bt709", "transfer": "bt709", "space": "bt709", "range": "tv"},
            },
        },
        "visualQa": {"namedFrames": [{"frame": 1, "role": "opening"}, {"frame": 3, "role": "outro"}]},
    }
    profile.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    profile_hash = _sha(profile)
    token = (
        "AUTHORIZE FULL RENDER: TRIP TO ANDROMEDA | SPACE-JOURNEY | "
        f"SYNTHETIC-SMOKE-30-SDR | SCENE {scene_hash[:12]} | PROFILE {profile_hash[:12]}"
    )
    render_script = repository / "render-trackprompt-final.ps1"
    render_command = [
        powershell,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(render_script),
        "-ApprovedScenePath",
        str(scene),
        "-RenderProfilePath",
        str(profile),
        "-OutputDirectory",
        str(output),
        "-AuthorizationToken",
        token,
        "-BlenderExecutable",
        str(blender),
        "-PythonExecutable",
        sys.executable,
        "-FrameScanWorkers",
        "1",
    ]
    first_render = _run(render_command, "Three-frame authorized render")
    frames = output / "frames"
    rendered = sorted(path.name for path in frames.glob("frame_*.png"))
    if rendered != ["frame_000001.png", "frame_000002.png", "frame_000003.png"]:
        raise RuntimeError(f"Unexpected frame set: {rendered}")

    quarantine = output / "qa" / "resume-smoke-quarantine"
    quarantine.mkdir()
    (frames / "frame_000002.png").rename(quarantine / "frame_000002.png")
    dry_run = _run([*render_command, "-DryRun"], "Resume dry-run")
    dry_payload = _json_output(dry_run.stdout)
    if dry_payload["chunks"] != [{"startFrame": 2, "endFrame": 2, "frameCount": 1}]:
        raise RuntimeError(f"Resume plan was not limited to frame 2: {dry_payload['chunks']}")
    resumed = _run(render_command, "One-frame authorized resume")

    result: dict[str, object] = {
        "ok": True,
        "runRoot": str(run_root),
        "firstRender": _json_output(first_render.stdout),
        "resumePlan": dry_payload["chunks"],
        "resumed": _json_output(resumed.stdout),
        "encode": "skipped-no-explicit-ffmpeg",
    }
    if args.ffmpeg and args.ffprobe:
        ffmpeg = _absolute_command_path(args.ffmpeg)
        ffprobe = _absolute_command_path(args.ffprobe)
        delivery = output / "delivery" / "synthetic-final.mp4"
        progress = output / "logs" / "synthetic-delivery.progress.txt"
        encode_script = repository / "encode-trackprompt-final.ps1"
        encoded = _run(
            [
                powershell,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(encode_script),
                "-ApprovedScenePath",
                str(scene),
                "-RenderProfilePath",
                str(profile),
                "-ProductionDirectory",
                str(output),
                "-AudioPath",
                str(audio),
                "-OutputPath",
                str(delivery),
                "-OutputKind",
                "Delivery",
                "-FfmpegExecutable",
                str(ffmpeg),
                "-FfprobeExecutable",
                str(ffprobe),
                "-PythonExecutable",
                sys.executable,
                "-FrameScanWorkers",
                "1",
                "-ProgressPath",
                str(progress),
            ],
            "Tiny delivery encode",
        )
        progress_payload = progress.read_text(encoding="utf-8")
        if "progress=end" not in progress_payload or "frame=3" not in progress_payload:
            raise RuntimeError(f"Encode progress was not finalized: {progress_payload}")
        encode_payload = _json_output(encoded.stdout)
        verification_script = repository / "verify-trackprompt-final.ps1"
        verified = _run(
            [
                powershell,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(verification_script),
                "-ApprovedScenePath",
                str(scene),
                "-RenderProfilePath",
                str(profile),
                "-ProductionDirectory",
                str(output),
                "-MediaPath",
                str(delivery),
                "-AudioPath",
                str(audio),
                "-EncodeManifestPath",
                str(encode_payload["encodeManifest"]),
                "-OutputKind",
                "Delivery",
                "-FfprobeExecutable",
                str(ffprobe),
                "-PythonExecutable",
                sys.executable,
            ],
            "Tiny final verification",
        )
        result["encode"] = encode_payload
        result["verification"] = _json_output(verified.stdout)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
