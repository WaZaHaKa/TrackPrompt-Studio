from __future__ import annotations

import csv
import io
import json
from datetime import UTC, datetime
from statistics import median
from typing import Any

from ..schemas import Confidence
from .schemas import MasteringReport, TrackComparison, TransitionType
from .store import CatalogueStore

NUMERIC_MEASUREMENTS = (
    "bpm",
    "integratedLoudnessLufs",
    "loudnessRangeLu",
    "samplePeakDbfs",
    "macroDynamicRangeDb",
    "stereoWidth",
    "phaseCorrelation",
    "spectralCentroidHz",
    "onsetDensity",
)


def _feature_value(root: dict[str, Any], *path: str) -> Any:
    current: Any = root
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    if isinstance(current, dict):
        return current.get("value")
    return None


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _measurement_text(measurement: dict[str, float | str | None], name: str) -> str:
    value = measurement.get(name)
    return "withheld" if value is None else str(value)


def _measurements(analysis: dict[str, Any] | None) -> dict[str, float | str | None]:
    if analysis is None:
        return {name: None for name in NUMERIC_MEASUREMENTS}
    values: dict[str, float | str | None] = {
        "bpm": _number(_feature_value(analysis, "rhythm", "bpm")),
        "key": _feature_value(analysis, "harmony", "key"),
        "mode": _feature_value(analysis, "harmony", "mode"),
        "integratedLoudnessLufs": _number(
            _feature_value(analysis, "production", "integratedLoudnessLufs")
        ),
        "loudnessRangeLu": _number(_feature_value(analysis, "production", "loudnessRangeLu")),
        "samplePeakDbfs": _number(_feature_value(analysis, "production", "peakDbfs")),
        "macroDynamicRangeDb": _number(
            _feature_value(analysis, "production", "macroDynamicRangeDb")
        ),
        "stereoWidth": _number(_feature_value(analysis, "production", "stereoWidth")),
        "phaseCorrelation": _number(
            _feature_value(analysis, "signalQuality", "phaseCorrelation")
        ),
        "spectralCentroidHz": _number(
            _feature_value(analysis, "timbre", "spectralCentroidHz")
        ),
        "onsetDensity": _number(_feature_value(analysis, "rhythm", "onsetDensity")),
        "lowEndWeight": _feature_value(analysis, "production", "lowEndWeight"),
        "brightness": _feature_value(analysis, "production", "highFrequencyBrightness"),
        "transientEmphasis": _feature_value(analysis, "production", "transientEmphasis"),
        "mixDensity": _feature_value(analysis, "production", "mixDensity"),
        "monoCompatibility": _feature_value(analysis, "production", "monoCompatibility"),
        "vocalPresence": _feature_value(analysis, "vocals", "presence"),
    }
    return values


def build_mastering_report(store: CatalogueStore, batch_id: str) -> MasteringReport:
    rows = store.report_inputs(batch_id)
    measurement_sets = [_measurements(row.get("analysis")) for row in rows]
    medians: dict[str, float] = {}
    minima: dict[str, float] = {}
    maxima: dict[str, float] = {}
    withheld: dict[str, int] = {}
    for name in NUMERIC_MEASUREMENTS:
        values: list[float] = []
        for item in measurement_sets:
            candidate = item.get(name)
            if isinstance(candidate, int | float) and not isinstance(candidate, bool):
                values.append(float(candidate))
        withheld[name] = len(measurement_sets) - len(values)
        if values:
            medians[name] = median(values)
            minima[name] = min(values)
            maxima[name] = max(values)

    tracks: list[TrackComparison] = []
    for row, measurements in zip(rows, measurement_sets, strict=True):
        deviations: dict[str, float | None] = {}
        outliers: list[str] = []
        for name in NUMERIC_MEASUREMENTS:
            value = measurements.get(name)
            if not isinstance(value, int | float) or name not in medians:
                deviations[name] = None
                continue
            deviation = float(value) - medians[name]
            deviations[name] = round(deviation, 4)
            population: list[float] = []
            for item in measurement_sets:
                candidate = item.get(name)
                if isinstance(candidate, int | float) and not isinstance(candidate, bool):
                    population.append(float(candidate))
            absolute_deviations = [abs(item - medians[name]) for item in population]
            mad = median(absolute_deviations) if absolute_deviations else 0.0
            floor = {
                "integratedLoudnessLufs": 1.5,
                "stereoWidth": 0.12,
                "phaseCorrelation": 0.15,
                "spectralCentroidHz": 500.0,
                "samplePeakDbfs": 1.5,
            }.get(name, 0.0)
            if len(population) >= 3 and abs(deviation) > max(floor, mad * 3.0):
                outliers.append(name)
        warnings: list[str] = []
        transition_type = TransitionType(str(row["transition_type"]))
        if transition_type == TransitionType.CROSSFADE:
            warnings.append(
                "The adjacent transition is a mixed crossfade; stable-core measurements should be preferred."
            )
        stable_seconds = max(
            0.0,
            float(row["stable_core_end_seconds"]) - float(row["stable_core_start_seconds"]),
        )
        if stable_seconds < 20:
            warnings.append("No sufficiently long stable core was available for uncontaminated comparison.")
        analysis = row.get("analysis")
        if isinstance(analysis, dict):
            analysis_warnings = analysis.get("warnings")
            if isinstance(analysis_warnings, list):
                warnings.extend(str(item)[:500] for item in analysis_warnings if isinstance(item, str))
        else:
            warnings.append("Analysis is missing or withheld for this reviewed segment.")
        tracks.append(
            TrackComparison(
                segment_id=str(row["id"]),
                order=int(row["sequence_index"]),
                label=str(row["label"]),
                source_start_seconds=float(row["start_seconds"]),
                source_end_seconds=float(row["end_seconds"]),
                duration_seconds=float(row["end_seconds"]) - float(row["start_seconds"]),
                boundary_confidence=Confidence(str(row["confidence"])),
                transition_type=transition_type,
                stable_core_seconds=stable_seconds,
                measurements=measurements,
                deviations=deviations,
                outliers=outliers,
                warnings=list(dict.fromkeys(warnings)),
            )
        )
    observations: list[str] = []
    for track in tracks:
        if track.outliers:
            observations.append(
                f"Track {track.order + 1} differs materially from the set median for {', '.join(track.outliers)}."
            )
    return MasteringReport(
        batch_id=batch_id,
        generated_at=datetime.now(UTC),
        tracks=tracks,
        medians={key: round(value, 4) for key, value in medians.items()},
        minima={key: round(value, 4) for key, value in minima.items()},
        maxima={key: round(value, 4) for key, value in maxima.items()},
        withheld_counts=withheld,
        observations=observations,
        estimator_limitations=[
            "This is an analytical comparison, not an automatic mastering decision or target prescription.",
            "Sample peak is not true peak. No universal LUFS, tonal-balance, or stereo target is assumed.",
            "Crossfaded material contains both tracks; TrackPrompt does not claim source separation.",
            "Missing or low-confidence measurements remain withheld rather than manufactured.",
        ],
    )


def report_markdown(report: MasteringReport) -> str:
    lines = [
        "# TrackPrompt mastering comparison",
        "",
        f"Batch: `{report.batch_id}`",
        f"Generated: {report.generated_at.isoformat()}",
        "",
        "| # | Track | Range (s) | LUFS | Sample peak dBFS | Stereo | Phase | Outliers |",
        "| ---: | --- | --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for track in report.tracks:
        measurement = track.measurements
        lines.append(
            f"| {track.order + 1} | {track.label} | {track.source_start_seconds:.3f}-{track.source_end_seconds:.3f} "
            f"| {_measurement_text(measurement, 'integratedLoudnessLufs')} "
            f"| {_measurement_text(measurement, 'samplePeakDbfs')} "
            f"| {_measurement_text(measurement, 'stereoWidth')} "
            f"| {_measurement_text(measurement, 'phaseCorrelation')} "
            f"| {', '.join(track.outliers) or 'none'} |"
        )
    lines.extend(["", "## Set medians", ""])
    lines.extend(f"- {key}: {value}" for key, value in report.medians.items())
    lines.extend(["", "## Observations", ""])
    lines.extend(f"- {item}" for item in (report.observations or ["No configured outlier rule fired."]))
    lines.extend(["", "## Estimator limitations", ""])
    lines.extend(f"- {item}" for item in report.estimator_limitations)
    return "\n".join(lines) + "\n"


def report_csv(report: MasteringReport) -> str:
    output = io.StringIO(newline="")
    fields = [
        "order", "segment_id", "label", "source_start_seconds", "source_end_seconds",
        "duration_seconds", "boundary_confidence", "transition_type", "stable_core_seconds",
        *NUMERIC_MEASUREMENTS, "outliers", "warnings",
    ]
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for track in report.tracks:
        writer.writerow(
            {
                "order": track.order + 1,
                "segment_id": track.segment_id,
                "label": track.label,
                "source_start_seconds": track.source_start_seconds,
                "source_end_seconds": track.source_end_seconds,
                "duration_seconds": track.duration_seconds,
                "boundary_confidence": track.boundary_confidence.value,
                "transition_type": track.transition_type.value,
                "stable_core_seconds": track.stable_core_seconds,
                **{name: track.measurements.get(name) for name in NUMERIC_MEASUREMENTS},
                "outliers": ";".join(track.outliers),
                "warnings": ";".join(track.warnings),
            }
        )
    return output.getvalue()


def report_json(report: MasteringReport) -> bytes:
    return json.dumps(
        report.model_dump(mode="json", by_alias=True),
        ensure_ascii=False,
        indent=2,
        allow_nan=False,
    ).encode("utf-8")
