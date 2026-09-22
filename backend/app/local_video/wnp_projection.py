"""Compatibility import for the shared, observation-only WNP projection."""
from app.wnp.projection import (
    ProjectionError,
    ProjectionResult,
    WNPProjection,
    load_wnp_kit,
    sanitize_observation,
)

__all__ = ["ProjectionError", "ProjectionResult", "WNPProjection", "load_wnp_kit", "sanitize_observation"]
