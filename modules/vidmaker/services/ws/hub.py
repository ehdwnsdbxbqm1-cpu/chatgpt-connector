from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any


@dataclass
class PendingRequest:
    request_id: str
    payload: dict[str, Any]
    deadline: float
    attempts: int = 0


class ExtensionOfflineError(RuntimeError):
    code = "EXTENSION_OFFLINE"


class RequestRouter:
    """
    백엔드 WS 라우터.

    핵심 변경점:
    1) extension reconnect grace window 동안 요청 즉시 실패 금지
    2) grace window 내 재연결 시 pending 요청 재전송
    3) EXTENSION_OFFLINE은 'grace 초과 + 재연결 실패' 조건에서만 반환
    """

    def __init__(
        self,
        *,
        reconnect_grace_window_sec: float = 3.0,
        resend_interval_sec: float = 0.5,
    ) -> None:
        self.reconnect_grace_window_sec = reconnect_grace_window_sec
        self.resend_interval_sec = resend_interval_sec

        self._extension_connected = asyncio.Event()
        self._pending_futures: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._pending_requests: dict[str, PendingRequest] = {}
        self._lock = asyncio.Lock()
        self._last_disconnect_ts = 0.0

    def mark_extension_connected(self) -> None:
        self._extension_connected.set()

    def mark_extension_disconnected(self) -> None:
        self._last_disconnect_ts = time.monotonic()
        self._extension_connected.clear()

    async def register_request(self, request_id: str, payload: dict[str, Any], timeout_sec: float) -> asyncio.Future:
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        deadline = time.monotonic() + timeout_sec

        async with self._lock:
            self._pending_futures[request_id] = future
            self._pending_requests[request_id] = PendingRequest(
                request_id=request_id,
                payload=payload,
                deadline=deadline,
            )

        return future

    async def handle_disconnect_grace(self, request_id: str, send_func) -> None:
        """
        연결이 내려간 직후에는 짧게 대기하며 재연결되면 자동 재전송한다.
        """
        start = time.monotonic()
        while True:
            async with self._lock:
                pending = self._pending_requests.get(request_id)
                future = self._pending_futures.get(request_id)

            if pending is None or future is None or future.done():
                return

            now = time.monotonic()
            if now >= pending.deadline:
                future.set_result(
                    {
                        "ok": False,
                        "error_code": "EXTENSION_TIMEOUT",
                        "message": "deadline exceeded while waiting reconnect",
                    }
                )
                await self.cleanup(request_id)
                return

            grace_exceeded = (now - start) > self.reconnect_grace_window_sec
            if self._extension_connected.is_set():
                await self._resend_pending_once(pending, send_func)
                return

            if grace_exceeded:
                future.set_result(
                    {
                        "ok": False,
                        "error_code": ExtensionOfflineError.code,
                        "message": "extension reconnect grace exceeded",
                        "reconnect_grace_window_sec": self.reconnect_grace_window_sec,
                    }
                )
                await self.cleanup(request_id)
                return

            await asyncio.sleep(self.resend_interval_sec)

    async def _resend_pending_once(self, pending: PendingRequest, send_func) -> None:
        pending.attempts += 1
        payload = {
            **pending.payload,
            "meta": {
                **pending.payload.get("meta", {}),
                "resent": True,
                "resent_attempt": pending.attempts,
                "phase": "EXTENSION_RECONNECTED",
            },
        }
        await send_func(payload)

    async def resolve(self, request_id: str, message: dict[str, Any]) -> None:
        async with self._lock:
            future = self._pending_futures.get(request_id)

        if future and not future.done():
            future.set_result(message)
        await self.cleanup(request_id)

    async def cleanup(self, request_id: str) -> None:
        async with self._lock:
            self._pending_futures.pop(request_id, None)
            self._pending_requests.pop(request_id, None)


class Hub:
    def __init__(self) -> None:
        self.request_router = RequestRouter()

    async def send_to_extension(self, request_id: str, payload: dict[str, Any], timeout_sec: float, send_func) -> dict[str, Any]:
        future = await self.request_router.register_request(request_id, payload, timeout_sec)

        if self.request_router._extension_connected.is_set():
            await send_func(payload)
        else:
            asyncio.create_task(self.request_router.handle_disconnect_grace(request_id, send_func))

        return await future
