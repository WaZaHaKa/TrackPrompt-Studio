from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class R131ProvisionalSelection(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    protagonist_design: Literal["protagonist-b-ancient-engine"] = Field(
        default="protagonist-b-ancient-engine",
        alias="protagonistDesign",
    )
    architectural_material_language: Literal["weathered-stone-metal-crystal-v1"] = Field(
        default="weathered-stone-metal-crystal-v1",
        alias="architecturalMaterialLanguage",
    )
    gate_construction: Literal["nested-ring-monolith-v1"] = Field(
        default="nested-ring-monolith-v1",
        alias="gateConstruction",
    )
    exposure_lighting_treatment: Literal["restrained-teal-cyan-amber-v1"] = Field(
        default="restrained-teal-cyan-amber-v1",
        alias="exposureLightingTreatment",
    )
    status: Literal["selected-for-refinement"] = "selected-for-refinement"
    artist_approved: Literal[False] = Field(default=False, alias="artistApproved")
    human_artist_approval: Literal["pending"] = Field(
        default="pending",
        alias="humanArtistApproval",
    )


class R131RefinementStatus(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    revision_id: Literal["andromeda-r13.1-selected-refinement"] = Field(
        default="andromeda-r13.1-selected-refinement",
        alias="revisionId",
    )
    preview_only: Literal[True] = Field(default=True, alias="previewOnly")
    selection: R131ProvisionalSelection = Field(default_factory=R131ProvisionalSelection)
    motion_proof: Literal["ready", "complete"] = Field(default="ready", alias="motionProof")
    duration_seconds: float = Field(default=4.0, alias="durationSeconds", ge=3.0, le=5.0)
    artist_approved: Literal[False] = Field(default=False, alias="artistApproved")
    human_artist_approval: Literal["pending"] = Field(
        default="pending",
        alias="humanArtistApproval",
    )
    calibration_readiness: Literal["blocked"] = Field(
        default="blocked",
        alias="calibrationReadiness",
    )
    production_authorization: Literal[False] = Field(
        default=False,
        alias="productionAuthorization",
    )

    @model_validator(mode="after")
    def enforce_provisional_boundary(self) -> R131RefinementStatus:
        if self.selection.artist_approved or self.selection.human_artist_approval != "pending":
            raise ValueError("R13.1 selection is provisional and cannot contain approval.")
        if self.duration_seconds != 4.0:
            raise ValueError("R13.1 uses one deterministic four-second motion proof.")
        return self


def r131_selected_for_refinement_status() -> R131RefinementStatus:
    return R131RefinementStatus()
