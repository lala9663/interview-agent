"""브리핑 그래프: 블라인드 필터 → 요약 → JD 질문 → 이력서 질문 → 금지 항목 검사 → (걸리면 1회 재생성) → 마무리."""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import Field

from app.blind.filter import BlindFilter
from app.graphs.structured import call_structured
from app.matching.tfidf import pair_similarity
from app.prompts import briefing as prompts
from app.providers.base import LLMProvider
from app.schemas.common import CamelModel
from app.schemas.internal import BriefingRequest, BriefingResponse, BriefingSummary, GeneratedQuestion

MAX_REGENERATIONS = 1


class _Questions(CamelModel):
    questions: list[GeneratedQuestion] = Field(default_factory=list)


class BriefingState(TypedDict, total=False):
    request: BriefingRequest
    profile: dict[str, Any]
    excluded: list[str]
    summary: BriefingSummary
    jd_questions: list[GeneratedQuestion]
    resume_questions: list[GeneratedQuestion]
    violations: list[str]
    attempt: int
    response: BriefingResponse


def build_briefing_graph(llm: LLMProvider, blind: BlindFilter):
    async def blind_filter(state: BriefingState) -> dict:
        profile, excluded = blind.filter_profile(state["request"].candidate.profile)
        return {"profile": profile, "excluded": excluded, "attempt": 0, "violations": []}

    def _common(state: BriefingState) -> dict:
        req = state["request"]
        return {"track": req.track, "interview_type": req.interview_type, "job_title": req.job_title}

    async def summarize(state: BriefingState) -> dict:
        req = state["request"]
        summary = await call_structured(llm, prompts.build_summary(
            **_common(state), jd_text=req.jd_text, profile=state["profile"],
            violations=state.get("violations")), BriefingSummary)
        return {"summary": summary}

    async def jd_questions(state: BriefingState) -> dict:
        req = state["request"]
        if req.question_count.jd <= 0:
            return {"jd_questions": []}
        out = await call_structured(llm, prompts.build_jd_questions(
            **_common(state), jd_text=req.jd_text, question_bank=[q.to_wire() for q in req.question_bank],
            count=req.question_count.jd, violations=state.get("violations")), _Questions)
        for q in out.questions:
            if q.source not in ("JD", "BANK"):
                q.source = "JD"
        return {"jd_questions": out.questions}

    async def resume_questions(state: BriefingState) -> dict:
        req = state["request"]
        if req.question_count.resume <= 0:
            return {"resume_questions": []}
        out = await call_structured(llm, prompts.build_resume_questions(
            **_common(state), profile=state["profile"], summary=state["summary"].to_wire(),
            count=req.question_count.resume, violations=state.get("violations")), _Questions)
        for q in out.questions:
            q.source = "RESUME"
        return {"resume_questions": out.questions}

    async def blind_check(state: BriefingState) -> dict:
        found = blind.check_obj(state["summary"].to_wire())
        for q in state["jd_questions"] + state["resume_questions"]:
            found += blind.check_obj([q.text, q.intent, q.category])
        return {"violations": list(dict.fromkeys(f"{v.label}({v.matched})" for v in found)),
                "attempt": state.get("attempt", 0) + 1}

    def route(state: BriefingState) -> str:
        if state["violations"] and state["attempt"] <= MAX_REGENERATIONS:
            return "summarize"  # 금지 항목을 알려 주고 다시 만든다
        return "finalize"

    async def finalize(state: BriefingState) -> dict:
        req = state["request"]
        # 재생성 뒤에도 남은 금지 항목은 지운다: 요약은 문장을 가리고, 질문은 통째로 뺀다
        summary_dict, _ = blind.scrub(state["summary"].to_wire())
        questions: list[GeneratedQuestion] = []
        for q in state["jd_questions"][:req.question_count.jd] + state["resume_questions"][:req.question_count.resume]:
            if blind.check_obj([q.text, q.intent, q.category]) or not q.text.strip():
                continue
            if any(pair_similarity(q.text, kept.text) >= 0.9 for kept in questions):
                continue
            questions.append(q)
        return {"response": BriefingResponse(summary=BriefingSummary.model_validate(summary_dict),
                                             questions=questions, excluded=state["excluded"], model=llm.model)}

    g = StateGraph(BriefingState)
    g.add_node("blind_filter", blind_filter)
    g.add_node("summarize", summarize)
    g.add_node("jd_questions", jd_questions)
    g.add_node("resume_questions", resume_questions)
    g.add_node("blind_check", blind_check)
    g.add_node("finalize", finalize)
    g.add_edge(START, "blind_filter")
    g.add_edge("blind_filter", "summarize")
    g.add_edge("summarize", "jd_questions")
    g.add_edge("jd_questions", "resume_questions")
    g.add_edge("resume_questions", "blind_check")
    g.add_conditional_edges("blind_check", route, {"summarize": "summarize", "finalize": "finalize"})
    g.add_edge("finalize", END)
    return g.compile()
