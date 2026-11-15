"""꼬리질문 생성 시점 관리.

지원자 발화가 확정되고 `followup_debounce_s` 동안 새 말소리가 없으면 "답이 끝났다"고 보고 꼬리질문을 만든다.
면접관이 `followup.request`를 보내면 기다리지 않고 바로 만든다. 한 방에서 동시에 하나만 만든다.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from app.matching.matcher import KIND_FOLLOWUP, MatchCandidate
from app.schemas import ws as events
from app.schemas.backend import FollowUpItem, FollowUpsRequest
from app.schemas.common import Speaker

if TYPE_CHECKING:
    from app.live.deps import Utterance
    from app.live.session import RoomSession

log = logging.getLogger(__name__)


class FollowUpScheduler:
    def __init__(self, session: "RoomSession") -> None:
        self.s = session
        self._timer: asyncio.Task | None = None
        self._running: asyncio.Task | None = None
        self.suggested: list[str] = []
        self.matched: set[str] = set()  # 면접관이 말로 물은 꼬리질문의 id(followup.matched를 한 번만 내기 위해)
        self._counts: dict[int, int] = {}

    def cancel(self) -> None:
        for task in (self._timer, self._running):
            if task is not None and not task.done():
                task.cancel()
        self._timer = self._running = None

    def speech_started(self) -> None:
        """새 말소리가 들리면 대기 중인 타이머를 물린다(답이 이어지는 중일 수 있다)."""
        if self._timer is not None and not self._timer.done():
            self._timer.cancel()
            self._timer = None

    def on_final(self, utt: "Utterance") -> None:
        self.speech_started()
        if utt.speaker == Speaker.CANDIDATE.value:
            self._timer = asyncio.create_task(self._debounced(utt.seq))

    def request_now(self) -> None:
        seqs = sorted(self.s.utterances)
        if not seqs:
            return
        answers = [q for q in seqs if self.s.utterances[q].speaker == Speaker.CANDIDATE.value]
        self._start(answers[-1] if answers else seqs[-1], explicit=True)

    async def _debounced(self, seq: int) -> None:
        await asyncio.sleep(self.s.live.followup_debounce_s)
        if self.s.chunker.in_speech or self.s.queue_depth > 0:  # 타이머 자신은 작업자 밖에서 돈다
            return  # 말이 이어지고 있다: 다음 확정 때 다시 잡힌다
        self._start(seq, explicit=False)

    def _start(self, seq: int, *, explicit: bool) -> None:
        if self._running is not None and not self._running.done():
            if not explicit:
                return
            self._running.cancel()
        self._running = asyncio.create_task(self._generate(seq, explicit))

    def _answer_chars(self, seq: int) -> int:
        """seq에서 거슬러 올라가며 이어진 지원자 발화의 글자 수."""
        total = 0
        for s in sorted((q for q in self.s.utterances if q <= seq), reverse=True):
            if self.s.utterances[s].speaker != Speaker.CANDIDATE.value:
                break
            total += len(self.s.utterances[s].refined_text)
        return total

    async def _generate(self, seq: int, explicit: bool) -> None:
        s = self.s
        if not explicit and self._answer_chars(seq) < s.live.followup_min_chars:
            return
        seqs = sorted(q for q in s.utterances if q <= seq)[-s.live.followup_context_utterances:]
        qa = [{"seq": q, "speaker": s.utterances[q].speaker, "text": s.utterances[q].refined_text} for q in seqs]
        asked = [q.text for q in s.context.questions if q.id in s.asked_question_ids]
        asked += [u.refined_text for u in s.utterances.values() if u.speaker == Speaker.INTERVIEWER.value][-20:]
        try:
            items = await s.deps.graphs.followup(qa=qa, candidate_brief=s.context.candidate_brief, asked=asked,
                                                 suggested=self.suggested[-12:],
                                                 job_title=s.context.job_title, timeout_s=s.live.followup_timeout_s)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 꼬리질문 실패는 전사에 영향을 주지 않는다
            log.warning("room %s 꼬리질문 생성 실패: %r", s.room_id, exc)
            await s.emit(events.error(events.LLM_FAILED, "꼬리질문을 만들지 못했습니다.", True))
            return
        if not items:
            return
        first = self._counts.get(seq, 0) + 1  # 같은 seq로 다시 만들어도 id가 겹치지 않게 번호를 잇는다
        self._counts[seq] = first + len(items) - 1
        out = [{"id": f"f-{seq}-{i}", "text": it.text, "rationale": it.rationale} for i, it in enumerate(items, first)]
        self.suggested += [o["text"] for o in out]
        s.matcher.add([MatchCandidate(KIND_FOLLOWUP, o["text"], ref=o["id"]) for o in out])
        await s.emit(events.followup_suggested(seq, out))
        s.deps.outbox.save_followups(s.room_id, FollowUpsRequest(
            based_on_seq=seq, items=[FollowUpItem(client_id=o["id"], text=o["text"], rationale=o["rationale"]) for o in out]))
