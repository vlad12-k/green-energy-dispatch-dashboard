"""Fail-closed request bounds for the public simulation-only demo."""

import asyncio
from collections import deque
import json
from math import ceil
from time import monotonic
from starlette.types import ASGIApp, Message, Receive, Scope, Send


_SIMULATION_PATH = "/api/v1/simulations/ev-depot/evaluate"
_RATE_WINDOW_SECONDS = 60.0


class PublicDemoGuardMiddleware:
    """Expose one bounded synthetic endpoint without trusting browser secrets."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        simulation_only_mode: bool,
        max_request_body_bytes: int,
        api_rate_limit_requests_per_minute: int,
    ) -> None:
        self.app = app
        self.simulation_only_mode = simulation_only_mode
        self.max_request_body_bytes = max_request_body_bytes
        self.api_rate_limit_requests_per_minute = api_rate_limit_requests_per_minute
        self._request_times: deque[float] = deque()
        self._rate_lock = asyncio.Lock()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self.simulation_only_mode:
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path == "/api" or path.startswith("/api/"):
            if path != _SIMULATION_PATH:
                await self._respond(send, 404, "Not found")
                return
            if scope.get("method") != "POST":
                await self._respond(send, 405, "Method not allowed", allow="POST")
                return

            declared_size = self._content_length(scope)
            if declared_size is not None and declared_size > self.max_request_body_bytes:
                await self._respond(send, 413, "Request body exceeds the configured limit")
                return

            retry_after = await self._claim_rate_limit_slot()
            if retry_after is not None:
                await self._respond(
                    send,
                    429,
                    "Rate limit exceeded; retry later",
                    retry_after=retry_after,
                )
                return

            body = bytearray()
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                if message["type"] != "http.request":
                    continue
                chunk = message.get("body", b"")
                if len(chunk) > self.max_request_body_bytes - len(body):
                    await self._respond(send, 413, "Request body exceeds the configured limit")
                    return
                body.extend(chunk)
                if not message.get("more_body", False):
                    break

            replay_pending = True

            async def replay_receive() -> Message:
                nonlocal replay_pending
                if replay_pending:
                    replay_pending = False
                    return {"type": "http.request", "body": bytes(body), "more_body": False}
                return await receive()

            await self.app(scope, replay_receive, send)
            return

        await self.app(scope, receive, send)

    @staticmethod
    def _content_length(scope: Scope) -> int | None:
        for name, value in scope.get("headers", []):
            if name.lower() == b"content-length":
                try:
                    length = int(value)
                except ValueError:
                    return None
                return length if length >= 0 else None
        return None

    async def _claim_rate_limit_slot(self) -> int | None:
        now = monotonic()
        async with self._rate_lock:
            while self._request_times and now - self._request_times[0] >= _RATE_WINDOW_SECONDS:
                self._request_times.popleft()
            if len(self._request_times) >= self.api_rate_limit_requests_per_minute:
                return max(1, ceil(_RATE_WINDOW_SECONDS - (now - self._request_times[0])))
            self._request_times.append(now)
        return None

    @staticmethod
    async def _respond(
        send: Send,
        status: int,
        detail: str,
        *,
        allow: str | None = None,
        retry_after: int | None = None,
    ) -> None:
        payload = json.dumps({"detail": detail}, separators=(",", ":")).encode()
        headers: list[tuple[bytes, bytes]] = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(payload)).encode()),
            (b"cache-control", b"no-store"),
        ]
        if allow is not None:
            headers.append((b"allow", allow.encode()))
        if retry_after is not None:
            headers.append((b"retry-after", str(retry_after).encode()))
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": payload})
