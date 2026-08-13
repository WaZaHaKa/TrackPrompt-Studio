from __future__ import annotations

from pathlib import Path
from typing import Any

from .assembly import build_assembly_plan, write_assembly_plan, write_powershell_runner
from .davinci import (
    export_edit_sheet_csv,
    export_edl,
    export_fcp7_xml,
    export_fcpxml,
    export_marker_csv,
)
from .jsonio import atomic_write_json


def export_davinci_package(
    value: dict[str, Any],
    *,
    output_root: Path,
    ffmpeg: str | None = None,
) -> dict[str, str]:
    output_root.mkdir(parents=True, exist_ok=True)
    resolved_path = output_root / "resolved-timeline.json"
    atomic_write_json(resolved_path, value)

    fcpxml_path = output_root / "trackprompt-timeline.fcpxml"
    fcp7_path = output_root / "trackprompt-timeline.xml"
    edl_path = output_root / "trackprompt-timeline.edl"
    edit_sheet_path = output_root / "edit-sheet.csv"
    marker_path = output_root / "davinci-markers.csv"
    export_fcpxml(value, fcpxml_path)
    export_fcp7_xml(value, fcp7_path)
    export_edl(value, edl_path)
    export_edit_sheet_csv(value, edit_sheet_path)
    export_marker_csv(value, marker_path)

    timeline = value.get("timeline", {})
    width = int(timeline.get("width", 1920)) if isinstance(timeline, dict) else 1920
    preview_label = "4k" if width >= 3840 else "1080p"
    preview_path = output_root / f"autonomous-preview-{preview_label}.mp4"
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

    readme = output_root / "README-DAVINCI.txt"
    readme.write_text(
        "TRACKPROMPT GCP VIDEO FAST LANE — DAVINCI HANDOFF\n\n"
        "Preferred import order:\n"
        "1. Import trackprompt-timeline.fcpxml.\n"
        "2. If your Resolve build rejects it, import trackprompt-timeline.xml.\n"
        "3. EDL and edit-sheet.csv are conservative fallbacks.\n\n"
        "The timeline is 24 FPS and uses the original song as the master clock.\n"
        "Generated clips are video-only; the source master is attached locally.\n"
        "ASSEMBLE-PREVIEW.ps1 creates an immediate H.264 preview without Resolve.\n"
        "Make final artistic adjustments, transitions, grading and overlays in Resolve.\n",
        encoding="utf-8",
        newline="\n",
    )
    return {
        "resolvedTimeline": str(resolved_path),
        "fcpxml": str(fcpxml_path),
        "fcp7Xml": str(fcp7_path),
        "edl": str(edl_path),
        "editSheet": str(edit_sheet_path),
        "markers": str(marker_path),
        "assemblyPlan": str(assembly_json),
        "assemblyPowerShell": str(assembly_ps1),
        "previewOutput": str(preview_path),
        "readme": str(readme),
    }
