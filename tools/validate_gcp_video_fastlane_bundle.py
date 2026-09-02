from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile

sys.dont_write_bytecode = True
from pathlib import Path
from typing import Any


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    arguments = parser.parse_args()
    root = arguments.root.resolve()
    backend = root / "backend"
    sys.path.insert(0, str(backend))

    from app.video_generation.planning import compile_project_plan

    errors: list[str] = []
    project = root / "video-projects" / "the-glitch-is-me"
    required = [
        root / "START-HERE-GCP-VIDEO-FASTLANE.md",
        root / "README-GCP-VIDEO-FASTLANE-STARTER.md",
        root / "CODEX-GCP-VIDEO-FASTLANE-PROMPT.md",
        root / "DIRECTORY-MAP.txt",
        root / "VALIDATION-REPORT.md",
        root / "PACKAGE-MANIFEST.json",
        project / "creative-bible.json",
        project / "shot-bank.json",
        project / "chapter-map.json",
        project / "edit-blueprint.json",
        root / "tools" / "RUN-GCP-VIDEO-FASTLANE.ps1",
        root / "tools" / "SETUP-GCP-VIDEO-FASTLANE.ps1",
        root / "tools" / "VERIFY-GCP-VIDEO-FASTLANE.ps1",
    ]
    for path in required:
        if not path.is_file():
            errors.append(f"missing required file: {path.relative_to(root)}")

    json_files = sorted(root.glob("schemas/*.json"))
    json_files += sorted(root.glob("video-projects/**/*.json"))
    json_files += sorted(root.glob("config/*.json"))
    for path in json_files:
        try:
            _json(path)
        except Exception as exc:
            errors.append(f"invalid JSON {path.relative_to(root)}: {exc}")

    shot_bank = _json(project / "shot-bank.json")
    shots = shot_bank.get("shots", []) if isinstance(shot_bank, dict) else []
    if len(shots) != 16:
        errors.append(f"shot bank expected 16 shots, got {len(shots)}")
    ids = [item.get("shotId") for item in shots if isinstance(item, dict)]
    if ids != [f"shot-{index:03d}" for index in range(1, 17)]:
        errors.append("shot IDs/order are not exactly shot-001 through shot-016")

    prompts = sorted((project / "prompts").glob("shot-*.txt"))
    requests = sorted((project / "provider-request-examples").glob("shot-*.request.json"))
    if len(prompts) != 16:
        errors.append(f"expected 16 plain-text prompts, got {len(prompts)}")
    if len(requests) != 16:
        errors.append(f"expected 16 provider request examples, got {len(requests)}")

    config_names = [
        "project-config.json",
        "project-config.quality-1080p.json",
        "project-config.4k-optional.json",
        "project-config.smoke.json",
    ]
    compiled: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="trackprompt-video-fastlane-"):
        for name in config_names:
            try:
                plan = compile_project_plan(
                    project_config_path=project / name,
                    creative_bible_path=project / "creative-bible.json",
                    shot_bank_path=project / "shot-bank.json",
                    gcs_bucket="gs://example-trackprompt-video",
                )
                compiled.append(
                    {
                        "config": name,
                        "shots": len(plan.shots),
                        "baseEstimatedUsd": plan.base_estimated_cost_usd,
                        "maxSpendUsd": plan.max_spend_usd,
                        "planDigest": plan.plan_digest,
                    }
                )
            except Exception as exc:
                errors.append(f"could not compile {name}: {exc}")

    manifest_path = root / "PACKAGE-MANIFEST.json"
    if manifest_path.is_file():
        try:
            manifest = _json(manifest_path)
            entries = manifest.get("files", []) if isinstance(manifest, dict) else []
            if not isinstance(entries, list):
                errors.append("PACKAGE-MANIFEST.json files must be an array")
            else:
                for item in entries:
                    if not isinstance(item, dict):
                        errors.append("PACKAGE-MANIFEST.json contains a non-object entry")
                        continue
                    relative_value = item.get("path")
                    expected_sha = item.get("sha256")
                    expected_bytes = item.get("bytes")
                    if not isinstance(relative_value, str) or not relative_value:
                        errors.append("PACKAGE-MANIFEST.json contains an invalid path")
                        continue
                    candidate = (root / relative_value).resolve()
                    try:
                        candidate.relative_to(root)
                    except ValueError:
                        errors.append(f"manifest path escapes root: {relative_value}")
                        continue
                    if not candidate.is_file():
                        errors.append(f"manifest file missing: {relative_value}")
                        continue
                    data = candidate.read_bytes()
                    actual_sha = hashlib.sha256(data).hexdigest()
                    if actual_sha != expected_sha:
                        errors.append(f"manifest SHA-256 mismatch: {relative_value}")
                    if len(data) != expected_bytes:
                        errors.append(f"manifest byte count mismatch: {relative_value}")
        except Exception as exc:
            errors.append(f"invalid PACKAGE-MANIFEST.json: {exc}")

    forbidden_suffixes = {".wav", ".mp3", ".flac", ".m4a", ".mp4", ".mov", ".mkv", ".pem", ".key", ".p12"}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if path.suffix.lower() in forbidden_suffixes:
            errors.append(f"forbidden private/generated binary in starter: {relative}")
        if path.name == ".env" or path.name.endswith(".service-account.json"):
            errors.append(f"forbidden credential file in starter: {relative}")
        if "__pycache__" in relative.parts or path.suffix == ".pyc":
            errors.append(f"Python cache must not be packaged: {relative}")

    text = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in {".py", ".json", ".md", ".txt", ".ps1", ".cmd"}
    ).lower()
    secret_markers = (
        "-----begin " + "private key-----",
        "private_" + "key_id\"",
        "ollama_" + "api_key=",
    )
    for secret_marker in secret_markers:
        if secret_marker in text:
            errors.append(f"forbidden secret marker found: {secret_marker}")

    result = {
        "schemaVersion": "1.0.0",
        "status": "passed" if not errors else "failed",
        "root": str(root),
        "jsonFiles": len(json_files),
        "shotCount": len(shots),
        "promptFiles": len(prompts),
        "requestExamples": len(requests),
        "compiledProfiles": compiled,
        "errors": errors,
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
