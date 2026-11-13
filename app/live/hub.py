"""이 인스턴스에 붙은 WebSocket 단말 관리.

방의 첫 단말이 붙으면 EventBus의 방 이벤트 채널을 구독하고, 받은 이벤트를 이 인스턴스의 그 방 단말 모두에게 보낸다.
이벤트를 만든 인스턴스와 단말이 붙은 인스턴스가 달라도(Redis 버스) 같은 경로로 전달된다.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from starlette.websockets import WebSocket, WebSocketState

from app.state.event_bus import EventBus, Subscription, events_channel

log = logging.getLogger(__name__)

CLOSE_EVENT = "_close"  # 내부 이벤트: 방의 모든 단말을 닫는다(클라이언트로는 나가지 않는다)
SEND_TIMEOUT_S = 3.0


class Connection:
    def __init__(self, ws: WebSocket, *, audio: bool, user_id: str = "") -> None:
        self.id = uuid.uuid4().hex[:12]
        self.ws = ws
        self.audio = audio
        self.user_id = user_id
        self._lock = asyncio.Lock()

    async def send(self, event: dict[str, Any]) -> bool:
        if self.ws.application_state != WebSocketState.CONNECTED:
            return False
        try:
            async with self._lock:
                await asyncio.wait_for(self.ws.send_json(event), SEND_TIMEOUT_S)
            return True
        except Exception:  # noqa: BLE001 - 끊긴 단말은 조용히 뺀다
            return False

    async def close(self, code: int, reason: str = "") -> None:
        try:
            async with self._lock:
                if self.ws.application_state == WebSocketState.CONNECTED:
                    await self.ws.close(code=code, reason=reason)
        except Exception:  # noqa: BLE001
            pass


class LocalHub:
    def __init__(self, bus: EventBus, key_prefix: str = "ia") -> None:
        self._bus = bus
        self._prefix = key_prefix
        self._rooms: dict[int, set[Connection]] = {}
        self._subs: dict[int, Subscription] = {}
        self._lock = asyncio.Lock()

    def connections(self, room_id: int) -> list[Connection]:
        return list(self._rooms.get(room_id, ()))

    def count(self) -> int:
        return sum(len(c) for c in self._rooms.values())

    async def join(self, room_id: int, conn: Connection) -> None:
        async with self._lock:
            self._rooms.setdefault(room_id, set()).add(conn)
            if room_id not in self._subs:
                async def handler(event: dict[str, Any], rid: int = room_id) -> None:
                    await self._fanout(rid, event)

                self._subs[room_id] = await self._bus.subscribe(events_channel(self._prefix, room_id), handler)

    async def leave(self, room_id: int, conn: Connection) -> None:
        async with self._lock:
            conns = self._rooms.get(room_id)
            if conns is None:
                return
            conns.discard(conn)
            if not conns:
                self._rooms.pop(room_id, None)
                sub = self._subs.pop(room_id, None)
                if sub is not None:
                    await sub.close()

    async def _fanout(self, room_id: int, event: dict[str, Any]) -> None:
        conns = self.connections(room_id)
        if not conns:
            return
        if event.get("type") == CLOSE_EVENT:
            await asyncio.gather(*(c.close(int(event.get("code", 1000)), str(event.get("reason", ""))) for c in conns))
            return
        await asyncio.gather(*(c.send(event) for c in conns))
