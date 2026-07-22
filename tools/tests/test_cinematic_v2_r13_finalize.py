from __future__ import annotations

import pytest

from tools.finalize_cinematic_v2_r13_lookdev import R13_VARIANT_IDS, build_r13_review


def _render_manifest() -> dict[str, object]:
    return {"variants": [{"id": identifier} for identifier in R13_VARIANT_IDS]}


def _diagnostics(*, review_count: int = 0) -> dict[str, object]:
    return {
        "variants": [{"id": identifier} for identifier in R13_VARIANT_IDS],
        "summary": {
            "ordinaryNearBlackReviewCount": review_count,
            "subjectSeparationReviewCount": 0,
            "gateSeparationReviewCount": 0,
        },
    }


def test_r13_review_recommends_without_selecting_or_approving() -> None:
    review = build_r13_review(_render_manifest(), _diagnostics())
    assert review["codexAssistedRecommendation"]["protagonistDesign"] == (
        "protagonist-b-ancient-engine"
    )
    assert review["codexAssistedRecommendation"]["approvalGranted"] is False
    assert review["selectedLook"] == {
        "protagonistDesign": None,
        "architecturalMaterialLanguage": None,
        "gateConstruction": None,
        "exposureLightingTreatment": None,
        "status": "pending-human-operator-selection",
    }
    assert review["humanReview"]["artistApproved"] is False
    assert review["motionTest"]["rendered"] is False
    assert review["motionTest"]["status"] == "blocked-pending-look-selection"


def test_r13_review_rejects_unresolved_near_black_flag() -> None:
    with pytest.raises(ValueError, match="unresolved diagnostic review flags"):
        build_r13_review(_render_manifest(), _diagnostics(review_count=1))


def test_r13_review_rejects_variant_order_drift() -> None:
    render = _render_manifest()
    variants = render["variants"]
    assert isinstance(variants, list)
    variants.reverse()
    with pytest.raises(ValueError, match="identity or ordering"):
        build_r13_review(render, _diagnostics())
