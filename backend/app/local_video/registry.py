from __future__ import annotations

import hashlib
import json
import os
import urllib.parse
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..privacy import secure_private_directory, secure_private_file
from .models import LocalVideoWorkflowRequest, LocalVideoWorkflowView
from .workflow import discover_keyframe_semantic_nodes, discover_semantic_nodes


class WorkflowRegistry:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        secure_private_directory(self.root)

    def _path(self, workflow_id: str) -> Path:
        return self.root / f"{workflow_id}.json"

    @staticmethod
    def _source_url(value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urllib.parse.urlsplit(value)
        if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("The workflow source URL is invalid")
        return value

    def install(self, request: LocalVideoWorkflowRequest) -> LocalVideoWorkflowView:
        if not request.workflow or len(request.workflow) > 10_000:
            raise ValueError("The workflow is empty or unexpectedly large")
        if not all(isinstance(key, str) and isinstance(node, dict) for key, node in request.workflow.items()):
            raise ValueError("The workflow must be an API-format ComfyUI node object")
        encoded_workflow = json.dumps(
            request.workflow,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        if len(encoded_workflow) > 20_000_000:
            raise ValueError("The workflow is unexpectedly large")
        digest = hashlib.sha256(encoded_workflow).hexdigest()
        installed_at = datetime.now(UTC)
        semantic_roles: dict[str, list[str]] = {}
        missing: list[str] = []
        if request.capability == "wan22-i2v":
            mapping = discover_semantic_nodes(request.workflow)
            semantic_roles = {key: list(value) for key, value in mapping.role_nodes.items()}
            missing = list(mapping.missing_roles)
            if missing:
                raise ValueError("The I2V workflow is missing required semantic roles")
        elif request.capability in {"keyframe-flux", "keyframe-sdxl"}:
            mapping = discover_keyframe_semantic_nodes(request.workflow)
            semantic_roles = {key: list(value) for key, value in mapping.role_nodes.items()}
            missing = list(mapping.missing_roles)
            if missing:
                raise ValueError("The keyframe workflow is missing required semantic roles")
        source_url = self._source_url(request.source_url)
        record = {
            "schemaVersion": "1.0.0",
            "workflowId": request.workflow_id,
            "capability": request.capability,
            "workflowSha256": digest,
            "semanticRoles": semantic_roles,
            "missingRoles": missing,
            "sourceUrl": source_url,
            "sourceRevision": request.source_revision,
            "installedAt": installed_at.isoformat(),
            "workflow": request.workflow,
        }
        path = self._path(request.workflow_id)
        temporary = path.with_name(f".{path.name}.partial-{uuid4().hex}")
        try:
            temporary.write_text(
                json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                encoding="utf-8",
            )
            secure_private_file(temporary)
            os.replace(temporary, path)
            secure_private_file(path)
        finally:
            temporary.unlink(missing_ok=True)
        public_record = dict(record)
        public_record.pop("workflow", None)
        return LocalVideoWorkflowView.model_validate(public_record)

    def load(self, workflow_id: str) -> tuple[LocalVideoWorkflowView, dict[str, Any]]:
        path = self._path(workflow_id)
        if not path.is_file() or path.stat().st_size > 20_000_000:
            raise KeyError("Local ComfyUI workflow not found")
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or not isinstance(value.get("workflow"), dict):
            raise ValueError("The registered workflow is invalid")
        workflow = value.pop("workflow")
        return LocalVideoWorkflowView.model_validate(value), workflow

    def list(self) -> list[LocalVideoWorkflowView]:
        result: list[LocalVideoWorkflowView] = []
        for path in sorted(self.root.glob("*.json"), key=lambda item: item.name.casefold()):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(value, dict):
                    continue
                value.pop("workflow", None)
                result.append(LocalVideoWorkflowView.model_validate(value))
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
                continue
        return result
