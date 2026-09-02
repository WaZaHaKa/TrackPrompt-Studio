from __future__ import annotations

from dataclasses import dataclass

from ..schemas import Confidence


@dataclass(frozen=True, slots=True)
class KeyConfidenceDecision:
    confidence: Confidence
    label: str
    ambiguous: bool
    reason: str | None


def classify_key_confidence(
    *,
    best_fit: float,
    runner_up_margin: float,
    temporal_consistency: float,
    tonal_concentration: float,
    usable_seconds: float,
) -> KeyConfidenceDecision:
    """Conservative policy for uncalibrated tonal-template evidence.

    Template fit and the derived metrics are evidence scores, not probabilities.
    A near tie always wins over an otherwise respectable absolute fit.
    """
    if usable_seconds < 3.0 or tonal_concentration < 0.055:
        return KeyConfidenceDecision(
            Confidence.UNKNOWN,
            "tonal center uncertain",
            True,
            "Too little concentrated harmonic material was available.",
        )
    if runner_up_margin < 0.025:
        return KeyConfidenceDecision(
            Confidence.LOW,
            "ambiguous",
            True,
            "The leading key candidates are too close to distinguish reliably.",
        )
    if best_fit >= 0.72 and runner_up_margin >= 0.08 and temporal_consistency >= 0.72:
        return KeyConfidenceDecision(Confidence.HIGH, "clear", False, None)
    if best_fit >= 0.52 and runner_up_margin >= 0.045 and temporal_consistency >= 0.5:
        return KeyConfidenceDecision(
            Confidence.MEDIUM,
            "moderately defined",
            False,
            "The tonal estimate is useful but not fully stable across time.",
        )
    return KeyConfidenceDecision(
        Confidence.LOW,
        "tonal center uncertain",
        True,
        "The absolute fit, candidate separation, or temporal consistency was weak.",
    )


def classify_tempo_confidence(
    *,
    autocorrelation_strength: float,
    estimator_agreement: bool,
    grid_alignment: float,
    temporal_consistency: float,
    duration_seconds: float,
    octave_normalized_without_agreement: bool,
) -> Confidence:
    if duration_seconds < 4.0 or grid_alignment < 0.28:
        return Confidence.LOW
    if autocorrelation_strength < 0.1 and not estimator_agreement:
        return Confidence.LOW
    if octave_normalized_without_agreement:
        return Confidence.MEDIUM
    if (
        duration_seconds >= 8.0
        and autocorrelation_strength >= 0.22
        and grid_alignment >= 0.62
        and temporal_consistency >= 0.6
        and estimator_agreement
    ):
        return Confidence.HIGH
    if autocorrelation_strength >= 0.1 and grid_alignment >= 0.42:
        return Confidence.MEDIUM
    return Confidence.LOW
