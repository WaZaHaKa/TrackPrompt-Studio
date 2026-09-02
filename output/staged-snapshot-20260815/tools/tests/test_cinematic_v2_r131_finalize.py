from __future__ import annotations

import pytest

from tools.finalize_cinematic_v2_r131_proof import (
    EXPECTED_REVIEW_IDS,
    build_r131_review,
)


def _render() -> dict[str, object]:
    return {
        "reviewStates": [{"id": identifier} for identifier in EXPECTED_REVIEW_IDS],
        "selection": {
            "protagonistDesign": "protagonist-b-ancient-engine",
            "architecturalMaterialLanguage": "weathered-stone-metal-crystal-v1",
            "gateConstruction": "nested-ring-monolith-v1",
            "exposureLightingTreatment": "restrained-teal-cyan-amber-v1",
            "status": "selected-for-refinement",
            "artistApproved": False,
            "humanArtistApproval": "pending",
        },
    }


def test_r131_review_recommends_revise_without_artistic_approval() -> None:
    review = build_r131_review(
        _render(),
        {"technicalPass": True},
        {"summary": {"technicalPass": True}},
    )
    assert review["codexAssistedRecommendation"]["decision"] == "REVISE"
    assert review["codexAssistedRecommendation"]["approvalGranted"] is False
    assert review["motionProof"]["status"] == "complete"
    assert review["humanReview"] == {
        "status": "pending",
        "reviewer": None,
        "artistApproved": False,
    }
    assert review["calibrationReadiness"] == "blocked"
    assert review["productionAuthorization"] is False


def test_r131_review_rejects_failed_motion_diagnostics() -> None:
    with pytest.raises(ValueError, match="motion diagnostics"):
        build_r131_review(
            _render(),
            {"technicalPass": False},
            {"summary": {"technicalPass": True}},
        )


def test_r131_review_rejects_automatic_approval() -> None:
    render = _render()
    render["selection"]["artistApproved"] = True
    with pytest.raises(ValueError, match="cannot be artist-approved"):
        build_r131_review(
            render,
            {"technicalPass": True},
            {"summary": {"technicalPass": True}},
        )
