from __future__ import annotations

import argparse
import json

import numpy as np

from ..config import Settings
from ..tagging.music import WINDOW_WEIGHTING_METHOD, TransformersClapMusicTagger


def _bounded_club_sample(sample_rate: int = 16_000, duration: float = 4.0) -> np.ndarray:
    sample_count = int(sample_rate * duration)
    signal = np.zeros(sample_count, dtype=np.float32)
    beat_seconds = 60.0 / 128.0
    hit_time = np.arange(int(sample_rate * 0.18), dtype=np.float32) / sample_rate
    kick = 0.22 * np.sin(2 * np.pi * 55.0 * hit_time) * np.exp(-20.0 * hit_time)
    for position in np.arange(0.0, duration, beat_seconds):
        start = int(position * sample_rate)
        end = min(sample_count, start + kick.size)
        signal[start:end] += kick[: end - start]
    time = np.arange(sample_count, dtype=np.float32) / sample_rate
    signal += 0.025 * np.sin(2 * np.pi * 110.0 * time)
    return np.clip(signal, -1.0, 1.0)


def _rank(scores: dict[str, float], limit: int = 3) -> list[dict[str, object]]:
    return [
        {"id": item_id, "similarity": round(score, 5)}
        for item_id, score in sorted(scores.items(), key=lambda item: item[1], reverse=True)[
            :limit
        ]
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke", action="store_true")
    arguments = parser.parse_args()
    adapter = TransformersClapMusicTagger(Settings.from_env())
    capability = adapter.capability()
    payload: dict[str, object] = capability.model_dump(mode="json", by_alias=True)
    payload.update(
        {
            "taxonomyVersion": adapter.taxonomy.taxonomy_version,
            "scoreType": "cosine similarity (not a calibrated probability)",
            "representativeWindowWeightingMethod": WINDOW_WEIGHTING_METHOD,
            "hierarchyStages": [
                "broad family",
                "family-gated subgenre",
                "separate descriptive tags",
            ],
        }
    )
    if arguments.smoke and capability.available:
        signal = _bounded_club_sample()
        broad_scores = adapter._entry_similarities(signal, adapter.taxonomy.broad_genres)
        broad_ids = [
            item_id
            for item_id, _score in sorted(
                broad_scores.items(),
                key=lambda item: item[1],
                reverse=True,
            )[:2]
        ]
        subgenres = [
            item for item in adapter.taxonomy.subgenres if item.parent in broad_ids
        ]
        subgenre_scores = adapter._entry_similarities(signal, subgenres) if subgenres else {}
        tag_scores = adapter._entry_similarities(signal, adapter.taxonomy.descriptive_tags)
        top_subgenre_id = (
            max(subgenre_scores, key=lambda item_id: subgenre_scores[item_id])
            if subgenre_scores
            else None
        )
        top_subgenre = next(
            (item for item in subgenres if item.id == top_subgenre_id),
            None,
        )
        hierarchy_consistent = top_subgenre is None or top_subgenre.parent in broad_ids
        electronic_club_regression = (
            broad_scores.get("electronic-dance", -1.0)
            > broad_scores.get("r-and-b-soul", -1.0)
            and max(broad_scores, key=lambda item_id: broad_scores[item_id])
            != "experimental"
            and top_subgenre is not None
            and top_subgenre.parent == "electronic-dance"
        )
        all_scores = [*broad_scores.values(), *subgenre_scores.values(), *tag_scores.values()]
        payload.update(
            {
                "tinyInference": bool(all_scores) and all(
                    np.isfinite(value) for value in all_scores
                ),
                "broadFamilySampleResult": _rank(broad_scores),
                "subgenreSampleResult": _rank(subgenre_scores),
                "descriptiveTagSampleResult": _rank(tag_scores),
                "hierarchyConsistent": hierarchy_consistent,
                "electronicClubRegression": electronic_club_regression,
                "sampleDescription": (
                    "bounded synthetic four-on-the-floor pulse; diagnostic ranking is a "
                    "smoke test, not an accuracy claim"
                ),
            }
        )
    adapter.cleanup()
    print(json.dumps(payload, indent=2))
    smoke_passed = (
        payload.get("tinyInference") is True
        and payload.get("hierarchyConsistent") is True
        and payload.get("electronicClubRegression") is True
    )
    return 0 if capability.available and (not arguments.smoke or smoke_passed) else 1


if __name__ == "__main__":
    raise SystemExit(main())
