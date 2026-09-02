from __future__ import annotations

from ..store import JobStore
from .schemas import (
    ArtDirectionReviewCollection,
    CinematicPlanBundle,
    ShotPlan,
    StoryPlan,
)
from .validation import validate_cinematic_privacy, validate_plan_pair


def write_plan_bundle(store: JobStore, job_id: str, bundle: CinematicPlanBundle) -> None:
    validate_plan_pair(bundle.story_plan, bundle.shot_plan)
    store.write_json(
        job_id,
        "story-plan.json",
        bundle.story_plan.model_dump(mode="json", by_alias=True),
    )
    try:
        store.write_json(
            job_id,
            "shot-plan.json",
            bundle.shot_plan.model_dump(mode="json", by_alias=True),
        )
    except Exception:
        store.delete_json(job_id, "story-plan.json")
        raise


def read_plan_bundle(store: JobStore, job_id: str) -> CinematicPlanBundle | None:
    story_payload = store.read_json(job_id, "story-plan.json")
    shot_payload = store.read_json(job_id, "shot-plan.json")
    if story_payload is None and shot_payload is None:
        return None
    if story_payload is None or shot_payload is None:
        raise ValueError("stored cinematic plan is incomplete")
    story = StoryPlan.model_validate(story_payload)
    shots = ShotPlan.model_validate(shot_payload)
    validate_plan_pair(story, shots)
    return CinematicPlanBundle(story_plan=story, shot_plan=shots)


def read_reviews(store: JobStore, job_id: str) -> ArtDirectionReviewCollection:
    payload = store.read_json(job_id, "art-direction-reviews.json")
    return ArtDirectionReviewCollection.model_validate(payload or {"reviews": []})


def write_reviews(store: JobStore, job_id: str, reviews: ArtDirectionReviewCollection) -> None:
    payload = reviews.model_dump(mode="json", by_alias=True)
    validate_cinematic_privacy(payload)
    store.write_json(job_id, "art-direction-reviews.json", payload)
