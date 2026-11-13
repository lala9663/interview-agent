"""방 상태·이벤트 버스, 그리고 인스턴스 두 대가 같은 상태·버스를 쓸 때의 동작.

Redis 구현은 실제 Redis 없이 **가짜 클라이언트 객체**로 코드 경로만 확인한다(명령 이름·인자 순서는 가짜가
우리 가정대로 받는 것이므로, 실제 Redis에서의 동작 확인은 아니다: docs/open-questions.md).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from starlette.websockets import WebSocketState

from app.container import Container
from app.live.hub import Connection
from app.settings import ServerSettings, Settings, StateSettings
from app.state.event_bus import MemoryEventBus, RedisEventBus, control_channel, events_channel
from app.state.factory import build_state
from app.state.room_store import MemoryRoomStateStore, RedisRoomStateStore


class FakeRedis:
    """redis.asyncio 클라이언트 중 우리가 쓰는 부분만 흉내 낸다."""

    def __init__(self) -> None:
        self.data: dict[str, tuple[Any, float | None]] = {}
        self.subscribers: dict[str, list[asyncio.Queue]] = {}

    def _get(self, key: str) -> Any:
        value = self.data.get(key)
        if value is None or (value[1] is not None and value[1] < time.monotonic()):
            self.data.pop(key, None)
            return None
        return value[0]

    async def get(self, key: str) -> Any:
        v = self._get(key)
        return None if v is None else str(v).encode()

    async def set(self, key: str, value: Any, nx: bool = False, ex: int | None = None) -> bool | None:
        if nx and self._get(key) is not None:
            return None
        self.data[key] = (value, time.monotonic() + ex if ex else None)
        return True

    async def incr(self, key: str) -> int:
        value = int(self._get(key) or 0) + 1
        self.data[key] = (value, None)
        return value

    async def expire(self, key: str, seconds: int) -> int:
        if self._get(key) is None:
            return 0
        self.data[key] = (self.data[key][0], time.monotonic() + int(seconds))
        return 1

    async def delete(self, *keys: str) -> int:
        return sum(self.data.pop(k, None) is not None for k in keys)

    async def eval(self, script: str, numkeys: int, key: str, *args: Any) -> int:
        if self._get(key) != args[0]:
            return 0
        if "del" in script:
            return await self.delete(key)
        return await self.expire(key, int(args[1]))

    async def publish(self, channel: str, message: str) -> int:
        for q in self.subscribers.get(channel, []):
            q.put_nowait({"type": "message", "data": message.encode()})
        return len(self.subscribers.get(channel, []))

    def pubsub(self) -> "FakePubSub":
        return FakePubSub(self)


class FakePubSub:
    def __init__(self, redis: FakeRedis) -> None:
        self.redis = redis
        self.queue: asyncio.Queue = asyncio.Queue()
        self.channels: list[str] = []

    async def subscribe(self, channel: str) -> None:
        self.channels.append(channel)
        self.redis.subscribers.setdefault(channel, []).append(self.queue)

    async def get_message(self, ignore_subscribe_messages: bool = True, timeout: float = 1.0) -> Any:
        try:
            return await asyncio.wait_for(self.queue.get(), timeout)
        except asyncio.TimeoutError:
            return None

    async def unsubscribe(self) -> None:
        for channel in self.channels:
            self.redis.subscribers[channel].remove(self.queue)

    async def aclose(self) -> None:
        pass


async def _check_store(store) -> None:
    assert await store.last_seq(1) == 0
    assert [await store.next_seq(1) for _ in range(3)] == [1, 2, 3] and await store.next_seq(2) == 1
    assert await store.last_seq(1) == 3
    assert await store.acquire_audio(1, "a:1", 30) is True
    assert await store.acquire_audio(1, "b:9", 30) is False  # 다른 단말은 못 잡는다
    assert await store.acquire_audio(1, "a:1", 30) is True  # 같은 단말은 다시 잡아도 된다
    assert await store.audio_holder(1) == "a:1"
    await store.release_audio(1, "b:9")  # 주인이 아니면 풀지 못한다
    assert await store.audio_holder(1) == "a:1"
    await store.refresh_audio(1, "a:1", 30)
    await store.release_audio(1, "a:1")
    assert await store.audio_holder(1) is None and await store.acquire_audio(1, "b:9", 30) is True
    await store.set_owner(1, "agent-1")
    assert await store.get_owner(1) == "agent-1" and await store.get_owner(2) is None
    await store.delete_room(1)
    assert await store.last_seq(1) == 0 and await store.get_owner(1) is None and await store.audio_holder(1) is None


async def test_memory_room_state_store():
    await _check_store(MemoryRoomStateStore())


async def test_redis_room_state_store_logic_with_fake_client():
    redis = FakeRedis()
    await _check_store(RedisRoomStateStore(redis, prefix="t"))
    store = RedisRoomStateStore(redis, prefix="t")
    assert await store.acquire_audio(5, "a:1", 30) and "t:room:5:audio" in redis.data


async def test_audio_lock_expires():
    store = MemoryRoomStateStore()
    assert await store.acquire_audio(1, "a:1", 0)  # TTL이 지나면(단말이 죽은 경우) 다른 단말이 잡을 수 있다
    assert await store.acquire_audio(1, "b:2", 30) is True


async def _check_bus(bus) -> None:
    got: list[tuple[str, dict]] = []

    async def handler_a(msg: dict) -> None:
        got.append(("a", msg))

    async def handler_b(msg: dict) -> None:
        got.append(("b", msg))

    sub_a = await bus.subscribe("room:1", handler_a)
    sub_b = await bus.subscribe("room:1", handler_b)
    sub_c = await bus.subscribe("room:2", handler_b)
    await bus.publish("room:1", {"type": "transcript.final", "refinedText": "한글"})
    await asyncio.sleep(0.05)
    assert sorted(got, key=lambda x: x[0]) == [("a", {"type": "transcript.final", "refinedText": "한글"}),
                                               ("b", {"type": "transcript.final", "refinedText": "한글"})]
    await sub_a.close()
    got.clear()
    await bus.publish("room:1", {"n": 2})
    await asyncio.sleep(0.05)
    assert got == [("b", {"n": 2})]
    await sub_b.close()
    await sub_c.close()


async def test_memory_event_bus():
    await _check_bus(MemoryEventBus())


async def test_redis_event_bus_logic_with_fake_client():
    await _check_bus(RedisEventBus(FakeRedis()))


def test_default_state_is_memory_and_channel_names():
    store, bus, client = build_state(StateSettings())
    assert isinstance(store, MemoryRoomStateStore) and isinstance(bus, MemoryEventBus) and client is None
    assert events_channel("ia", 9001) == "ia:room:9001:events"
    assert control_channel("ia", 9001) == "ia:room:9001:control"


class _FakeSocket:
    """LocalHub가 보는 WebSocket의 최소 모양."""

    def __init__(self) -> None:
        self.application_state = WebSocketState.CONNECTED
        self.sent: list[dict] = []
        self.closed: int | None = None

    async def send_json(self, event: dict) -> None:
        self.sent.append(event)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = code


async def test_two_instances_share_events_commands_and_finalize(settings, providers):
    """인스턴스 A에 마이크가 붙고, 인스턴스 B에 구독 단말이 붙은 상황(같은 상태·버스 = Redis 역할)."""
    shared = (MemoryRoomStateStore(), MemoryEventBus())

    def instance(name: str) -> Container:
        s = Settings(server=ServerSettings(instance_id=name), backend=settings.backend, live=settings.live,
                     audio=settings.audio)
        return Container(s, providers=providers, shared_state=shared)

    a, b = instance("agent-a"), instance("agent-b")
    await a.startup()
    await b.startup()
    try:
        session, _ = await a.rooms.open_session(9001)  # 방의 주인은 A
        assert await shared[0].get_owner(9001) == "agent-a" and 9001 not in b.rooms.sessions
        assert await shared[0].acquire_audio(9001, "agent-a:mic", 30)
        assert not await shared[0].acquire_audio(9001, "agent-b:mic2", 30)  # B로 붙은 두 번째 마이크는 막힌다

        socket = _FakeSocket()
        listener = Connection(socket, audio=False)  # B에 붙은 구독 단말
        await b.hub.join(9001, listener)
        await session.emit({"type": "transcript.partial", "seq": 1, "text": "안녕하세요"})
        assert socket.sent == [{"type": "transcript.partial", "seq": 1, "text": "안녕하세요"}]  # A의 이벤트가 B의 단말에

        await b.rooms.send_control(9001, {"type": "role", "role": "CANDIDATE"})  # B에서 보낸 명령이 A의 세션에
        assert session.role_override == "CANDIDATE"

        flushed, scheduled = await b.rooms.finalize(9001, post_stt=False)  # B로 온 finalize를 A가 처리하고 답한다
        assert (flushed, scheduled) == (True, False)
        assert 9001 not in a.rooms.sessions and socket.closed == 1000
        assert await shared[0].get_owner(9001) is None
        assert await b.rooms.finalize(9001, post_stt=True) == (True, False)  # 이미 끝난 방은 할 일이 없다
    finally:
        await a.shutdown()
        await b.shutdown()


class _BrokenBus(MemoryEventBus):
    """publish가 실패하는 버스(Redis가 끊긴 상황)."""

    async def publish(self, channel: str, message: dict) -> None:
        raise ConnectionError("bus down")


class _BrokenSeqStore(MemoryRoomStateStore):
    async def next_seq(self, room_id: int) -> int:
        raise ConnectionError("state store down")


async def test_pipeline_survives_bus_and_state_store_failure(settings, providers):
    """이벤트 버스·상태 저장소가 끊겨도 전사와 백엔드 저장, 음성 모으기는 계속된다."""
    from app.demo import synth

    c = Container(settings, providers=providers, shared_state=(_BrokenSeqStore(), _BrokenBus()))
    await c.startup()
    try:
        session, _ = await c.rooms.open_session(9001)
        for frame in synth.frames(synth.conversation([(2.0, 1.0), (3.0, 1.0)])):
            await session.feed(frame)
        for _ in range(200):
            if len(c.backend.of("utterances", 9001)) == 2:
                break
            await asyncio.sleep(0.02)
        saved = [b["items"][0]["seq"] for b in c.backend.of("utterances", 9001)]
        assert saved == [1, 2]  # 세션 카운터로 seq가 이어진다
        assert await c.audio_store.duration_ms(9001) >= 7000  # 음성은 그대로 모였다(사후 STT 가능)
    finally:
        await c.shutdown()
