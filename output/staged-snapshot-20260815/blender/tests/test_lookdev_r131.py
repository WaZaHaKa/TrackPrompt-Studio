from __future__ import annotations

from trackprompt_visualizer.lookdev_r131 import (
    R131_EVENT_FRAMES,
    R131_REVIEW_STATES,
    R131_SELECTION,
    r131_contract,
    validate_r131_contract,
)


def test_r131_records_the_exact_provisional_selection() -> None:
    contract = r131_contract()
    validate_r131_contract(contract)
    assert contract["selection"] == R131_SELECTION
    assert contract["artistApproved"] is False
    assert contract["humanArtistApproval"] == "pending"


def test_r131_refines_b_with_a_silhouette_and_one_band() -> None:
    protagonist = r131_contract()["protagonist"]
    assert protagonist == {
        "base": "protagonist-b-ancient-engine",
        "silhouetteReference": "protagonist-a-directional-shell",
        "majorArmorBands": 1,
        "transparentAtmosphereLayers": 1,
        "integratedFrontAperture": True,
        "asymmetricOrientationCues": True,
        "restrainedRearWake": True,
        "wireCage": False,
        "boundedCompression": True,
    }


def test_r131_is_one_four_second_vertical_motion_proof() -> None:
    contract = r131_contract()
    assert contract["frameRange"] == {
        "start": 1,
        "end": 120,
        "fps": 30,
        "durationSeconds": 4.0,
    }
    assert contract["render"]["width"] == 1080
    assert contract["render"]["height"] == 1920
    assert contract["render"]["finalTemporalSamples"] == 64
    assert contract["motion"]["cameraCuts"] == 0
    assert contract["motion"]["rawAudioMacroMotion"] is False


def test_r131_review_states_cover_every_requested_artistic_gate() -> None:
    criteria = {
        criterion
        for state in R131_REVIEW_STATES
        for criterion in state["criteria"]
    }
    assert criteria == {
        "protagonist-orientation",
        "silhouette-clarity",
        "independent-movement",
        "camera-lag",
        "parallax",
        "gate-depth",
        "compression-readability",
        "post-crossing-recovery",
        "gate-sealing",
        "material-noise",
        "exposure",
        "story-clarity",
    }
    assert list(R131_EVENT_FRAMES.values()) == sorted(R131_EVENT_FRAMES.values())
