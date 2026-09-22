"""Bounded read-only Companion transport checks; no service/tunnel/config changes."""
from __future__ import annotations

import importlib
import json
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .projection import load_wnp_kit


def freshness(timestamp: object, *, now: datetime | None = None, maximum_age: int = 90) -> str:
    if not isinstance(timestamp, str):
        return "unknown"
    try:
        age = ((now or datetime.now(UTC)) - datetime.fromisoformat(timestamp.replace("Z", "+00:00"))).total_seconds()
        return "fresh" if 0 <= age <= maximum_age else "stale"
    except (ValueError, TypeError):
        return "unknown"


def describe(snapshot: dict[str, Any]) -> dict[str, Any]:
    observations = snapshot.get("observations", {})
    pipeline = snapshot.get("extensions", {}).get("trackprompt", {}).get("pipeline", {})
    return {"run": snapshot.get("run"), "sequence": snapshot["sequence"], "state": snapshot["state"],
            "stage": snapshot.get("detail"), "progress": snapshot.get("progress"),
            "worker_freshness": freshness(observations.get("worker", {}).get("heartbeat_at")),
            "provider_freshness": freshness(observations.get("provider", {}).get("observed_at")),
            "provider_state": observations.get("provider", {}).get("state", "unknown"),
            "technical_verified": pipeline.get("technical_verified", False),
            "creative_approval": pipeline.get("creative_approval", "unknown"), "pipeline": pipeline}


def local_check(root: Path) -> dict[str, Any]:
    store_module, _ = load_wnp_kit()
    store = store_module.Store(root)
    node = store.node()
    snapshot = store.snapshot()
    selected = None
    outbox = root / ".wnp/trackprompt-outbox.sqlite3"
    pending = None
    if outbox.is_file():
        with sqlite3.connect(outbox.resolve().as_uri() + "?mode=ro", uri=True) as connection:
            pending = connection.execute("SELECT count(*) FROM outbox WHERE ack_sequence IS NULL").fetchone()[0]
            if connection.execute("SELECT 1 FROM sqlite_master WHERE name='selection'").fetchone():
                row = connection.execute("SELECT run FROM selection WHERE singleton=1").fetchone()
                selected = row[0] if row else None
    return {"ok": True, "mode": "local-check", "project": node["project"], "node": node["node"],
            "stream": node["stream"], "selected_run": selected, "pending": pending,
            "transport": "not-checked", "companion_ui": "unverified", **describe(snapshot)}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


def probe(root: Path, origin: str, *, expected_project: str, expected_node: str) -> dict[str, Any]:
    """Use existing private credential, no proxies/redirects/remote URLs or writes."""
    parsed = urllib.parse.urlsplit(origin)
    if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.username or parsed.password
            or parsed.path not in {"", "/"} or parsed.query or parsed.fragment or parsed.port is None):
        raise ValueError("invalid_loopback_origin")
    token_path = root / ".wnp/auth/read.token"
    if token_path.is_symlink() or token_path.stat().st_size > 256:
        raise ValueError("invalid_reader_credential")
    token = token_path.read_text(encoding="utf-8").strip()
    if not token or "\r" in token or "\n" in token:
        raise ValueError("invalid_reader_credential")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())

    def get(path: str) -> dict[str, Any]:
        request = urllib.request.Request(origin.rstrip("/") + path, headers={"Authorization": "Bearer " + token})
        with opener.open(request, timeout=4) as response:
            raw = response.read(1_048_577)
            if len(raw) > 1_048_576:
                raise ValueError("response_too_large")
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError("invalid_response")
            return value

    load_wnp_kit()
    model = importlib.import_module("wnp_host.model")
    node = get("/v1/node")
    project = get("/v1/project")
    if (node.get("protocol") != "wnp/1" or project.get("protocol") != "wnp/1"
            or node.get("project") != expected_project or project.get("project") != expected_project
            or node.get("node") != expected_node):
        raise ValueError("identity_conflict")
    status = get("/v1/status")
    model.snapshot(status, node["stream"])
    return {"ok": True, "mode": "endpoint-probe", "transport": "authenticated",
            "project": expected_project, "node": expected_node, "stream": node["stream"],
            "companion_ui": "unverified", **describe(status)}
