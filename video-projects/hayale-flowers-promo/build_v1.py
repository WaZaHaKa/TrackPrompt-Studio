"""Build the reviewed Hayale edit with TrackPrompt's existing Resolve exporter.

This project adapter binds recorded assets to the existing resolved-timeline
contract. It never invokes a provider, changes source media, or claims a grade.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))
from app.subprocess_utils import run_process_bounded  # noqa: E402
from app.video_generation.exporter import export_davinci_package  # noqa: E402
from app.video_generation.jsonio import atomic_write_json, sha256_file, sha256_json  # noqa: E402

PROJECT = Path(__file__).resolve().parent
RUNTIME = REPO / ".trackprompt-data/video-generation/hayale-flowers-promo"
SOURCE = Path("D:/HayaleFlowers/promo-2026-09-15")


def run(command: list[str]) -> bytes:
    result = run_process_bounded(
        command, timeout_seconds=600, stdout_limit=2_000_000, stderr_limit=65_536
    )
    if result.returncode or result.stdout_exceeded or result.stderr_exceeded:
        raise RuntimeError(
            "Bounded render/verification failed; package is not verified"
        )
    return result.stdout


def validate_cut(cut: dict[str, Any], assets: dict[str, Any]) -> None:
    if cut.get("name") not in ("master", "hero"):
        raise ValueError("Cut name must be master or hero")
    for key, minimum, maximum in (
        ("fps", 1, 60),
        ("width", 64, 8192),
        ("height", 64, 8192),
    ):
        if type(cut.get(key)) is not int or not minimum <= cut[key] <= maximum:
            raise ValueError("Invalid timeline dimensions or frame rate")
    cursor = 0
    for event in cut["segments"]:
        asset = assets[event["assetId"]]
        if not asset["v1Eligible"] or asset["kind"] not in ("video", "still"):
            raise ValueError("V1 requires original video or JPEG stills")
        if asset["sha256"] != event["sourceSha256"]:
            raise ValueError("Select does not match inspected source identity")
        if event["inputKind"] != asset["kind"]:
            raise ValueError("Select has incorrect input kind")
        if (
            event["timelineStartFrames"] != cursor
            or event["durationFrames"] <= 0
            or event["sourceInFrames"] < 0
        ):
            raise ValueError("Invalid range, gap, or overlap")
        if asset["kind"] == "video":
            duration = float(asset["probe"]["format"]["duration"])
            if (event["sourceInFrames"] + event["durationFrames"]) / cut[
                "fps"
            ] > duration:
                raise ValueError("Select extends beyond the recorded video")
        elif event["sourceInFrames"] != 0:
            raise ValueError("A still must start at zero")
        cursor += event["durationFrames"]
    if cursor != cut["durationFrames"]:
        raise ValueError("Cut duration does not match events")


def resolved_cut(cut: dict[str, Any], assets: dict[str, Any]) -> dict[str, Any]:
    validate_cut(cut, assets)
    events = []
    markers = []
    for index, choice in enumerate(cut["segments"], 1):
        asset = assets[choice["assetId"]]
        path = SOURCE / asset["relativePath"]
        if (
            not path.resolve().is_relative_to(SOURCE.resolve())
            or path.is_symlink()
            or path.is_junction()
        ):
            raise ValueError("Source escapes immutable root")
        if sha256_file(path) != choice["sourceSha256"]:
            raise ValueError("Source changed since inspection")
        event = dict(
            choice,
            shotId=f"shot-{index:03d}",
            clipPath=str(path),
            transitionIn="cut",
            transitionOut="cut",
            treatment="original real media; centered aspect crop; no grade",
            playbackRate=1.0,
            cropScale=1.0,
            cropX=0.5,
            cropY=0.5,
        )
        events.append(event)
        markers.append(
            {
                "title": choice["chapterId"],
                "chapterId": choice["chapterId"],
                "startFrames": choice["timelineStartFrames"],
                "durationFrames": choice["durationFrames"],
            }
        )
    base = "hayale-promo-v1" if cut["name"] == "master" else "hayale-hero-v1"
    return {
        "schemaVersion": "1.1.0",
        "projectId": "hayale-flowers-promo",
        "title": base,
        "timeline": {
            key: cut[key] for key in ("fps", "width", "height", "durationFrames")
        }
        | {"audioPath": None, "normalizeSdr": True},
        "segments": events,
        "markers": markers,
        "localEditDigest": sha256_json(cut),
        "sourceClipSha256": {e["shotId"]: e["sourceSha256"] for e in events},
        "editorial": {
            "exportFiles": {
                "fcpxml": base + ".fcpxml",
                "fcp7": base + ".xml",
                "edl": base + ".edl",
                "preview1080p": base + "-reference.mp4",
            },
            "coverage": {
                "requiredShotIds": [e["shotId"] for e in events],
                "eventCountRange": [len(events), len(events)],
            },
            "handoff": {
                "title": base,
                "summary": "Ungraded V1 reference. Review framing, motion and input color in Resolve. Customer faces and source audio omitted. No final visual approval.",
            },
        },
    }


def verify_package(directory: Path, ffmpeg: str, ffprobe: str) -> dict[str, Any]:
    if directory.is_symlink() or directory.is_junction():
        raise ValueError("Redirected package directory refused")
    edit = json.loads((directory / "edit-plan.json").read_text())
    timeline = edit["timeline"]
    preview = directory / edit["editorial"]["exportFiles"]["preview1080p"]
    probes = []
    event_hashes = []
    for event in edit["segments"]:
        path = Path(event["derivedMediaPath"])
        if not path.resolve().is_relative_to(directory.resolve()):
            raise ValueError("Derived event escapes package")
        metadata = json.loads(
            run(
                [
                    ffprobe,
                    "-v",
                    "error",
                    "-count_frames",
                    "-show_streams",
                    "-of",
                    "json",
                    str(path),
                ]
            )
        )
        streams = metadata["streams"]
        if (
            len(streams) != 1
            or streams[0]["codec_type"] != "video"
            or int(streams[0]["nb_read_frames"]) != event["durationFrames"]
        ):
            raise ValueError("Derived event has wrong duration or unexpected audio")
        probes.append(
            {
                "segmentId": event["segmentId"],
                "frames": int(streams[0]["nb_read_frames"]),
                "sha256": sha256_file(path),
            }
        )
        frame_output = run(
            [
                ffmpeg,
                "-v",
                "error",
                "-i",
                str(path),
                "-map",
                "0:v:0",
                "-f",
                "framemd5",
                "-",
            ]
        )
        event_hashes.extend(
            line.rsplit(b",", 1)[-1].strip()
            for line in frame_output.splitlines()
            if line and not line.startswith(b"#")
        )
    metadata = json.loads(
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
    video = metadata["streams"][0]
    expected = (
        timeline["width"],
        timeline["height"],
        timeline["durationFrames"],
        f"{timeline['fps']}/1",
    )
    actual = (
        video["width"],
        video["height"],
        int(video["nb_read_frames"]),
        video["avg_frame_rate"],
    )
    if actual != expected or len(metadata["streams"]) != 1:
        raise ValueError("Preview frame/aspect/audio contract failed")
    frame_output = run(
        [
            ffmpeg,
            "-v",
            "error",
            "-i",
            str(preview),
            "-map",
            "0:v:0",
            "-f",
            "framemd5",
            "-",
        ]
    )
    preview_hashes = [
        line.rsplit(b",", 1)[-1].strip()
        for line in frame_output.splitlines()
        if line and not line.startswith(b"#")
    ]
    if preview_hashes != event_hashes:
        raise ValueError("Preview does not match the decoded edit-event sequence")
    references = []
    for extension in ("fcpxml", "xml"):
        path = next(directory.glob(f"*.{extension}"))
        root = ET.parse(path).getroot()
        urls = [element.attrib["src"] for element in root.findall(".//media-rep")]
        urls += [element.text for element in root.findall(".//pathurl")]
        for uri in urls:
            decoded = unquote(urlparse(uri).path)
            local = Path(
                decoded[1:] if len(decoded) > 2 and decoded[2] == ":" else decoded
            )
            if not local.is_file() or not local.resolve().is_relative_to(
                directory.resolve()
            ):
                raise ValueError("XML references missing or external derived media")
            references.append(local.name)
    coverage = json.loads((directory / "coverage-report.json").read_text())
    if not coverage["continuous"] or not coverage["allRequiredShotsUsed"]:
        raise ValueError("Timeline coverage failed")
    edl_events = [
        line
        for line in next(directory.glob("*.edl")).read_text().splitlines()
        if line[:3].isdigit()
    ]
    if len(edl_events) != len(edit["segments"]):
        raise ValueError("EDL event count mismatch")
    report = {
        "status": "technical-pass-operator-review-required",
        "xmlSyntax": "pass",
        "mediaReferences": "pass",
        "timelineCoverage": "pass",
        "previewMatchesEventDecodedFrames": "pass",
        "decodedFrames": len(preview_hashes),
        "timeline": timeline,
        "previewSha256": sha256_file(preview),
        "events": probes,
        "xmlReferenceCount": len(references),
        "edlEventCount": len(edl_events),
        "resolveImport": "not-run",
        "motionReview": "operator-required",
        "finalGrade": "not-applied",
        "finalVisualApproval": "not-granted",
        "previewColorMetadata": {
            k: video.get(k, "unknown")
            for k in ("color_space", "color_primaries", "color_transfer", "color_range")
        },
    }
    atomic_write_json(directory / "verification-report.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--ffprobe", default="ffprobe")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--render", action="store_true", help="Explicitly create video locally"
    )
    modes.add_argument("--verify-only", action="store_true")
    parser.add_argument(
        "--run-id", default="v1", help="Use a new v1-NAME to preserve prior runs"
    )
    args = parser.parse_args()
    if not re.fullmatch(r"v1(?:-[a-zA-Z0-9-]{1,80})?", args.run_id):
        raise ValueError(
            "Run ID must be v1 or v1-NAME using letters, numbers and hyphens"
        )
    destination = RUNTIME / args.run_id
    for ancestor in (
        REPO / ".trackprompt-data",
        RUNTIME.parent,
        RUNTIME,
        destination,
    ):
        if ancestor.is_symlink() or ancestor.is_junction():
            raise ValueError("Redirected runtime root refused")
    inventory = json.loads((RUNTIME / "inspection/media-inventory.json").read_text())
    assets = {a["assetId"]: a for a in inventory["assets"]}
    decisions = json.loads((PROJECT / "edit-decisions.json").read_text())
    if sorted(cut.get("name", "") for cut in decisions["cuts"]) != ["hero", "master"]:
        raise ValueError("Exactly one master and one hero cut are required")
    values = [(cut, resolved_cut(cut, assets)) for cut in decisions["cuts"]]
    if not args.render and not args.verify_only:
        print(
            json.dumps(
                {
                    "mode": "plan-only",
                    "renderStarted": False,
                    "runId": args.run_id,
                    "cuts": [
                        {
                            "name": cut["name"],
                            "seconds": cut["durationFrames"] / cut["fps"],
                            "width": cut["width"],
                            "height": cut["height"],
                            "fps": cut["fps"],
                            "events": len(cut["segments"]),
                        }
                        for cut, _ in values
                    ],
                },
                indent=2,
            )
        )
        return
    if args.render:
        if destination.exists():
            raise ValueError("V1 already exists; preserve it and use --verify-only")
        destination.mkdir()
        for cut, value in values:
            directory = destination / cut["name"]
            export_davinci_package(value, output_root=directory, ffmpeg=args.ffmpeg)
            shutil.copyfile(PROJECT / "grade-plan.json", directory / "grade-plan.json")
            assembly = json.loads((directory / "assembly-plan.json").read_text())
            for index, command in enumerate(assembly["commands"], 1):
                print(
                    f"{cut['name']}: render step {index}/{len(assembly['commands'])}",
                    flush=True,
                )
                run(command)
            manifest = json.loads((directory / "render-manifest.json").read_text())
            manifest["status"] = "reference-rendered-awaiting-verification"
            atomic_write_json(directory / "render-manifest.json", manifest)
    reports = []
    for cut in decisions["cuts"]:
        validate_cut(cut, assets)
        directory = destination / cut["name"]
        if (
            directory.is_symlink()
            or directory.is_junction()
            or not directory.resolve().is_relative_to(destination.resolve())
        ):
            raise ValueError("Redirected cut directory refused")
        stored_edit = json.loads(
            (destination / cut["name"] / "edit-plan.json").read_text()
        )
        if stored_edit["localEditDigest"] != sha256_json(cut):
            raise ValueError("Package does not match the current edit decisions")
        reports.append(
            verify_package(destination / cut["name"], args.ffmpeg, args.ffprobe)
        )
    changed = []
    current_paths = {
        p.relative_to(SOURCE).as_posix() for p in SOURCE.rglob("*") if p.is_file()
    }
    expected_paths = {a["relativePath"] for a in inventory["assets"]}
    if current_paths != expected_paths:
        raise ValueError("Source file set changed")
    for asset in inventory["assets"]:
        path = SOURCE / asset["relativePath"]
        stat = path.stat()
        if (
            stat.st_size != asset["bytes"]
            or stat.st_mtime_ns != asset["mtimeNs"]
            or sha256_file(path) != asset["sha256"]
        ):
            changed.append(asset["assetId"])
    if changed:
        raise ValueError("Source-original preservation check failed")
    report = {
        "status": "technical-pass-operator-review-required",
        "sourceOriginals": "pass",
        "sourceFilesHashed": len(assets),
        "sourceBytes": sum(a["bytes"] for a in assets.values()),
        "sourceSetSizeMtimeAndSha256Unchanged": True,
        "cuts": reports,
        "enhancedStillsUsed": False,
        "providerCalls": 0,
        "approvedAudioAvailable": False,
    }
    atomic_write_json(destination / "verification-report.json", report)
    print(
        json.dumps(
            {
                "sourceFilesUnchanged": len(assets),
                "cuts": [r["decodedFrames"] for r in reports],
            }
        )
    )


if __name__ == "__main__":
    main()
