"""방 이벤트 중계.

서버가 여러 대일 때: 마이크 WebSocket은 한 인스턴스에 붙고(그 인스턴스가 방의 파이프라인을 돌린다),
그 인스턴스가 만든 이벤트는 EventBus(Redis pub/sub)를 거쳐 다른 인스턴스에 붙은 구독 단말에도 간다.
한 대일 때는 MemoryEventBus로 같은 코드 경로를 탄다.

채널:
  events(room)  : 서버 → 클라이언트 이벤트(API 명세 3.2). 모든 인스턴스의 허브가 구독해 자기 단말에 보낸다
  control(room) : 클라이언트 → 방 주인 인스턴스로 가는 명령(role, utterance.fix, finalize 등)
"""

from __future__ import annotations

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from typing import Any, Awaitable, Callable

log = logging.getLogger(__name__)
Handler = Callable[[dict[str, Any]], Awaitable[None]]


def events_channel(prefix: str, room_id: int) -> str:
    return f"{prefix}:room:{room_id}:events"


def control_channel(prefix: str, room_id: int) -> str:
    return f"{prefix}:room:{room_id}:control"


class Subscription(ABC):
    @abstractmethod
    async def close(self) -> None:
        ...


class EventBus(ABC):
    @abstractmethod
    async def publish(self, channel: str, message: dict[str, Any]) -> None:
        ...

    @abstractmethod
    async def subscribe(self, channel: str, handler: Handler) -> Subscription:
        ...

    async def close(self) -> None:  # noqa: B027
        ...


class _MemorySubscription(Subscription):
    def __init__(self, bus: "MemoryEventBus", channel: str, handler: Handler) -> None:
        self._bus, self._channel, self._handler = bus, channel, handler

    async def close(self) -> None:
        handlers = self._bus._handlers.get(self._channel)
        if handlers and self._handler in handlers:
            handlers.remove(self._handler)
            if not handlers:
                self._bus._handlers.pop(self._channel, None)


class MemoryEventBus(EventBus):
    """프로세스 안에서만 도는 버스(서버 한 대, 테스트)."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = {}

    async def publish(self, channel: str, message: dict[str, Any]) -> None:
        for handler in list(self._handlers.get(channel, ())):
            try:
                await handler(message)
            except Exception:  # noqa: BLE001 - 한 구독자의 오류가 다른 구독자를 막지 않게
                log.exception("이벤트 처리 실패: %s", channel)

    async def subscribe(self, channel: str, handler: Handler) -> Subscription:
        self._handlers.setdefault(channel, []).append(handler)
        return _MemorySubscription(self, channel, handler)


class _RedisSubscription(Subscription):
    def __init__(self, pubsub: Any, task: asyncio.Task) -> None:
        self._pubsub, self._task = pubsub, task

    async def close(self) -> None:
        self._task.cancel()
        try:
            await self._pubsub.unsubscribe()
            await self._pubsub.aclose()
        except Exception:  # noqa: BLE001
            log.debug("redis pubsub 닫기 실패", exc_info=True)


class RedisEventBus(EventBus):
    """Redis pub/sub. 메시지는 JSON 문자열. (Redis가 없는 환경이라 실제 Redis로는 아직 검증하지 못했다.)"""

    def __init__(self, client: Any) -> None:
        self._redis = client

    async def publish(self, channel: str, message: dict[str, Any]) -> None:
        await self._redis.publish(channel, json.dumps(message, ensure_ascii=False))

    async def subscribe(self, channel: str, handler: Handler) -> Subscription:
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(channel)

        async def listen() -> None:
            while True:
                try:
                    msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                    if msg and msg.get("type") == "message":
                        data = msg["data"]
                        await handler(json.loads(data.decode() if isinstance(data, bytes) else data))
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 - 연결이 끊겨도 다시 시도
                    log.exception("redis 구독 오류: %s", channel)
                    await asyncio.sleep(1.0)

        return _RedisSubscription(pubsub, asyncio.create_task(listen()))
