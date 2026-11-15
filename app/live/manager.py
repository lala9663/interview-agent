"""방 세션 관리: 만들기, 명령 전달, 마무리(finalize), 정리.

방의 주인 인스턴스 = 마이크 단말이 붙은 인스턴스. 그 인스턴스만 `RoomSession`을 가진다.
다른 인스턴스에 붙은 단말의 명령(role, utterance.fix …)과 다른 인스턴스로 들어온 finalize 요청은
EventBus의 control 채널로 주인 인스턴스에 전달된다.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

from app.live.deps import LiveDeps
from app.live.hub import CLOSE_EVENT
from app.live.post_stt import run_post_stt
from app.live.session import RoomSession
from app.schemas import ws as events
from app.schemas.backend import RoomContext
from app.state.event_bus import Subscription, control_channel, events_channel

log = logging.getLogger(__name__)
FINALIZE = "_finalize"
_REMOTE_FINALIZE_WAIT_S = 25.0


class RoomManager:
    def __init__(self, deps: LiveDeps) -> None:
        self.deps = deps
        self.sessions: dict[int, RoomSession] = {}
        self._control: dict[int, Subscription] = {}
        self._post_tasks: set[asyncio.Task] = set()
        self._locks: dict[int, asyncio.Lock] = {}
        self._post_sem = asyncio.Semaphore(max(1, deps.settings.live.post_stt_concurrent_rooms))
        self._reaper: asyncio.Task | None = None
        self._prefix = deps.settings.state.key_prefix

    # ---------- 세션 ----------
    async def open_session(self, room_id: int) -> tuple[RoomSession, bool]:
        """마이크 단말이 붙을 때 부른다. (세션, 백엔드 context를 읽었는지)"""
        async with self._locks.setdefault(room_id, asyncio.Lock()):
            existing = self.sessions.get(room_id)
            if existing is not None:
                return existing, True
            context_ok = True
            try:
                context = await self.deps.backend.get_context(room_id)
            except Exception as exc:  # noqa: BLE001 - 백엔드가 없어도 전사는 한다
                log.warning("room %s context 읽기 실패, 빈 context로 시작: %r", room_id, exc)
                context, context_ok = RoomContext(room_id=room_id), False
            session = RoomSession(room_id, self.deps, context)
            session.start()
            self.sessions[room_id] = session
            await self.deps.state.set_owner(room_id, self.deps.instance_id)

            async def on_control(msg: dict[str, Any], rid: int = room_id) -> None:
                await self._on_control(rid, msg)

            self._control[room_id] = await self.deps.bus.subscribe(control_channel(self._prefix, room_id), on_control)
            if not context_ok:
                asyncio.create_task(self._retry_context(session))
            if self._reaper is None or self._reaper.done():
                self._reaper = asyncio.create_task(self._reap_idle())
            return session, context_ok

    async def _retry_context(self, session: RoomSession) -> None:
        for delay in (2, 5, 10, 30):
            await asyncio.sleep(delay)
            if session.closed:
                return
            await session.refresh_context()
            if session.context.interview_id is not None:
                return

    def can_accept_room(self, room_id: int) -> bool:
        return room_id in self.sessions or len(self.sessions) < self.deps.settings.live.max_rooms

    async def send_control(self, room_id: int, msg: dict[str, Any]) -> None:
        await self.deps.bus.publish(control_channel(self._prefix, room_id), msg)

    async def _on_control(self, room_id: int, msg: dict[str, Any]) -> None:
        session = self.sessions.get(room_id)
        if session is None:
            return
        if msg.get("type") == FINALIZE:
            flushed, scheduled = await self._finalize_local(session, bool(msg.get("postStt", True)))
            if msg.get("replyTo"):
                await self.deps.bus.publish(msg["replyTo"], {"flushed": flushed, "postSttScheduled": scheduled})
            return
        try:
            await session.handle_control(msg)
        except Exception:  # noqa: BLE001
            log.exception("room %s 명령 처리 오류: %s", room_id, msg.get("type"))

    # ---------- 마무리 ----------
    async def finalize(self, room_id: int, post_stt: bool) -> tuple[bool, bool]:
        """(flushed, postSttScheduled). 방이 다른 인스턴스에 있으면 그쪽에 시키고 답을 기다린다."""
        session = self.sessions.get(room_id)
        if session is not None:
            return await self._finalize_local(session, post_stt)
        owner = await self.deps.state.get_owner(room_id)
        if owner is None or owner == self.deps.instance_id:
            return True, False  # 연 적이 없는 방: 할 일이 없다
        reply_channel = f"{self._prefix}:reply:{uuid.uuid4().hex}"
        answer: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()

        async def on_reply(msg: dict[str, Any]) -> None:
            if not answer.done():
                answer.set_result(msg)

        sub = await self.deps.bus.subscribe(reply_channel, on_reply)
        try:
            await self.send_control(room_id, {"type": FINALIZE, "postStt": post_stt, "replyTo": reply_channel})
            msg = await asyncio.wait_for(answer, _REMOTE_FINALIZE_WAIT_S)
            return bool(msg.get("flushed")), bool(msg.get("postSttScheduled"))
        except asyncio.TimeoutError:
            log.warning("room %s: 주인 인스턴스(%s)가 finalize에 답하지 않음", room_id, owner)
            return False, False
        finally:
            await sub.close()

    async def _finalize_local(self, session: RoomSession, post_stt: bool) -> tuple[bool, bool]:
        room_id = session.room_id
        if session.closed:
            return True, False
        session.closed = True  # 더 받지 않는다
        flushed = await session.flush(self.deps.settings.live.finalize_flush_timeout_s)
        scheduled = post_stt and await self.deps.audio_store.duration_ms(room_id) > 0
        await self.deps.bus.publish(events_channel(self._prefix, room_id),
                                    {"type": CLOSE_EVENT, "code": events.CLOSE_NORMAL, "reason": "room finalized"})
        await session.stop()
        if scheduled:
            task = asyncio.create_task(self._post_stt_then_cleanup(session), name=f"room-{room_id}-post-stt")
            self._post_tasks.add(task)
            task.add_done_callback(self._post_tasks.discard)
        else:
            await self._cleanup(session, release_audio=True)
        return flushed, scheduled

    async def _post_stt_then_cleanup(self, session: RoomSession) -> None:
        try:
            # 여러 방이 한꺼번에 끝나도 메모리가 한 번에 불어나지 않게 _post_sem으로 묶는다. 끝나면 음성을 놓아 준다
            await run_post_stt(session, self._post_sem)
        except Exception:  # noqa: BLE001
            log.exception("room %s 사후 STT 작업 오류", session.room_id)
        finally:
            await self._cleanup(session, release_audio=False)

    async def _cleanup(self, session: RoomSession, *, release_audio: bool) -> None:
        room_id = session.room_id
        if release_audio:
            await self.deps.audio_store.release(room_id)
        self.deps.providers.stt_live.reset_session(str(room_id))
        self.sessions.pop(room_id, None)
        self._locks.pop(room_id, None)
        sub = self._control.pop(room_id, None)
        if sub is not None:
            await sub.close()
        await self.deps.state.delete_room(room_id)

    async def _reap_idle(self) -> None:
        """마이크가 끊긴 채 오래 방치된 방을 정리한다(finalize가 오지 않은 경우)."""
        limit = self.deps.settings.live.idle_close_minutes * 60
        while self.sessions:
            await asyncio.sleep(30)
            now = time.monotonic()
            for session in list(self.sessions.values()):
                if not session.closed and not session.audio_connected and now - session.last_audio_at > limit:
                    log.warning("room %s: %d분 동안 마이크가 없어 정리한다(사후 STT 포함)", session.room_id, limit // 60)
                    await self._finalize_local(session, post_stt=True)

    async def wait_post_stt(self, timeout_s: float = 30.0) -> None:
        """진행 중인 사후 STT가 끝날 때까지 기다린다(테스트·종료용)."""
        if self._post_tasks:
            await asyncio.wait(list(self._post_tasks), timeout=timeout_s)

    async def shutdown(self) -> None:
        if self._reaper is not None:
            self._reaper.cancel()
        for task in list(self._post_tasks):
            task.cancel()
        for session in list(self.sessions.values()):
            await session.stop()
            await self._cleanup(session, release_audio=True)

    def stats(self) -> dict[str, Any]:
        p = self.deps.providers
        return {
            "instanceId": self.deps.instance_id,
            "rooms": len(self.sessions),
            "postSttRunning": len(self._post_tasks),
            "outboxPending": self.deps.outbox.pending,
            "outboxDropped": self.deps.outbox.dropped,
            "providers": {name: {"impl": prov.impl, "calls": prov.calls, "failures": prov.failures,
                                 "waiting": prov.waiting, "health": prov.health}
                          for name, prov in (("llm.fast", p.llm_fast), ("llm.quality", p.llm_quality),
                                             ("stt.live", p.stt_live), ("stt.post", p.stt_post))},
            "roomStats": [{"roomId": s.room_id, "queueDepth": s.queue_depth, "maxQueueDepth": s.max_queue_depth,
                           "utterances": len(s.utterances), "droppedChunks": s.dropped_chunks,
                           "audioConnected": s.audio_connected} for s in self.sessions.values()],
        }
