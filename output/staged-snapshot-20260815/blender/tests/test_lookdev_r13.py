from __future__ import annotations

from trackprompt_visualizer.lookdev_r13 import (
    R13_VARIANTS,
    r13_lookdev_contract,
    validate_r13_contract,
)


def test_r13_has_three_controlled_protagonist_variants() -> None:
    contract = r13_lookdev_contract()
    validate_r13_contract(contract)
    protagonist = [variant for variant in R13_VARIANTS if variant["kind"] == "protagonist"]
    assert len(protagonist) == 3
    assert {variant["cameraRigId"] for variant in protagonist} == {
        "hero-controlled-comparison"
    }
    assert {variant["lightingRigId"] for variant in protagonist} == {
        "hero-controlled-comparison"
    }
    assert {variant["heroVariant"] for variant in protagonist} == {"A", "B", "C"}


def test_r13_bounds_architecture_and_gate_look_tests() -> None:
    contract = r13_lookdev_contract()
    identifiers = {variant["id"] for variant in contract["variants"]}
    assert "architecture-chamber-module" in identifiers
    assert "architecture-gate-monolith" in identifiers
    assert {
        variant.get("gateState")
        for variant in contract["variants"]
        if variant["kind"] == "gate"
    } == {"open", "compression", "sealed"}
    assert contract["constructionSystem"]["structuralConnections"] is True
    assert contract["constructionSystem"]["limitedEmissiveAccents"] is True
    assert contract["gateSystem"]["nestedMembraneDepth"] is True
    assert contract["gateSystem"]["sealingMechanism"] is True


def test_r13_remains_human_gated_and_blocks_motion_until_selection() -> None:
    contract = r13_lookdev_contract()
    assert contract["previewOnly"] is True
    assert contract["fullSequenceRender"] is False
    assert contract["selection"] == {
        "protagonistDesign": None,
        "architecturalMaterialLanguage": None,
        "gateConstruction": None,
        "exposureLightingTreatment": None,
        "status": "pending-human-operator-selection",
        "humanArtistApproval": "pending",
        "artistApproved": False,
    }
    assert contract["motionTest"]["status"] == "blocked-pending-look-selection"
    assert contract["renderFormat"] == {
        "width": 1080,
        "height": 1920,
        "phoneWidth": 180,
        "phoneHeight": 320,
        "responsiveComposition": "native-vertical-not-crop",
    }


def test_r13_encodes_direction_and_bounded_deformation_requirements() -> None:
    requirements = r13_lookdev_contract()["protagonistRequirements"]
    assert all(requirements.values())
    gate_variants = [variant for variant in R13_VARIANTS if variant["kind"] == "gate"]
    assert [variant["heroState"] for variant in gate_variants] == [
        "approach",
        "gate-pressure",
        "recovery",
    ]
