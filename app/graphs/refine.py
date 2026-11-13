"""다듬기 그래프: 전사 한 발화를 읽기 좋게 고친다. 뜻이 바뀐 것 같으면 원문을 그대로 쓴다."""

from __future__ import annotations

from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from app.prompts import live as prompts
from app.providers.base import LLMProvider

# 다듬은 글이 원문보다 지나치게 길거나 짧으면 뜻이 바뀐 것으로 보고 버린다
MAX_RATIO = 1.3
MIN_RATIO = 0.4


class RefineState(TypedDict, total=False):
    raw: str
    glossary: list[str]
    timeout_s: float | None
    candidate: str
    refined: str
    changed: bool


def build_refine_graph(llm: LLMProvider):
    async def refine(state: RefineState) -> dict:
        text = await llm.complete(prompts.build_refine(utterance=state["raw"], glossary=state.get("glossary")),
                                  timeout_s=state.get("timeout_s"), max_retries=0)
        return {"candidate": text.strip().strip('"').strip()}

    async def guard(state: RefineState) -> dict:
        raw, cand = state["raw"].strip(), state.get("candidate", "")
        if not cand or "\n" in cand.strip():
            return {"refined": raw, "changed": False}
        ratio = len(cand) / max(1, len(raw))
        if ratio > MAX_RATIO or (ratio < MIN_RATIO and len(raw) > 10):
            return {"refined": raw, "changed": False}
        return {"refined": cand, "changed": cand != raw}

    g = StateGraph(RefineState)
    g.add_node("refine", refine)
    g.add_node("guard", guard)
    g.add_edge(START, "refine")
    g.add_edge("refine", "guard")
    g.add_edge("guard", END)
    return g.compile()
