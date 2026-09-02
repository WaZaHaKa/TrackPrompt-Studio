from __future__ import annotations

import csv
import json
import os
from io import StringIO
from pathlib import Path
from typing import Any
from uuid import uuid4

from .models import LocalVideoTimelineScene
from .package import LocalVideoProjectPackage

OUTPUT_DIRECTORIES = (
    "analysis",
    "references",
    "keyframes",
    "raw-video",
    "scene-masters",
    "edit",
    "review/thumbnail-candidates",
    "manifests",
    "final",
)


def _atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.partial-{uuid4().hex}")
    try:
        temporary.write_text(value, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_json(path: Path, value: object) -> None:
    _atomic_text(
        path,
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n",
    )


def _feature_numbers(value: object) -> list[float]:
    if isinstance(value, dict):
        return _feature_numbers(value.get("value"))
    if not isinstance(value, list):
        return []
    return [
        float(item)
        for item in value
        if isinstance(item, (int, float)) and not isinstance(item, bool) and item >= 0
    ]


def _safe_analysis(value: dict[str, Any]) -> dict[str, Any]:
    result = json.loads(json.dumps(value, ensure_ascii=False))
    if not isinstance(result, dict):
        raise ValueError("The analysis output view is invalid")
    file_info = result.get("file")
    if isinstance(file_info, dict):
        file_info["displayName"] = "project-audio"
        file_info.pop("privateMetadata", None)
    return result


def publish_output_view(
    *,
    package: LocalVideoProjectPackage,
    revision_id: str,
    analysis_id: str,
    analysis: dict[str, Any],
    audio_sha256: str,
    duration_seconds: float,
    timeline: tuple[LocalVideoTimelineScene, ...],
) -> Path:
    root = (package.root / "outputs").resolve()
    if root.parent != package.root:
        raise ValueError("The project output view escaped the package root")
    for relative in OUTPUT_DIRECTORIES:
        (root / relative).mkdir(parents=True, exist_ok=True)
    safe_analysis = _safe_analysis(analysis)
    _atomic_json(root / "analysis" / "audio-analysis.json", safe_analysis)
    structure = safe_analysis.get("structure")
    sections = structure.get("sections") if isinstance(structure, dict) else []
    _atomic_json(
        root / "analysis" / "section-map.json",
        {
            "schemaVersion": "1.0.0",
            "projectId": package.project_id,
            "durationSeconds": duration_seconds,
            "sections": sections if isinstance(sections, list) else [],
            "timeline": [item.model_dump(mode="json", by_alias=True) for item in timeline],
        },
    )
    _atomic_json(
        root / "analysis" / "archive-revision.json",
        {
            "schemaVersion": "1.0.0",
            "projectId": package.project_id,
            "revisionId": revision_id,
            "analysisId": analysis_id,
            "retentionPolicy": "persistent-until-explicit-project-delete",
            "audioContentSha256": audio_sha256,
            "durationSeconds": duration_seconds,
            "packageDigest": package.package_digest,
        },
    )
    rhythm = safe_analysis.get("rhythm")
    rhythm_value = rhythm if isinstance(rhythm, dict) else {}
    markers: list[tuple[float, str]] = []
    for field, kind in (
        ("beatTimestamps", "beat"),
        ("downbeatTimestamps", "downbeat"),
        ("onsetTimestamps", "onset"),
    ):
        markers.extend((seconds, kind) for seconds in _feature_numbers(rhythm_value.get(field)))
    lines: list[list[str]] = [["seconds", "kind"]]
    lines.extend([[f"{seconds:.6f}", kind] for seconds, kind in sorted(set(markers))])
    buffer = StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerows(lines)
    _atomic_text(root / "analysis" / "beat-markers.csv", buffer.getvalue())
    return root
