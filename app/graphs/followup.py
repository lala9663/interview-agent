"""꼬리질문 그래프: 최근 문답 + 지원자 요약 + 이미 한 질문 → 후보 2~3개 → 금지 항목 검사·중복 제거."""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import Field

from app.blind.filter import BlindFilter
from app.graphs.structured import call_structured
from app.matching.tfidf import pair_similarity
from app.prompts import live as prompts
from app.providers.base import LLMProvider
from app.schemas.common import CamelModel

MAX_ITEMS = 3
SUGGESTED_DEDUPE = 0.9  # 이미 제안한 후보와는 거의 같은 문장일 때만 중복으로 본다


class FollowUpCandidate(CamelModel):
    text: str
    rationale: str = ""


class _FollowUps(CamelModel):
    items: list[FollowUpCandidate] = Field(default_factory=list)


class FollowUpState(TypedDict, total=False):
    qa: list[dict[str, Any]]  # [{seq, speaker, text}]
    candidate_brief: str
    asked: list[str]  # 면접관이 실제로 한 질문
    suggested: list[str]  # 이 방에서 이미 제안한 꼬리질문
    job_title: str
    timeout_s: float | None
    generated: list[FollowUpCandidate]
    items: list[FollowUpCandidate]


def build_followup_graph(llm: LLMProvider, blind: BlindFilter, dedupe_threshold: float = 0.7):
    async def generate(state: FollowUpState) -> dict:
        out = await call_structured(llm, prompts.build_followup(
            qa=state["qa"], candidate_brief=state.get("candidate_brief", ""),
            asked=state.get("asked", []) + state.get("suggested", []),
            job_title=state.get("job_title", "")), _FollowUps, timeout_s=state.get("timeout_s"))
        return {"generated": out.items}

    async def screen(state: FollowUpState) -> dict:
        asked = state.get("asked", [])
        kept: list[FollowUpCandidate] = []
        for item in state.get("generated", []):
            text = item.text.strip()
            if not text or blind.check_obj([text, item.rationale]):
                continue  # 금지 항목이 있는 후보는 내보내지 않는다
            if any(pair_similarity(text, q) >= dedupe_threshold for q in asked):
                continue  # 이미 한 질문
            if any(pair_similarity(text, q) >= SUGGESTED_DEDUPE for q in state.get("suggested", [])):
                continue  # 이미 제안한 후보
            if any(pair_similarity(text, k.text) >= dedupe_threshold for k in kept):
                continue  # 후보끼리 중복
            kept.append(FollowUpCandidate(text=text, rationale=item.rationale.strip()))
        return {"items": kept[:MAX_ITEMS]}

    g = StateGraph(FollowUpState)
    g.add_node("generate", generate)
    g.add_node("screen", screen)
    g.add_edge(START, "generate")
    g.add_edge("generate", "screen")
    g.add_edge("screen", END)
    return g.compile()
