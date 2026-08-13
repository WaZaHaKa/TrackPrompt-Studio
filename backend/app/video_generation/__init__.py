"""TrackPrompt GCP video-generation fast lane.

This package is additive and side-effect free at import time. Network and billable
operations require explicit CLI actions and a project-level budget authorization.
"""

from .contracts import SCHEMA_VERSION

__all__ = ["SCHEMA_VERSION"]

__version__ = "0.1.0"
