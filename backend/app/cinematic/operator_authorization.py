from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator

from ..schemas import APIModel
from .production_contracts import (
    AndromedaV2FinalCalibration,
    AndromedaV2TechnicalAuthorization,
    EnabledFinalOutputMatrix,
    OperatorStartGateStatus,
    canonical_sha256,
    file_sha256,
)

HORIZONTAL_VARIANT_ID = "horizontal-16x9-1080p"
VERTICAL_VARIANT_ID = "vertical-9x16-1080p"
_HEX_64 = r"^[0-9a-f]{64}$"
_CURRENT_RELEASE_HOLD_CANONICAL_SHA256 = (
    "e32ff8053329f7bb72565d6145791339cc28adca4ce8ec7698e7bd016247ab21"
)


class OperatorAuthorizationError(ValueError):
    """A fail-closed operator-authorization validation failure."""


class AndromedaV2OperatorStartAuthorization(APIModel):
    schema_version: Literal["1.1.0"]
    kind: Literal["trackprompt-final-operator-start-authorization"]
    authorization_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,119}$")
    status: Literal["authorized"]
    authorized_by_role: Literal["project-owner-operator"]
    authorized_at: datetime
    decision: Literal["start-or-resume-enabled-output-matrix"]
    explicit_full_render_start_authorized: Literal[True]
    package_id: Literal["andromeda-v2-final-render-package-v2"]
    technical_authorization_id: Literal[
        "andromeda-v2-technical-authorization-v2"
    ]
    calibration_id: Literal["andromeda-v2-final-calibration-v2"]
    technical_authorization_sha256: str = Field(pattern=_HEX_64)
    calibration_sha256: str = Field(pattern=_HEX_64)
    package_manifest_sha256: str = Field(pattern=_HEX_64)
    release_identity_sha256: str = Field(pattern=_HEX_64)
    output_matrix_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,119}$")
    enabled_variant_ids: list[
        Literal["horizontal-16x9-1080p", "vertical-9x16-1080p"]
    ] = Field(min_length=1, max_length=2)
    output_matrix_sha256: str = Field(pattern=_HEX_64)
    confirmation_phrase_sha256: str = Field(pattern=_HEX_64)

    @field_validator("authorized_at")
    @classmethod
    def timezone_bound_authorization(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("operator authorization timestamp must include a timezone")
        return value

    @model_validator(mode="after")
    def exact_matrix_shape(self) -> AndromedaV2OperatorStartAuthorization:
        expected = [HORIZONTAL_VARIANT_ID]
        if len(self.enabled_variant_ids) == 2:
            expected.append(VERTICAL_VARIANT_ID)
        if self.enabled_variant_ids != expected:
            raise ValueError(
                "operator authorization must bind horizontal-only or ordered "
                "horizontal-plus-vertical variants"
            )
        return self


class AndromedaV2ReleaseHold(APIModel):
    schema_version: Literal["1.0.0"]
    kind: Literal["trackprompt-andromeda-v2-release-hold"]
    hold_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,119}$")
    recorded_at: date
    status: Literal["blocked"]
    release_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,119}$")
    release_identity_sha256: str = Field(pattern=_HEX_64)
    package_manifest_sha256: str = Field(pattern=_HEX_64)
    calibration_sha256: str = Field(pattern=_HEX_64)
    technical_authorization_sha256: str = Field(pattern=_HEX_64)
    production_start_eligible: Literal[False]
    finish_line_sprint_complete: Literal[False]
    artistic_equivalence_to_r131_proven: Literal[False]
    evidence_source: str = Field(min_length=1, max_length=240)
    findings: list[str] = Field(min_length=1)
    required_resolution: list[str] = Field(min_length=1)
    existing_immutable_evidence_unmodified: Literal[True]
    operator_start_artifact_must_not_be_created_or_used: Literal[True]
    full_production_render_started: Literal[False]


@dataclass(frozen=True)
class OperatorAuthorizationContext:
    calibration: AndromedaV2FinalCalibration
    technical_authorization: AndromedaV2TechnicalAuthorization
    calibration_sha256: str
    technical_authorization_sha256: str
    package_manifest_sha256: str
    output_matrix_sha256: str

    @property
    def output_matrix(self) -> EnabledFinalOutputMatrix:
        return self.calibration.identity.output_matrix


def expected_enabled_variant_ids(*, enable_vertical: bool) -> tuple[str, ...]:
    if enable_vertical:
        return (HORIZONTAL_VARIANT_ID, VERTICAL_VARIANT_ID)
    return (HORIZONTAL_VARIANT_ID,)


def _load_model(
    path: Path,
    model_type: type[AndromedaV2FinalCalibration]
    | type[AndromedaV2TechnicalAuthorization],
) -> AndromedaV2FinalCalibration | AndromedaV2TechnicalAuthorization:
    try:
        payload = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise OperatorAuthorizationError(
            f"required authorization input is unavailable: {path}"
        ) from exc
    try:
        return model_type.model_validate_json(payload)
    except ValueError as exc:
        raise OperatorAuthorizationError(
            f"required authorization input is invalid: {path.name}: {exc}"
        ) from exc


def load_operator_authorization_context(
    calibration_path: Path,
    technical_authorization_path: Path,
    *,
    package_manifest_path: Path,
) -> OperatorAuthorizationContext:
    calibration_model = _load_model(
        calibration_path,
        AndromedaV2FinalCalibration,
    )
    technical_model = _load_model(
        technical_authorization_path,
        AndromedaV2TechnicalAuthorization,
    )
    assert isinstance(calibration_model, AndromedaV2FinalCalibration)
    assert isinstance(technical_model, AndromedaV2TechnicalAuthorization)

    calibration_sha256 = file_sha256(calibration_path)
    technical_authorization_sha256 = file_sha256(technical_authorization_path)
    try:
        package_manifest_sha256 = file_sha256(package_manifest_path)
    except OSError as exc:
        raise OperatorAuthorizationError(
            "required package manifest is unavailable"
        ) from exc
    if calibration_model.identity != technical_model.identity:
        raise OperatorAuthorizationError(
            "calibration and technical authorization do not bind the same exact "
            "release and enabled output matrix"
        )
    if (
        calibration_model.release_identity_sha256
        != technical_model.release_identity_sha256
    ):
        raise OperatorAuthorizationError(
            "calibration and technical authorization release identities differ"
        )
    if calibration_model.calibration_id != technical_model.calibration_id:
        raise OperatorAuthorizationError(
            "technical authorization does not bind the supplied calibration ID"
        )
    if technical_model.calibration_sha256 != calibration_sha256:
        raise OperatorAuthorizationError(
            "technical authorization does not bind the supplied calibration hash"
        )
    if (
        not technical_model.technical_ready
        or technical_model.status != "technically-ready"
    ):
        raise OperatorAuthorizationError(
            "technical authorization is not technically ready"
        )
    if (
        technical_model.production_start_allowed
        or technical_model.final_render_started
        or technical_model.operator_start_gate.status
        != OperatorStartGateStatus.NOT_AUTHORIZED
        or technical_model.operator_start_gate.explicit_full_render_start_authorized
    ):
        raise OperatorAuthorizationError(
            "technical authorization must remain immutable and operator-gated; "
            "approval belongs only in the separate local operator artifact"
        )
    if (
        not all(
            variant.calibration_complete
            for variant in calibration_model.variant_calibrations
        )
        or not calibration_model.sla_satisfied
        or not calibration_model.deterministic_effects_verified
        or not calibration_model.all_required_bakes_complete
        or not calibration_model.dependency_health_passed
        or not calibration_model.vram_stable
        or not calibration_model.disk_headroom_satisfied
    ):
        raise OperatorAuthorizationError(
            "the supplied exact enabled-matrix calibration is not production ready"
        )

    matrix_payload = calibration_model.identity.output_matrix.model_dump(
        mode="json",
        by_alias=True,
    )
    return OperatorAuthorizationContext(
        calibration=calibration_model,
        technical_authorization=technical_model,
        calibration_sha256=calibration_sha256,
        technical_authorization_sha256=technical_authorization_sha256,
        package_manifest_sha256=package_manifest_sha256,
        output_matrix_sha256=canonical_sha256(matrix_payload),
    )


def require_expected_matrix(
    context: OperatorAuthorizationContext,
    *,
    enable_vertical: bool,
) -> None:
    actual = tuple(str(value) for value in context.output_matrix.enabled_variant_ids)
    expected = expected_enabled_variant_ids(enable_vertical=enable_vertical)
    if actual != expected:
        requested = "horizontal-plus-vertical" if enable_vertical else "horizontal-only"
        raise OperatorAuthorizationError(
            f"the supplied release does not bind the requested {requested} "
            f"enabled matrix; expected {list(expected)}, found {list(actual)}"
        )


def enforce_operator_release_hold(
    context: OperatorAuthorizationContext,
    release_hold_path: Path,
    *,
    package_manifest_sha256: str,
) -> None:
    try:
        release_hold_payload = release_hold_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise OperatorAuthorizationError(
            "the configured release-hold record is unavailable"
        ) from exc
    try:
        release_hold = AndromedaV2ReleaseHold.model_validate_json(
            release_hold_payload
        )
    except ValueError as exc:
        raise OperatorAuthorizationError(
            f"the configured release-hold record is invalid: {exc}"
        ) from exc

    release_hold_sha256 = canonical_sha256(
        release_hold.model_dump(mode="json", by_alias=True)
    )
    if release_hold_sha256 != _CURRENT_RELEASE_HOLD_CANONICAL_SHA256:
        raise OperatorAuthorizationError(
            "the configured release-hold record does not match the immutable "
            "current hold"
        )

    if (
        release_hold.release_identity_sha256
        != context.calibration.release_identity_sha256
    ):
        return
    if release_hold.release_id != context.calibration.identity.release_id:
        raise OperatorAuthorizationError(
            "the release-hold identity is internally inconsistent"
        )
    if release_hold.calibration_sha256 != context.calibration_sha256:
        raise OperatorAuthorizationError(
            "the release-hold calibration binding differs for the same release identity"
        )
    if release_hold.package_manifest_sha256 != package_manifest_sha256:
        raise OperatorAuthorizationError(
            "the release-hold package-manifest binding differs for the same "
            "release identity"
        )
    if (
        release_hold.technical_authorization_sha256
        != context.technical_authorization_sha256
    ):
        raise OperatorAuthorizationError(
            "the release-hold technical-authorization binding differs for the "
            "same release identity"
        )
    raise OperatorAuthorizationError(
        f"release start is blocked by {release_hold.hold_id}; corrected bounded "
        "visual proof and a fresh exact release identity are required"
    )


def operator_confirmation_phrase(context: OperatorAuthorizationContext) -> str:
    variants = ",".join(
        str(value) for value in context.output_matrix.enabled_variant_ids
    )
    return (
        "AUTHORIZE ANDROMEDA V2 START OR RESUME | "
        f"RELEASE {context.calibration.release_identity_sha256} | "
        f"PACKAGE {context.package_manifest_sha256} | "
        f"MATRIX {context.output_matrix.matrix_id} | "
        f"VARIANTS {variants}"
    )


def _confirmation_phrase_sha256(context: OperatorAuthorizationContext) -> str:
    return hashlib.sha256(operator_confirmation_phrase(context).encode("utf-8")).hexdigest()


def _write_new_json(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise OperatorAuthorizationError(
            "operator authorization already exists and will not be overwritten"
        ) from exc
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload.encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def create_operator_start_authorization(
    calibration_path: Path,
    technical_authorization_path: Path,
    output_path: Path,
    *,
    package_manifest_path: Path,
    typed_confirmation: str,
    enable_vertical: bool,
    authorized_at: datetime | None = None,
    authorization_id: str | None = None,
) -> AndromedaV2OperatorStartAuthorization:
    context = load_operator_authorization_context(
        calibration_path,
        technical_authorization_path,
        package_manifest_path=package_manifest_path,
    )
    require_expected_matrix(context, enable_vertical=enable_vertical)
    expected_confirmation = operator_confirmation_phrase(context)
    if not hmac.compare_digest(typed_confirmation, expected_confirmation):
        raise OperatorAuthorizationError(
            "typed confirmation did not exactly match the release-bound phrase; "
            "no operator authorization was written"
        )

    timestamp = authorized_at or datetime.now(UTC)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise OperatorAuthorizationError(
            "operator authorization timestamp must include a timezone"
        )
    generated_id = authorization_id or (
        "andromeda-v2-operator-start-"
        f"{timestamp.astimezone(UTC).strftime('%Y%m%d-%H%M%S')}-"
        f"{secrets.token_hex(4)}"
    )
    authorization = AndromedaV2OperatorStartAuthorization(
        schema_version="1.1.0",
        kind="trackprompt-final-operator-start-authorization",
        authorization_id=generated_id,
        status="authorized",
        authorized_by_role="project-owner-operator",
        authorized_at=timestamp,
        decision="start-or-resume-enabled-output-matrix",
        explicit_full_render_start_authorized=True,
        package_id=context.technical_authorization.package_id,
        technical_authorization_id=(
            context.technical_authorization.authorization_id
        ),
        calibration_id=context.calibration.calibration_id,
        technical_authorization_sha256=context.technical_authorization_sha256,
        calibration_sha256=context.calibration_sha256,
        package_manifest_sha256=context.package_manifest_sha256,
        release_identity_sha256=context.calibration.release_identity_sha256,
        output_matrix_id=context.output_matrix.matrix_id,
        enabled_variant_ids=[
            str(value) for value in context.output_matrix.enabled_variant_ids
        ],
        output_matrix_sha256=context.output_matrix_sha256,
        confirmation_phrase_sha256=_confirmation_phrase_sha256(context),
    )
    serialized = authorization.model_dump_json(by_alias=True, indent=2)
    _write_new_json(output_path, f"{serialized}\n")
    return authorization


def validate_operator_start_authorization(
    calibration_path: Path,
    technical_authorization_path: Path,
    operator_authorization_path: Path,
    *,
    package_manifest_path: Path,
    enable_vertical: bool,
) -> tuple[AndromedaV2OperatorStartAuthorization, OperatorAuthorizationContext]:
    context = load_operator_authorization_context(
        calibration_path,
        technical_authorization_path,
        package_manifest_path=package_manifest_path,
    )
    require_expected_matrix(context, enable_vertical=enable_vertical)
    try:
        authorization = AndromedaV2OperatorStartAuthorization.model_validate_json(
            operator_authorization_path.read_text(encoding="utf-8")
        )
    except OSError as exc:
        raise OperatorAuthorizationError(
            "the separate operator-start authorization is unavailable"
        ) from exc
    except ValueError as exc:
        raise OperatorAuthorizationError(
            f"the separate operator-start authorization is invalid: {exc}"
        ) from exc

    expected_bindings = {
        "package ID": (
            authorization.package_id,
            context.technical_authorization.package_id,
        ),
        "technical-authorization ID": (
            authorization.technical_authorization_id,
            context.technical_authorization.authorization_id,
        ),
        "calibration ID": (
            authorization.calibration_id,
            context.calibration.calibration_id,
        ),
        "technical-authorization hash": (
            authorization.technical_authorization_sha256,
            context.technical_authorization_sha256,
        ),
        "calibration hash": (
            authorization.calibration_sha256,
            context.calibration_sha256,
        ),
        "package-manifest hash": (
            authorization.package_manifest_sha256,
            context.package_manifest_sha256,
        ),
        "release identity": (
            authorization.release_identity_sha256,
            context.calibration.release_identity_sha256,
        ),
        "output-matrix ID": (
            authorization.output_matrix_id,
            context.output_matrix.matrix_id,
        ),
        "enabled output variants": (
            tuple(str(value) for value in authorization.enabled_variant_ids),
            tuple(str(value) for value in context.output_matrix.enabled_variant_ids),
        ),
        "output-matrix hash": (
            authorization.output_matrix_sha256,
            context.output_matrix_sha256,
        ),
        "typed-confirmation phrase": (
            authorization.confirmation_phrase_sha256,
            _confirmation_phrase_sha256(context),
        ),
    }
    for label, (actual, expected) in expected_bindings.items():
        if actual != expected:
            raise OperatorAuthorizationError(
                f"operator authorization has a stale or wrong {label} binding"
            )
    return authorization, context


def context_summary(
    context: OperatorAuthorizationContext,
    *,
    include_confirmation_phrase: bool,
) -> dict[str, object]:
    identity = context.calibration.identity
    result: dict[str, object] = {
        "schemaVersion": "1.0.0",
        "kind": "trackprompt-operator-authorization-context",
        "releaseId": identity.release_id,
        "projectId": identity.project_id,
        "packageId": context.technical_authorization.package_id,
        "technicalAuthorizationId": (
            context.technical_authorization.authorization_id
        ),
        "technicalAuthorizationSha256": (
            context.technical_authorization_sha256
        ),
        "calibrationId": context.calibration.calibration_id,
        "calibrationSha256": context.calibration_sha256,
        "packageManifestSha256": context.package_manifest_sha256,
        "releaseIdentitySha256": context.calibration.release_identity_sha256,
        "outputMatrixId": context.output_matrix.matrix_id,
        "outputMatrixSha256": context.output_matrix_sha256,
        "enabledVariantIds": [
            str(value) for value in context.output_matrix.enabled_variant_ids
        ],
        "variants": [
            {
                "id": str(variant.id),
                "sceneSha256": variant.scene_sha256,
                "renderProfileSha256": variant.render_profile_sha256,
                "outputPattern": variant.output_pattern,
            }
            for variant in context.output_matrix.variants
        ],
        "sourceAudioSha256": identity.source_audio_sha256,
        "sourceCueSha256": identity.source_cue_sha256,
        "ownerCreativeAcceptanceSha256": (
            identity.owner_creative_acceptance_sha256
        ),
        "encodingProfilesSha256": identity.encoding_profiles_sha256,
        "technicalReady": context.technical_authorization.technical_ready,
        "committedTechnicalAuthorizationRemainsOperatorGated": True,
    }
    if include_confirmation_phrase:
        result["requiredTypedConfirmation"] = operator_confirmation_phrase(context)
    return result


def read_json(path: Path) -> dict[str, object]:
    """Read a JSON object for tests and thin command adapters."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OperatorAuthorizationError(f"could not read JSON object: {path}") from exc
    if not isinstance(value, dict):
        raise OperatorAuthorizationError(f"JSON document must be an object: {path}")
    return value
