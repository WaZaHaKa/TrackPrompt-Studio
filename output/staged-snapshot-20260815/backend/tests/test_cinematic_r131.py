from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.cinematic.r131 import (
    R131ProvisionalSelection,
    R131RefinementStatus,
    r131_selected_for_refinement_status,
)


def test_r131_records_exact_provisional_selection_without_approval() -> None:
    payload = r131_selected_for_refinement_status().model_dump(by_alias=True)
    assert payload["selection"] == {
        "protagonistDesign": "protagonist-b-ancient-engine",
        "architecturalMaterialLanguage": "weathered-stone-metal-crystal-v1",
        "gateConstruction": "nested-ring-monolith-v1",
        "exposureLightingTreatment": "restrained-teal-cyan-amber-v1",
        "status": "selected-for-refinement",
        "artistApproved": False,
        "humanArtistApproval": "pending",
    }
    assert payload["motionProof"] == "ready"
    assert payload["durationSeconds"] == 4.0
    assert payload["calibrationReadiness"] == "blocked"
    assert payload["productionAuthorization"] is False


def test_r131_rejects_a_different_selected_identity() -> None:
    with pytest.raises(ValidationError):
        R131ProvisionalSelection(protagonistDesign="protagonist-a-directional-shell")


def test_r131_rejects_motion_duration_outside_deterministic_contract() -> None:
    with pytest.raises(ValidationError, match="four-second"):
        R131RefinementStatus(durationSeconds=3.5)


def test_r131_schema_cannot_be_artist_approved() -> None:
    with pytest.raises(ValidationError):
        R131RefinementStatus(artistApproved=True)
