"""보고서 그래프.

BRIEF(신입): LLM 한 번으로 총평·강점·우려·문답만. 빨리 끝나야 한다(1~2분 안).
DETAILED(경력): 총평 → 역량별 분석 → 이력 검증 → 다음 차수 확인점을 단계별로(오래 걸려도 된다).
"""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import Field

from app.blind.filter import BlindFilter
from app.graphs.structured import call_structured
from app.prompts import report as prompts
from app.providers.base import LLMProvider
from app.schemas.common import CamelModel
from app.schemas.internal import (
    CareerCheck,
    Competency,
    QaHighlight,
    ReportContent,
    ReportRequest,
    ReportResponse,
)


class _Overview(CamelModel):
    headline: str = ""
    overall_comment: str = ""
    strengths: list[str] = Field(default_factory=list)
    concerns: list[str] = Field(default_factory=list)
    qa_highlights: list[QaHighlight] = Field(default_factory=list)


class _Competencies(CamelModel):
    competencies: list[Competency] = Field(default_factory=list)


class _Career(CamelModel):
    career_checks: list[CareerCheck] = Field(default_factory=list)


class _Next(CamelModel):
    next_round_checks: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)


class ReportState(TypedDict, total=False):
    request: ReportRequest
    ctx: dict[str, Any]
    overview: _Overview
    competencies: list[Competency]
    career_checks: list[CareerCheck]
    next_round_checks: list[str]
    risks: list[str]
    response: ReportResponse


def build_report_graph(llm: LLMProvider, blind: BlindFilter):
    async def prepare(state: ReportState) -> dict:
        req = state["request"]
        return {"ctx": {
            "track": req.track, "interview_type": req.interview_type, "basis": req.basis,
            "job_title": req.job_title, "jd_text": req.jd_text, "briefing_summary": req.briefing_summary,
            "ai_evaluation": req.ai_evaluation.to_wire() if req.ai_evaluation else None,
            "previous_rounds": [r.to_wire() for r in req.previous_rounds],
            "transcript": [t.to_wire() for t in req.transcript],
        }}

    def route(state: ReportState) -> str:
        return "overview" if state["request"].detail == "DETAILED" else "brief"

    async def brief(state: ReportState) -> dict:
        return {"overview": await call_structured(llm, prompts.build_brief(state["ctx"]), _Overview)}

    async def overview(state: ReportState) -> dict:
        return {"overview": await call_structured(llm, prompts.build_overview(state["ctx"]), _Overview)}

    async def competencies(state: ReportState) -> dict:
        out = await call_structured(llm, prompts.build_competencies(state["ctx"]), _Competencies)
        return {"competencies": out.competencies}

    async def career(state: ReportState) -> dict:
        out = await call_structured(llm, prompts.build_career(state["ctx"]), _Career)
        return {"career_checks": out.career_checks}

    async def next_round(state: ReportState) -> dict:
        out = await call_structured(llm, prompts.build_next(state["ctx"], state["overview"].to_wire()), _Next)
        return {"next_round_checks": out.next_round_checks, "risks": out.risks}

    async def finalize(state: ReportState) -> dict:
        req, ov = state["request"], state["overview"]
        seqs = {t.seq for t in req.transcript}
        highlights = []
        for h in ov.qa_highlights:
            h.seqs = [s for s in h.seqs if s in seqs]  # 전사에 없는 번호는 지운다
            if h.question.strip() or h.answer_summary.strip():
                highlights.append(h)
        detailed = req.detail == "DETAILED"
        content = ReportContent(
            headline=ov.headline, overall_comment=ov.overall_comment, strengths=ov.strengths, concerns=ov.concerns,
            qa_highlights=highlights,
            competencies=state.get("competencies", []) if detailed else [],
            career_checks=state.get("career_checks", []) if detailed else [],
            next_round_checks=state.get("next_round_checks", []) if detailed else [],
            risks=state.get("risks", []) if detailed else [],
        )
        clean, _ = blind.scrub(content.to_wire())  # 금지 항목이 있는 문장은 가린다
        return {"response": ReportResponse(content=ReportContent.model_validate(clean), model=llm.model)}

    g = StateGraph(ReportState)
    for name, fn in [("prepare", prepare), ("brief", brief), ("overview", overview), ("competencies", competencies),
                     ("career", career), ("next_round", next_round), ("finalize", finalize)]:
        g.add_node(name, fn)
    g.add_edge(START, "prepare")
    g.add_conditional_edges("prepare", route, {"brief": "brief", "overview": "overview"})
    g.add_edge("brief", "finalize")
    g.add_edge("overview", "competencies")
    g.add_edge("competencies", "career")
    g.add_edge("career", "next_round")
    g.add_edge("next_round", "finalize")
    g.add_edge("finalize", END)
    return g.compile()
