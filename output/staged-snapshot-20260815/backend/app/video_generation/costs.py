from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from .contracts import ContractError, GenerationProfile

# Snapshot: 2026-08-13. The live application should make this table versioned and
# operator-reviewable because provider pricing can change.
PRICE_SNAPSHOT_DATE = "2026-08-13"
VIDEO_ONLY_USD_PER_OUTPUT_SECOND: dict[tuple[str, str], Decimal] = {
    ("veo-3.1-fast-generate-001", "720p"): Decimal("0.08"),
    ("veo-3.1-fast-generate-001", "1080p"): Decimal("0.10"),
    ("veo-3.1-generate-001", "720p"): Decimal("0.20"),
    ("veo-3.1-generate-001", "1080p"): Decimal("0.20"),
    ("veo-3.1-generate-001", "4k"): Decimal("0.40"),
}


@dataclass(frozen=True)
class CostEstimate:
    model_id: str
    resolution: str
    duration_seconds: int
    sample_count: int
    shot_count: int
    rate_usd_per_second: Decimal
    base_usd: Decimal
    conservative_usd: Decimal
    reserve_factor: Decimal

    def to_dict(self) -> dict[str, object]:
        return {
            "pricingSnapshotDate": PRICE_SNAPSHOT_DATE,
            "modelId": self.model_id,
            "resolution": self.resolution,
            "durationSecondsPerShot": self.duration_seconds,
            "sampleCount": self.sample_count,
            "shotCount": self.shot_count,
            "rateUsdPerOutputSecond": float(self.rate_usd_per_second),
            "baseEstimatedUsd": float(self.base_usd),
            "conservativeEstimatedUsd": float(self.conservative_usd),
            "retryReserveFactor": float(self.reserve_factor),
        }


def rate_for(profile: GenerationProfile) -> Decimal:
    if profile.generate_audio:
        raise ContractError("audio-generation pricing is intentionally excluded")
    try:
        return VIDEO_ONLY_USD_PER_OUTPUT_SECOND[(profile.model_id, profile.resolution)]
    except KeyError as exc:
        raise ContractError(
            f"No reviewed video-only rate for {profile.model_id}/{profile.resolution}"
        ) from exc


def per_shot_cost(profile: GenerationProfile) -> Decimal:
    return (rate_for(profile) * Decimal(profile.duration_seconds) * Decimal(profile.sample_count)).quantize(
        Decimal("0.0001"), rounding=ROUND_HALF_UP
    )


def estimate(
    profile: GenerationProfile,
    shot_count: int,
    retry_reserve_factor: float,
) -> CostEstimate:
    if shot_count <= 0:
        raise ContractError("shot_count must be positive")
    reserve = Decimal(str(retry_reserve_factor))
    if reserve < 1:
        raise ContractError("retry reserve factor must be at least 1")
    base = (per_shot_cost(profile) * shot_count).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    conservative = (base * reserve).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return CostEstimate(
        model_id=profile.model_id,
        resolution=profile.resolution,
        duration_seconds=profile.duration_seconds,
        sample_count=profile.sample_count,
        shot_count=shot_count,
        rate_usd_per_second=rate_for(profile),
        base_usd=base,
        conservative_usd=conservative,
        reserve_factor=reserve,
    )


def sum_reserved_costs(values: Iterable[float]) -> Decimal:
    return sum((Decimal(str(value)) for value in values), start=Decimal("0"))
