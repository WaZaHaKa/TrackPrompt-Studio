from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from .contracts import ContractError
from .jsonio import atomic_write_json, read_json, sha256_json

AUTH_SCHEMA_VERSION = "1.0.0"


def authorization_phrase(project_id: str, max_spend_usd: float) -> str:
    return f"AUTHORIZE {project_id} VIDEO BATCH UP TO USD {max_spend_usd:.2f}"


@dataclass(frozen=True)
class BatchAuthorization:
    schema_version: str
    project_id: str
    plan_digest: str
    max_spend_usd: float
    authorized_at: str
    expires_at: str
    confirmation_digest: str

    @classmethod
    def create(
        cls,
        *,
        project_id: str,
        plan_digest: str,
        max_spend_usd: float,
        confirmation: str,
        valid_hours: int = 24,
    ) -> BatchAuthorization:
        expected = authorization_phrase(project_id, max_spend_usd)
        if confirmation != expected:
            raise ContractError("Confirmation did not match the displayed project-level phrase")
        if valid_hours < 1 or valid_hours > 168:
            raise ContractError("validHours must be between 1 and 168")
        now = datetime.now(UTC)
        expires = now + timedelta(hours=valid_hours)
        return cls(
            schema_version=AUTH_SCHEMA_VERSION,
            project_id=project_id,
            plan_digest=plan_digest,
            max_spend_usd=round(max_spend_usd, 2),
            authorized_at=now.isoformat(),
            expires_at=expires.isoformat(),
            confirmation_digest=sha256_json({"confirmation": confirmation}),
        )

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> BatchAuthorization:
        if value.get("schemaVersion") != AUTH_SCHEMA_VERSION:
            raise ContractError("unsupported authorization schemaVersion")
        return cls(
            schema_version=AUTH_SCHEMA_VERSION,
            project_id=str(value["projectId"]),
            plan_digest=str(value["planDigest"]),
            max_spend_usd=float(value["maxSpendUsd"]),
            authorized_at=str(value["authorizedAt"]),
            expires_at=str(value["expiresAt"]),
            confirmation_digest=str(value["confirmationDigest"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "projectId": self.project_id,
            "planDigest": self.plan_digest,
            "maxSpendUsd": self.max_spend_usd,
            "authorizedAt": self.authorized_at,
            "expiresAt": self.expires_at,
            "confirmationDigest": self.confirmation_digest,
        }

    def validate_for(
        self,
        *,
        project_id: str,
        plan_digest: str,
        current_reserved_usd: float,
        next_request_usd: float,
    ) -> None:
        if self.project_id != project_id:
            raise ContractError("authorization belongs to another project")
        if self.plan_digest != plan_digest:
            raise ContractError("authorization does not bind this exact plan")
        if datetime.now(UTC) >= datetime.fromisoformat(self.expires_at):
            raise ContractError("project-level budget authorization expired")
        projected = Decimal(str(current_reserved_usd)) + Decimal(str(next_request_usd))
        if projected > Decimal(str(self.max_spend_usd)):
            raise ContractError(
                "The next request would exceed the authorized maximum: "
                f"${projected:.2f} > ${self.max_spend_usd:.2f}"
            )


def save_authorization(path: Path, authorization: BatchAuthorization) -> None:
    atomic_write_json(path, authorization.to_dict())


def load_authorization(path: Path) -> BatchAuthorization:
    value = read_json(path)
    if not isinstance(value, dict):
        raise ContractError("authorization root must be an object")
    return BatchAuthorization.from_dict(value)
