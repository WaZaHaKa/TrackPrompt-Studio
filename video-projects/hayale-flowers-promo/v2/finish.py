"""Local-only finishing using the existing TrackPrompt Resolve/FFmpeg package."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from plan import FPS, PREPARATION, TOTAL_FRAMES, edit_plan

from app.subprocess_utils import run_process_bounded
from app.video_generation.exporter import export_davinci_package
from app.video_generation.jsonio import atomic_write_json, sha256_file, sha256_json


def run(command: list[str], *, timeout: int = 600) -> bytes:
    result = run_process_bounded(
        command, timeout_seconds=timeout, stdout_limit=2_000_000, stderr_limit=65_536
    )
    if result.returncode or result.stdout_exceeded or result.stderr_exceeded:
        raise RuntimeError(
            "Local finishing command failed; inspect retained output before retrying"
        )
    return result.stdout


def logo_command(ffmpeg: str, clip: Path, plate: Path, destination: Path) -> list[str]:
    return [
        ffmpeg,
        "-nostdin",
        "-v",
        "error",
        "-n",
        "-i",
        str(clip),
        "-loop",
        "1",
        "-framerate",
        "24",
        "-t",
        "8",
        "-i",
        str(plate),
        "-filter_complex",
        "[0:v]fps=24,scale=1920:1080:out_range=tv:out_color_matrix=bt709,setsar=1[base];[1:v]format=rgba,fade=t=in:st=3.5:d=0.5:alpha=1[lock];[base][lock]overlay=shortest=1,format=yuv420p,setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709[v]",
        "-map",
        "[v]",
        "-t",
        "8",
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "16",
        "-movflags",
        "+faststart",
        str(destination),
    ]


def audio_command(ffmpeg: str, source: Path, destination: Path) -> list[str]:
    return [
        ffmpeg,
        "-nostdin",
        "-v",
        "error",
        "-n",
        "-i",
        str(source),
        "-map",
        "0:a:0",
        "-vn",
        "-af",
        "apad,atrim=duration=60",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-c:a",
        "pcm_s24le",
        "-map_metadata",
        "-1",
        str(destination),
    ]


def verify_interchange(output: Path, value: dict[str, Any]) -> None:
    fcpxml = ET.parse(output / "hayale-promo-v2.fcpxml").getroot()
    fcp7 = ET.parse(output / "hayale-promo-v2.xml").getroot()
    sequence = fcpxml.find(".//sequence")
    if (
        sequence is None
        or Fraction(sequence.attrib["duration"].removesuffix("s")) != 60
    ):
        raise ValueError("FCPXML duration is not 60 seconds")
    if fcp7.findtext(".//sequence/duration") != "1440":
        raise ValueError("FCP7 duration does not match the edit")
    clips = fcpxml.findall(".//asset-clip[@lane='1']")
    if len(clips) != 8:
        raise ValueError("FCPXML event count is incorrect")
    for clip, event in zip(clips, value["segments"], strict=True):
        if (
            Fraction(clip.attrib["offset"].removesuffix("s")) * 24
            != event["timelineStartFrames"]
            or Fraction(clip.attrib["duration"].removesuffix("s")) * 24
            != event["durationFrames"]
        ):
            raise ValueError("FCPXML event timing differs from the edit")
    for root in (fcpxml, fcp7):
        uris = [element.attrib["src"] for element in root.findall(".//media-rep")]
        uris += [element.text for element in root.findall(".//pathurl")]
        for uri in uris:
            raw = unquote(urlparse(uri).path)
            path = Path(raw[1:] if len(raw) > 2 and raw[2] == ":" else raw)
            if not path.is_file() or not path.resolve().is_relative_to(
                output.resolve()
            ):
                raise ValueError(
                    "Interchange references missing or external derived media"
                )
    events = [
        line
        for line in (output / "hayale-promo-v2.edl").read_text().splitlines()
        if line[:3].isdigit()
    ]
    if len(events) != 8 or not events[-1].endswith("00:01:00:00"):
        raise ValueError("EDL event count or final timecode is incorrect")


def finish_package(
    *,
    output: Path,
    clips: dict[str, Path],
    cues: dict[str, Any],
    provider_digest: str,
    ffmpeg: str,
    ffprobe: str,
    logo_sha256: str,
    preparation: Path = PREPARATION,
) -> dict[str, Any]:
    if output.exists():
        raise ValueError("Output already exists; prior deliveries must be preserved")
    if set(clips) != {f"shot-{n:03d}" for n in range(1, 8)}:
        raise ValueError("All seven verified unique clips are required")
    plate = preparation / "references/scene-07.png"
    if sha256_file(plate) != logo_sha256:
        raise ValueError("The prepared logo plate changed after plan authorization")
    output.mkdir(parents=True)
    raw_hashes = {key: sha256_file(path) for key, path in clips.items()}
    value = edit_plan(cues["onsets"])
    audio = Path(cues["audioPath"]) if cues.get("audioPath") else None
    if audio is not None:
        if sha256_file(audio) != cues["sourceSha256"]:
            raise ValueError("Audio changed since cue preparation")
        master = output / "promo-audio-60s.wav"
        run(audio_command(ffmpeg, audio, master))
        value["timeline"]["audioPath"] = str(master)
    logo = output / "logo-bookend-locked.mp4"
    run(logo_command(ffmpeg, clips["shot-007"], plate, logo))
    local_clips = {**clips, "shot-007": logo}
    for event in value["segments"]:
        event["clipPath"] = str(local_clips[event["shotId"]])
    value["providerPlanDigest"] = provider_digest
    value["sourceClipSha256"] = raw_hashes
    value["localEditDigest"] = sha256_json(
        {
            "edit": value,
            "audio": cues.get("sourceSha256"),
            "logoPlate": sha256_file(plate),
            "finishingVersion": "hayale-v2-1",
        }
    )
    exports = export_davinci_package(value, output_root=output, ffmpeg=ffmpeg)
    assembly = json.loads(Path(exports["assemblyPlan"]).read_text())
    for index, command in enumerate(assembly["commands"], 1):
        print(f"Local assembly {index}/{len(assembly['commands'])}", flush=True)
        run(command)
    preview = Path(exports["previewOutput"])
    evidence = json.loads(
        run(
            [
                ffprobe,
                "-v",
                "error",
                "-count_frames",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                str(preview),
            ]
        )
    )
    video = [s for s in evidence["streams"] if s["codec_type"] == "video"]
    sound = [s for s in evidence["streams"] if s["codec_type"] == "audio"]
    if len(video) != 1 or (
        video[0]["width"],
        video[0]["height"],
        int(video[0]["nb_read_frames"]),
        video[0]["avg_frame_rate"],
    ) != (1920, 1080, TOTAL_FRAMES, f"{FPS}/1"):
        raise ValueError("Final video frame, dimensions or FPS check failed")
    if len(sound) != (1 if audio else 0) or (
        sound and (sound[0]["sample_rate"], sound[0]["channels"]) != ("48000", 2)
    ):
        raise ValueError("Final audio contract failed")
    if abs(float(evidence["format"]["duration"]) - 60) > 0.1:
        raise ValueError("Final container is not 60 seconds")
    run(
        [
            ffmpeg,
            "-nostdin",
            "-v",
            "error",
            "-xerror",
            "-i",
            str(preview),
            "-f",
            "null",
            "-",
        ],
        timeout=300,
    )
    coverage = json.loads(Path(exports["coverageReport"]).read_text())
    if not coverage["continuous"] or not coverage["allRequiredShotsUsed"]:
        raise ValueError("Timeline coverage failed")
    verify_interchange(output, value)
    if any(sha256_file(clips[key]) != digest for key, digest in raw_hashes.items()):
        raise ValueError("An immutable provider clip changed during finishing")
    if audio and sha256_file(audio) != cues["sourceSha256"]:
        raise ValueError("Original audio changed during finishing")
    if sha256_file(plate) != logo_sha256:
        raise ValueError("The prepared logo plate changed during finishing")
    report = {
        "status": "technical-pass-operator-review-required",
        "durationSeconds": 60,
        "frames": 1440,
        "fps": 24,
        "size": [1920, 1080],
        "audioPresent": bool(audio),
        "previewSha256": sha256_file(preview),
        "providerPlanDigest": provider_digest,
        "rawClipsUnchanged": True,
        "sourceAudioUnchanged": True,
        "logoGenerationCount": 1,
        "logoTimelineUses": 2,
        "logoTreatment": "generated animation resolves to the unchanged supplied logo between 3.5 and 4 seconds",
        "creativeReview": "pending",
        "resolveImport": "not-run",
        "syncMethod": value["syncMethod"],
    }
    atomic_write_json(output / "verification-report.json", report)
    atomic_write_json(
        output / "source-provenance.json",
        {
            "rawClips": {key: str(path) for key, path in clips.items()},
            "rawClipSha256": raw_hashes,
            "logoRawClip": str(clips["shot-007"]),
            "logoFinishingPlate": str(plate),
            "logoDerivedClip": str(logo),
            "musicCues": cues,
        },
    )
    atomic_write_json(
        output / "grade-plan.json",
        {
            "status": "operator-review-required",
            "look": "warm natural daylight, clean ivory whites, natural greens and burgundy/blush/gold brand accents",
            "perShot": "match exposure, white balance and saturation between store tours and floral macros; inspect AI identity drift",
            "bakedFinalGrade": False,
        },
    )
    manifest = json.loads((output / "render-manifest.json").read_text())
    manifest["status"] = report["status"]
    atomic_write_json(output / "render-manifest.json", manifest)
    return report
