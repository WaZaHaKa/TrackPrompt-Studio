from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit

ASGIApp = Callable[[dict[str, Any], Callable[[], Awaitable[dict[str, Any]]], Callable[[dict[str, Any]], Awaitable[None]]], Awaitable[None]]


class RequestBodyTooLarge(RuntimeError):
    pass


class LocalRequestBoundaryMiddleware:
    """Bound request bodies before parsing and enforce the local API boundary."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        max_upload_request_bytes: int,
        allowed_origins: tuple[str, ...],
        allowed_hosts: tuple[str, ...],
        max_api_request_bytes: int = 256 * 1024,
        max_chunk_request_bytes: int = 32 * 1024 * 1024,
        max_active_uploads: int = 3,
    ) -> None:
        self.app = app
        self.max_upload_request_bytes = max_upload_request_bytes
        self.max_api_request_bytes = max_api_request_bytes
        self.max_chunk_request_bytes = max_chunk_request_bytes
        self.upload_slots = asyncio.Semaphore(max_active_uploads)
        self.allowed_origins = {origin.rstrip("/").casefold() for origin in allowed_origins}
        origin_hosts = {
            host.casefold()
            for origin in allowed_origins
            if (host := urlsplit(origin).hostname) is not None
        }
        self.allowed_hosts = {host.casefold() for host in allowed_hosts} | origin_hosts

    @staticmethod
    async def _error(send: Callable[[dict[str, Any]], Awaitable[None]], status: int, code: str, message: str) -> None:
        payload = json.dumps(
            {"error": {"code": code, "message": message, "details": None}},
            separators=(",", ":"),
        ).encode()
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(payload)).encode("ascii")),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": payload})

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[[], Awaitable[dict[str, Any]]],
        send: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        headers = {
            key.decode("latin-1").casefold(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }
        method = str(scope.get("method", "GET")).upper()
        path = str(scope.get("path", ""))
        is_api = path.startswith("/api/")
        if is_api:
            host_header = headers.get("host", "")
            try:
                hostname = urlsplit(f"//{host_header}").hostname
            except ValueError:
                hostname = None
            if hostname is None or hostname.casefold() not in self.allowed_hosts:
                await self._error(send, 403, "host_not_allowed", "This local API does not accept the requested Host.")
                return
            if headers.get("sec-fetch-site", "").casefold() == "cross-site":
                await self._error(
                    send,
                    403,
                    "cross_site_request_rejected",
                    "This local API does not accept cross-site browser requests.",
                )
                return
        if method in {"POST", "PATCH", "DELETE"} and is_api:
            origin = headers.get("origin")
            if origin is not None and origin.rstrip("/").casefold() not in self.allowed_origins:
                await self._error(send, 403, "origin_not_allowed", "This local API does not accept the browser Origin.")
                return

        is_upload = method == "POST" and path == "/api/analyses"
        is_resumable_chunk = method == "PATCH" and path.startswith("/api/upload-sessions/")
        is_capped_api_body = is_upload or is_resumable_chunk or (is_api and method in {"POST", "PATCH", "PUT"})
        if not is_capped_api_body:
            await self.app(scope, receive, send)
            return
        request_limit = self.max_upload_request_bytes if is_upload else (
            self.max_chunk_request_bytes if is_resumable_chunk else self.max_api_request_bytes
        )
        raw_length = headers.get("content-length")
        if raw_length is not None:
            try:
                declared_length = int(raw_length)
            except ValueError:
                await self._error(send, 400, "invalid_content_length", "Content-Length must be a valid non-negative integer.")
                return
            if declared_length < 0:
                await self._error(send, 400, "invalid_content_length", "Content-Length must be a valid non-negative integer.")
                return
            if declared_length > request_limit:
                await self._error(
                    send,
                    413,
                    "request_body_too_large",
                    "The API request exceeds the configured local limit.",
                )
                return

        consumed = 0

        async def limited_receive() -> dict[str, Any]:
            nonlocal consumed
            message = await receive()
            if message.get("type") == "http.request":
                body = message.get("body", b"")
                consumed += len(body) if isinstance(body, bytes) else 0
                if consumed > request_limit:
                    raise RequestBodyTooLarge
            return message

        if is_resumable_chunk:
            await self.upload_slots.acquire()
        try:
            try:
                await self.app(scope, limited_receive, send)
            except RequestBodyTooLarge:
                await self._error(
                    send,
                    413,
                    "request_body_too_large",
                    "The API request exceeds the configured local limit.",
                )
        finally:
            if is_resumable_chunk:
                self.upload_slots.release()
