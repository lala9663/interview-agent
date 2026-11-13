"""방 하나의 실시간 파이프라인(마이크가 붙은 인스턴스에만 있다).

  PCM 프레임 → [AudioStore에 모음] → VAD 청크 → stt_queue → STT → transcript.partial
            → final_queue → (다듬기 ∥ 화자 판정) → transcript.final → 질문 매칭 → 백엔드 저장 → 꼬리질문

원칙: 면접 흐름이 멈추지 않는다.
- 큐가 밀리면(soft) partial과 다듬기를 건너뛰고 raw를 그대로 final로 낸다.
- 더 밀리면(hard) 가장 오래된 청크를 버리고 OVERLOADED를 알린다(그 구간은 사후 STT가 채운다).
- 다듬기가 제한 시간을 넘으면 raw 그대로 final을 내고, 나중에 고쳐 보내지 않는다.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from app.audio.chunker import AudioChunk, SpeechChunker
from app.live.deps import LiveDeps, PendingUtterance, Utterance, glossary_from_context
from app.live.followups import FollowUpScheduler
from app.matching.matcher import KIND_BANK, KIND_PREPARED, MatchCandidate, QuestionMatcher
from app.providers.base import ProviderBusy, ProviderError, SttRequest
from app.schemas import ws as events
from app.schemas.backend import BackendEvent, RoomContext, UtterancesRequest
from app.schemas.common import Speaker, SpeakerMethod
from app.speaker.base import RecentUtterance, SpeakerContext
from app.speaker.chain import build_speaker_chain
from app.state.event_bus import events_channel

log = logging.getLogger(__name__)
_SPEAKER_CONTEXT = 8


class RoomSession:
    def __init__(self, room_id: int, deps: LiveDeps, context: RoomContext) -> None:
        self.room_id = room_id
        self.deps = deps
        s = deps.settings
        self.live = s.live
        self.rule_as = s.speaker.rule_wire_method
        self.context = context
        self.glossary = glossary_from_context(context)
        self.matcher = QuestionMatcher(n_min=s.matching.ngram_min, n_max=s.matching.ngram_max,
                                       embedding=deps.providers.embedding)
        self._load_candidates(context)
        self.chain = build_speaker_chain(s.speaker, deps.providers.llm_fast,
                                         llm_timeout_s=self.live.speaker_llm_timeout_s)
        self.chunker = SpeechChunker.from_settings(s.audio)
        self.role_override: str | None = None
        self.utterances: dict[int, Utterance] = {}
        self.asked_question_ids: set[int] = set()
        self.audio_connected = False
        self.last_audio_at = time.monotonic()
        self.closed = False
        self.max_queue_depth = 0
        self.dropped_chunks = 0
        self._stt_queue: asyncio.Queue[tuple[AudioChunk, str | None, float]] = asyncio.Queue()
        self._final_queue: asyncio.Queue[PendingUtterance] = asyncio.Queue()
        self._pending: PendingUtterance | None = None
        self._inflight = 0  # 작업자가 꺼내서 처리 중인 항목 수
        self._seq = 0
        self._last_bus_error_at = 0.0
        self._tasks: list[asyncio.Task] = []
        self._last_status: tuple | None = None
        self._last_overload_at = 0.0
        self._dropped_audio_ms = 0
        self._last_drop_report_at = 0.0
        self._context_checked_at = 0.0
        self._channel = events_channel(s.state.key_prefix, room_id)
        self.followups = FollowUpScheduler(self)

    # ---------- 준비 ----------
    def _load_candidates(self, context: RoomContext) -> None:
        prepared = [MatchCandidate(KIND_PREPARED, q.text, question_id=q.id) for q in context.questions]
        bank = [MatchCandidate(KIND_BANK, q.text, question_id=q.id) for q in context.question_bank]
        self.matcher.replace(prepared + bank, keep_kinds=("FOLLOWUP",))

    def start(self) -> None:
        self._tasks = [asyncio.create_task(self._stt_worker(), name=f"room-{self.room_id}-stt"),
                       asyncio.create_task(self._final_worker(), name=f"room-{self.room_id}-final"),
                       asyncio.create_task(self._ticker(), name=f"room-{self.room_id}-tick")]

    async def stop(self) -> None:
        self.closed = True
        self.followups.cancel()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []

    async def refresh_context(self) -> None:
        """백엔드에서 context를 다시 읽는다(방 상태 확인, 면접 중 추가된 질문 반영)."""
        try:
            self.context = await self.deps.backend.get_context(self.room_id)
            self._load_candidates(self.context)
        except Exception as exc:  # noqa: BLE001
            log.warning("room %s context 다시 읽기 실패: %r", self.room_id, exc)

    # ---------- 이벤트 ----------
    async def emit(self, event: dict[str, Any]) -> None:
        try:
            await self.deps.bus.publish(self._channel, event)
        except Exception as exc:  # noqa: BLE001 - 버스(Redis)가 끊겨도 전사·저장·음성 모으기는 계속한다
            now = time.monotonic()
            if now - self._last_bus_error_at >= 10.0:
                self._last_bus_error_at = now
                log.error("room %s 이벤트 방송 실패(이벤트 버스 확인 필요): %r", self.room_id, exc)

    async def _next_seq(self) -> int:
        """방 안에서 1부터 늘어나는 번호. 상태 저장소가 끊기면 이 세션의 카운터로 이어 간다."""
        try:
            self._seq = max(self._seq + 1, await self.deps.state.next_seq(self.room_id))
        except Exception as exc:  # noqa: BLE001
            self._seq += 1
            log.warning("room %s seq 저장소 오류, 세션 카운터 사용: %r", self.room_id, exc)
        return self._seq

    @property
    def queue_depth(self) -> int:
        """아직 끝나지 않은 일의 수: 두 큐에서 기다리는 것 + 처리 중인 것."""
        return self._stt_queue.qsize() + self._final_queue.qsize() + self._inflight

    def status_event(self) -> dict[str, Any]:
        llm = self.deps.providers.llm_fast.health
        return events.status(self.deps.providers.stt_live.health, llm, self.queue_depth, self.audio_connected)

    async def emit_status(self, *, force: bool = False, ticking: bool = False) -> None:
        ev = self.status_event()
        key = (ev["stt"], ev["llm"], ev["queueDepth"], ev["audioConnected"])
        if key == self._last_status and not force:
            return
        last = self._last_status
        depth_only = last is not None and key[:2] + key[3:] == last[:2] + last[3:]
        soft = self.live.stt_queue_soft
        if depth_only and not force and not ticking and max(key[2], last[2]) < soft:
            return  # 밀리지 않은 상태의 큐 깊이 변화(0↔1)는 바로 알리지 않고 주기 알림에 맡긴다
        self._last_status = key
        await self.emit(ev)

    async def _ticker(self) -> None:
        while True:
            await asyncio.sleep(self.live.status_interval_s)
            await self.emit_status(ticking=True)

    # ---------- 오디오 입력 ----------
    async def accepting_audio(self) -> bool:
        """방이 LIVE인지. CREATED면 2초에 한 번 백엔드에 다시 묻는다."""
        if self.context.status == "LIVE":
            return True
        now = time.monotonic()
        if now - self._context_checked_at >= 2.0:
            self._context_checked_at = now
            await self.refresh_context()
            if self.context.status != "LIVE":
                await self.emit(events.error(events.ROOM_NOT_LIVE, "방이 아직 시작되지 않았습니다.", True))
        return self.context.status == "LIVE"

    async def set_audio_connected(self, connected: bool) -> None:
        self.audio_connected = connected
        self.last_audio_at = time.monotonic()
        if not connected:
            await self._enqueue(self.chunker.flush())  # 끊기기 전까지의 말은 처리한다
        await self.emit_status(force=True)

    async def feed(self, pcm: bytes) -> None:
        self.last_audio_at = time.monotonic()
        dropped_ms = await self.deps.audio_store.append(self.room_id, pcm)
        if dropped_ms:
            self._report_audio_drop(dropped_ms)
        chunks = self.chunker.feed(pcm)
        if self.chunker.in_speech:
            self.followups.speech_started()
        if chunks:
            await self._enqueue(chunks)

    def _report_audio_drop(self, dropped_ms: int) -> None:
        self._dropped_audio_ms += dropped_ms
        now = time.monotonic()
        if now - self._last_drop_report_at >= 60.0:  # 1분에 한 번만 알린다
            self._last_drop_report_at = now
            self.deps.outbox.post_event(self.room_id, BackendEvent(
                type="AUDIO_DROPPED", message="방 음성이 메모리 상한을 넘어 오래된 구간을 버렸습니다.",
                data={"droppedMs": self._dropped_audio_ms}))

    async def _enqueue(self, chunks: list[AudioChunk]) -> None:
        for chunk in chunks:
            if self._stt_queue.qsize() >= self.live.stt_queue_hard:
                try:
                    old, _, _ = self._stt_queue.get_nowait()
                    self._stt_queue.task_done()
                    self.dropped_chunks += 1
                    log.warning("room %s 과부하: %dms 청크를 버림", self.room_id, old.duration_ms)
                except asyncio.QueueEmpty:
                    pass
                await self._notify_overload()
            self._stt_queue.put_nowait((chunk, self.role_override, time.monotonic()))
        self.max_queue_depth = max(self.max_queue_depth, self.queue_depth)
        await self.emit_status()

    async def _notify_overload(self) -> None:
        now = time.monotonic()
        if now - self._last_overload_at >= 5.0:  # 5초에 한 번만 알린다
            self._last_overload_at = now
            await self.emit(events.error(events.OVERLOADED, "처리가 밀려 일부 음성의 실시간 전사를 건너뜁니다.", True))

    # ---------- STT 단계 ----------
    async def _stt_worker(self) -> None:
        while True:
            chunk, role, enqueued_at = await self._stt_queue.get()
            self._inflight += 1
            try:
                await self._handle_chunk(chunk, role, enqueued_at)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - 한 청크의 오류가 방을 멈추지 않게
                log.exception("room %s 청크 처리 오류", self.room_id)
            finally:
                self._inflight -= 1
                self._stt_queue.task_done()

    async def _flush_pending(self) -> None:
        if self._pending is not None:
            pending, self._pending = self._pending, None
            await self._final_queue.put(pending)

    async def _handle_chunk(self, chunk: AudioChunk, role: str | None, enqueued_at: float) -> None:
        degraded = self._stt_queue.qsize() >= self.live.stt_queue_soft
        if self._pending is not None and not chunk.continuation:
            await self._flush_pending()
        text = ""
        budget = self.live.max_chunk_wait_s - (time.monotonic() - enqueued_at)  # 이 청크가 더 기다려도 되는 시간
        if chunk.pcm and budget <= 0:
            # 너무 오래 기다린 청크: 지금 전사해도 늦다. 건너뛰고 새 청크를 먼저 처리한다
            self.dropped_chunks += 1
            await self._notify_overload()
        elif chunk.pcm:
            try:
                result = await self.deps.providers.stt_live.transcribe(SttRequest(
                    pcm=chunk.pcm, sample_rate=self.deps.settings.audio.sample_rate, session=str(self.room_id),
                    prompt=", ".join(self.glossary) or None), queue_timeout_s=budget)
                text = result.text.strip()
            except ProviderBusy:  # STT 자리가 나지 않았다(전체가 밀려 있다)
                self.dropped_chunks += 1
                await self._notify_overload()
            except ProviderError as exc:
                log.warning("room %s STT 실패: %s", self.room_id, exc)
                await self.emit(events.error(events.STT_FAILED, "음성 인식에 실패했습니다. 이 구간은 면접 뒤 보정본에서 채워집니다.", True))
                await self.emit_status(force=True)
        if text:
            if self._pending is None:
                seq = await self._next_seq()
                self._pending = PendingUtterance(seq, text, chunk.start_ms, chunk.end_ms, role, degraded, enqueued_at)
            else:  # 강제로 잘린 발화의 다음 조각: 같은 seq에 이어 붙인다
                self._pending.text = f"{self._pending.text} {text}"
                self._pending.end_ms = chunk.end_ms
                self._pending.degraded = self._pending.degraded or degraded
            if not degraded:
                await self.emit(events.transcript_partial(self._pending.seq, self._pending.text))
        if not chunk.forced:
            await self._flush_pending()
        await self.emit_status()

    # ---------- 확정 단계 ----------
    async def _final_worker(self) -> None:
        while True:
            pending = await self._final_queue.get()
            self._inflight += 1
            try:
                await self._finalize_utterance(pending)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("room %s 발화 확정 오류 seq=%s", self.room_id, pending.seq)
            finally:
                self._inflight -= 1
                self._final_queue.task_done()

    def recent(self, n: int = _SPEAKER_CONTEXT) -> list[RecentUtterance]:
        seqs = sorted(self.utterances)[-n:]
        return [RecentUtterance(s, self.utterances[s].speaker, self.utterances[s].refined_text) for s in seqs]

    async def _refine(self, pending: PendingUtterance) -> str:
        if pending.degraded or self._stt_queue.qsize() >= self.live.stt_queue_soft:
            return pending.text  # 밀려 있으면 다듬지 않는다
        try:
            return await asyncio.wait_for(
                self.deps.graphs.refine(pending.text, glossary=self.glossary, timeout_s=self.live.refine_timeout_s),
                timeout=self.live.refine_timeout_s + 0.5)
        except Exception as exc:  # noqa: BLE001 - 느리거나 실패하면 raw 그대로
            log.info("room %s 다듬기 생략 seq=%s: %r", self.room_id, pending.seq, exc)
            return pending.text

    async def _finalize_utterance(self, pending: PendingUtterance) -> None:
        match = await self.matcher.best(pending.text)
        ctx = SpeakerContext(text=pending.text, role_override=pending.role_override, match=match,
                             recent=self.recent(), duration_ms=pending.end_ms - pending.start_ms)
        refined, decision = await asyncio.gather(self._refine(pending), self.chain.decide(ctx))
        utt = Utterance(pending.seq, decision.speaker.value, decision.method, pending.text, refined,
                        pending.start_ms, pending.end_ms)
        self.utterances[utt.seq] = utt
        await self.emit(events.transcript_final(utt.seq, utt.speaker, utt.wire_method(self.rule_as), utt.raw_text,
                                                utt.refined_text, utt.start_ms, utt.end_ms))
        if utt.speaker == Speaker.INTERVIEWER.value:
            prepared = await self.matcher.best(pending.text, kinds=(KIND_PREPARED,))
            if prepared and prepared.similarity >= self.deps.settings.matching.threshold \
                    and prepared.candidate.question_id is not None:
                utt.matched_question_id = prepared.candidate.question_id
                self.asked_question_ids.add(utt.matched_question_id)
                await self.emit(events.question_matched(utt.seq, utt.matched_question_id, prepared.similarity))
        self.save(utt)
        self.followups.on_final(utt)
        await self.emit_status()

    def save(self, utt: Utterance) -> None:
        self.deps.outbox.save_utterances(self.room_id, UtterancesRequest(items=[utt.to_item(self.rule_as)]))

    # ---------- 클라이언트 명령 ----------
    async def handle_control(self, msg: dict[str, Any]) -> None:
        kind = msg.get("type")
        if kind == "role":
            role = msg.get("role")
            if role in events.CLIENT_ROLES:
                self.role_override = None if role == "AUTO" else role
        elif kind == "question.asked":
            await self._question_asked(msg.get("questionId"))
        elif kind == "followup.request":
            self.followups.request_now()
        elif kind == "utterance.fix":
            await self._fix_speaker(msg.get("seq"), msg.get("speaker"))

    async def _question_asked(self, question_id: Any) -> None:
        if not isinstance(question_id, int):
            return
        self.asked_question_ids.add(question_id)
        last_seq = max(self.utterances) if self.utterances else 0
        self.deps.outbox.post_event(self.room_id, BackendEvent(
            type="QUESTION_ASKED", message="면접관이 질문을 했다고 표시함", data={"questionId": question_id, "seq": last_seq}))
        if question_id not in {q.id for q in self.context.questions}:
            await self.refresh_context()  # 면접 중에 추가된 질문일 수 있다

    async def _fix_speaker(self, seq: Any, speaker: Any) -> None:
        utt = self.utterances.get(seq) if isinstance(seq, int) else None
        if utt is None or speaker not in (Speaker.INTERVIEWER.value, Speaker.CANDIDATE.value):
            return
        utt.speaker, utt.method = speaker, SpeakerMethod.MANUAL
        await self.emit(events.transcript_updated(utt.seq, utt.speaker, SpeakerMethod.MANUAL.value))
        self.save(utt)

    # ---------- 마무리 ----------
    async def flush(self, timeout_s: float) -> bool:
        """남은 음성을 끝까지 처리하고 저장을 내보낸다. 시간 안에 끝나면 True."""
        self.followups.cancel()

        async def drain() -> None:
            await self._enqueue(self.chunker.flush())
            await self._stt_queue.join()
            await self._flush_pending()
            await self._final_queue.join()

        try:
            await asyncio.wait_for(drain(), timeout=timeout_s)
        except asyncio.TimeoutError:
            log.warning("room %s 마무리 flush 시간 초과", self.room_id)
            return False
        return await self.deps.outbox.drain(timeout_s=min(5.0, timeout_s))
