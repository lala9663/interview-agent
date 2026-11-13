"""그래프를 한 번만 컴파일해 두고 부르는 창구. API와 방 파이프라인은 이것만 쓴다."""

from __future__ import annotations

from typing import Any

from app.blind.filter import BlindFilter
from app.graphs.briefing import build_briefing_graph
from app.graphs.evaluation import build_evaluation_graph
from app.graphs.followup import FollowUpCandidate, build_followup_graph
from app.graphs.refine import build_refine_graph
from app.graphs.report import build_report_graph
from app.providers.registry import ProviderSet
from app.schemas.internal import (
    BriefingRequest,
    BriefingResponse,
    EvaluationRequest,
    EvaluationResponse,
    ReportRequest,
    ReportResponse,
)


class GraphRunner:
    def __init__(self, providers: ProviderSet, blind: BlindFilter, *, dedupe_threshold: float = 0.7) -> None:
        self.providers = providers
        # 면접 전후(품질 우선)는 llm.quality, 면접 중(속도 우선)은 llm.fast
        self._briefing = build_briefing_graph(providers.llm_quality, blind)
        self._evaluation = build_evaluation_graph(providers.llm_quality, blind)
        self._report = build_report_graph(providers.llm_quality, blind)
        self._refine = build_refine_graph(providers.llm_fast)
        self._followup = build_followup_graph(providers.llm_fast, blind, dedupe_threshold)

    async def briefing(self, request: BriefingRequest) -> BriefingResponse:
        return (await self._briefing.ainvoke({"request": request}))["response"]

    async def evaluation(self, request: EvaluationRequest) -> EvaluationResponse:
        return (await self._evaluation.ainvoke({"request": request}))["response"]

    async def report(self, request: ReportRequest) -> ReportResponse:
        return (await self._report.ainvoke({"request": request}))["response"]

    async def refine(self, raw: str, *, glossary: list[str] | None = None, timeout_s: float | None = None) -> str:
        out = await self._refine.ainvoke({"raw": raw, "glossary": glossary or [], "timeout_s": timeout_s})
        return out["refined"]

    async def followup(self, *, qa: list[dict[str, Any]], candidate_brief: str, asked: list[str],
                       suggested: list[str] | None = None, job_title: str = "",
                       timeout_s: float | None = None) -> list[FollowUpCandidate]:
        out = await self._followup.ainvoke({"qa": qa, "candidate_brief": candidate_brief, "asked": asked,
                                            "suggested": suggested or [],
                                            "job_title": job_title, "timeout_s": timeout_s})
        return out["items"]
