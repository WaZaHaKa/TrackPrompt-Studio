from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, cast

from .assembly import build_assembly_plan, write_assembly_plan, write_powershell_runner
from .davinci import (
    export_edit_sheet_csv,
    export_edl,
    export_fcp7_xml,
    export_fcpxml,
    export_marker_csv,
)
from .jsonio import atomic_write_json, atomic_write_text
from .timeline import editorial_export_files


def _enrich_derived_paths(value: dict[str, Any], output_root: Path) -> dict[str, Any]:
    enriched = cast(dict[str, Any], json.loads(json.dumps(value)))
    segments = cast(list[dict[str, Any]], enriched.get("segments", []))
    for index, item in enumerate(segments, start=1):
        item["derivedMediaPath"] = str(
            (output_root / "derived-media" / f"event-{index:04d}.mp4").resolve()
        )
    return enriched


def _write_relink_map(value: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(
            [
                "segment_id",
                "shot_id",
                "derived_media_path",
                "original_clip_path",
                "source_in_frames",
                "duration_frames",
                "treatment",
            ]
        )
        for item in cast(list[dict[str, Any]], value["segments"]):
            writer.writerow(
                [
                    item["segmentId"],
                    item["shotId"],
                    item["derivedMediaPath"],
                    item["clipPath"],
                    item["sourceInFrames"],
                    item["durationFrames"],
                    item.get("treatment", ""),
                ]
            )


def _coverage(value: dict[str, Any]) -> dict[str, Any]:
    timeline = cast(dict[str, Any], value["timeline"])
    segments = cast(list[dict[str, Any]], value["segments"])
    editorial = value.get("editorial", {})
    coverage_rules = editorial.get("coverage", {}) if isinstance(editorial, dict) else {}
    if not isinstance(coverage_rules, dict):
        coverage_rules = {}
    cursor = 0
    gaps: list[dict[str, int]] = []
    for item in segments:
        start = int(item["timelineStartFrames"])
        if start != cursor:
            gaps.append({"expectedStart": cursor, "actualStart": start})
        cursor = start + int(item["durationFrames"])
    shots = sorted({str(item["shotId"]) for item in segments})
    unique_edits = {
        (
            str(item["shotId"]),
            int(item["sourceInFrames"]),
            str(item.get("treatment", "")),
        )
        for item in segments
    }
    expected_shots = coverage_rules.get(
        "requiredShotIds",
        [f"shot-{index:03d}" for index in range(1, 17)],
    )
    if not isinstance(expected_shots, list):
        expected_shots = []
    expected_shot_ids = sorted(str(item) for item in expected_shots)
    event_range = coverage_rules.get("eventCountRange", [1, 10_000])
    if not isinstance(event_range, list) or len(event_range) != 2:
        event_range = [1, 10_000]
    chapters: dict[str, list[dict[str, Any]]] = {}
    for item in segments:
        chapters.setdefault(str(item["chapterId"]), []).append(item)
    chapter_checks: dict[str, bool] = {}
    requirements = coverage_rules.get("chapterRequirements", [])
    if isinstance(requirements, list):
        for index, requirement in enumerate(requirements, start=1):
            if not isinstance(requirement, dict):
                continue
            check_id = str(requirement.get("id", f"chapter-requirement-{index:02d}"))
            chapter_items = chapters.get(str(requirement.get("chapterId", "")), [])
            actual_ids = {str(item["shotId"]) for item in chapter_items}
            required_ids = requirement.get("requiredShotIds", [])
            if not isinstance(required_ids, list):
                required_ids = []
            expected_end = requirement.get("endsWithShotId")
            chapter_checks[check_id] = bool(chapter_items) and set(
                str(item) for item in required_ids
            ).issubset(actual_ids) and (
                expected_end is None or str(chapter_items[-1]["shotId"]) == str(expected_end)
            )
    expected_opening = coverage_rules.get("openingShotId")
    expected_closing = coverage_rules.get("closingShotId")
    if expected_opening is not None:
        chapter_checks["opening-shot"] = bool(segments) and str(segments[0]["shotId"]) == str(
            expected_opening
        )
    if expected_closing is not None:
        chapter_checks["closing-shot"] = bool(segments) and str(segments[-1]["shotId"]) == str(
            expected_closing
        )
    return {
        "schemaVersion": "1.0.0",
        "localEditDigest": value.get("localEditDigest"),
        "eventCount": len(segments),
        "eventCountInRequiredRange": int(event_range[0]) <= len(segments) <= int(event_range[1]),
        "durationFrames": int(timeline["durationFrames"]),
        "coveredFrames": cursor,
        "continuous": not gaps and cursor == int(timeline["durationFrames"]),
        "gaps": gaps,
        "shotIds": shots,
        "allSixteenShotsUsed": len(expected_shot_ids) == 16 and shots == expected_shot_ids,
        "allRequiredShotsUsed": shots == expected_shot_ids,
        "uniqueSourceTreatmentCount": len(unique_edits),
        "noIdenticalSourceTreatmentRepeats": len(unique_edits) == len(segments),
        "openingShotId": segments[0]["shotId"] if segments else None,
        "closingShotId": segments[-1]["shotId"] if segments else None,
        "chapterShotIds": {
            chapter_id: sorted({str(item["shotId"]) for item in items})
            for chapter_id, items in sorted(chapters.items())
        },
        "editorialChecks": chapter_checks,
        "editorialChecksPassed": all(chapter_checks.values()),
    }


def export_davinci_package(
    value: dict[str, Any],
    *,
    output_root: Path,
    ffmpeg: str | None = None,
) -> dict[str, str]:
    output_root.mkdir(parents=True, exist_ok=True)
    value = _enrich_derived_paths(value, output_root)
    resolved_path = output_root / "resolved-timeline.json"
    edit_plan_path = output_root / "edit-plan.json"
    atomic_write_json(resolved_path, value)
    atomic_write_json(edit_plan_path, value)

    files = editorial_export_files(value)
    fcpxml_path = output_root / files["fcpxml"]
    fcp7_path = output_root / files["fcp7"]
    edl_path = output_root / files["edl"]
    edit_sheet_path = output_root / "edit-sheet.csv"
    marker_path = output_root / "davinci-markers.csv"
    relink_path = output_root / "relink-map.csv"
    coverage_path = output_root / "coverage-report.json"
    manifest_path = output_root / "render-manifest.json"
    verification_path = output_root / "verification-report.json"
    export_fcpxml(value, fcpxml_path)
    export_fcp7_xml(value, fcp7_path)
    export_edl(value, edl_path)
    export_edit_sheet_csv(value, edit_sheet_path)
    export_marker_csv(value, marker_path)
    _write_relink_map(value, relink_path)
    coverage = _coverage(value)
    atomic_write_json(coverage_path, coverage)

    timeline = cast(dict[str, Any], value.get("timeline", {}))
    width = int(timeline.get("width", 1920))
    preview_path = output_root / files["preview4k" if width >= 3840 else "preview1080p"]
    assembly_plan = build_assembly_plan(
        value,
        output_path=preview_path,
        work_root=output_root / "assembly-work",
        ffmpeg=ffmpeg,
    )
    assembly_json = output_root / "assembly-plan.json"
    assembly_ps1 = output_root / "ASSEMBLE-PREVIEW.ps1"
    write_assembly_plan(assembly_plan, assembly_json)
    write_powershell_runner(assembly_plan, assembly_ps1)

    atomic_write_json(
        manifest_path,
        {
            "schemaVersion": "1.0.0",
            "status": "planned",
            "localEditDigest": value.get("localEditDigest"),
            "providerPlanDigest": value.get("providerPlanDigest"),
            "providerOperationSnapshot": value.get("providerOperationSnapshot", {}),
            "audio": value.get("audio", {}),
            "sourceClipSha256": value.get("sourceClipSha256", {}),
            "timeline": timeline,
            "coverage": coverage,
            "exportFiles": files,
            "previewPath": str(preview_path.resolve()),
        },
    )
    atomic_write_json(
        verification_path,
        {
            "schemaVersion": "1.0.0",
            "status": "pending-assembly",
            "localEditDigest": value.get("localEditDigest"),
        },
    )

    editorial = value.get("editorial", {})
    handoff = editorial.get("handoff", {}) if isinstance(editorial, dict) else {}
    if not isinstance(handoff, dict):
        handoff = {}
    handoff_title = str(handoff.get("title", value.get("title", "TrackPrompt project")))
    handoff_summary = str(
        handoff.get(
            "summary",
            "Conservative local assembly for final artistic finishing in DaVinci Resolve.",
        )
    )
    readme = output_root / "README-DAVINCI.txt"
    atomic_write_text(
        readme,
        f"TRACKPROMPT — {handoff_title.upper()} — DAVINCI HANDOFF\n\n"
        "1. Import the FCPXML rough cut.\n"
        "2. If needed, use the FCP 7 XML or EDL fallback.\n"
        "3. Relink through relink-map.csv; derived-media contains one rendered event per edit.\n"
        "4. The one continuous 48 kHz stereo master is the only audible source.\n\n"
        f"{handoff_summary}\n\n"
        f"The timeline is {width}x{int(timeline.get('height', 1080))}, 24 FPS, Rec.709 SDR. "
        "Generated provider clips remain "
        "immutable; all retimes, crops, reversals, fades and overlays exist only in derived media.\n"
        "Apply only final artistic touches, grading and optional title work in DaVinci Resolve.\n",
    )
    return {
        "resolvedTimeline": str(resolved_path),
        "editPlan": str(edit_plan_path),
        "fcpxml": str(fcpxml_path),
        "fcp7Xml": str(fcp7_path),
        "edl": str(edl_path),
        "editSheet": str(edit_sheet_path),
        "markers": str(marker_path),
        "relinkMap": str(relink_path),
        "coverageReport": str(coverage_path),
        "renderManifest": str(manifest_path),
        "verificationReport": str(verification_path),
        "assemblyPlan": str(assembly_json),
        "assemblyPowerShell": str(assembly_ps1),
        "previewOutput": str(preview_path),
        "readme": str(readme),
    }
