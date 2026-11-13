"""사후 STT: 방이 끝난 뒤 모아 둔 방 전체 음성을 다시 전사해 보정 전사를 백엔드에 올린다(계약 4.4, 5장).

실시간 전사와 다른 점: 밀려서 버린 청크가 없고, 청크를 더 길게(최대 28초) 잡아 문맥이 넓고, `stt.post` 역할의
모델·동시 실행 수를 따로 쓴다. 끝나면 `PUT refined-transcript`를 부르고 음성을 놓아 준다
(MEMORY_ONLY면 삭제, KEEP이면 파일로 보관).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, replace

from app.audio.chunker import AudioChunk, SpeechChunker
from app.live.deps import LiveDeps, Utterance
from app.live.session import RoomSession
from app.matching.matcher import KIND_PREPARED
from app.providers.base import SttRequest
from app.schemas.backend import BackendEvent, RefinedTranscriptRequest
from app.schemas.common import Speaker, SpeakerMethod
from app.speaker.base import RecentUtterance, SpeakerContext

log = logging.getLogger(__name__)
_REFINE_CONCURRENCY = 2  # 진행 중인 다른 방의 llm.fast 몫을 뺏지 않게 낮게 둔다


@dataclass
class _Segment:
    start_ms: int
    end_ms: int
    chunks: list[AudioChunk]
    text: str = ""


def segment_audio(deps: LiveDeps, pcm: bytes) -> list[_Segment]:
    """방 전체 음성을 발화 단위로 자른다(강제 분할 청크는 한 발화로 묶는다). CPU 작업이라 스레드에서 돌린다."""
    chunker = SpeechChunker.from_settings(deps.settings.audio, post=True)
    segments: list[_Segment] = []
    for chunk in chunker.feed(pcm) + chunker.flush():
        if chunk.continuation and segments:
            if chunk.pcm:
                segments[-1].chunks.append(chunk)
                segments[-1].end_ms = chunk.end_ms
        elif chunk.pcm:
            segments.append(_Segment(chunk.start_ms, chunk.end_ms, [chunk]))
    return segments


def _manual_speaker(live: list[Utterance], start_ms: int, end_ms: int) -> str | None:
    """실시간 전사에서 사람이 화자를 지정한 발화와 절반 넘게 겹치면 그 화자를 물려받는다."""
    best, best_overlap = None, 0
    for u in live:
        overlap = min(end_ms, u.end_ms) - max(start_ms, u.start_ms)
        if overlap > best_overlap:
            best, best_overlap = u, overlap
    if best is not None and best.method == SpeakerMethod.MANUAL and best_overlap * 2 >= (end_ms - start_ms):
        return best.speaker
    return None


async def build_refined_transcript(session: RoomSession) -> RefinedTranscriptRequest | None:
    deps, room_id = session.deps, session.room_id
    audio = await deps.audio_store.read_all(room_id)
    if not audio.pcm:
        return None
    segments = await asyncio.to_thread(segment_audio, deps, audio.pcm)
    stt, session_key = deps.providers.stt_post, f"post:{room_id}"
    prompt = ", ".join(session.glossary) or None
    rate = deps.settings.audio.sample_rate

    async def transcribe(seg: _Segment) -> None:
        texts = []
        for chunk in seg.chunks:
            result = await stt.transcribe(SttRequest(pcm=chunk.pcm, sample_rate=rate, session=session_key, prompt=prompt))
            texts.append(result.text.strip())
        seg.text = " ".join(t for t in texts if t)

    try:
        await asyncio.gather(*(transcribe(seg) for seg in segments))
    finally:
        stt.reset_session(session_key)
    segments = [s for s in segments if s.text]

    refined_texts = [s.text for s in segments]
    if deps.settings.live.post_stt_refine:
        sem = asyncio.Semaphore(_REFINE_CONCURRENCY)

        async def refine(i: int) -> None:
            async with sem:
                try:
                    refined_texts[i] = await deps.graphs.refine(segments[i].text, glossary=session.glossary)
                except Exception as exc:  # noqa: BLE001 - 다듬기 실패는 원문으로
                    log.info("room %s 사후 다듬기 생략: %r", room_id, exc)

        await asyncio.gather(*(refine(i) for i in range(len(segments))))

    live = sorted(session.utterances.values(), key=lambda u: u.seq)
    rule_as = session.rule_as
    # 메모리 상한 때문에 앞부분 음성을 버렸다면, 그 구간은 실시간 전사를 그대로 쓴다
    items: list[Utterance] = [replace(u) for u in live if audio.offset_ms and u.end_ms <= audio.offset_ms]
    recent = [RecentUtterance(u.seq, u.speaker, u.refined_text) for u in items][-8:]
    for seg, refined in zip(segments, refined_texts):
        start, end = seg.start_ms + audio.offset_ms, seg.end_ms + audio.offset_ms
        match = await session.matcher.best(seg.text)
        decision = await session.chain.decide(SpeakerContext(
            text=seg.text, role_override=_manual_speaker(live, start, end), match=match, recent=recent[-8:],
            duration_ms=end - start))
        utt = Utterance(len(items) + 1, decision.speaker.value, decision.method, seg.text, refined, start, end)
        if utt.speaker == Speaker.INTERVIEWER.value:
            prepared = await session.matcher.best(seg.text, kinds=(KIND_PREPARED,))
            if prepared and prepared.similarity >= deps.settings.matching.threshold:
                utt.matched_question_id = prepared.candidate.question_id
        items.append(utt)
        recent.append(RecentUtterance(utt.seq, utt.speaker, utt.refined_text))
    for i, utt in enumerate(items, 1):
        utt.seq = i  # 보정 전사의 seq는 1부터 다시 매긴다
    return RefinedTranscriptRequest(stt_model=stt.model, items=[u.to_item(rule_as) for u in items])


async def run_post_stt(session: RoomSession) -> bool:
    """사후 STT를 돌리고 콜백을 보낸다. 성공하면 True. 어떤 경우에도 끝나면 음성을 놓아 준다."""
    deps, room_id = session.deps, session.room_id
    error = "모아 둔 음성이 없습니다."
    try:
        for attempt in range(1, deps.settings.live.post_stt_max_attempts + 1):
            try:
                body = await build_refined_transcript(session)
                if body is None:
                    break
                deps.outbox.put_refined_transcript(room_id, body)
                log.info("room %s 사후 STT 완료: 발화 %d건", room_id, len(body.items))
                return True
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                error = repr(exc)
                log.warning("room %s 사후 STT 실패 (%d번째): %r", room_id, attempt, exc)
                await asyncio.sleep(min(10.0, 2.0 * attempt))
        deps.outbox.post_event(room_id, BackendEvent(type="POST_STT_FAILED", message="사후 STT에 실패했습니다.",
                                                     data={"error": error[:300]}))
        return False
    finally:
        await deps.audio_store.release(room_id)
