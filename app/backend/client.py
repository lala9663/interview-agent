"""백엔드 클라이언트 (계약 5장). Agent는 DB에 닿지 않고, 저장은 모두 이 인터페이스로 백엔드에 맡긴다.

- `HttpBackendClient`: 실제 백엔드(`BACKEND_MODE=http`)
- `FakeBackendClient`: 단독 실행·테스트용(`BACKEND_MODE=fake`). context는 데모 데이터, 저장은 메모리와 로그에만
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

import httpx

from app.demo.data import demo_context
from app.schemas.backend import (
    BackendEvent,
    FollowUpsRequest,
    RefinedTranscriptRequest,
    RoomContext,
    UtterancesRequest,
)
from app.settings import BackendSettings

log = logging.getLogger(__name__)


class BackendError(Exception):
    pass


class BackendClient(ABC):
    @abstractmethod
    async def get_context(self, room_id: int) -> RoomContext:
        ...

    @abstractmethod
    async def save_utterances(self, room_id: int, body: UtterancesRequest) -> None:
        ...

    @abstractmethod
    async def save_followups(self, room_id: int, body: FollowUpsRequest) -> None:
        ...

    @abstractmethod
    async def put_refined_transcript(self, room_id: int, body: RefinedTranscriptRequest) -> None:
        ...

    @abstractmethod
    async def post_event(self, room_id: int, body: BackendEvent) -> None:
        ...

    async def close(self) -> None:  # noqa: B027
        ...


class HttpBackendClient(BackendClient):
    def __init__(self, settings: BackendSettings, internal_key: str,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._client = httpx.AsyncClient(
            base_url=settings.base_url.rstrip("/") + "/internal/v1", timeout=settings.timeout_s,
            headers={"X-Internal-Key": internal_key}, transport=transport)

    async def _send(self, method: str, path: str, body: Any = None) -> httpx.Response:
        try:
            response = await self._client.request(method, path, json=body)
            response.raise_for_status()
            return response
        except httpx.HTTPError as exc:
            raise BackendError(f"{method} {path} 실패: {exc!r}") from exc

    async def get_context(self, room_id: int) -> RoomContext:
        return RoomContext.model_validate((await self._send("GET", f"/rooms/{room_id}/context")).json())

    async def save_utterances(self, room_id: int, body: UtterancesRequest) -> None:
        await self._send("POST", f"/rooms/{room_id}/utterances", body.to_wire())

    async def save_followups(self, room_id: int, body: FollowUpsRequest) -> None:
        await self._send("POST", f"/rooms/{room_id}/follow-ups", body.to_wire())

    async def put_refined_transcript(self, room_id: int, body: RefinedTranscriptRequest) -> None:
        await self._send("PUT", f"/rooms/{room_id}/refined-transcript", body.to_wire())

    async def post_event(self, room_id: int, body: BackendEvent) -> None:
        await self._send("POST", f"/rooms/{room_id}/events", body.to_wire())

    async def close(self) -> None:
        await self._client.aclose()


class FakeBackendClient(BackendClient):
    """백엔드 없이 돌릴 때. 받은 요청은 `calls`에 쌓인다(테스트가 들여다본다)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, int, dict[str, Any]]] = []
        self.context_overrides: dict[int, dict[str, Any]] = {}
        self.fail = False  # True면 저장이 실패한다(재시도 큐 확인용)

    def _record(self, name: str, room_id: int, body: dict[str, Any]) -> None:
        if self.fail:
            raise BackendError(f"fake backend: {name} 실패(설정됨)")
        self.calls.append((name, room_id, body))
        log.info("[fake-backend] %s room=%s %s", name, room_id, str(body)[:200])

    def of(self, name: str, room_id: int | None = None) -> list[dict[str, Any]]:
        return [b for n, r, b in self.calls if n == name and (room_id is None or r == room_id)]

    async def get_context(self, room_id: int) -> RoomContext:
        if self.fail:
            raise BackendError("fake backend: context 실패(설정됨)")
        data = demo_context(room_id) | self.context_overrides.get(room_id, {})
        return RoomContext.model_validate(data)

    async def save_utterances(self, room_id: int, body: UtterancesRequest) -> None:
        self._record("utterances", room_id, body.to_wire())

    async def save_followups(self, room_id: int, body: FollowUpsRequest) -> None:
        self._record("follow-ups", room_id, body.to_wire())

    async def put_refined_transcript(self, room_id: int, body: RefinedTranscriptRequest) -> None:
        self._record("refined-transcript", room_id, body.to_wire())

    async def post_event(self, room_id: int, body: BackendEvent) -> None:
        self._record("events", room_id, body.to_wire())


def build_backend(settings: BackendSettings, internal_key: str) -> BackendClient:
    return HttpBackendClient(settings, internal_key) if settings.mode == "http" else FakeBackendClient()
