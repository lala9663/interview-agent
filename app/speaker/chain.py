"""화자 판정 체인: ① MANUAL → ② CHANNEL → ③ QUESTION_MATCH → ④ 규칙 → ⑤ LLM.

순서와 구성은 `SPEAKER_CHAIN`(쉼표로 구분)으로 바꾼다. 아무도 답하지 못하면 UNKNOWN / NONE.
"""

from __future__ import annotations

import logging
from typing import Callable

from app.graphs.structured import call_structured
from app.prompts import live as prompts
from app.providers.base import LLMProvider
from app.schemas.common import CamelModel, Speaker, SpeakerMethod
from app.settings import SpeakerSettings
from app.speaker.base import SpeakerContext, SpeakerDecision, SpeakerJudge
from app.speaker.rules import RuleJudge

log = logging.getLogger(__name__)


class ManualJudge(SpeakerJudge):
    """① 면접관이 화면에서 지정한 역할(role 메시지). AUTO면 넘어간다."""

    name = "manual"

    async def judge(self, ctx: SpeakerContext) -> SpeakerDecision | None:
        if ctx.role_override in (Speaker.INTERVIEWER.value, Speaker.CANDIDATE.value):
            return SpeakerDecision(Speaker(ctx.role_override), SpeakerMethod.MANUAL, 1.0, "role 메시지")
        return None


class ChannelJudge(SpeakerJudge):
    """② 마이크·트랙이 사람별로 나뉘어 올 때. 지금은 자리만 있다(ctx.channel_speaker가 항상 None).

    경력 화상(Knox Teams)에서 참가자별 트랙을 받을 수 있게 되거나, 대면에서 마이크를 2개 쓰게 되면
    오디오 수신부가 `channel_speaker`를 채우면 된다.
    """

    name = "channel"

    async def judge(self, ctx: SpeakerContext) -> SpeakerDecision | None:
        if ctx.channel_speaker in (Speaker.INTERVIEWER.value, Speaker.CANDIDATE.value):
            return SpeakerDecision(Speaker(ctx.channel_speaker), SpeakerMethod.CHANNEL, 1.0, "채널")
        return None


class QuestionMatchJudge(SpeakerJudge):
    """③ 준비된 질문·질문 은행·방금 제안한 꼬리질문과 비슷하면 면접관의 말이다."""

    name = "question_match"

    def __init__(self, threshold: float = 0.55) -> None:
        self.threshold = threshold

    async def judge(self, ctx: SpeakerContext) -> SpeakerDecision | None:
        if ctx.match is not None and ctx.match.similarity >= self.threshold:
            return SpeakerDecision(Speaker.INTERVIEWER, SpeakerMethod.QUESTION_MATCH, ctx.match.similarity,
                                   f"{ctx.match.candidate.kind} 질문과 유사도 {ctx.match.similarity:.2f}")
        return None


class _LlmVerdict(CamelModel):
    speaker: str = "UNKNOWN"
    confidence: float = 0.0


class LlmJudge(SpeakerJudge):
    """⑤ 최근 대화와 함께 LLM에 묻는다. 확신이 낮으면 UNKNOWN. 호출이 실패하면 판정하지 않는다."""

    name = "llm"

    def __init__(self, llm: LLMProvider, *, min_confidence: float = 0.6, timeout_s: float = 3.0,
                 context_utterances: int = 6) -> None:
        self.llm = llm
        self.min_confidence = min_confidence
        self.timeout_s = timeout_s
        self.context_utterances = context_utterances

    async def judge(self, ctx: SpeakerContext) -> SpeakerDecision | None:
        recent = [{"seq": r.seq, "speaker": r.speaker, "text": r.text} for r in ctx.recent[-self.context_utterances:]]
        try:
            verdict = await call_structured(self.llm, prompts.build_speaker(utterance=ctx.text, recent=recent),
                                            _LlmVerdict, retries=0, timeout_s=self.timeout_s)
        except Exception as exc:  # noqa: BLE001 - 화자 판정 실패가 전사를 막지 않게
            log.warning("LLM 화자 판정 실패: %r", exc)
            return None
        if verdict.speaker in (Speaker.INTERVIEWER.value, Speaker.CANDIDATE.value) \
                and verdict.confidence >= self.min_confidence:
            return SpeakerDecision(Speaker(verdict.speaker), SpeakerMethod.LLM, verdict.confidence, "LLM 문맥 판정")
        return SpeakerDecision(Speaker.UNKNOWN, SpeakerMethod.LLM, verdict.confidence, "LLM 확신 낮음")


class SpeakerChain:
    def __init__(self, judges: list[SpeakerJudge]) -> None:
        self.judges = judges

    async def decide(self, ctx: SpeakerContext) -> SpeakerDecision:
        for judge in self.judges:
            decision = await judge.judge(ctx)
            if decision is not None:
                return decision
        return SpeakerDecision(Speaker.UNKNOWN, SpeakerMethod.NONE, 0.0, "판정 불가")


def build_speaker_chain(settings: SpeakerSettings, llm: LLMProvider | None = None, *,
                        llm_timeout_s: float = 3.0) -> SpeakerChain:
    factories: dict[str, Callable[[], SpeakerJudge | None]] = {
        "manual": ManualJudge,
        "channel": ChannelJudge,
        "question_match": lambda: QuestionMatchJudge(settings.question_match_threshold),
        "rule": lambda: RuleJudge(settings.rule_min_confidence),
        "llm": lambda: LlmJudge(llm, min_confidence=settings.llm_min_confidence,
                                timeout_s=llm_timeout_s) if llm is not None else None,
    }
    judges: list[SpeakerJudge] = []
    for name in [n.strip() for n in settings.chain.split(",") if n.strip()]:
        if name not in factories:
            raise ValueError(f"알 수 없는 화자 판정기 '{name}'. 등록된 것: {sorted(factories)}")
        judge = factories[name]()
        if judge is not None:
            judges.append(judge)
    return SpeakerChain(judges)
