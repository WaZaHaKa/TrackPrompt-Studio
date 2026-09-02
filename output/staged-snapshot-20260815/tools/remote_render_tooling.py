from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Sequence

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from tools.final_render_tooling import (  # noqa: E402
    ToolingError,
    _validate_exr,
    _validate_png,
    commit_chunk,
    load_render_profile,
    sha256_file,
)

PACKAGE_SCHEMA_VERSION = "1.0.0"
PACKAGE_KIND = "trackprompt-remote-render-package"
WORKER_KIND = "trackprompt-remote-worker-return"
_SENSITIVE_KEY_TERMS = (
    "lyric",
    "transcript",
    "prompt",
    "credential",
    "password",
    "secret",
    "authorizationtoken",
    "tokenhash",
    "modelpath",
    "sourcefilename",
    "sourcepath",
    "audiofile",
    "audiopath",
    "audiosha",
    "approvedaudio",
)
_ABSOLUTE_PATH = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\|/)")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _canonical_hash(payload: dict[str, Any]) -> str:
    content = {key: value for key, value in payload.items() if key not in {"integrity", "profileSha256"}}
    encoded = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest().upper()


def _scrub_private_value(value: Any) -> Any:
    if isinstance(value, dict):
        scrubbed: dict[str, Any] = {}
        for key, child in value.items():
            normalized = re.sub(r"[^a-z0-9]", "", str(key).casefold())
            if any(term in normalized for term in _SENSITIVE_KEY_TERMS):
                continue
            cleaned = _scrub_private_value(child)
            if cleaned is not None:
                scrubbed[str(key)] = cleaned
        return scrubbed
    if isinstance(value, list):
        return [cleaned for child in value if (cleaned := _scrub_private_value(child)) is not None]
    if isinstance(value, str) and _ABSOLUTE_PATH.match(value.strip()):
        return None
    return value


def _package_hash(manifest: dict[str, Any]) -> str:
    unsigned = {key: value for key, value in manifest.items() if key != "packageSha256"}
    encoded = json.dumps(unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest().upper()


def _safe_package_profile(source: Path, sanitized_scene_hash: str) -> dict[str, Any]:
    raw = _scrub_private_value(json.loads(source.read_text(encoding="utf-8-sig")))
    if not isinstance(raw, dict):
        raise ToolingError("invalid-profile", "Source profile must be a JSON object.")
    raw["id"] = f"remote-{uuid.uuid4()}".upper()
    raw["profileId"] = f"{raw.get('profileId', 'PROFILE')}-REMOTE-PACKAGE"
    raw["displayName"] = f"{raw.get('displayName', raw['profileId'])} - REMOTE PACKAGE"
    raw.pop("approvedScenePath", None)
    raw["approvedSceneSha256"] = sanitized_scene_hash
    approved = raw.setdefault("approvedScene", {})
    if isinstance(approved, dict):
        approved.update({"sha256": sanitized_scene_hash})
        approved.pop("path", None)
        approved.pop("manifestPath", None)
        approved.pop("manifestSha256", None)
    raw.pop("sceneManifestPath", None)
    raw["sourceIdentities"] = {"sourceProductionProfileSha256": sha256_file(source)}
    raw["audio"] = {"identityStatus": "excluded-from-remote-package"}
    output = raw.setdefault("output", {})
    if isinstance(output, dict):
        output["rootDirectory"] = "."
        output.pop("lastKnownFreeGiB", None)
    raw["authorization"] = {
        "status": "remote-package-only",
        "reason": "Remote workers render assigned frames only and cannot authorize local production.",
    }
    raw["remoteWorker"] = {
        "privateAudioIncluded": False,
        "encodingAllowed": False,
        "assignedRangesOnly": True,
        "sourceProductionProfileSha256": sha256_file(source),
    }
    raw["updatedAt"] = _now()
    if isinstance(raw.get("timestamps"), dict):
        raw["timestamps"]["updatedAt"] = raw["updatedAt"]
    raw["profileSha256"] = ""
    raw.setdefault("integrity", {})["profileSha256"] = ""
    profile_hash = _canonical_hash(raw)
    raw["profileSha256"] = profile_hash
    raw["integrity"]["profileSha256"] = profile_hash
    return raw


def generate_chunks(frame_start: int, frame_end: int, frames_per_chunk: int, workers: list[str]) -> list[dict[str, Any]]:
    if frame_start < 1 or frame_end < frame_start or frames_per_chunk < 1 or not workers:
        raise ToolingError("invalid-chunk-plan", "Chunk planning requires a valid range, size, and at least one worker.")
    result: list[dict[str, Any]] = []
    for index, start in enumerate(range(frame_start, frame_end + 1, frames_per_chunk)):
        end = min(frame_end, start + frames_per_chunk - 1)
        worker = workers[index % len(workers)]
        result.append(
            {
                "chunkId": f"chunk-{start:06d}-{end:06d}",
                "workerId": worker,
                "startFrame": start,
                "endFrame": end,
                "expectedFrameCount": end - start + 1,
                "leaseStatus": "unassigned",
                "returnArchiveName": f"chunk-{start:06d}-{end:06d}-{worker}.zip",
            }
        )
    return result


def _checksum_payload(root: Path, excluded: set[str]) -> dict[str, Any]:
    files = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        if relative in excluded:
            continue
        files.append({"path": relative, "sizeBytes": path.stat().st_size, "sha256": sha256_file(path)})
    return {"schemaVersion": PACKAGE_SCHEMA_VERSION, "kind": "trackprompt-remote-checksum-manifest", "files": files}


def create_package(
    sanitized_scene: Path,
    source_profile: Path,
    destination: Path,
    *,
    sanitization_report: Path | None,
    workers: int,
    frames_per_chunk: int,
    blender_version: str,
) -> dict[str, Any]:
    sanitized_scene = sanitized_scene.resolve(strict=True)
    source_profile = source_profile.resolve(strict=True)
    destination = destination.resolve()
    if workers < 1 or frames_per_chunk < 1:
        raise ToolingError("invalid-package-plan", "Remote package planning requires at least one worker and one frame per chunk.")
    if destination.exists() and any(destination.iterdir()):
        raise ToolingError("package-output-not-empty", "Remote package destination must be new and empty.")
    destination.mkdir(parents=True, exist_ok=True)
    scene_target = destination / "scene" / "trackprompt-remote.blend"
    scene_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(sanitized_scene, scene_target)
    scene_hash = sha256_file(scene_target)
    profile_payload = _safe_package_profile(source_profile, scene_hash)
    profile_target = destination / "profile" / "render-profile.remote.json"
    _atomic_json(profile_target, profile_payload)
    profile = load_render_profile(profile_target)
    if profile.approved_scene_sha256 != scene_hash:
        raise ToolingError("profile-scene-mismatch", "Sanitized package profile does not bind the sanitized scene.")
    source_scene_hash: str | None = None
    if sanitization_report is not None:
        report_payload = json.loads(sanitization_report.resolve(strict=True).read_text(encoding="utf-8-sig"))
        if report_payload.get("sanitizedSceneSha256") != scene_hash or report_payload.get("privateAudioIncluded") is not False:
            raise ToolingError("invalid-sanitization-report", "Sanitization report does not match the audio-free scene.")
        source_scene_hash = str(report_payload.get("sourceSceneSha256"))
        shutil.copy2(sanitization_report, destination / "sanitization-report.json")
    for source, relative in (
        (REPOSITORY_ROOT / "render_trackprompt_worker.py", "render_trackprompt_worker.py"),
        (REPOSITORY_ROOT / "render-trackprompt-worker.ps1", "render-trackprompt-worker.ps1"),
        (REPOSITORY_ROOT / "blender" / "render_remote_chunk.py", "blender/render_remote_chunk.py"),
        (REPOSITORY_ROOT / "blender" / "render_final_chunk.py", "blender/render_final_chunk.py"),
        (REPOSITORY_ROOT / "tools" / "final_render_tooling.py", "tools/final_render_tooling.py"),
        (REPOSITORY_ROOT / "tools" / "remote_render_tooling.py", "tools/remote_render_tooling.py"),
    ):
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    for init_path in (destination / "blender" / "__init__.py", destination / "tools" / "__init__.py"):
        init_path.write_text("", encoding="utf-8")
    worker_ids = [f"remote-{index:02d}" for index in range(1, workers + 1)]
    chunks = generate_chunks(profile.frame_start, profile.frame_end, frames_per_chunk, worker_ids)
    for chunk in chunks:
        chunk.update(
            {
                "sceneSha256": scene_hash,
                "profileSha256": sha256_file(profile_target),
                "outputFormat": profile.image.format,
                "bitDepth": profile.image.bit_depth,
            }
        )
    _atomic_json(destination / "chunk-manifest.json", {"schemaVersion": PACKAGE_SCHEMA_VERSION, "kind": "trackprompt-remote-chunk-plan", "chunks": chunks})
    (destination / "WORKER-INSTRUCTIONS.md").write_text(
        "# TrackPrompt remote worker\n\n"
        "This package contains no private source audio. Run only an assigned chunk. Do not encode or upload results automatically.\n\n"
        "Windows: `powershell -NoProfile -ExecutionPolicy Bypass -File .\\render-trackprompt-worker.ps1 -PackageDirectory . -ChunkId chunk-000001-000150`\n",
        encoding="utf-8",
    )
    checksum_path = destination / "checksum-manifest.json"
    _atomic_json(checksum_path, _checksum_payload(destination, {"checksum-manifest.json", "package-manifest.json"}))
    package_manifest = {
        "schemaVersion": PACKAGE_SCHEMA_VERSION,
        "kind": PACKAGE_KIND,
        "packageId": f"pkg-{scene_hash[:12].lower()}-{sha256_file(profile_target)[:12].lower()}",
        "createdAt": _now(),
        "blenderVersion": blender_version.replace("Blender ", "").strip(),
        "scene": {"relativePath": "scene/trackprompt-remote.blend", "sha256": scene_hash, "sourceSceneSha256": source_scene_hash},
        "profile": {
            "relativePath": "profile/render-profile.remote.json",
            "sha256": sha256_file(profile_target),
            "sourceProductionProfileSha256": sha256_file(source_profile),
        },
        "packageSha256": "",
        "checksumManifest": "checksum-manifest.json",
        "checksumSha256": sha256_file(checksum_path),
        "frameContract": {
            "frameStart": profile.frame_start,
            "frameEnd": profile.frame_end,
            "frameCount": profile.frame_count,
            "fps": profile.fps,
            "width": profile.width,
            "height": profile.height,
            "format": profile.image.format,
            "bitDepth": profile.image.bit_depth,
            "colorMode": profile.image.color_mode,
            "filenamePattern": profile.image.filename_pattern,
        },
        "colorManagement": profile.color_management,
        "privateAudioIncluded": False,
        "networkUploadAuthorized": False,
        "encodingAllowed": False,
        "deterministicSeed": profile.raw.get("render", {}).get("seed", 0),
        "compositorRequirements": profile.raw.get("compositor", {"enabled": profile.raw.get("render", {}).get("useCompositing", True)}),
        "renderCommand": "render-trackprompt-worker.ps1 -PackageDirectory . -ChunkId <assigned-chunk-id>",
    }
    package_manifest["packageSha256"] = _package_hash(package_manifest)
    _atomic_json(destination / "package-manifest.json", package_manifest)
    return validate_package(destination)


def validate_package(root: Path) -> dict[str, Any]:
    root = root.resolve(strict=True)
    manifest = json.loads((root / "package-manifest.json").read_text(encoding="utf-8-sig"))
    if manifest.get("kind") != PACKAGE_KIND or manifest.get("schemaVersion") != PACKAGE_SCHEMA_VERSION:
        raise ToolingError("invalid-package", "Package manifest kind or schema is unsupported.")
    checksum_path = root / str(manifest.get("checksumManifest"))
    if sha256_file(checksum_path) != manifest.get("checksumSha256"):
        raise ToolingError("checksum-manifest-mismatch", "Checksum manifest identity does not match the package contract.")
    if _package_hash(manifest) != manifest.get("packageSha256"):
        raise ToolingError("package-hash-mismatch", "Package manifest identity does not match its package SHA-256.")
    checksums = json.loads(checksum_path.read_text(encoding="utf-8-sig"))
    issues: list[dict[str, Any]] = []
    for item in checksums.get("files", []):
        path = (root / str(item.get("path"))).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            issues.append({"code": "path-escape", "path": item.get("path")})
            continue
        if not path.is_file():
            issues.append({"code": "missing-file", "path": item.get("path")})
        elif path.stat().st_size != item.get("sizeBytes") or sha256_file(path) != item.get("sha256"):
            issues.append({"code": "file-identity-mismatch", "path": item.get("path")})
    expected_paths = {str(item.get("path")) for item in checksums.get("files", [])}
    expected_paths.update({"checksum-manifest.json", "package-manifest.json"})
    actual_paths = {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}
    for unexpected in sorted(actual_paths - expected_paths):
        issues.append({"code": "unexpected-package-file", "path": unexpected})
    scene = root / manifest["scene"]["relativePath"]
    profile = root / manifest["profile"]["relativePath"]
    if not scene.is_file() or sha256_file(scene) != manifest["scene"]["sha256"]:
        issues.append({"code": "scene-hash-mismatch"})
    if not profile.is_file() or sha256_file(profile) != manifest["profile"]["sha256"]:
        issues.append({"code": "profile-hash-mismatch"})
    forbidden_terms = (".wav", "lyrics", "transcript", "visual-cues", "analysis.json", "authorization.json")
    for path in (item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix().casefold()
        if any(term in relative for term in forbidden_terms):
            issues.append({"code": "private-artifact-present", "path": relative})
    return {"ok": not issues, "package": str(root), "packageId": manifest.get("packageId"), "packageSha256": manifest.get("packageSha256"), "issues": issues}


def validate_return(return_root: Path, package_root: Path) -> dict[str, Any]:
    package_result = validate_package(package_root)
    if not package_result["ok"]:
        raise ToolingError("invalid-package", "Source package failed validation.")
    package = json.loads((package_root / "package-manifest.json").read_text(encoding="utf-8-sig"))
    worker = json.loads((return_root / "worker-manifest.json").read_text(encoding="utf-8-sig"))
    issues: list[dict[str, Any]] = []
    for field, expected in (
        ("packageId", package["packageId"]),
        ("packageSha256", package["packageSha256"]),
        ("sceneSha256", package["scene"]["sha256"]),
        ("profileSha256", package["profile"]["sha256"]),
        ("blenderVersion", package["blenderVersion"]),
    ):
        if worker.get(field) != expected:
            issues.append({"code": f"{field}-mismatch"})
    contract = package["frameContract"]
    chunk_plan = json.loads((package_root / "chunk-manifest.json").read_text(encoding="utf-8-sig"))
    assignments = [item for item in chunk_plan.get("chunks", []) if item.get("chunkId") == worker.get("chunkId")]
    if len(assignments) != 1:
        issues.append({"code": "assignment-missing-or-ambiguous"})
    else:
        assignment = assignments[0]
        for field in ("startFrame", "endFrame", "expectedFrameCount"):
            if worker.get(field) != assignment.get(field):
                issues.append({"code": f"assignment-{field}-mismatch"})
    seen: set[int] = set()
    frames = worker.get("frames", [])
    if not isinstance(frames, list):
        frames = []
    for item in frames:
        frame = item.get("frame")
        if not isinstance(frame, int) or frame in seen:
            issues.append({"code": "duplicate-or-invalid-frame", "frame": frame})
            continue
        seen.add(frame)
        extension = "png" if contract["format"] == "PNG" else "exr"
        expected_name = f"frame_{frame:06d}.{extension}"
        if item.get("fileName") != expected_name:
            issues.append({"code": "noncanonical-frame-name", "frame": frame})
            continue
        path = return_root / "frames" / expected_name
        if not path.is_file() or path.stat().st_size == 0:
            issues.append({"code": "missing-or-empty-frame", "frame": frame})
            continue
        if sha256_file(path) != item.get("sha256"):
            issues.append({"code": "frame-hash-mismatch", "frame": frame})
            continue
        try:
            validator = _validate_png if contract["format"] == "PNG" else _validate_exr
            width, height, bit_depth, _, _ = validator(path)
            if (width, height, bit_depth) != (
                contract["width"],
                contract["height"],
                contract["bitDepth"],
            ):
                issues.append({"code": "frame-contract-mismatch", "frame": frame})
        except ToolingError:
            issues.append({"code": "corrupt-frame", "frame": frame})
    expected = set(range(int(worker.get("startFrame", 0)), int(worker.get("endFrame", -1)) + 1))
    if seen != expected:
        issues.append({"code": "missing-or-unexpected-frame-set", "missing": sorted(expected - seen), "unexpected": sorted(seen - expected)})
    listed_files = {str(item.get("fileName")) for item in frames if isinstance(item, dict)}
    actual_frame_files = {path.name for path in (return_root / "frames").iterdir() if path.is_file()} if (return_root / "frames").is_dir() else set()
    if actual_frame_files != listed_files:
        issues.append({"code": "unlisted-or-missing-return-file", "unlisted": sorted(actual_frame_files - listed_files), "missing": sorted(listed_files - actual_frame_files)})
    return {"ok": not issues, "workerId": worker.get("workerId"), "startFrame": worker.get("startFrame"), "endFrame": worker.get("endFrame"), "frameCount": len(seen), "issues": issues, "manifest": worker}


def import_return(
    returned: Path,
    package: Path,
    local_profile_path: Path,
    local_scene_path: Path,
    output: Path,
) -> dict[str, Any]:
    output = output.resolve(strict=True)
    quarantine = output / "qa" / "remote-quarantine" / f"return-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    quarantine.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(returned.resolve(strict=True), quarantine)
    validation = validate_return(quarantine, package.resolve(strict=True))
    package_manifest = json.loads((package / "package-manifest.json").read_text(encoding="utf-8-sig"))
    if sha256_file(local_profile_path.resolve(strict=True)) != package_manifest["profile"]["sourceProductionProfileSha256"]:
        validation["issues"].append({"code": "source-production-profile-mismatch"})
        validation["ok"] = False
    if not validation["ok"]:
        return {"ok": False, "published": [], "quarantine": str(quarantine), "validation": validation}
    local_profile = load_render_profile(local_profile_path)
    worker = validation["manifest"]
    existing: list[dict[str, Any]] = []
    missing: list[int] = []
    frames_directory = output / local_profile.frames_subdirectory
    for item in worker["frames"]:
        frame = int(item["frame"])
        destination = frames_directory / local_profile.image.filename(frame)
        if destination.exists():
            existing.append({"frame": frame, "sameSha256": sha256_file(destination) == item["sha256"]})
        else:
            missing.append(frame)
    published: list[int] = []
    ranges: list[tuple[int, int]] = []
    for frame in sorted(missing):
        if not ranges or frame != ranges[-1][1] + 1:
            ranges.append((frame, frame))
        else:
            ranges[-1] = (ranges[-1][0], frame)
    for start, end in ranges:
        temporary = output / "checkpoints" / f".inflight-remote-{start:06d}-{end:06d}-{uuid.uuid4().hex}" / "frames"
        temporary.mkdir(parents=True, exist_ok=False)
        for frame in range(start, end + 1):
            source = quarantine / "frames" / local_profile.image.filename(frame)
            shutil.copy2(source, temporary / source.name)
        result = commit_chunk(
            local_profile,
            local_scene_path,
            output,
            temporary,
            start=start,
            end=end,
            stdout_log=None,
            stderr_log=None,
            workers=4,
        )
        published.extend(result["publishedFrames"])
    audit = {
        "schemaVersion": PACKAGE_SCHEMA_VERSION,
        "kind": "trackprompt-remote-frame-import",
        "importedAt": _now(),
        "quarantine": str(quarantine),
        "publishedFrames": published,
        "existingFrames": existing,
        "policy": "keep-first-valid-frame; differing duplicates remain quarantined for operator review",
    }
    _atomic_json(quarantine / "import-audit.json", audit)
    return {"ok": True, "published": published, "existing": existing, "quarantine": str(quarantine), "validation": validation}


def estimate(seconds_per_frame: float, frame_count: int, workers: int, hourly_rate: float, per_frame: float, transfer_hours: float, storage_cost: float, egress_cost: float) -> dict[str, Any]:
    gpu_hours = seconds_per_frame * frame_count / 3600.0
    compute = gpu_hours * hourly_rate + frame_count * per_frame
    return {
        "ok": True,
        "totalGpuHours": gpu_hours,
        "expectedWallHours": gpu_hours / workers + transfer_hours,
        "conservativeWallHours": gpu_hours * 1.25 / workers + transfer_hours,
        "expectedCost": compute + storage_cost + egress_cost,
        "conservativeCost": compute * 1.25 + storage_cost + egress_cost,
        "confidence": "LOW",
        "providerBenchmarkRequired": True,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Provider-neutral TrackPrompt remote render packaging and import tooling.")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create-package")
    create.add_argument("--sanitized-scene", required=True)
    create.add_argument("--source-profile", required=True)
    create.add_argument("--sanitization-report")
    create.add_argument("--destination", required=True)
    create.add_argument("--workers", type=int, default=1)
    create.add_argument("--frames-per-chunk", type=int, default=150)
    create.add_argument("--blender-version", default="5.2.0 LTS")
    validate = sub.add_parser("validate-package")
    validate.add_argument("--package", required=True)
    plan = sub.add_parser("plan-chunks")
    plan.add_argument("--frame-start", type=int, default=1)
    plan.add_argument("--frame-end", type=int, default=13029)
    plan.add_argument("--frames-per-chunk", type=int, default=150)
    plan.add_argument("--workers", required=True)
    cost = sub.add_parser("estimate")
    cost.add_argument("--seconds-per-frame", required=True, type=float)
    cost.add_argument("--frame-count", default=13029, type=int)
    cost.add_argument("--workers", default=1, type=int)
    cost.add_argument("--hourly-rate", default=0.0, type=float)
    cost.add_argument("--per-frame-price", default=0.0, type=float)
    cost.add_argument("--transfer-hours", default=0.0, type=float)
    cost.add_argument("--storage-cost", default=0.0, type=float)
    cost.add_argument("--egress-cost", default=0.0, type=float)
    returned = sub.add_parser("validate-return")
    returned.add_argument("--return-directory", required=True)
    returned.add_argument("--package", required=True)
    importing = sub.add_parser("import-return")
    importing.add_argument("--return-directory", required=True)
    importing.add_argument("--package", required=True)
    importing.add_argument("--local-profile", required=True)
    importing.add_argument("--local-scene", required=True)
    importing.add_argument("--output", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "create-package":
            result = create_package(Path(args.sanitized_scene), Path(args.source_profile), Path(args.destination), sanitization_report=Path(args.sanitization_report) if args.sanitization_report else None, workers=args.workers, frames_per_chunk=args.frames_per_chunk, blender_version=args.blender_version)
        elif args.command == "validate-package":
            result = validate_package(Path(args.package))
        elif args.command == "plan-chunks":
            workers = [item.strip() for item in args.workers.split(",") if item.strip()]
            result = {"ok": True, "chunks": generate_chunks(args.frame_start, args.frame_end, args.frames_per_chunk, workers)}
        elif args.command == "estimate":
            result = estimate(args.seconds_per_frame, args.frame_count, args.workers, args.hourly_rate, args.per_frame_price, args.transfer_hours, args.storage_cost, args.egress_cost)
        elif args.command == "validate-return":
            result = validate_return(Path(args.return_directory), Path(args.package))
        elif args.command == "import-return":
            result = import_return(Path(args.return_directory), Path(args.package), Path(args.local_profile), Path(args.local_scene), Path(args.output))
        else:  # pragma: no cover
            raise ToolingError("unknown-command", "Unknown remote render command.")
        print(json.dumps(result, ensure_ascii=True, separators=(",", ":")))
        return 0 if result.get("ok") else 4
    except (OSError, KeyError, ValueError, json.JSONDecodeError, ToolingError) as exc:
        code = exc.code if isinstance(exc, ToolingError) else "remote-tooling-error"
        print(json.dumps({"ok": False, "error": {"code": code, "message": str(exc)[:500]}}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
