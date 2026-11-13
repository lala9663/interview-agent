"""백엔드 → Agent 내부 REST (계약 4장). 모두 동기 호출이고 상태를 남기지 않는다(finalize만 방 상태를 건드린다)."""

from __future__ import annotations

import hmac

from fastapi import APIRouter, Depends, Request

from app.api.errors import ApiError
from app.container import Container
from app.schemas.internal import (
    BlindCheckItem,
    BlindCheckRequest,
    BlindCheckResponse,
    BlindViolationOut,
    BriefingRequest,
    BriefingResponse,
    EvaluationRequest,
    EvaluationResponse,
    FinalizeRequest,
    FinalizeResponse,
    HealthResponse,
    ReportRequest,
    ReportResponse,
)

health_router = APIRouter()


def get_container(request: Request) -> Container:
    return request.app.state.container


def require_internal_key(request: Request) -> None:
    expected = get_container(request).settings.security.internal_api_key
    given = request.headers.get("X-Internal-Key", "")
    if not hmac.compare_digest(given.encode(), expected.encode()):
        raise ApiError(401, "UNAUTHORIZED", "X-Internal-Key가 없거나 올바르지 않습니다.")


router = APIRouter(prefix="/internal/v1", dependencies=[Depends(require_internal_key)])


@health_router.get("/health", response_model=HealthResponse)
async def health(c: Container = Depends(get_container)) -> HealthResponse:
    return HealthResponse(status="UP", llm=c.providers.llm_quality.impl, stt=c.providers.stt_live.impl)


@router.post("/briefings", response_model=BriefingResponse)
async def briefings(body: BriefingRequest, c: Container = Depends(get_container)) -> BriefingResponse:
    return await c.graphs.briefing(body)


@router.post("/evaluations", response_model=EvaluationResponse)
async def evaluations(body: EvaluationRequest, c: Container = Depends(get_container)) -> EvaluationResponse:
    return await c.graphs.evaluation(body)


@router.post("/reports", response_model=ReportResponse)
async def reports(body: ReportRequest, c: Container = Depends(get_container)) -> ReportResponse:
    return await c.graphs.report(body)


@router.post("/rooms/{room_id}/finalize", response_model=FinalizeResponse, status_code=202)
async def finalize(room_id: int, body: FinalizeRequest | None = None,
                   c: Container = Depends(get_container)) -> FinalizeResponse:
    flushed, scheduled = await c.rooms.finalize(room_id, body.post_stt if body else True)
    return FinalizeResponse(flushed=flushed, post_stt_scheduled=scheduled,
                            audio_retention=c.settings.audio.retention)


@router.post("/blind/check", response_model=BlindCheckResponse)
async def blind_check(body: BlindCheckRequest, c: Container = Depends(get_container)) -> BlindCheckResponse:
    results = []
    for i, hits in enumerate(c.blind.check_texts(body.texts)):
        results.append(BlindCheckItem(index=i, clean=not hits, violations=[
            BlindViolationOut(topic=v.topic, label=v.label, matched=v.matched, rule_type=v.rule_type) for v in hits]))
    return BlindCheckResponse(clean=all(r.clean for r in results), rules_version=c.blind.rules.version,
                              results=results)


@router.get("/stats")
async def stats(c: Container = Depends(get_container)) -> dict:
    """운영·부하 테스트용(계약 밖의 추가 경로): 방 수, 큐 깊이, provider 호출 수."""
    return c.rooms.stats()
