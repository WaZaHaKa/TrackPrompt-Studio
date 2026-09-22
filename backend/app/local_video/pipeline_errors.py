"""Shared error identity without importing optional local-generation modules."""
from __future__ import annotations

from typing import Any


class MorphPipelineError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.safe_message = message
        self.diagnostics: dict[str, Any] = {}
