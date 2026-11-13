"""REST 계약 모양(camelCase)과 백엔드 클라이언트·재시도 큐."""

from __future__ import annotations

import asyncio
import json

import httpx

from app.backend.client import FakeBackendClient, HttpBackendClient
from app.backend.outbox import BackendOutbox
from app.demo.data import demo_briefing_request
from app.schemas.backend import BackendEvent, FollowUpItem, FollowUpsRequest, UtteranceItem, UtterancesRequest
from app.settings import BackendSettings
from tests.conftest import INTERNAL
from tests.test_graphs import CRITERIA, TRANSCRIPT


def test_health_needs_no_key(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "UP", "llm": "fake", "stt": "fake"}


def test_internal_key_required(client):
    for headers in ({}, {"X-Internal-Key": "wrong"}):
        r = client.post("/internal/v1/briefings", json=demo_briefing_request(), headers=headers)
        assert r.status_code == 401
        assert r.json() == {"code": "UNAUTHORIZED", "message": "X-Internal-Key가 없거나 올바르지 않습니다.", "details": {}}


def test_validation_error_uses_contract_shape(client):
    r = client.post("/internal/v1/briefings", json={"track": "SENIOR"}, headers=INTERNAL)
    body = r.json()
    assert r.status_code == 400 and body["code"] == "VALIDATION_FAILED" and set(body) == {"code", "message", "details"}
    assert {"field": "track", "message": body["details"]["errors"][0]["message"]} in body["details"]["errors"]
    assert client.get("/internal/v1/nope", headers=INTERNAL).json()["code"] == "NOT_FOUND"


def test_briefing_contract_shape(client):
    r = client.post("/internal/v1/briefings", json=demo_briefing_request(), headers=INTERNAL)
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"summary", "questions", "excluded", "model"}
    assert set(body["summary"]) == {"headline", "keyPoints", "strengths", "checkPoints", "timeline"}
    assert set(body["summary"]["timeline"][0]) == {"period", "text"}
    assert all(set(q) == {"source", "category", "text", "intent"} for q in body["questions"])
    assert len(body["questions"]) == 10 and body["model"] == "fake-llm"


def test_evaluation_contract_shape(client):
    r = client.post("/internal/v1/evaluations", headers=INTERNAL, json={
        "track": "NEW_GRAD", "interviewType": "PT", "jobTitle": "SW개발", "jdText": "...", "criteria": CRITERIA,
        "transcript": TRANSCRIPT, "briefingSummary": {}})
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"scores", "overallComment", "model"}
    assert all(set(s) == {"criterionCode", "score", "rationale", "evidenceSeqs"} for s in body["scores"])
    assert all(isinstance(s["score"], int) for s in body["scores"])


def test_report_contract_shape(client):
    base = {"track": "EXPERIENCED", "basis": "REFINED", "interviewType": "JOB", "jobTitle": "SW개발", "jdText": "...",
            "transcript": TRANSCRIPT, "briefingSummary": {}, "aiEvaluation": {"scores": [], "overallComment": ""},
            "previousRounds": [{"type": "PORTFOLIO", "headline": "...", "checkPoints": []}]}
    keys = {"headline", "overallComment", "strengths", "concerns", "qaHighlights", "competencies", "careerChecks",
            "nextRoundChecks", "risks"}
    brief = client.post("/internal/v1/reports", headers=INTERNAL, json=base | {"detail": "BRIEF"}).json()
    assert set(brief) == {"content", "model"} and set(brief["content"]) == keys
    assert set(brief["content"]["qaHighlights"][0]) == {"question", "answerSummary", "seqs"}
    assert brief["content"]["competencies"] == [] and brief["content"]["nextRoundChecks"] == []
    detailed = client.post("/internal/v1/reports", headers=INTERNAL, json=base | {"detail": "DETAILED"}).json()
    assert set(detailed["content"]["competencies"][0]) == {"name", "assessment", "evidence"}
    assert detailed["content"]["nextRoundChecks"] and detailed["content"]["risks"]


def test_finalize_unknown_room_is_a_noop_202(client):
    r = client.post("/internal/v1/rooms/4242/finalize", json={"postStt": True}, headers=INTERNAL)
    assert r.status_code == 202
    assert r.json() == {"flushed": True, "postSttScheduled": False, "audioRetention": "MEMORY_ONLY"}
    assert client.post("/internal/v1/rooms/4242/finalize", headers=INTERNAL).status_code == 202  # 본문 없이도 된다


def test_cors_allows_frontend_origin(client):
    r = client.options("/health", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"})
    assert r.headers["access-control-allow-origin"] == "http://localhost:5173"


# ---------- 백엔드 클라이언트 ----------
def _utterances() -> UtterancesRequest:
    return UtterancesRequest(items=[UtteranceItem(seq=12, speaker="CANDIDATE", speaker_method="LLM", raw_text="r",
                                                  refined_text="f", start_ms=1, end_ms=2)])


async def test_http_backend_client_follows_contract_paths():
    seen: list[tuple[str, str, str | None, dict | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        seen.append((request.method, request.url.path, request.headers.get("x-internal-key"), body))
        if request.method == "GET":
            return httpx.Response(200, json={"roomId": 9001, "interviewId": 501, "status": "LIVE", "track": "EXPERIENCED",
                                             "interviewType": "JOB", "mode": "ONLINE", "jobTitle": "SW", "jdText": "jd",
                                             "candidateBrief": "b", "questions": [{"id": 1, "source": "JD", "category": "c",
                                                                                  "text": "q"}], "questionBank": []})
        return httpx.Response(204)

    client = HttpBackendClient(BackendSettings(mode="http", base_url="http://backend:8080/"), "key-1",
                               transport=httpx.MockTransport(handler))
    ctx = await client.get_context(9001)
    assert ctx.interview_id == 501 and ctx.track == "EXPERIENCED" and ctx.questions[0].text == "q"
    await client.save_utterances(9001, _utterances())
    await client.save_followups(9001, FollowUpsRequest(based_on_seq=13, items=[FollowUpItem(client_id="f-13-1", text="t")]))
    await client.post_event(9001, BackendEvent(type="QUESTION_ASKED", message="m", data={"questionId": 7001}))
    await client.close()
    assert [(m, p) for m, p, _, _ in seen] == [
        ("GET", "/internal/v1/rooms/9001/context"), ("POST", "/internal/v1/rooms/9001/utterances"),
        ("POST", "/internal/v1/rooms/9001/follow-ups"), ("POST", "/internal/v1/rooms/9001/events")]
    assert all(key == "key-1" for _, _, key, _ in seen)
    assert seen[1][3] == {"items": [{"seq": 12, "speaker": "CANDIDATE", "speakerMethod": "LLM", "rawText": "r",
                                     "refinedText": "f", "startMs": 1, "endMs": 2, "matchedQuestionId": None}]}
    assert seen[2][3] == {"basedOnSeq": 13, "items": [{"clientId": "f-13-1", "text": "t", "rationale": ""}]}
    assert seen[3][3] == {"type": "QUESTION_ASKED", "message": "m", "data": {"questionId": 7001}}


async def test_outbox_retries_until_backend_recovers():
    backend = FakeBackendClient()
    backend.fail = True
    outbox = BackendOutbox(backend, BackendSettings(retry_base_delay_s=0.01, retry_max_attempts=6))
    outbox.save_utterances(9001, _utterances())  # 바로 돌아온다(면접 흐름을 막지 않는다)
    await asyncio.sleep(0.05)
    assert backend.calls == [] and outbox.pending == 1 and not outbox.healthy
    backend.fail = False
    assert await outbox.drain(timeout_s=2.0)
    assert len(backend.of("utterances", 9001)) == 1 and outbox.healthy and outbox.dropped == 0
    await outbox.stop()


async def test_outbox_gives_up_after_max_attempts():
    backend = FakeBackendClient()
    backend.fail = True
    outbox = BackendOutbox(backend, BackendSettings(retry_base_delay_s=0.001, retry_max_attempts=3))
    outbox.post_event(1, BackendEvent(type="AUDIO_DROPPED"))
    assert await outbox.drain(timeout_s=2.0)
    assert outbox.dropped == 1 and backend.calls == []
    await outbox.stop()
