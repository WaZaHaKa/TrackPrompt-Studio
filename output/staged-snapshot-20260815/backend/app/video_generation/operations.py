from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .contracts import ContractError
from .jsonio import atomic_write_json, read_json

OPERATION_SCHEMA_VERSION = "1.0.0"


@dataclass(frozen=True)
class OperationRecord:
    operation_id: str
    project_id: str
    plan_digest: str
    shot_id: str
    model_id: str
    status: str
    submitted_at: str
    updated_at: str
    reserved_cost_usd: float
    operation_name: str | None = None
    storage_uri: str | None = None
    output_uris: tuple[str, ...] = ()
    error: dict[str, Any] | None = None
    raw_response_path: str | None = None

    @classmethod
    def new(
        cls,
        *,
        operation_id: str,
        project_id: str,
        plan_digest: str,
        shot_id: str,
        model_id: str,
        reserved_cost_usd: float,
        operation_name: str,
        storage_uri: str | None,
    ) -> OperationRecord:
        now = datetime.now(UTC).isoformat()
        return cls(
            operation_id=operation_id,
            project_id=project_id,
            plan_digest=plan_digest,
            shot_id=shot_id,
            model_id=model_id,
            status="submitted",
            submitted_at=now,
            updated_at=now,
            reserved_cost_usd=reserved_cost_usd,
            operation_name=operation_name,
            storage_uri=storage_uri,
        )

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> OperationRecord:
        if value.get("schemaVersion") != OPERATION_SCHEMA_VERSION:
            raise ContractError("unsupported operation schemaVersion")
        return cls(
            operation_id=str(value["operationId"]),
            project_id=str(value["projectId"]),
            plan_digest=str(value["planDigest"]),
            shot_id=str(value["shotId"]),
            model_id=str(value["modelId"]),
            status=str(value["status"]),
            submitted_at=str(value["submittedAt"]),
            updated_at=str(value["updatedAt"]),
            reserved_cost_usd=float(value["reservedCostUsd"]),
            operation_name=value.get("operationName"),
            storage_uri=value.get("storageUri"),
            output_uris=tuple(str(item) for item in value.get("outputUris", [])),
            error=value.get("error"),
            raw_response_path=value.get("rawResponsePath"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": OPERATION_SCHEMA_VERSION,
            "operationId": self.operation_id,
            "projectId": self.project_id,
            "planDigest": self.plan_digest,
            "shotId": self.shot_id,
            "modelId": self.model_id,
            "status": self.status,
            "submittedAt": self.submitted_at,
            "updatedAt": self.updated_at,
            "reservedCostUsd": round(self.reserved_cost_usd, 4),
            "operationName": self.operation_name,
            "storageUri": self.storage_uri,
            "outputUris": list(self.output_uris),
            "error": self.error,
            "rawResponsePath": self.raw_response_path,
        }

    def updated(
        self,
        *,
        status: str,
        output_uris: Iterable[str] | None = None,
        error: dict[str, Any] | None = None,
        raw_response_path: str | None = None,
    ) -> OperationRecord:
        return OperationRecord(
            **{
                **self.__dict__,
                "status": status,
                "updated_at": datetime.now(UTC).isoformat(),
                "output_uris": tuple(output_uris or self.output_uris),
                "error": error,
                "raw_response_path": raw_response_path or self.raw_response_path,
            }
        )


def operation_path(root: Path, operation_id: str) -> Path:
    return root / "operations" / f"{operation_id}.json"


def save_operation(root: Path, record: OperationRecord) -> Path:
    path = operation_path(root, record.operation_id)
    atomic_write_json(path, record.to_dict())
    return path


def load_operation(path: Path) -> OperationRecord:
    value = read_json(path)
    if not isinstance(value, dict):
        raise ContractError("operation record root must be an object")
    return OperationRecord.from_dict(value)


def list_operations(root: Path) -> tuple[OperationRecord, ...]:
    directory = root / "operations"
    if not directory.exists():
        return ()
    records = []
    for path in sorted(directory.glob("*.json")):
        records.append(load_operation(path))
    return tuple(records)


def reserved_cost(root: Path, *, plan_digest: str) -> float:
    # Conservatively count every submitted request. This can over-reserve after
    # provider failures, but cannot silently exceed the operator's cap.
    return round(
        sum(
            record.reserved_cost_usd
            for record in list_operations(root)
            if record.plan_digest == plan_digest and record.status not in {"cancelled-before-submit"}
        ),
        4,
    )
