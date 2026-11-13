"""평가 그래프: 기준별 점수 + 근거 발화 seq. 근거가 없는 항목은 점수를 내지 않는다(응답에서 뺀다)."""

from __future__ import annotations

from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import Field

from app.blind.filter import BlindFilter
from app.graphs.structured import call_structured
from app.prompts import evaluation as prompts
from app.providers.base import LLMProvider
from app.schemas.common import CamelModel
from app.schemas.internal import CriterionScore, EvaluationRequest, EvaluationResponse


class _RawScore(CamelModel):
    criterion_code: str = ""
    score: int | None = None
    rationale: str = ""
    evidence_seqs: list[int] = Field(default_factory=list)


class _RawEvaluation(CamelModel):
    scores: list[_RawScore] = Field(default_factory=list)
    overall_comment: str = ""


class EvaluationState(TypedDict, total=False):
    request: EvaluationRequest
    raw: _RawEvaluation
    response: EvaluationResponse


def build_evaluation_graph(llm: LLMProvider, blind: BlindFilter):
    async def score(state: EvaluationState) -> dict:
        req = state["request"]
        if not req.criteria or not req.transcript:
            return {"raw": _RawEvaluation()}
        raw = await call_structured(llm, prompts.build(
            track=req.track, interview_type=req.interview_type, job_title=req.job_title, jd_text=req.jd_text,
            criteria=[c.to_wire() for c in req.criteria], transcript=[t.to_wire() for t in req.transcript],
            briefing_summary=req.briefing_summary), _RawEvaluation)
        return {"raw": raw}

    async def validate(state: EvaluationState) -> dict:
        req, raw = state["request"], state["raw"]
        # 근거로 쓸 수 있는 것은 면접관이 아닌 발화뿐이다
        valid_seqs = {t.seq for t in req.transcript if t.speaker != "INTERVIEWER" and t.text.strip()}
        criteria = {c.code: c for c in req.criteria}
        scores: list[CriterionScore] = []
        seen: set[str] = set()
        for item in raw.scores:
            crit = criteria.get(item.criterion_code)
            if crit is None or crit.code in seen:
                continue
            evidence = sorted({s for s in item.evidence_seqs if s in valid_seqs})
            if item.score is None or not evidence:
                continue  # 근거 없음 → 점수 없음
            seen.add(crit.code)
            rationale, _ = blind.mask_sentences(item.rationale)
            scores.append(CriterionScore(criterion_code=crit.code, score=max(1, min(crit.max_score, item.score)),
                                         rationale=rationale, evidence_seqs=evidence))
        comment, _ = blind.mask_sentences(raw.overall_comment)
        skipped = [c.name or c.code for c in req.criteria if c.code not in seen]
        if skipped:
            comment = (comment + " " if comment else "") + "근거가 부족해 점수를 내지 않은 항목: " + ", ".join(skipped) + "."
        return {"response": EvaluationResponse(scores=scores, overall_comment=comment, model=llm.model)}

    g = StateGraph(EvaluationState)
    g.add_node("score", score)
    g.add_node("validate", validate)
    g.add_edge(START, "score")
    g.add_edge("score", "validate")
    g.add_edge("validate", END)
    return g.compile()
