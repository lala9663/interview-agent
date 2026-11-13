"""설정에 따라 RoomStateStore와 EventBus를 만든다(memory | redis, Sentinel 지원)."""

from __future__ import annotations

from typing import Any

from app.settings import StateSettings
from app.state.event_bus import EventBus, MemoryEventBus, RedisEventBus
from app.state.room_store import MemoryRoomStateStore, RedisRoomStateStore, RoomStateStore


def build_redis_client(settings: StateSettings) -> Any:
    """Redis 클라이언트. `STATE_REDIS_SENTINELS`가 있으면 Sentinel로 master를 찾는다."""
    import redis.asyncio as aioredis  # redis를 쓰는 경우에만 불러온다

    password = settings.redis_password or None
    if settings.redis_sentinels.strip():
        from redis.asyncio.sentinel import Sentinel

        nodes = []
        for item in settings.redis_sentinels.split(","):
            host, _, port = item.strip().partition(":")
            nodes.append((host, int(port or 26379)))
        sentinel = Sentinel(nodes, socket_timeout=2.0, password=password, sentinel_kwargs={"password": password})
        return sentinel.master_for(settings.redis_sentinel_master, db=settings.redis_db, password=password)
    return aioredis.from_url(settings.redis_url, password=password)


def build_state(settings: StateSettings) -> tuple[RoomStateStore, EventBus, Any]:
    """(상태 저장소, 이벤트 버스, redis 클라이언트 또는 None)."""
    client = None
    if settings.store == "redis" or settings.event_bus == "redis":
        client = build_redis_client(settings)
    store: RoomStateStore = (
        RedisRoomStateStore(client, prefix=settings.key_prefix, room_ttl_hours=settings.room_ttl_hours)
        if settings.store == "redis" else MemoryRoomStateStore()
    )
    bus: EventBus = RedisEventBus(client) if settings.event_bus == "redis" else MemoryEventBus()
    return store, bus, client
