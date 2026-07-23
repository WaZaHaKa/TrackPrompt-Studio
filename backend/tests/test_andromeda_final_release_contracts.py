from __future__ import annotations

import copy
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

from app.cinematic.production_contracts import (
    ENCODING_PROFILES_SHA256,
    OWNER_CREATIVE_ACCEPTANCE_SHA256,
    SOURCE_AUDIO_SHA256,
    SOURCE_CUE_SHA256,
    AndromedaV2FinalCalibration,
    AndromedaV2FinalPackageManifest,
    AndromedaV2FinalRelease,
    AndromedaV2TechnicalAuthorization,
    FinalLookProfile,
    FinalReleaseIdentity,
    FinalReleaseObjectiveGateId,
    OutputVariantSet,
    OwnerAttestedCreativeAcceptance,
    PackageManifest,
    ProductionAuthorization,
    ShotPlanV2,
    StoryPlanV2,
    file_sha256,
    final_release_identity_sha256,
    load_and_validate_final_release,
)

HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
CALIBRATION_SHA256 = "d" * 64
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]

REQUIRED_ARTIFACT_ROLES = (
    "final-scene",
    "output-variants",
    "story-plan",
    "shot-plan",
    "owner-creative-acceptance",
    "final-look-profile",
    "horizontal-render-profile",
    "encoding-profiles",
    "final-calibration-v2",
    "technical-authorization-v2",
    "deterministic-effects-and-disk-report",
    "live-dashboard-proof",
    "full-audio-animatic",
    "animatic-media-qa-report",
    "dependency-health-report",
    "source-revision-report",
    "worker-requirements",
    "release-report",
)

GATE_EVIDENCE = {
    "deterministic-effects-and-disk": ["deterministic-effects-and-disk-report"],
    "live-dashboard": ["live-dashboard-proof"],
    "animatic-and-media-qa": [
        "full-audio-animatic",
        "animatic-media-qa-report",
    ],
    "calibration-and-enabled-matrix-sla": ["final-calibration-v2"],
    "dependency-health": ["dependency-health-report"],
    "enabled-output-matrix-identity": ["output-variants"],
    "scene-profile-source-identity": [
        "final-scene",
        "story-plan",
        "shot-plan",
        "owner-creative-acceptance",
        "final-look-profile",
        "horizontal-render-profile",
        "encoding-profiles",
        "source-revision-report",
    ],
    "worker-requirements": ["worker-requirements"],
}


def _identity_payload() -> dict[str, object]:
    return {
        "schemaVersion": "2.0.0",
        "releaseId": "andromeda-v2-final-release-v2",
        "projectId": "trip-to-andromeda-v2",
        "sourceAudioSha256": SOURCE_AUDIO_SHA256,
        "sourceCueSha256": SOURCE_CUE_SHA256,
        "ownerCreativeAcceptanceSha256": OWNER_CREATIVE_ACCEPTANCE_SHA256,
        "encodingProfilesSha256": ENCODING_PROFILES_SHA256,
        "lookProfileSha256": HASH_A,
        "storyPlanSha256": HASH_B,
        "shotPlanSha256": HASH_C,
        "outputVariantContractSha256": "1" * 64,
        "builderSourceSha256": "2" * 64,
        "sourceTreeSha256": "3" * 64,
        "gitCommitSha": "4" * 40,
        "deterministicSeed": 84291,
        "outputMatrix": {
            "schemaVersion": "2.0.0",
            "matrixId": "andromeda-v2-horizontal-only-v2",
            "enabledVariantIds": ["horizontal-16x9-1080p"],
            "variants": [
                {
                    "id": "horizontal-16x9-1080p",
                    "enabled": True,
                    "required": True,
                    "width": 1920,
                    "height": 1080,
                    "fps": 30,
                    "frameStart": 1,
                    "frameEnd": 13029,
                    "compositionProfileId": "andromeda-v2-horizontal-master-v2",
                    "cameraName": "TP_ANDROMEDA_V2_CAMERA_HORIZONTAL",
                    "sceneSha256": "5" * 64,
                    "renderProfileSha256": "6" * 64,
                    "workerRequirementId": "local-rtx3060-eevee-v2",
                    "outputPattern": (
                        "test-output/andromeda-v2/final-horizontal/frame_######.png"
                    ),
                }
            ],
        },
        "workerRequirements": [
            {
                "id": "local-rtx3060-eevee-v2",
                "deviceClass": "gpu",
                "renderer": "BLENDER_EEVEE",
                "blenderVersion": "5.2.0 LTS",
                "minimumVramMib": 8192,
                "maximumWorkersPerDevice": 1,
                "chunkSizeFrames": 300,
                "deterministicSeed": 84291,
                "requiredCapabilities": [
                    "lossless-png-16",
                    "atomic-frame-publication",
                    "persistent-heartbeats",
                ],
            }
        ],
    }


def _calibration_payload(
    identity_payload: dict[str, object],
) -> dict[str, object]:
    identity = FinalReleaseIdentity.model_validate(identity_payload)
    matrix = identity.output_matrix.variants[0]
    return {
        "schemaVersion": "2.0.0",
        "kind": "trackprompt-final-render-calibration",
        "calibrationId": "andromeda-v2-final-calibration-v2",
        "identity": identity_payload,
        "releaseIdentitySha256": final_release_identity_sha256(identity),
        "variantCalibrations": [
            {
                "outputVariantId": matrix.id,
                "sceneSha256": matrix.scene_sha256,
                "renderProfileSha256": matrix.render_profile_sha256,
                "compositionProfileId": matrix.composition_profile_id,
                "cameraName": matrix.camera_name,
                "workerRequirementId": matrix.worker_requirement_id,
                "width": matrix.width,
                "height": matrix.height,
                "fps": matrix.fps,
                "sampleFrames": [652, 1950, 3779, 6150, 7688, 8834, 11987],
                "p50SecondsPerFrame": 0.562,
                "p90SecondsPerFrame": 0.993,
                "weightedSecondsPerFrame": 0.701,
                "projectedRenderSecondsP50": 7312.0,
                "projectedRenderSecondsP90": 12940.0,
                "projectedOutputBytes": 25_000_000_000,
                "calibrationComplete": True,
            }
        ],
        "stageForecasts": [
            {"stage": "rendering", "p50Seconds": 7312.0, "p90Seconds": 12940.0},
            {
                "stage": "frame-validation",
                "p50Seconds": 300.0,
                "p90Seconds": 600.0,
            },
            {"stage": "encoding", "p50Seconds": 900.0, "p90Seconds": 1500.0},
            {"stage": "media-qa", "p50Seconds": 300.0, "p90Seconds": 500.0},
            {"stage": "publication", "p50Seconds": 10.0, "p90Seconds": 20.0},
        ],
        "aggregateP50Seconds": 8822.0,
        "aggregateP90Seconds": 15_560.0,
        "slaLimitSeconds": 86_400,
        "slaSatisfied": True,
        "deterministicEffectsVerified": True,
        "allRequiredBakesComplete": True,
        "dependencyHealthPassed": True,
        "vramStable": True,
        "diskFreeBytes": 100_000_000_000,
        "projectedPeakDiskBytes": 50_000_000_000,
        "diskSafetyMultiplier": 1.25,
        "diskHeadroomSatisfied": True,
    }


def _artifacts(extra_count: int = 0) -> list[dict[str, object]]:
    identity_hashes = {
        "final-scene": "5" * 64,
        "output-variants": "1" * 64,
        "story-plan": HASH_B,
        "shot-plan": HASH_C,
        "owner-creative-acceptance": OWNER_CREATIVE_ACCEPTANCE_SHA256,
        "final-look-profile": HASH_A,
        "horizontal-render-profile": "6" * 64,
        "encoding-profiles": ENCODING_PROFILES_SHA256,
        "final-calibration-v2": CALIBRATION_SHA256,
    }
    artifacts = [
        {
            "role": role,
            "path": f"production/andromeda-v2/final/{role}.json",
            "sha256": identity_hashes.get(role, f"{index + 1:064x}"),
            "immutable": True,
        }
        for index, role in enumerate(REQUIRED_ARTIFACT_ROLES)
    ]
    artifacts.extend(
        {
            "role": f"additional-evidence-{index:03d}",
            "path": (
                "production/andromeda-v2/final/"
                f"additional-evidence-{index:03d}.json"
            ),
            "sha256": f"{index + 1000:064x}",
            "immutable": True,
        }
        for index in range(extra_count)
    )
    return artifacts


def _source_bindings() -> list[dict[str, object]]:
    return [
        {
            "role": "source-audio",
            "sha256": SOURCE_AUDIO_SHA256,
            "sizeBytes": 76_608_080,
            "privateLocalArtifact": True,
            "committed": False,
        },
        {
            "role": "source-cue",
            "sha256": SOURCE_CUE_SHA256,
            "sizeBytes": 1_276_886,
            "privateLocalArtifact": True,
            "committed": False,
        },
    ]


def _objective_gates(
    *,
    blocked_gate: str | None = None,
) -> list[dict[str, object]]:
    return [
        {
            "id": gate_id,
            "status": "blocked" if gate_id == blocked_gate else "satisfied",
            "evidenceArtifactRoles": evidence_roles,
            "summary": f"Objective evidence for {gate_id}.",
            "evidenceKind": "objective-technical-evidence",
        }
        for gate_id, evidence_roles in GATE_EVIDENCE.items()
    ]


def _operator_gate(*, authorized: bool, identity_sha256: str) -> dict[str, object]:
    if not authorized:
        return {
            "status": "not-authorized",
            "authorizationId": None,
            "authorizedByRole": None,
            "authorizedAt": None,
            "authorizedReleaseIdentitySha256": None,
            "explicitFullRenderStartAuthorized": False,
        }
    return {
        "status": "authorized",
        "authorizationId": "owner-start-andromeda-v2-final-release-v2",
        "authorizedByRole": "project-owner-operator",
        "authorizedAt": "2026-07-23T12:00:00+03:00",
        "authorizedReleaseIdentitySha256": identity_sha256,
        "explicitFullRenderStartAuthorized": True,
    }


def _authorization_payload(
    identity_payload: dict[str, object],
    *,
    operator_authorized: bool = False,
    blocked_gate: str | None = None,
) -> dict[str, object]:
    identity = FinalReleaseIdentity.model_validate(identity_payload)
    identity_sha256 = final_release_identity_sha256(identity)
    technical_ready = blocked_gate is None
    return {
        "schemaVersion": "2.0.0",
        "kind": "trackprompt-final-technical-authorization",
        "authorizationId": "andromeda-v2-technical-authorization-v2",
        "packageId": "andromeda-v2-final-render-package-v2",
        "calibrationId": "andromeda-v2-final-calibration-v2",
        "calibrationSha256": CALIBRATION_SHA256,
        "identity": identity_payload,
        "releaseIdentitySha256": identity_sha256,
        "status": "technically-ready" if technical_ready else "blocked",
        "technicalReady": technical_ready,
        "objectiveGates": _objective_gates(blocked_gate=blocked_gate),
        "operatorStartGate": _operator_gate(
            authorized=operator_authorized,
            identity_sha256=identity_sha256,
        ),
        "productionStartAllowed": technical_ready and operator_authorized,
        "finalRenderStarted": False,
        "codexHumanArtisticApproval": False,
        "humanArtisticJudgmentSource": "owner-attested-r13.1-baseline-not-codex",
    }


def _package_payload(
    identity_payload: dict[str, object],
    *,
    technical_ready: bool = True,
    operator_authorized: bool = False,
    extra_artifact_count: int = 0,
) -> dict[str, object]:
    identity = FinalReleaseIdentity.model_validate(identity_payload)
    production_start_allowed = technical_ready and operator_authorized
    status = (
        "start-authorized"
        if production_start_allowed
        else (
            "technically-ready-operator-start-blocked"
            if technical_ready
            else "blocked"
        )
    )
    return {
        "schemaVersion": "2.0.0",
        "kind": "trackprompt-final-render-package",
        "packageId": "andromeda-v2-final-render-package-v2",
        "projectId": "trip-to-andromeda-v2",
        "identity": identity_payload,
        "releaseIdentitySha256": final_release_identity_sha256(identity),
        "calibrationId": "andromeda-v2-final-calibration-v2",
        "calibrationSha256": CALIBRATION_SHA256,
        "technicalAuthorizationId": "andromeda-v2-technical-authorization-v2",
        "status": status,
        "technicalReady": technical_ready,
        "artifacts": _artifacts(extra_artifact_count),
        "sourceBindings": _source_bindings(),
        "productionStartAllowed": production_start_allowed,
        "codexHumanArtisticApproval": False,
    }


def _final_release(
    *,
    operator_authorized: bool = False,
    extra_artifact_count: int = 0,
) -> AndromedaV2FinalRelease:
    identity_payload = _identity_payload()
    return AndromedaV2FinalRelease(
        calibration=AndromedaV2FinalCalibration.model_validate(
            _calibration_payload(identity_payload)
        ),
        package_manifest=AndromedaV2FinalPackageManifest.model_validate(
            _package_payload(
                identity_payload,
                operator_authorized=operator_authorized,
                extra_artifact_count=extra_artifact_count,
            )
        ),
        technical_authorization=AndromedaV2TechnicalAuthorization.model_validate(
            _authorization_payload(
                identity_payload,
                operator_authorized=operator_authorized,
            )
        ),
    )


def _write_model(path: Path, model: BaseModel) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = model.model_dump_json(by_alias=True, indent=2)
    path.write_text(f"{serialized}\n", encoding="utf-8")


def _materialize_filesystem_release(
    repository_root: Path,
) -> tuple[Path, Path, Path, dict[str, Path]]:
    identity_payload = _identity_payload()
    evidence_root = repository_root / "test-output" / "ignored-local-final-evidence"
    evidence_root.mkdir(parents=True, exist_ok=True)
    fixture_sources = {
        "owner-creative-acceptance": (
            REPOSITORY_ROOT
            / "production"
            / "andromeda-v2"
            / "creative-acceptance.json"
        ),
        "encoding-profiles": (
            REPOSITORY_ROOT
            / "production"
            / "andromeda-v2"
            / "encoding-profiles.json"
        ),
    }
    artifact_paths: dict[str, Path] = {}
    for role in REQUIRED_ARTIFACT_ROLES:
        if role in {"final-calibration-v2", "technical-authorization-v2"}:
            continue
        artifact_path = evidence_root / f"{role}.artifact"
        source = fixture_sources.get(role)
        artifact_path.write_bytes(
            source.read_bytes()
            if source is not None
            else f"bounded local evidence: {role}\n".encode()
        )
        artifact_paths[role] = artifact_path

    role_hashes = {
        role: file_sha256(path) for role, path in artifact_paths.items()
    }
    identity_payload["outputVariantContractSha256"] = role_hashes["output-variants"]
    identity_payload["storyPlanSha256"] = role_hashes["story-plan"]
    identity_payload["shotPlanSha256"] = role_hashes["shot-plan"]
    identity_payload["lookProfileSha256"] = role_hashes["final-look-profile"]
    matrix = identity_payload["outputMatrix"]
    assert isinstance(matrix, dict)
    variants = matrix["variants"]
    assert isinstance(variants, list)
    variants[0]["sceneSha256"] = role_hashes["final-scene"]
    variants[0]["renderProfileSha256"] = role_hashes["horizontal-render-profile"]

    calibration = AndromedaV2FinalCalibration.model_validate(
        _calibration_payload(identity_payload)
    )
    calibration_path = (
        repository_root
        / "production"
        / "andromeda-v2"
        / "final-calibration-v2.json"
    )
    _write_model(calibration_path, calibration)
    calibration_sha256 = file_sha256(calibration_path)

    authorization_payload = _authorization_payload(identity_payload)
    authorization_payload["calibrationSha256"] = calibration_sha256
    authorization = AndromedaV2TechnicalAuthorization.model_validate(
        authorization_payload
    )
    authorization_path = (
        repository_root
        / "production"
        / "andromeda-v2"
        / "technical-authorization-v2.json"
    )
    _write_model(authorization_path, authorization)

    artifact_paths["final-calibration-v2"] = calibration_path
    artifact_paths["technical-authorization-v2"] = authorization_path
    artifacts: list[dict[str, object]] = []
    for role in REQUIRED_ARTIFACT_ROLES:
        artifact_path = artifact_paths[role]
        artifacts.append(
            {
                "role": role,
                "path": artifact_path.relative_to(repository_root).as_posix(),
                "sha256": file_sha256(artifact_path),
                "immutable": True,
            }
        )

    package_payload = _package_payload(identity_payload)
    package_payload["calibrationSha256"] = calibration_sha256
    package_payload["artifacts"] = artifacts
    package_manifest = AndromedaV2FinalPackageManifest.model_validate(
        package_payload
    )
    package_manifest_path = (
        repository_root / "production" / "andromeda-v2" / "package-manifest-v2.json"
    )
    _write_model(package_manifest_path, package_manifest)
    return (
        calibration_path,
        package_manifest_path,
        authorization_path,
        artifact_paths,
    )


def test_technical_readiness_does_not_authorize_production_start() -> None:
    release = _final_release()

    assert release.technical_authorization.technical_ready is True
    assert release.technical_authorization.status == "technically-ready"
    assert release.technical_authorization.production_start_allowed is False
    assert release.package_manifest.status == "technically-ready-operator-start-blocked"
    assert release.package_manifest.production_start_allowed is False
    assert release.technical_authorization.codex_human_artistic_approval is False
    assert release.package_manifest.codex_human_artistic_approval is False
    assert release.technical_authorization.operator_start_gate.status == "not-authorized"
    assert {
        gate.id for gate in release.technical_authorization.objective_gates
    } == set(FinalReleaseObjectiveGateId)


@pytest.mark.parametrize(
    ("field", "message"),
    (
        ("sourceAudioSha256", "source-audio"),
        ("sourceCueSha256", "source-cue"),
        ("ownerCreativeAcceptanceSha256", "creative-acceptance"),
        ("encodingProfilesSha256", "encoding-profiles"),
    ),
)
def test_final_identity_rejects_wrong_immutable_source_hash(
    field: str,
    message: str,
) -> None:
    payload = _identity_payload()
    payload[field] = "f" * 64

    with pytest.raises(ValidationError, match=message):
        FinalReleaseIdentity.model_validate(payload)


@pytest.mark.parametrize(
    ("role", "message"),
    (
        ("owner-creative-acceptance", "owner-creative-acceptance"),
        ("final-look-profile", "final-look-profile"),
        ("encoding-profiles", "encoding-profiles"),
        ("horizontal-render-profile", "render profile"),
    ),
)
def test_final_package_rejects_artifact_identity_drift(
    role: str,
    message: str,
) -> None:
    identity_payload = _identity_payload()
    payload = _package_payload(identity_payload)
    artifacts = payload["artifacts"]
    assert isinstance(artifacts, list)
    artifact = next(item for item in artifacts if item["role"] == role)
    artifact["sha256"] = "f" * 64

    with pytest.raises(ValidationError, match=message):
        AndromedaV2FinalPackageManifest.model_validate(payload)


def test_legacy_foundation_documents_remain_schema_compatible() -> None:
    production_root = REPOSITORY_ROOT / "production" / "andromeda-v2"
    cinematic_root = REPOSITORY_ROOT / "backend" / "app" / "cinematic"

    documents = (
        (
            production_root / "creative-acceptance.json",
            OwnerAttestedCreativeAcceptance,
        ),
        (production_root / "final-look-profile.json", FinalLookProfile),
        (production_root / "output-variants.json", OutputVariantSet),
        (production_root / "production-authorization.json", ProductionAuthorization),
        (production_root / "package-manifest.json", PackageManifest),
        (
            cinematic_root / "templates" / "trip_to_andromeda_story_v2.json",
            StoryPlanV2,
        ),
        (
            cinematic_root / "templates" / "trip_to_andromeda_shots_v2.json",
            ShotPlanV2,
        ),
    )

    for path, model_type in documents:
        model_type.model_validate_json(path.read_text(encoding="utf-8"))


def test_exact_operator_authorization_is_the_separate_start_gate() -> None:
    release = _final_release(operator_authorized=True)

    assert release.technical_authorization.technical_ready is True
    assert release.technical_authorization.production_start_allowed is True
    assert release.package_manifest.production_start_allowed is True
    assert release.package_manifest.status == "start-authorized"

    payload = _authorization_payload(_identity_payload(), operator_authorized=True)
    operator_gate = payload["operatorStartGate"]
    assert isinstance(operator_gate, dict)
    operator_gate["authorizedReleaseIdentitySha256"] = "f" * 64
    with pytest.raises(ValidationError, match="exact release identity"):
        AndromedaV2TechnicalAuthorization.model_validate(payload)


def test_technical_ready_rejects_any_unsatisfied_objective_gate() -> None:
    payload = _authorization_payload(_identity_payload())
    gates = payload["objectiveGates"]
    assert isinstance(gates, list)
    gates[0]["status"] = "blocked"

    with pytest.raises(ValidationError, match="every objective gate"):
        AndromedaV2TechnicalAuthorization.model_validate(payload)


def test_calibration_must_match_the_exact_enabled_matrix_identity() -> None:
    payload = _calibration_payload(_identity_payload())
    calibrations = payload["variantCalibrations"]
    assert isinstance(calibrations, list)
    calibrations[0]["renderProfileSha256"] = "f" * 64

    with pytest.raises(ValidationError, match="calibration identity"):
        AndromedaV2FinalCalibration.model_validate(payload)


def test_final_release_rejects_identity_drift_between_documents() -> None:
    base_identity = _identity_payload()
    altered_identity = copy.deepcopy(base_identity)
    altered_identity["sourceTreeSha256"] = "f" * 64

    with pytest.raises(ValidationError, match="identities must agree"):
        AndromedaV2FinalRelease(
            calibration=AndromedaV2FinalCalibration.model_validate(
                _calibration_payload(base_identity)
            ),
            package_manifest=AndromedaV2FinalPackageManifest.model_validate(
                _package_payload(base_identity)
            ),
            technical_authorization=AndromedaV2TechnicalAuthorization.model_validate(
                _authorization_payload(altered_identity)
            ),
        )


def test_final_package_has_no_fixed_low_artifact_count_ceiling() -> None:
    release = _final_release(extra_artifact_count=128)

    assert len(release.package_manifest.artifacts) == len(REQUIRED_ARTIFACT_ROLES) + 128


def test_calibration_gate_cannot_contradict_measured_disk_or_sla() -> None:
    identity_payload = _identity_payload()
    calibration_payload = _calibration_payload(identity_payload)
    calibration_payload["diskFreeBytes"] = 10
    calibration_payload["diskHeadroomSatisfied"] = False
    release_payload = {
        "calibration": calibration_payload,
        "packageManifest": _package_payload(identity_payload),
        "technicalAuthorization": _authorization_payload(identity_payload),
    }

    with pytest.raises(ValidationError, match="deterministic-effects/disk gate"):
        AndromedaV2FinalRelease.model_validate(release_payload)


def test_objective_gates_require_their_exact_canonical_evidence_roles() -> None:
    payload = _authorization_payload(_identity_payload())
    gates = payload["objectiveGates"]
    assert isinstance(gates, list)
    gates[0]["evidenceArtifactRoles"] = ["final-scene"]

    with pytest.raises(ValidationError, match="exact canonical evidence roles"):
        AndromedaV2TechnicalAuthorization.model_validate(payload)


def test_calibration_aggregate_must_equal_sequential_stage_sum() -> None:
    payload = _calibration_payload(_identity_payload())
    payload["aggregateP50Seconds"] = 1.0
    payload["aggregateP90Seconds"] = 2.0

    with pytest.raises(ValidationError, match="sequential sum of stage forecasts"):
        AndromedaV2FinalCalibration.model_validate(payload)


@pytest.mark.parametrize(
    "output_pattern",
    (
        "",
        "test-output/andromeda-v2/final-horizontal/frame.png",
        "test-output//andromeda-v2/frame_######.png",
        "test-output/andromeda-v2/frame_############.png",
    ),
)
def test_enabled_output_pattern_is_normalized_and_frame_addressable(
    output_pattern: str,
) -> None:
    payload = _identity_payload()
    matrix = payload["outputMatrix"]
    assert isinstance(matrix, dict)
    variants = matrix["variants"]
    assert isinstance(variants, list)
    variants[0]["outputPattern"] = output_pattern

    with pytest.raises(ValidationError):
        FinalReleaseIdentity.model_validate(payload)


def test_final_release_loader_verifies_ignored_local_evidence(
    tmp_path: Path,
) -> None:
    (
        calibration_path,
        package_manifest_path,
        authorization_path,
        _artifact_paths,
    ) = _materialize_filesystem_release(tmp_path)

    release = load_and_validate_final_release(
        calibration_path,
        package_manifest_path,
        authorization_path,
        repository_root=tmp_path,
    )

    assert release.technical_authorization.technical_ready is True
    assert release.technical_authorization.production_start_allowed is False


def test_final_release_loader_rejects_tampered_local_evidence(
    tmp_path: Path,
) -> None:
    (
        calibration_path,
        package_manifest_path,
        authorization_path,
        artifact_paths,
    ) = _materialize_filesystem_release(tmp_path)
    artifact_paths["live-dashboard-proof"].write_bytes(b"tampered\n")

    with pytest.raises(
        ValueError,
        match="artifact hash mismatch for role live-dashboard-proof",
    ):
        load_and_validate_final_release(
            calibration_path,
            package_manifest_path,
            authorization_path,
            repository_root=tmp_path,
        )
