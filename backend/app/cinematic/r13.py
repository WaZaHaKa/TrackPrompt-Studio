from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

R13_REVISION_ID = "andromeda-r13-lookdev-lock"


class R13LookSelection(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    protagonist_design: str | None = Field(default=None, alias="protagonistDesign")
    architectural_material_language: str | None = Field(
        default=None,
        alias="architecturalMaterialLanguage",
    )
    gate_construction: str | None = Field(default=None, alias="gateConstruction")
    exposure_lighting_treatment: str | None = Field(
        default=None,
        alias="exposureLightingTreatment",
    )
    status: Literal["pending-human-operator-selection", "selected"] = (
        "pending-human-operator-selection"
    )

    @model_validator(mode="after")
    def validate_selection_completeness(self) -> R13LookSelection:
        choices = (
            self.protagonist_design,
            self.architectural_material_language,
            self.gate_construction,
            self.exposure_lighting_treatment,
        )
        if self.status == "selected" and any(choice is None for choice in choices):
            raise ValueError("A selected R13 look requires all four operator choices.")
        if self.status == "pending-human-operator-selection" and any(
            choice is not None for choice in choices
        ):
            raise ValueError("Pending R13 look selection cannot contain partial choices.")
        return self


class R13LookDevelopmentStatus(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    revision_id: Literal["andromeda-r13-lookdev-lock"] = Field(
        default="andromeda-r13-lookdev-lock",
        alias="revisionId",
    )
    preview_only: Literal[True] = Field(default=True, alias="previewOnly")
    structural: Literal["pass"] = "pass"
    look_development: Literal["ready-for-human-selection"] = Field(
        default="ready-for-human-selection",
        alias="lookDevelopment",
    )
    selected_look: R13LookSelection = Field(alias="selectedLook")
    human_artist_approval: Literal["pending", "approved", "revise"] = Field(
        default="pending",
        alias="humanArtistApproval",
    )
    artist_approved: bool = Field(default=False, alias="artistApproved")
    motion_test: Literal[
        "blocked-pending-look-selection",
        "ready-for-bounded-motion-test",
        "complete",
    ] = Field(default="blocked-pending-look-selection", alias="motionTest")
    calibration_readiness: Literal["blocked"] = Field(
        default="blocked",
        alias="calibrationReadiness",
    )
    production_authorization: Literal[False] = Field(
        default=False,
        alias="productionAuthorization",
    )

    @model_validator(mode="after")
    def enforce_human_gate(self) -> R13LookDevelopmentStatus:
        if self.human_artist_approval == "approved" and not self.artist_approved:
            raise ValueError("Approved human review and artistApproved must agree.")
        if self.artist_approved and self.human_artist_approval != "approved":
            raise ValueError("R13 artistic approval must come from an approved human review.")
        if self.artist_approved and self.selected_look.status != "selected":
            raise ValueError("R13 artistic approval requires all four operator choices.")
        if self.selected_look.status == "pending-human-operator-selection":
            if self.motion_test != "blocked-pending-look-selection":
                raise ValueError("R13 motion testing is blocked until all look choices are selected.")
        elif self.motion_test == "blocked-pending-look-selection":
            raise ValueError("A fully selected R13 look may advance to the bounded motion test.")
        return self


def pending_r13_status() -> R13LookDevelopmentStatus:
    return R13LookDevelopmentStatus(
        selected_look=R13LookSelection(),
    )
