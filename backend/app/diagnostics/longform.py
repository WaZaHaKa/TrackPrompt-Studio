from __future__ import annotations

import argparse
import json
import tracemalloc
from pathlib import Path

from ..catalog.segmentation import segment_longform_source
from ..catalog.store import CatalogueStore
from ..config import Settings
from ..media import probe_media


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a bounded private long-form segmentation diagnostic.")
    source_group = parser.add_mutually_exclusive_group()
    source_group.add_argument("--asset-id")
    source_group.add_argument("--source", type=Path)
    args = parser.parse_args()
    settings = Settings.from_env()
    output: dict[str, object] = {
        "supportedDurationSeconds": settings.max_longform_duration_seconds,
        "scanCadenceSeconds": settings.longform_scan_cadence_seconds,
        "scanChunkSeconds": settings.longform_scan_chunk_seconds,
        "fullSourceStft": False,
    }
    source: Path | None = None
    asset_id = args.asset_id
    duration_seconds = 0.0
    if asset_id:
        store = CatalogueStore(settings)
        asset = store.get_asset(asset_id)
        source = store.asset_source_path(asset_id)
        duration_seconds = float(asset["duration_seconds"])
    elif args.source:
        probe = probe_media(
            args.source,
            args.source.name,
            settings,
            max_bytes=settings.max_source_upload_bytes,
            max_duration_seconds=settings.max_longform_duration_seconds,
            source_kind="long-form diagnostic source",
        )
        source = args.source
        duration_seconds = probe.file.duration_seconds
        asset_id = "00000000-0000-4000-8000-000000000000"
    if source is not None and asset_id is not None:
        tracemalloc.start()
        result = segment_longform_source(
            asset_id,
            source,
            duration_seconds,
            settings,
        )
        _current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        output.update(
            {
                "observationCount": result.observation_count,
                "candidateCount": result.candidate_count,
                "refinedBoundaryCount": max(0, len(result.segments) - 1),
                "elapsedSeconds": result.elapsed_seconds,
                "sourceSecondsPerScanSecond": round(
                    duration_seconds / max(result.elapsed_seconds, 0.001), 3
                ),
                "peakPcmBufferBytes": result.peak_buffer_bytes,
                "peakPythonMemoryBytes": peak,
                "transitionTypes": sorted({item.transition_type.value for item in result.segments}),
            }
        )
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
