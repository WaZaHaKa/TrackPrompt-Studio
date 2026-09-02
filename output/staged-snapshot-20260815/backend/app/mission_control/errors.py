from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .models import StructuredError


class MissionControlError(RuntimeError):
    def __init__(
        self,
        status_code: int,
        code: str,
        title: str,
        summary: str,
        recommended_action: str,
        *,
        likely_cause: str | None = None,
        retryable: bool = False,
        context: dict[str, Any] | None = None,
        technical_details: str | None = None,
        related_path: str | None = None,
        job_id: str | None = None,
    ) -> None:
        super().__init__(summary)
        self.status_code = status_code
        self.error = StructuredError(
            code=code,
            title=title,
            summary=summary,
            likely_cause=likely_cause,
            recommended_action=recommended_action,
            retryable=retryable,
            context=context or {},
            technical_details=technical_details,
            related_path=related_path,
            timestamp=datetime.now(UTC),
            job_id=job_id,
        )
