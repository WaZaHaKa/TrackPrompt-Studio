#!/usr/bin/env python3
"""Read-only forensic discovery for TrackPrompt Andromeda V2 production artifacts.

The tool deliberately does not mutate release evidence, create human approval,
remove a release hold, authorize production, launch Blender, or encode media.
It discovers the newest real (non-fixture) artifacts, validates file/hash links,
and writes a compact upload bundle that can be reviewed without sharing audio,
Blender scenes, frames, or final media.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import inspect
import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")
AUDIO_SUFFIXES = {".wav", ".flac", ".mp3", ".aif", ".aiff", ".m4a", ".ogg"}

REQUIRED_BUNDLE_FILES = (
    "package-manifest-v2.json",
    "v2-calibration.json",
    "technical-authorization-v2.json",
    "evidence/release-report.json",
)

REQUIRED_ARTIFACT_ROLES = (
    "animatic-media-qa-report",
    "animatic-receipt",
    "builder-source",
    "dependency-health-report",
    "deterministic-effects-and-disk-report",
    "encoding-profiles",
    "exposure-mobile-readability-report",
    "final-look-profile",
    "final-quality-transition-report",
    "final-resolution-calibration-evidence",
    "final-scene",
    "final-scene-receipt",
    "full-audio-animatic",
    "gates-to-rupture-media",
    "hardware-and-storage-report",
    "horizontal-render-profile",
    "horizontal-scene-build-receipt",
    "human-review-closure",
    "human-visual-qa-approval",
    "live-dashboard-proof",
    "motion-health-report",
    "output-variants",
    "owner-creative-acceptance",
    "rupture-to-transformation-media",
    "shot-plan",
    "source-revision-report",
    "story-plan",
    "transformation-to-arrival-media",
    "verification-report",
    "vertical-bounded-proof-media",
    "vertical-bounded-proof-media-qa",
    "vertical-composition-proof",
    "vertical-master-scene",
    "vertical-render-profile",
    "vertical-scene-build-receipt",
    "worker-requirements",
)

KEY_UPLOAD_NAMES = {
    "package-manifest-v2.json",
    "v2-calibration.json",
    "technical-authorization-v2.json",
    "release-report.json",
    "release-hold.json",
    "creative-acceptance.json",
    "output-variants.json",
    "final-look-profile.json",
    "encoding-profiles.json",
    "hardware-and-storage-report.json",
    "final-scene.json",
    "source-revision-report.json",
    "dependency-health-report.json",
    "deterministic-effects-and-disk-report.json",
    "exposure-mobile-readability-report.json",
    "final-quality-transition-report.json",
    "live-dashboard-proof.json",
    "motion-health-report.json",
    "vertical-composition-proof.json",
    "worker-requirements.json",
    "human-visual-qa-approval.json",
    "human-review-closure.json",
}

EXCLUDED_EXACT_DIRS = {
    ".git",
    "node_modules",
    "__pycache__",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    ".venv",
    "venv",
    "dist",
    "deep-models",
    "render-packages",
    "final-output",
    ".trackprompt-data",
    "frames",
    "checkpoints",
    "delivery",
    "master",
    "logs",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def text_dump(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    os.replace(temporary, path)


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def safe_stat(path: Path) -> dict[str, Any]:
    try:
        stat = path.stat()
        return {
            "exists": True,
            "sizeBytes": stat.st_size,
            "modifiedUtc": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
        }
    except OSError as exc:
        return {"exists": False, "error": f"{type(exc).__name__}: {exc}"}


def load_json(path: Path) -> tuple[Any | None, str | None]:
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            return json.load(handle), None
    except Exception as exc:  # noqa: BLE001 - forensic report must preserve the exact failure class
        return None, f"{type(exc).__name__}: {exc}"


def is_excluded_relative(relative: Path) -> bool:
    for part in relative.parts:
        lowered = part.lower()
        if lowered in EXCLUDED_EXACT_DIRS:
            return True
        if lowered.startswith(".pytest") or lowered.startswith("pytest-"):
            return True
        if lowered.startswith("test_final_release_loader_"):
            return True
        if "synthetic" in lowered and lowered.endswith(("fixture", "fixtures")):
            return True
    return False


def iter_real_files(root: Path) -> Iterator[Path]:
    for current, dirs, files in os.walk(root):
        current_path = Path(current)
        try:
            relative_current = current_path.relative_to(root)
        except ValueError:
            continue
        filtered: list[str] = []
        for directory in dirs:
            relative = relative_current / directory
            if not is_excluded_relative(relative):
                filtered.append(directory)
        dirs[:] = filtered
        for file_name in files:
            candidate = current_path / file_name
            try:
                relative = candidate.relative_to(root)
            except ValueError:
                continue
            if not is_excluded_relative(relative):
                yield candidate


def flatten_json(value: Any, prefix: str = "$.") -> Iterator[tuple[str, Any]]:
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_prefix = f"{prefix}{key}"
            yield child_prefix, child
            yield from flatten_json(child, child_prefix + ".")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            child_prefix = f"{prefix}[{index}]"
            yield child_prefix, child
            yield from flatten_json(child, child_prefix + ".")


def find_key_values(value: Any, wanted_key: str) -> list[Any]:
    found: list[Any] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key) == wanted_key:
                found.append(child)
            found.extend(find_key_values(child, wanted_key))
    elif isinstance(value, list):
        for child in value:
            found.extend(find_key_values(child, wanted_key))
    return found


def first_scalar(value: Any, keys: Sequence[str]) -> Any | None:
    for key in keys:
        for candidate in find_key_values(value, key):
            if isinstance(candidate, (str, int, float, bool)) or candidate is None:
                return candidate
    return None


def collect_hash_strings(value: Any) -> set[str]:
    result: set[str] = set()
    for _, child in flatten_json(value):
        if isinstance(child, str) and HEX64.fullmatch(child.strip()):
            result.add(child.lower())
    return result


def collect_role_references(value: Any) -> list[dict[str, Any]]:
    references: list[dict[str, Any]] = []

    def visit(node: Any, json_path: str) -> None:
        if isinstance(node, Mapping):
            raw_path = node.get("path")
            raw_sha = node.get("sha256")
            if isinstance(raw_path, str) and isinstance(raw_sha, str) and HEX64.fullmatch(raw_sha.strip()):
                references.append(
                    {
                        "jsonPath": json_path,
                        "role": node.get("role"),
                        "path": raw_path,
                        "sha256": raw_sha.lower(),
                        "sizeBytes": node.get("sizeBytes"),
                    }
                )
            for key, child in node.items():
                visit(child, f"{json_path}.{key}")
        elif isinstance(node, list):
            for index, child in enumerate(node):
                visit(child, f"{json_path}[{index}]")

    visit(value, "$")
    return references


def resolve_reference_path(raw_path: str, *, repository_root: Path, package_root: Path, source_file: Path) -> Path | None:
    expanded = os.path.expandvars(os.path.expanduser(raw_path.strip()))
    path = Path(expanded)
    candidates: list[Path] = []
    if path.is_absolute():
        candidates.append(path)
    else:
        candidates.extend(
            [
                repository_root / path,
                package_root / path,
                source_file.parent / path,
                package_root.parent / path,
            ]
        )
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate.resolve(strict=False)).lower()
        if key in seen:
            continue
        seen.add(key)
        if candidate.is_file():
            return candidate.resolve()
    return None


def subprocess_result(command: Sequence[str], cwd: Path, timeout: float = 20.0) -> dict[str, Any]:
    started = time.monotonic()
    try:
        completed = subprocess.run(
            list(command),
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
        return {
            "command": list(command),
            "returnCode": completed.returncode,
            "stdout": completed.stdout[-20000:],
            "stderr": completed.stderr[-20000:],
            "elapsedSeconds": round(time.monotonic() - started, 4),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "command": list(command),
            "error": f"{type(exc).__name__}: {exc}",
            "elapsedSeconds": round(time.monotonic() - started, 4),
        }


def git_snapshot(root: Path) -> dict[str, Any]:
    if not (root / ".git").exists():
        return {"available": False, "reason": ".git directory not found"}
    commands = {
        "branch": ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        "head": ["git", "rev-parse", "HEAD^{commit}"],
        "status": ["git", "status", "--short"],
        "remote": ["git", "remote", "-v"],
        "upstream": ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
    }
    results = {name: subprocess_result(command, root) for name, command in commands.items()}
    return {
        "available": True,
        "branch": results["branch"].get("stdout", "").strip() or None,
        "head": results["head"].get("stdout", "").strip() or None,
        "statusShort": results["status"].get("stdout", ""),
        "dirty": bool(results["status"].get("stdout", "").strip()),
        "remote": results["remote"].get("stdout", ""),
        "upstream": results["upstream"].get("stdout", "").strip() or None,
        "raw": results,
    }


def find_executables(root: Path) -> dict[str, Any]:
    common_blender = [
        Path(r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe"),
        Path(r"C:\Program Files\Blender Foundation\Blender 4.5\blender.exe"),
        Path(r"C:\Program Files\Blender Foundation\Blender 4.4\blender.exe"),
    ]
    backend_python = root / "backend" / ".venv" / "Scripts" / "python.exe"
    result: dict[str, Any] = {}
    for name in ("ffmpeg", "ffprobe", "git", "docker", "powershell", "pwsh"):
        found = shutil.which(name + (".exe" if os.name == "nt" else "")) or shutil.which(name)
        result[name] = found
    blender = shutil.which("blender.exe") or shutil.which("blender")
    if not blender:
        blender = next((str(path) for path in common_blender if path.is_file()), None)
    result["blender"] = blender
    result["backendPython"] = str(backend_python) if backend_python.is_file() else None
    result["missionControlLauncher"] = str(root / "WZHK-Media-Launcher.cmd") if (root / "WZHK-Media-Launcher.cmd").is_file() else None
    result["productionWrapper"] = str(root / "production" / "andromeda-v2" / "invoke-production.ps1") if (root / "production" / "andromeda-v2" / "invoke-production.ps1").is_file() else None
    result["operatorAuthorizationTool"] = str(root / "production" / "andromeda-v2" / "new-operator-authorization.ps1") if (root / "production" / "andromeda-v2" / "new-operator-authorization.ps1").is_file() else None
    result["latestProductionHelper"] = str(root / "tools" / "Invoke-AndromedaLatestProduction.ps1") if (root / "tools" / "Invoke-AndromedaLatestProduction.ps1").is_file() else None
    return result


def process_snapshot(root: Path) -> dict[str, Any]:
    if os.name != "nt":
        return {"supported": False, "reason": "tasklist inspection is Windows-only"}
    result = subprocess_result(["tasklist", "/FO", "CSV", "/NH"], root)
    rows: list[dict[str, str]] = []
    stdout = result.get("stdout", "")
    if stdout:
        for row in csv.reader(stdout.splitlines()):
            if len(row) >= 2:
                image = row[0]
                if image.lower() in {"blender.exe", "ffmpeg.exe", "ffprobe.exe", "python.exe", "powershell.exe", "pwsh.exe"}:
                    rows.append({"image": image, "pid": row[1], "memory": row[4] if len(row) > 4 else ""})
    return {"supported": True, "matches": rows, "raw": result}


def disk_snapshot(root: Path) -> dict[str, Any]:
    usage = shutil.disk_usage(root)
    return {
        "path": str(root),
        "totalGiB": round(usage.total / (1024**3), 3),
        "usedGiB": round(usage.used / (1024**3), 3),
        "freeGiB": round(usage.free / (1024**3), 3),
    }


def extract_source_bindings(value: Any) -> dict[str, dict[str, Any]]:
    bindings: dict[str, dict[str, Any]] = {}

    def visit(node: Any) -> None:
        if isinstance(node, Mapping):
            role = node.get("role")
            if role in {"source-audio", "source-cue"} and isinstance(node.get("sha256"), str):
                bindings[str(role)] = {
                    "sha256": str(node["sha256"]).lower(),
                    "sizeBytes": node.get("sizeBytes"),
                }
            for child in node.values():
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)

    visit(value)
    return bindings


def discover_matching_source(
    expected: dict[str, Any] | None,
    *,
    explicit_path: Path | None,
    roots: Sequence[Path],
    kind: str,
    maximum_files: int = 50000,
) -> dict[str, Any]:
    result: dict[str, Any] = {"kind": kind, "expected": expected, "matches": [], "searchedRoots": []}
    if not expected or not isinstance(expected.get("sha256"), str):
        result["status"] = "expected-identity-unavailable"
        return result

    expected_sha = str(expected["sha256"]).lower()
    expected_size = expected.get("sizeBytes")
    candidates: list[Path] = []
    if explicit_path is not None:
        candidates.append(explicit_path)
    for root in roots:
        if root.exists():
            result["searchedRoots"].append(str(root))
            if root.is_file():
                candidates.append(root)
                continue
            count = 0
            for current, dirs, files in os.walk(root):
                current_path = Path(current)
                dirs[:] = [d for d in dirs if not is_excluded_relative(Path(d)) and d.lower() not in {"frames", "checkpoints", "node_modules"}]
                for file_name in files:
                    count += 1
                    if count > maximum_files:
                        result.setdefault("warnings", []).append(f"search capped at {maximum_files} files under {root}")
                        dirs[:] = []
                        break
                    path = current_path / file_name
                    suffix = path.suffix.lower()
                    if kind == "source-audio" and suffix not in AUDIO_SUFFIXES:
                        continue
                    if kind == "source-cue" and file_name.lower() != "visual-cues.json":
                        continue
                    candidates.append(path)

    unique: dict[str, Path] = {}
    for candidate in candidates:
        try:
            key = str(candidate.resolve()).lower()
        except OSError:
            continue
        unique[key] = candidate

    for candidate in unique.values():
        if not candidate.is_file():
            continue
        try:
            stat = candidate.stat()
        except OSError:
            continue
        if isinstance(expected_size, int) and stat.st_size != expected_size:
            continue
        try:
            actual_sha = sha256_file(candidate)
        except OSError:
            continue
        if actual_sha == expected_sha:
            result["matches"].append(
                {
                    "path": str(candidate.resolve()),
                    "sha256": actual_sha,
                    "sizeBytes": stat.st_size,
                    "modifiedUtc": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
                }
            )
    result["status"] = "matched" if result["matches"] else "not-found"
    return result


def evaluate_reference(
    reference: dict[str, Any], *, repository_root: Path, package_root: Path, source_file: Path
) -> dict[str, Any]:
    resolved = resolve_reference_path(
        reference["path"],
        repository_root=repository_root,
        package_root=package_root,
        source_file=source_file,
    )
    output = dict(reference)
    output["resolvedPath"] = str(resolved) if resolved else None
    if resolved is None:
        output["valid"] = False
        output["error"] = "path-not-resolvable"
        return output
    try:
        actual_sha = sha256_file(resolved)
        actual_size = resolved.stat().st_size
    except OSError as exc:
        output["valid"] = False
        output["error"] = f"{type(exc).__name__}: {exc}"
        return output
    output["actualSha256"] = actual_sha
    output["actualSizeBytes"] = actual_size
    hash_valid = actual_sha == reference["sha256"]
    size_expected = reference.get("sizeBytes")
    size_valid = not isinstance(size_expected, int) or size_expected == actual_size
    output["hashValid"] = hash_valid
    output["sizeValid"] = size_valid
    output["valid"] = hash_valid and size_valid
    return output


def extract_forecast(data: Any) -> dict[str, Any]:
    aggregates = find_key_values(data, "aggregateForecast")
    enabled = find_key_values(data, "enabledVariantForecasts")
    aggregate = next((item for item in aggregates if isinstance(item, Mapping)), None)
    enabled_value = next((item for item in enabled if isinstance(item, list)), None)
    p90: float | None = None
    enabled_ids: list[str] = []
    if isinstance(aggregate, Mapping):
        total = aggregate.get("total")
        if isinstance(total, Mapping) and isinstance(total.get("p90Seconds"), (int, float)):
            p90 = float(total["p90Seconds"])
        raw_ids = aggregate.get("enabledVariantIds")
        if isinstance(raw_ids, list):
            enabled_ids = [str(item) for item in raw_ids]
    return {
        "aggregateForecast": aggregate,
        "enabledVariantForecasts": enabled_value,
        "aggregateP90Seconds": p90,
        "within24Hours": p90 is not None and p90 <= 86400.0,
        "enabledVariantIds": enabled_ids,
    }


def evaluate_package_candidate(
    manifest_path: Path,
    *,
    repository_root: Path,
    hold_data: Any | None,
    hold_path: Path | None,
    desired_matrix: str,
) -> dict[str, Any]:
    package_root = manifest_path.parent
    manifest, manifest_error = load_json(manifest_path)
    result: dict[str, Any] = {
        "manifestPath": str(manifest_path.resolve()),
        "packageRoot": str(package_root.resolve()),
        "modifiedUtc": safe_stat(manifest_path).get("modifiedUtc"),
        "manifestParseError": manifest_error,
        "requiredBundleFiles": {},
        "blockers": [],
        "warnings": [],
    }
    if manifest_error or manifest is None:
        result["blockers"].append("package manifest is not valid JSON")
        result["score"] = -1000
        return result

    result["manifestSha256"] = sha256_file(manifest_path)
    result["manifestSizeBytes"] = manifest_path.stat().st_size

    bundle_json: dict[str, Any] = {"manifest": manifest}
    for relative in REQUIRED_BUNDLE_FILES:
        path = package_root / Path(relative.replace("/", os.sep))
        data, error = load_json(path) if path.is_file() else (None, "missing")
        entry = {"path": str(path.resolve(strict=False)), **safe_stat(path), "parseError": error}
        if path.is_file():
            entry["sha256"] = sha256_file(path)
        result["requiredBundleFiles"][relative] = entry
        if error:
            result["blockers"].append(f"missing or invalid required bundle file: {relative}")
        else:
            bundle_json[relative] = data

    all_references: list[dict[str, Any]] = []
    for label, data in bundle_json.items():
        source_path = manifest_path if label == "manifest" else package_root / Path(label.replace("/", os.sep))
        for reference in collect_role_references(data):
            reference["sourceDocument"] = label
            all_references.append(
                evaluate_reference(
                    reference,
                    repository_root=repository_root,
                    package_root=package_root,
                    source_file=source_path,
                )
            )
    result["references"] = all_references
    invalid_references = [item for item in all_references if not item.get("valid")]
    result["invalidReferenceCount"] = len(invalid_references)
    if invalid_references:
        result["blockers"].append(f"{len(invalid_references)} hash/path reference(s) are invalid")

    roles = sorted({str(item.get("role")) for item in all_references if item.get("role")})
    result["artifactRoles"] = roles
    missing_roles = [role for role in REQUIRED_ARTIFACT_ROLES if role not in roles]
    result["missingRequiredArtifactRoles"] = missing_roles
    if missing_roles:
        result["warnings"].append(
            "required role coverage is incomplete in discoverable path/hash references; "
            "the authoritative finalizer may report a stricter schema-specific result"
        )

    combined = list(bundle_json.values())
    technical_ready = None
    production_start_allowed = None
    final_render_started = None
    operator_gate_status = None
    for data in combined:
        if technical_ready is None:
            technical_ready = first_scalar(data, ["technicalReady"])
        if production_start_allowed is None:
            production_start_allowed = first_scalar(data, ["productionStartAllowed"])
        if final_render_started is None:
            final_render_started = first_scalar(data, ["finalRenderStarted", "fullRenderStarted"])
        if operator_gate_status is None:
            gates = find_key_values(data, "operatorStartGate")
            gate = next((item for item in gates if isinstance(item, Mapping)), None)
            if isinstance(gate, Mapping):
                operator_gate_status = gate.get("status")

    result["technicalState"] = {
        "technicalReady": technical_ready,
        "productionStartAllowed": production_start_allowed,
        "finalRenderStarted": final_render_started,
        "operatorStartGateStatus": operator_gate_status,
    }
    if technical_ready is not True:
        result["blockers"].append("technical authorization does not report technicalReady=true")
    if final_render_started is True:
        result["warnings"].append("release evidence reports that a full render has already started")

    report_data = bundle_json.get("evidence/release-report.json")
    forecast = extract_forecast(report_data)
    result["forecast"] = forecast
    if forecast["aggregateP90Seconds"] is None:
        result["blockers"].append("release report lacks an exact aggregate total P90 forecast")
    elif not forecast["within24Hours"]:
        result["blockers"].append("aggregate total P90 exceeds 24 hours")

    enabled_ids = set(forecast["enabledVariantIds"])
    if "horizontal-16x9-1080p" not in enabled_ids:
        result["blockers"].append("required horizontal-16x9-1080p variant is not enabled in aggregate forecast")
    if desired_matrix == "dual" and "vertical-9x16-1080p" not in enabled_ids:
        result["blockers"].append("dual matrix requested but vertical-9x16-1080p is not enabled")

    human_approval = [item for item in all_references if item.get("role") == "human-visual-qa-approval"]
    human_closure = [item for item in all_references if item.get("role") == "human-review-closure"]
    result["humanRecords"] = {
        "visualQaApproval": human_approval,
        "reviewClosure": human_closure,
        "visualQaApprovalValid": bool(human_approval) and all(item.get("valid") for item in human_approval),
        "reviewClosureValid": bool(human_closure) and all(item.get("valid") for item in human_closure),
    }
    if not result["humanRecords"]["visualQaApprovalValid"]:
        result["blockers"].append("release does not bind a valid human-visual-qa-approval artifact")
    if not result["humanRecords"]["reviewClosureValid"]:
        result["blockers"].append("release does not bind a valid human-review-closure artifact")

    source_bindings: dict[str, dict[str, Any]] = {}
    for data in combined:
        source_bindings.update(extract_source_bindings(data))
    result["sourceBindings"] = source_bindings

    held = False
    hold_intersection: list[str] = []
    if hold_data is not None:
        candidate_hashes = set()
        for data in combined:
            candidate_hashes.update(collect_hash_strings(data))
        candidate_hashes.update(
            entry.get("sha256") for entry in result["requiredBundleFiles"].values() if entry.get("sha256")
        )
        candidate_hashes.add(result["manifestSha256"])
        hold_hashes = collect_hash_strings(hold_data)
        hold_intersection = sorted(hash_value for hash_value in candidate_hashes if hash_value in hold_hashes)
        held = bool(hold_intersection)
    result["releaseHold"] = {
        "path": str(hold_path.resolve()) if hold_path else None,
        "applies": held,
        "matchingHashes": hold_intersection,
    }
    if held:
        result["blockers"].append("tracked release hold binds this exact release identity")

    score = 0
    score += 20 if not manifest_error else -100
    score += 15 * sum(1 for entry in result["requiredBundleFiles"].values() if entry.get("exists") and not entry.get("parseError"))
    score += 30 if not invalid_references else -min(60, len(invalid_references) * 3)
    score += 30 if technical_ready is True else -30
    score += 20 if forecast["within24Hours"] else -20
    score += 15 if result["humanRecords"]["visualQaApprovalValid"] else -10
    score += 15 if result["humanRecords"]["reviewClosureValid"] else -10
    score += 30 if not held else -50
    result["score"] = score
    result["structurallyCoherent"] = not any(
        blocker.startswith("missing or invalid required bundle file") or "reference(s) are invalid" in blocker
        for blocker in result["blockers"]
    )
    result["readyForOperatorAuthorization"] = not result["blockers"]
    return result


def discover_package_candidates(root: Path, desired_matrix: str) -> tuple[list[dict[str, Any]], dict[str, Any] | None, Path | None]:
    hold_path = root / "production" / "andromeda-v2" / "release-hold.json"
    hold_data, hold_error = load_json(hold_path) if hold_path.is_file() else (None, None)
    candidates: list[dict[str, Any]] = []
    for path in iter_real_files(root):
        if path.name.lower() != "package-manifest-v2.json":
            continue
        relative_lower = str(path.relative_to(root)).replace("\\", "/").lower()
        if "/test_final_release_loader_" in relative_lower or "/ignored-local-final-evidence/" in relative_lower:
            continue
        candidates.append(
            evaluate_package_candidate(
                path,
                repository_root=root,
                hold_data=hold_data,
                hold_path=hold_path if hold_path.is_file() else None,
                desired_matrix=desired_matrix,
            )
        )
    candidates.sort(key=lambda item: (item.get("score", -10000), item.get("modifiedUtc") or ""), reverse=True)
    hold_summary = None
    if hold_path.is_file():
        hold_summary = {
            "path": str(hold_path.resolve()),
            "sha256": sha256_file(hold_path),
            "parseError": hold_error,
            "data": hold_data,
        }
    return candidates, hold_summary, hold_path if hold_path.is_file() else None


def discover_scenes(root: Path, limit: int = 30) -> list[dict[str, Any]]:
    paths: list[Path] = []
    for path in iter_real_files(root):
        if path.suffix.lower() != ".blend":
            continue
        lowered = path.name.lower()
        if "andromeda" in lowered or "space-journey-story" in lowered:
            paths.append(path)
    paths.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    result: list[dict[str, Any]] = []
    for path in paths[:limit]:
        stat = path.stat()
        result.append(
            {
                "path": str(path.resolve()),
                "sha256": sha256_file(path),
                "sizeBytes": stat.st_size,
                "modifiedUtc": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
            }
        )
    return result


def discover_profiles(root: Path, limit: int = 50) -> list[dict[str, Any]]:
    profiles: list[dict[str, Any]] = []
    for path in iter_real_files(root):
        if path.suffix.lower() != ".json":
            continue
        normalized = str(path).replace("\\", "/").lower()
        if "render-profiles" not in normalized and "/profiles/" not in normalized:
            continue
        data, error = load_json(path)
        if error or not isinstance(data, Mapping):
            continue
        if "resolution" not in data and "approvedSceneSha256" not in data and "profileId" not in data:
            continue
        stat = path.stat()
        resolution = data.get("resolution") if isinstance(data.get("resolution"), Mapping) else {}
        profiles.append(
            {
                "path": str(path.resolve()),
                "sha256": sha256_file(path),
                "sizeBytes": stat.st_size,
                "modifiedUtc": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
                "profileId": data.get("profileId"),
                "project": data.get("project"),
                "preset": data.get("preset"),
                "frameStart": data.get("frameStart"),
                "frameEnd": data.get("frameEnd"),
                "fps": data.get("fps"),
                "width": resolution.get("width") if isinstance(resolution, Mapping) else None,
                "height": resolution.get("height") if isinstance(resolution, Mapping) else None,
                "approvedSceneSha256": data.get("approvedSceneSha256"),
            }
        )
    profiles.sort(key=lambda item: item["modifiedUtc"], reverse=True)
    return profiles[:limit]


def discover_proof_roots(root: Path, limit: int = 50) -> list[dict[str, Any]]:
    test_output = root / "test-output"
    if not test_output.is_dir():
        return []
    roots: list[Path] = []
    for child in test_output.iterdir():
        if not child.is_dir():
            continue
        lowered = child.name.lower()
        if "andromeda" in lowered and "pytest" not in lowered and not lowered.startswith("."):
            roots.append(child)
    roots.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    result = []
    for path in roots[:limit]:
        files = [item.name for item in path.iterdir() if item.is_file()]
        result.append(
            {
                "path": str(path.resolve()),
                "modifiedUtc": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
                "topLevelFiles": sorted(files),
            }
        )
    return result


def inspect_pydantic_models(root: Path) -> dict[str, Any]:
    backend = root / "backend"
    if not backend.is_dir():
        return {"available": False, "reason": "backend directory missing"}
    sys.path.insert(0, str(backend))
    module_names = [
        "app.cinematic.release_finalization",
        "app.cinematic.schemas",
        "app.cinematic.models",
    ]
    schemas: dict[str, Any] = {}
    errors: dict[str, str] = {}
    try:
        from pydantic import BaseModel  # type: ignore
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "reason": f"pydantic import failed: {type(exc).__name__}: {exc}"}

    for module_name in module_names:
        try:
            module = __import__(module_name, fromlist=["*"])
        except Exception as exc:  # noqa: BLE001
            errors[module_name] = f"{type(exc).__name__}: {exc}"
            continue
        for name, candidate in vars(module).items():
            if not inspect.isclass(candidate):
                continue
            try:
                if not issubclass(candidate, BaseModel) or candidate is BaseModel:
                    continue
            except TypeError:
                continue
            lowered = name.lower()
            if not any(token in lowered for token in ("human", "approval", "closure", "release", "finalization", "authorization")):
                continue
            try:
                schemas[f"{module_name}.{name}"] = candidate.model_json_schema()
            except Exception as exc:  # noqa: BLE001
                errors[f"{module_name}.{name}"] = f"{type(exc).__name__}: {exc}"
    return {"available": bool(schemas), "schemas": schemas, "errors": errors}


def render_markdown(report: Mapping[str, Any]) -> str:
    summary = report["summary"]
    selected = report.get("selectedCandidate")
    lines = [
        "# Andromeda V2 production forensic report",
        "",
        f"Generated: `{report['generatedAt']}`",
        f"Repository: `{report['repositoryRoot']}`",
        "",
        "## Executive result",
        "",
        f"- Status: **{summary['status']}**",
        f"- Ready for read-only preflight: **{summary['readyForReadOnlyPreflight']}**",
        f"- Ready for operator authorization: **{summary['readyForOperatorAuthorization']}**",
        f"- Ready to start rendering: **{summary['readyForStart']}**",
        f"- Desired matrix: `{report['desiredMatrix']}`",
        "",
    ]
    if selected:
        lines.extend(
            [
                "## Selected release candidate",
                "",
                f"- Package: `{selected['manifestPath']}`",
                f"- Package SHA-256: `{selected.get('manifestSha256', 'unavailable')}`",
                f"- Score: `{selected.get('score')}`",
                f"- Technical ready: `{selected.get('technicalState', {}).get('technicalReady')}`",
                f"- Aggregate P90: `{selected.get('forecast', {}).get('aggregateP90Seconds')}` seconds",
                f"- Held by tracked release hold: `{selected.get('releaseHold', {}).get('applies')}`",
                "",
                "### Exact blockers",
                "",
            ]
        )
        blockers = selected.get("blockers") or []
        if blockers:
            lines.extend(f"- {blocker}" for blocker in blockers)
        else:
            lines.append("- None detected by the forensic audit.")
        lines.extend(["", "### Human records", ""])
        human = selected.get("humanRecords", {})
        lines.append(f"- Human visual-QA approval valid: `{human.get('visualQaApprovalValid')}`")
        lines.append(f"- Human review closure valid: `{human.get('reviewClosureValid')}`")
        lines.extend(["", "### Source identity", ""])
        source = report.get("sourceDiscovery", {})
        for key in ("source-audio", "source-cue"):
            item = source.get(key, {})
            lines.append(f"- {key}: `{item.get('status')}`")
            for match in item.get("matches", []):
                lines.append(f"  - `{match['path']}`")
    else:
        lines.extend(["## Selected release candidate", "", "No real package-manifest-v2.json candidate was found.", ""])

    lines.extend(["", "## What to upload for remote diagnosis", ""])
    lines.append("Upload the generated `andromeda-forensic-upload.zip`. It intentionally excludes audio, `.blend` files, frames, and final media.")
    lines.extend(["", "## Safety statement", ""])
    lines.append("This audit did not modify release evidence, remove a hold, create human approval, authorize production, render a frame, or encode media.")
    lines.append("")
    return "\n".join(lines)


def render_approval_checklist(report: Mapping[str, Any]) -> str:
    selected = report.get("selectedCandidate") or {}
    lines = [
        "# Human review worksheet — Andromeda V2",
        "",
        "This worksheet is not an authorization artifact. It records what the human operator must actually review before a schema-valid approval/closure can be created.",
        "",
        f"Package manifest: `{selected.get('manifestPath', 'not selected')}`",
        f"Package SHA-256: `{selected.get('manifestSha256', 'not available')}`",
        "",
        "## Review confirmations",
        "",
        "- [ ] I reviewed the exact corrected horizontal scene identity.",
        "- [ ] I reviewed the exact horizontal render profile.",
        "- [ ] I reviewed the full-song animatic from beginning to end.",
        "- [ ] I reviewed Gates → Rupture, Rupture → Transformation, and Transformation → Arrival transition proofs.",
        "- [ ] I reviewed motion-health and exposure/mobile-readability evidence.",
        "- [ ] I reviewed the independently authored vertical bounded proof, even if vertical production remains disabled.",
        "- [ ] I approve the exact final seven-act scene for production at the stated visual level.",
        "- [ ] I confirm there are no unresolved blocking artistic findings.",
        "",
        "Reviewer name: ______________________________",
        "",
        "Decision timestamp with timezone: ______________________________",
        "",
        "Decision: APPROVE / REVISE",
        "",
        "Notes:",
        "",
        "________________________________________________________________",
        "",
    ]
    return "\n".join(lines)


def copy_upload_artifacts(
    zip_path: Path,
    *,
    report_json: Path,
    report_md: Path,
    checklist: Path,
    model_schemas_path: Path,
    selected: Mapping[str, Any] | None,
    repository_root: Path,
) -> list[dict[str, Any]]:
    copied: list[dict[str, Any]] = []
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path, arcname in (
            (report_json, "forensic-report.json"),
            (report_md, "forensic-report.md"),
            (checklist, "human-review-worksheet.md"),
            (model_schemas_path, "pydantic-model-schemas.json"),
        ):
            if path.is_file():
                archive.write(path, arcname)
                copied.append({"source": str(path), "archivePath": arcname, "sha256": sha256_file(path)})

        if selected:
            package_root = Path(str(selected["packageRoot"]))
            candidates: set[Path] = set()
            for name in KEY_UPLOAD_NAMES:
                for base in (package_root, package_root / "evidence", repository_root / "production" / "andromeda-v2", repository_root / "production" / "andromeda-v2" / "evidence"):
                    path = base / name
                    if path.is_file():
                        candidates.add(path.resolve())
            for reference in selected.get("references", []):
                resolved = reference.get("resolvedPath")
                if not resolved:
                    continue
                path = Path(str(resolved))
                if path.suffix.lower() == ".json" and path.is_file():
                    candidates.add(path.resolve())

            for index, path in enumerate(sorted(candidates, key=lambda item: str(item).lower())):
                try:
                    relative = path.relative_to(repository_root)
                    arcname = "selected-artifacts/" + str(relative).replace("\\", "/")
                except ValueError:
                    arcname = f"selected-artifacts/external/{index:03d}-{path.name}"
                archive.write(path, arcname)
                copied.append({"source": str(path), "archivePath": arcname, "sha256": sha256_file(path)})

        inventory_bytes = (json.dumps(copied, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        archive.writestr("upload-inventory.json", inventory_bytes)
    return copied


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only Andromeda V2 production forensic audit")
    parser.add_argument("--repository-root", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--source-audio")
    parser.add_argument("--source-cue")
    parser.add_argument("--desired-matrix", choices=("horizontal-only", "dual"), default="horizontal-only")
    parser.add_argument("--source-search-root", action="append", default=[])
    parser.add_argument("--preflight-log")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(args.repository_root).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    if not root.is_dir():
        raise SystemExit(f"Repository root does not exist: {root}")

    candidates, hold_summary, _ = discover_package_candidates(root, args.desired_matrix)
    selected = candidates[0] if candidates else None

    source_search_roots: list[Path] = [Path(item).expanduser() for item in args.source_search_root]
    home = Path.home()
    defaults = [
        home / "OneDrive" / "Desktop" / "Gratis Project",
        root / "test-output",
        root / ".trackprompt-data" / "jobs",
    ]
    for path in defaults:
        if path.exists() and path not in source_search_roots:
            source_search_roots.append(path)

    source_bindings = selected.get("sourceBindings", {}) if selected else {}
    source_discovery = {
        "source-audio": discover_matching_source(
            source_bindings.get("source-audio"),
            explicit_path=Path(args.source_audio).expanduser() if args.source_audio else None,
            roots=source_search_roots,
            kind="source-audio",
        ),
        "source-cue": discover_matching_source(
            source_bindings.get("source-cue"),
            explicit_path=Path(args.source_cue).expanduser() if args.source_cue else None,
            roots=source_search_roots,
            kind="source-cue",
        ),
    }

    ready_preflight = bool(selected and selected.get("structurallyCoherent"))
    ready_operator = bool(selected and selected.get("readyForOperatorAuthorization"))
    source_ready = all(source_discovery[key].get("status") == "matched" for key in ("source-audio", "source-cue"))
    ready_start = ready_operator and source_ready
    if not selected:
        status = "NO_REAL_RELEASE_CANDIDATE"
    elif selected.get("releaseHold", {}).get("applies"):
        status = "BLOCKED_BY_TRACKED_RELEASE_HOLD"
    elif not selected.get("humanRecords", {}).get("visualQaApprovalValid") or not selected.get("humanRecords", {}).get("reviewClosureValid"):
        status = "BLOCKED_BY_MISSING_HUMAN_RECORDS"
    elif selected.get("forecast", {}).get("aggregateP90Seconds") is None:
        status = "BLOCKED_BY_MISSING_AGGREGATE_FORECAST"
    elif selected.get("blockers"):
        status = "BLOCKED_BY_TECHNICAL_OR_IDENTITY_ERRORS"
    elif not source_ready:
        status = "READY_EXCEPT_PRIVATE_SOURCE_PATHS"
    else:
        status = "READY_FOR_EXPLICIT_OPERATOR_START"

    pydantic_models = inspect_pydantic_models(root)
    model_schemas_path = output_root / "pydantic-model-schemas.json"
    json_dump(model_schemas_path, pydantic_models)

    report: dict[str, Any] = {
        "schemaVersion": "1.0.0",
        "kind": "trackprompt-andromeda-v2-production-forensic-report",
        "generatedAt": utc_now(),
        "repositoryRoot": str(root),
        "outputRoot": str(output_root),
        "desiredMatrix": args.desired_matrix,
        "summary": {
            "status": status,
            "readyForReadOnlyPreflight": ready_preflight,
            "readyForOperatorAuthorization": ready_operator,
            "privateSourcesResolved": source_ready,
            "readyForStart": ready_start,
            "candidateCount": len(candidates),
        },
        "selectedCandidate": selected,
        "releaseCandidates": candidates,
        "releaseHold": hold_summary,
        "sourceDiscovery": source_discovery,
        "git": git_snapshot(root),
        "executables": find_executables(root),
        "processes": process_snapshot(root),
        "disk": disk_snapshot(root),
        "newestScenes": discover_scenes(root),
        "newestRenderProfiles": discover_profiles(root),
        "newestAndromedaProofRoots": discover_proof_roots(root),
        "pydanticModelSchemas": {
            "path": str(model_schemas_path),
            "available": pydantic_models.get("available"),
            "modelCount": len(pydantic_models.get("schemas", {})),
            "errors": pydantic_models.get("errors", {}),
        },
        "preflightLog": None,
        "safety": {
            "releaseEvidenceModified": False,
            "releaseHoldModified": False,
            "humanApprovalCreated": False,
            "operatorAuthorizationCreated": False,
            "renderStarted": False,
            "encodingStarted": False,
        },
    }
    if args.preflight_log:
        log_path = Path(args.preflight_log).expanduser().resolve()
        report["preflightLog"] = {"path": str(log_path), **safe_stat(log_path)}
        if log_path.is_file():
            report["preflightLog"]["sha256"] = sha256_file(log_path)
            report["preflightLog"]["tail"] = log_path.read_text(encoding="utf-8", errors="replace")[-30000:]

    report_json = output_root / "andromeda-forensic-report.json"
    report_md = output_root / "andromeda-forensic-report.md"
    checklist = output_root / "human-review-worksheet.md"
    json_dump(report_json, report)
    text_dump(report_md, render_markdown(report))
    text_dump(checklist, render_approval_checklist(report))

    upload_zip = output_root / "andromeda-forensic-upload.zip"
    copied = copy_upload_artifacts(
        upload_zip,
        report_json=report_json,
        report_md=report_md,
        checklist=checklist,
        model_schemas_path=model_schemas_path,
        selected=selected,
        repository_root=root,
    )
    upload_summary = {
        "path": str(upload_zip),
        "sha256": sha256_file(upload_zip),
        "sizeBytes": upload_zip.stat().st_size,
        "fileCount": len(copied) + 1,
    }
    json_dump(output_root / "upload-bundle-summary.json", upload_summary)

    print(json.dumps({
        "status": status,
        "reportJson": str(report_json),
        "reportMarkdown": str(report_md),
        "uploadBundle": str(upload_zip),
        "uploadBundleSha256": upload_summary["sha256"],
        "selectedPackage": selected.get("manifestPath") if selected else None,
        "readyForStart": ready_start,
        "blockers": selected.get("blockers") if selected else ["no real release candidate found"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
