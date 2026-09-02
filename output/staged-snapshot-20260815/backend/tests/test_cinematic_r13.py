from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.cinematic.r13 import R13LookDevelopmentStatus, R13LookSelection, pending_r13_status


def test_pending_r13_status_is_preview_only_and_blocks_motion() -> None:
    status = pending_r13_status().model_dump(by_alias=True)
    assert status == {
        "revisionId": "andromeda-r13-lookdev-lock",
        "previewOnly": True,
        "structural": "pass",
        "lookDevelopment": "ready-for-human-selection",
        "selectedLook": {
            "protagonistDesign": None,
            "architecturalMaterialLanguage": None,
            "gateConstruction": None,
            "exposureLightingTreatment": None,
            "status": "pending-human-operator-selection",
        },
        "humanArtistApproval": "pending",
        "artistApproved": False,
        "motionTest": "blocked-pending-look-selection",
        "calibrationReadiness": "blocked",
        "productionAuthorization": False,
    }


def test_r13_rejects_partial_selection() -> None:
    with pytest.raises(ValidationError, match="partial choices"):
        R13LookSelection(protagonistDesign="protagonist-b-ancient-engine")


def test_r13_rejects_motion_before_operator_selection() -> None:
    with pytest.raises(ValidationError, match="motion testing is blocked"):
        R13LookDevelopmentStatus(
            selectedLook=R13LookSelection(),
            motionTest="ready-for-bounded-motion-test",
        )


def test_r13_rejects_automatic_artistic_approval() -> None:
    with pytest.raises(ValidationError, match="approval must come from"):
        R13LookDevelopmentStatus(
            selectedLook=R13LookSelection(),
            artistApproved=True,
        )


def test_r13_rejects_approval_before_all_operator_choices() -> None:
    with pytest.raises(ValidationError, match="all four operator choices"):
        R13LookDevelopmentStatus(
            selectedLook=R13LookSelection(),
            humanArtistApproval="approved",
            artistApproved=True,
        )
