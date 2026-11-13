"""방 상태 저장소: 인스턴스가 여러 대여도 같아야 하는 값만 둔다.

- seq 카운터: `transcript.*`의 seq (방 안에서 1부터)
- 마이크 잠금: 방마다 마이크 단말 하나. 다른 인스턴스에 붙어도 중복을 막는다
- 주인 인스턴스: 방 파이프라인(오디오 버퍼·청크·대화 문맥)이 어느 인스턴스에 있는지

대화 문맥과 오디오는 주인 인스턴스의 메모리에 있다(여기에 두지 않는다).
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any


class RoomStateStore(ABC):
    @abstractmethod
    async def next_seq(self, room_id: int) -> int:
        ...

    @abstractmethod
    async def last_seq(self, room_id: int) -> int:
        ...

    @abstractmethod
    async def acquire_audio(self, room_id: int, holder: str, ttl_s: int) -> bool:
        """마이크 잠금을 잡는다. 이미 다른 단말이 잡고 있으면 False."""

    @abstractmethod
    async def refresh_audio(self, room_id: int, holder: str, ttl_s: int) -> None:
        ...

    @abstractmethod
    async def release_audio(self, room_id: int, holder: str) -> None:
        ...

    @abstractmethod
    async def audio_holder(self, room_id: int) -> str | None:
        ...

    @abstractmethod
    async def set_owner(self, room_id: int, instance_id: str) -> None:
        ...

    @abstractmethod
    async def get_owner(self, room_id: int) -> str | None:
        ...

    @abstractmethod
    async def delete_room(self, room_id: int) -> None:
        ...

    async def close(self) -> None:  # noqa: B027
        ...


class MemoryRoomStateStore(RoomStateStore):
    def __init__(self) -> None:
        self._seq: dict[int, int] = {}
        self._audio: dict[int, tuple[str, float]] = {}
        self._owner: dict[int, str] = {}

    async def next_seq(self, room_id: int) -> int:
        self._seq[room_id] = self._seq.get(room_id, 0) + 1
        return self._seq[room_id]

    async def last_seq(self, room_id: int) -> int:
        return self._seq.get(room_id, 0)

    async def acquire_audio(self, room_id: int, holder: str, ttl_s: int) -> bool:
        current = self._audio.get(room_id)
        if current and current[0] != holder and current[1] > time.monotonic():
            return False
        self._audio[room_id] = (holder, time.monotonic() + ttl_s)
        return True

    async def refresh_audio(self, room_id: int, holder: str, ttl_s: int) -> None:
        current = self._audio.get(room_id)
        if current and current[0] == holder:
            self._audio[room_id] = (holder, time.monotonic() + ttl_s)

    async def release_audio(self, room_id: int, holder: str) -> None:
        current = self._audio.get(room_id)
        if current and current[0] == holder:
            del self._audio[room_id]

    async def audio_holder(self, room_id: int) -> str | None:
        current = self._audio.get(room_id)
        return current[0] if current and current[1] > time.monotonic() else None

    async def set_owner(self, room_id: int, instance_id: str) -> None:
        self._owner[room_id] = instance_id

    async def get_owner(self, room_id: int) -> str | None:
        return self._owner.get(room_id)

    async def delete_room(self, room_id: int) -> None:
        self._seq.pop(room_id, None)
        self._audio.pop(room_id, None)
        self._owner.pop(room_id, None)


_RELEASE_LUA = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end"
_REFRESH_LUA = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('expire', KEYS[1], ARGV[2]) else return 0 end"


class RedisRoomStateStore(RoomStateStore):
    """Redis 구현. (Redis가 없는 환경이라 실제 Redis로는 아직 검증하지 못했다.)

    키: {prefix}:room:{id}:seq (INCR), :audio (SET NX EX), :owner
    """

    def __init__(self, client: Any, *, prefix: str = "ia", room_ttl_hours: int = 12) -> None:
        self._redis = client
        self._prefix = prefix
        self._ttl = room_ttl_hours * 3600

    def _key(self, room_id: int, name: str) -> str:
        return f"{self._prefix}:room:{room_id}:{name}"

    @staticmethod
    def _text(value: Any) -> str | None:
        if value is None:
            return None
        return value.decode() if isinstance(value, bytes) else str(value)

    async def next_seq(self, room_id: int) -> int:
        key = self._key(room_id, "seq")
        value = int(await self._redis.incr(key))
        if value == 1:
            await self._redis.expire(key, self._ttl)
        return value

    async def last_seq(self, room_id: int) -> int:
        return int(self._text(await self._redis.get(self._key(room_id, "seq"))) or 0)

    async def acquire_audio(self, room_id: int, holder: str, ttl_s: int) -> bool:
        key = self._key(room_id, "audio")
        if await self._redis.set(key, holder, nx=True, ex=ttl_s):
            return True
        return self._text(await self._redis.get(key)) == holder

    async def refresh_audio(self, room_id: int, holder: str, ttl_s: int) -> None:
        await self._redis.eval(_REFRESH_LUA, 1, self._key(room_id, "audio"), holder, ttl_s)

    async def release_audio(self, room_id: int, holder: str) -> None:
        await self._redis.eval(_RELEASE_LUA, 1, self._key(room_id, "audio"), holder)

    async def audio_holder(self, room_id: int) -> str | None:
        return self._text(await self._redis.get(self._key(room_id, "audio")))

    async def set_owner(self, room_id: int, instance_id: str) -> None:
        await self._redis.set(self._key(room_id, "owner"), instance_id, ex=self._ttl)

    async def get_owner(self, room_id: int) -> str | None:
        return self._text(await self._redis.get(self._key(room_id, "owner")))

    async def delete_room(self, room_id: int) -> None:
        await self._redis.delete(*(self._key(room_id, n) for n in ("seq", "audio", "owner")))
