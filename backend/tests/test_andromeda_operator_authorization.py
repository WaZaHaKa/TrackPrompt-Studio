from __future__ import annotations

import copy
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.cinematic.operator_authorization import (
    HORIZONTAL_VARIANT_ID,
    VERTICAL_VARIANT_ID,
    OperatorAuthorizationError,
    create_operator_start_authorization,
    enforce_operator_release_hold,
    load_operator_authorization_context,
    operator_confirmation_phrase,
    validate_operator_start_authorization,
)
from app.cinematic.production_contracts import (
    AndromedaV2FinalCalibration,
    AndromedaV2FinalPackageManifest,
    AndromedaV2TechnicalAuthorization,
    EnabledFinalOutputVariant,
    FinalReleaseIdentity,
    canonical_sha256,
    file_sha256,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_ROOT = REPOSITORY_ROOT / "production" / "andromeda-v2"
CALIBRATION_PATH = PRODUCTION_ROOT / "v2-calibration.json"
PACKAGE_MANIFEST_PATH = PRODUCTION_ROOT / "package-manifest-v2.json"
TECHNICAL_AUTHORIZATION_PATH = (
    PRODUCTION_ROOT / "technical-authorization-v2.json"
)
RELEASE_HOLD_PATH = PRODUCTION_ROOT / "release-hold.json"


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(
        f"{json.dumps(payload, ensure_ascii=True, indent=2)}\n",
        encoding="utf-8",
    )


def _create_horizontal_authorization(output_path: Path) -> None:
    context = load_operator_authorization_context(
        CALIBRATION_PATH,
        TECHNICAL_AUTHORIZATION_PATH,
        package_manifest_path=PACKAGE_MANIFEST_PATH,
    )
    create_operator_start_authorization(
        CALIBRATION_PATH,
        TECHNICAL_AUTHORIZATION_PATH,
        output_path,
        package_manifest_path=PACKAGE_MANIFEST_PATH,
        typed_confirmation=operator_confirmation_phrase(context),
        enable_vertical=False,
        authorized_at=datetime(2026, 7, 23, 12, 0, tzinfo=UTC),
        authorization_id="synthetic-test-horizontal-operator-start",
    )


def _dual_release_documents(tmp_path: Path) -> tuple[Path, Path]:
    calibration_payload = json.loads(
        CALIBRATION_PATH.read_text(encoding="utf-8")
    )
    technical_payload = json.loads(
        TECHNICAL_AUTHORIZATION_PATH.read_text(encoding="utf-8")
    )
    identity = copy.deepcopy(calibration_payload["identity"])
    matrix = identity["outputMatrix"]
    matrix["matrixId"] = "synthetic-test-horizontal-plus-vertical-v2"
    matrix["enabledVariantIds"] = [
        HORIZONTAL_VARIANT_ID,
        VERTICAL_VARIANT_ID,
    ]
    matrix["variants"].append(
        {
            "id": VERTICAL_VARIANT_ID,
            "enabled": True,
            "required": False,
            "width": 1080,
            "height": 1920,
            "fps": 30,
            "frameStart": 1,
            "frameEnd": 13029,
            "compositionProfileId": "andromeda-v2-vertical-master-v1",
            "cameraName": "TP_ANDROMEDA_V2_CAMERA_VERTICAL",
            "sceneSha256": "7" * 64,
            "renderProfileSha256": "8" * 64,
            "workerRequirementId": "local-rtx3060-eevee-v1",
            "outputPattern": (
                "final-output/andromeda-v2-vertical/frames/frame_######.png"
            ),
        }
    )
    identity_model = FinalReleaseIdentity.model_validate(identity)
    release_identity_sha256 = canonical_sha256(
        identity_model.model_dump(mode="json", by_alias=True)
    )

    calibration_payload["identity"] = identity
    calibration_payload["releaseIdentitySha256"] = release_identity_sha256
    calibration_payload["variantCalibrations"].append(
        {
            "outputVariantId": VERTICAL_VARIANT_ID,
            "sceneSha256": "7" * 64,
            "renderProfileSha256": "8" * 64,
            "compositionProfileId": "andromeda-v2-vertical-master-v1",
            "cameraName": "TP_ANDROMEDA_V2_CAMERA_VERTICAL",
            "workerRequirementId": "local-rtx3060-eevee-v1",
            "width": 1080,
            "height": 1920,
            "fps": 30,
            "sampleFrames": [652, 6150, 11987],
            "p50SecondsPerFrame": 0.75,
            "p90SecondsPerFrame": 1.0,
            "weightedSecondsPerFrame": 0.8,
            "projectedRenderSecondsP50": 9771.75,
            "projectedRenderSecondsP90": 13029.0,
            "projectedOutputBytes": 50_277_960_909,
            "calibrationComplete": True,
        }
    )
    stage_forecasts = calibration_payload["stageForecasts"]
    stage_forecasts[0]["p50Seconds"] += 9771.75
    stage_forecasts[0]["p90Seconds"] += 13029.0
    calibration_payload["aggregateP50Seconds"] = sum(
        stage["p50Seconds"] for stage in stage_forecasts
    )
    calibration_payload["aggregateP90Seconds"] = sum(
        stage["p90Seconds"] for stage in stage_forecasts
    )
    calibration_payload["projectedPeakDiskBytes"] = 112_000_000_000
    calibration_payload["diskHeadroomSatisfied"] = True
    calibration_model = AndromedaV2FinalCalibration.model_validate(
        calibration_payload
    )
    calibration_path = tmp_path / "synthetic-dual-calibration.json"
    _write_json(
        calibration_path,
        calibration_model.model_dump(mode="json", by_alias=True),
    )

    technical_payload["identity"] = identity
    technical_payload["releaseIdentitySha256"] = release_identity_sha256
    technical_payload["calibrationSha256"] = file_sha256(calibration_path)
    technical_model = AndromedaV2TechnicalAuthorization.model_validate(
        technical_payload
    )
    technical_path = tmp_path / "synthetic-dual-technical-authorization.json"
    _write_json(
        technical_path,
        technical_model.model_dump(mode="json", by_alias=True),
    )
    return calibration_path, technical_path


def test_valid_local_operator_authorization_preserves_technical_evidence(
    tmp_path: Path,
) -> None:
    calibration_before = file_sha256(CALIBRATION_PATH)
    technical_before = file_sha256(TECHNICAL_AUTHORIZATION_PATH)
    output_path = tmp_path / "operator-start.json"

    _create_horizontal_authorization(output_path)
    authorization, context = validate_operator_start_authorization(
        CALIBRATION_PATH,
        TECHNICAL_AUTHORIZATION_PATH,
        output_path,
        package_manifest_path=PACKAGE_MANIFEST_PATH,
        enable_vertical=False,
    )

    assert authorization.explicit_full_render_start_authorized is True
    assert authorization.enabled_variant_ids == [HORIZONTAL_VARIANT_ID]
    assert authorization.package_manifest_sha256 == file_sha256(
        PACKAGE_MANIFEST_PATH
    )
    assert context.technical_authorization.production_start_allowed is False
    assert (
        context.technical_authorization.operator_start_gate.status
        == "not-authorized"
    )
    assert file_sha256(CALIBRATION_PATH) == calibration_before
    assert file_sha256(TECHNICAL_AUTHORIZATION_PATH) == technical_before


def test_wrong_typed_confirmation_writes_nothing(tmp_path: Path) -> None:
    output_path = tmp_path / "operator-start.json"

    with pytest.raises(
        OperatorAuthorizationError,
        match="typed confirmation did not exactly match",
    ):
        create_operator_start_authorization(
            CALIBRATION_PATH,
            TECHNICAL_AUTHORIZATION_PATH,
            output_path,
            package_manifest_path=PACKAGE_MANIFEST_PATH,
            typed_confirmation="AUTHORIZE SOMETHING ELSE",
            enable_vertical=False,
        )

    assert not output_path.exists()


def test_current_exact_release_hold_blocks_operator_start() -> None:
    context = load_operator_authorization_context(
        CALIBRATION_PATH,
        TECHNICAL_AUTHORIZATION_PATH,
        package_manifest_path=PACKAGE_MANIFEST_PATH,
    )

    with pytest.raises(
        OperatorAuthorizationError,
        match="corrected bounded visual proof and a fresh exact release identity",
    ):
        enforce_operator_release_hold(
            context,
            RELEASE_HOLD_PATH,
            package_manifest_sha256=file_sha256(PACKAGE_MANIFEST_PATH),
        )


def test_current_release_hold_rejects_changed_package_binding() -> None:
    context = load_operator_authorization_context(
        CALIBRATION_PATH,
        TECHNICAL_AUTHORIZATION_PATH,
        package_manifest_path=PACKAGE_MANIFEST_PATH,
    )

    with pytest.raises(
        OperatorAuthorizationError,
        match="release-hold package-manifest binding differs",
    ):
        enforce_operator_release_hold(
            context,
            RELEASE_HOLD_PATH,
            package_manifest_sha256="f" * 64,
        )


def test_missing_release_hold_fails_closed(tmp_path: Path) -> None:
    context = load_operator_authorization_context(
        CALIBRATION_PATH,
        TECHNICAL_AUTHORIZATION_PATH,
        package_manifest_path=PACKAGE_MANIFEST_PATH,
    )

    with pytest.raises(
        OperatorAuthorizationError,
        match="configured release-hold record is unavailable",
    ):
        enforce_operator_release_hold(
            context,
            tmp_path / "missing-release-hold.json",
            package_manifest_sha256=file_sha256(PACKAGE_MANIFEST_PATH),
        )


def test_tampering_current_release_hold_identity_cannot_unblock(
    tmp_path: Path,
) -> None:
    context = load_operator_authorization_context(
        CALIBRATION_PATH,
        TECHNICAL_AUTHORIZATION_PATH,
        package_manifest_path=PACKAGE_MANIFEST_PATH,
    )
    release_hold_payload = json.loads(
        RELEASE_HOLD_PATH.read_text(encoding="utf-8")
    )
    release_hold_payload["releaseIdentitySha256"] = "f" * 64
    tampered_release_hold_path = tmp_path / "tampered-release-hold.json"
    _write_json(tampered_release_hold_path, release_hold_payload)

    with pytest.raises(
        OperatorAuthorizationError,
        match="does not match the immutable current hold",
    ):
        enforce_operator_release_hold(
            context,
            tampered_release_hold_path,
            package_manifest_sha256=file_sha256(PACKAGE_MANIFEST_PATH),
        )


def test_cli_revalidates_package_before_operator_gate(tmp_path: Path) -> None:
    package_payload = json.loads(
        PACKAGE_MANIFEST_PATH.read_text(encoding="utf-8")
    )
    package_payload["artifacts"][0]["sha256"] = "f" * 64
    tampered_package_path = tmp_path / "tampered-package-manifest-v2.json"
    _write_json(tampered_package_path, package_payload)

    result = subprocess.run(
        [
            sys.executable,
            str(REPOSITORY_ROOT / "tools" / "andromeda_operator_authorization.py"),
            "inspect",
            "--repository-root",
            str(REPOSITORY_ROOT),
            "--calibration",
            str(CALIBRATION_PATH),
            "--package-manifest",
            str(tampered_package_path),
            "--technical-authorization",
            str(TECHNICAL_AUTHORIZATION_PATH),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 2
    payload = json.loads(result.stderr.strip().splitlines()[-1])
    assert payload["ok"] is False
    assert "the exact final-release package is invalid" in (
        payload["error"]["message"]
    )
    assert payload["productionRenderStarted"] is False


def test_cli_resolves_relative_release_inputs_from_repository_root(
    tmp_path: Path,
) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(REPOSITORY_ROOT / "tools" / "andromeda_operator_authorization.py"),
            "inspect",
            "--repository-root",
            str(REPOSITORY_ROOT),
            "--calibration",
            "production/andromeda-v2/v2-calibration.json",
            "--package-manifest",
            "production/andromeda-v2/package-manifest-v2.json",
            "--technical-authorization",
            "production/andromeda-v2/technical-authorization-v2.json",
        ],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 2
    payload = json.loads(result.stderr.strip().splitlines()[-1])
    assert payload["ok"] is False
    assert "release start is blocked by" in payload["error"]["message"]
    assert "unavailable" not in payload["error"]["message"]
    assert payload["productionRenderStarted"] is False


def test_stale_operator_authorization_is_rejected(tmp_path: Path) -> None:
    output_path = tmp_path / "operator-start.json"
    _create_horizontal_authorization(output_path)
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    payload["technicalAuthorizationSha256"] = "f" * 64
    _write_json(output_path, payload)

    with pytest.raises(
        OperatorAuthorizationError,
        match="stale or wrong technical-authorization hash binding",
    ):
        validate_operator_start_authorization(
            CALIBRATION_PATH,
            TECHNICAL_AUTHORIZATION_PATH,
            output_path,
            package_manifest_path=PACKAGE_MANIFEST_PATH,
            enable_vertical=False,
        )


def test_same_identity_package_change_invalidates_operator_authorization(
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "operator-start.json"
    _create_horizontal_authorization(output_path)
    package_payload = json.loads(
        PACKAGE_MANIFEST_PATH.read_text(encoding="utf-8")
    )
    package_payload["artifacts"] = list(reversed(package_payload["artifacts"]))
    AndromedaV2FinalPackageManifest.model_validate(package_payload)
    changed_package_path = tmp_path / "same-identity-changed-package.json"
    _write_json(changed_package_path, package_payload)

    with pytest.raises(
        OperatorAuthorizationError,
        match="stale or wrong package-manifest hash binding",
    ):
        validate_operator_start_authorization(
            CALIBRATION_PATH,
            TECHNICAL_AUTHORIZATION_PATH,
            output_path,
            package_manifest_path=changed_package_path,
            enable_vertical=False,
        )


def test_enabled_variant_rejects_output_pattern_outside_frames_namespace() -> None:
    calibration_payload = json.loads(
        CALIBRATION_PATH.read_text(encoding="utf-8")
    )
    variant_payload = copy.deepcopy(
        calibration_payload["identity"]["outputMatrix"]["variants"][0]
    )
    variant_payload["outputPattern"] = (
        "final-output/andromeda-v2-horizontal/notframes/"
        "frame_######.png"
    )

    with pytest.raises(
        ValueError,
        match=r"frames/frame_######\.png",
    ):
        EnabledFinalOutputVariant.model_validate(variant_payload)


def test_wrong_enabled_matrix_is_rejected(tmp_path: Path) -> None:
    output_path = tmp_path / "operator-start.json"
    _create_horizontal_authorization(output_path)

    with pytest.raises(
        OperatorAuthorizationError,
        match="does not bind the requested horizontal-plus-vertical enabled matrix",
    ):
        validate_operator_start_authorization(
            CALIBRATION_PATH,
            TECHNICAL_AUTHORIZATION_PATH,
            output_path,
            package_manifest_path=PACKAGE_MANIFEST_PATH,
            enable_vertical=True,
        )


def test_valid_synthetic_dual_matrix_requires_separate_exact_documents(
    tmp_path: Path,
) -> None:
    calibration_path, technical_path = _dual_release_documents(tmp_path)
    context = load_operator_authorization_context(
        calibration_path,
        technical_path,
        package_manifest_path=PACKAGE_MANIFEST_PATH,
    )
    output_path = tmp_path / "synthetic-dual-operator-start.json"

    create_operator_start_authorization(
        calibration_path,
        technical_path,
        output_path,
        package_manifest_path=PACKAGE_MANIFEST_PATH,
        typed_confirmation=operator_confirmation_phrase(context),
        enable_vertical=True,
        authorized_at=datetime(2026, 7, 23, 12, 0, tzinfo=UTC),
        authorization_id="synthetic-test-dual-operator-start",
    )
    authorization, validated_context = validate_operator_start_authorization(
        calibration_path,
        technical_path,
        output_path,
        package_manifest_path=PACKAGE_MANIFEST_PATH,
        enable_vertical=True,
    )

    assert authorization.enabled_variant_ids == [
        HORIZONTAL_VARIANT_ID,
        VERTICAL_VARIANT_ID,
    ]
    assert [
        str(value)
        for value in validated_context.output_matrix.enabled_variant_ids
    ] == [HORIZONTAL_VARIANT_ID, VERTICAL_VARIANT_ID]


def test_operator_authorization_never_overwrites(tmp_path: Path) -> None:
    output_path = tmp_path / "operator-start.json"
    _create_horizontal_authorization(output_path)
    original = output_path.read_bytes()
    context = load_operator_authorization_context(
        CALIBRATION_PATH,
        TECHNICAL_AUTHORIZATION_PATH,
        package_manifest_path=PACKAGE_MANIFEST_PATH,
    )

    with pytest.raises(
        OperatorAuthorizationError,
        match="already exists and will not be overwritten",
    ):
        create_operator_start_authorization(
            CALIBRATION_PATH,
            TECHNICAL_AUTHORIZATION_PATH,
            output_path,
            package_manifest_path=PACKAGE_MANIFEST_PATH,
            typed_confirmation=operator_confirmation_phrase(context),
            enable_vertical=False,
        )

    assert output_path.read_bytes() == original
