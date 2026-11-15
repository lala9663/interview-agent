"""WebSocket 흐름(API 명세 3장)과 방 마무리 → 사후 STT → refined-transcript 콜백."""

from __future__ import annotations

import json
import time
from typing import Any, Callable

import anyio
import pytest
from starlette.websockets import WebSocketDisconnect

from app.demo import synth
from app.demo.script import DEMO_SCRIPT
from app.live.ticket import make_ticket
from tests.conftest import INTERNAL

SECRET = "dev-room-ticket-secret-change-me-32bytes!!"
ROOM = 9001
_open: list[Any] = []


async def _receive(stream, timeout: float) -> dict[str, Any]:
    with anyio.fail_after(timeout):
        return await stream.receive()


def recv(ws, timeout: float = 10.0) -> dict[str, Any]:
    """제한 시간 안에 이벤트 하나를 받는다(안 오면 TimeoutError로 테스트 실패, 멈춰 서지 않는다)."""
    message = ws.portal.call(_receive, ws._send_rx, timeout)
    ws._raise_on_close(message)
    return json.loads(message["text"])


def recv_until(ws, done: Callable[[dict], bool], limit: int = 300) -> list[dict]:
    events = []
    for _ in range(limit):
        try:
            events.append(recv(ws))
        except TimeoutError:
            break
        if done(events[-1]):
            return events
    raise AssertionError(f"기다리던 이벤트가 오지 않음. 받은 것: {[e['type'] for e in events]}")


def connect(client, *, audio: bool, room: int = ROOM, ticket: str | None = None):
    ws = client.websocket_connect(f"/ws/v1/rooms/{room}")
    ws.__enter__()
    _open.append(ws)
    ws.send_json({"type": "auth", "ticket": ticket or make_ticket(SECRET, room_id=room, audio=audio)})
    return ws


@pytest.fixture(autouse=True)
def _close_sockets(client):
    """테스트가 연 WebSocket을 끝에 모두 정리한다(실패한 테스트가 다음 테스트를 막지 않게)."""
    yield
    while _open:
        try:
            _open.pop().__exit__(None, None, None)
        except Exception:  # noqa: BLE001
            pass


def send_audio(ws, turns: list[tuple[float, float]]) -> None:
    for frame in synth.frames(synth.conversation(turns)):
        ws.send_bytes(frame)


def wait_for(cond: Callable[[], bool], timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not cond():
        assert time.monotonic() < deadline, "조건이 시간 안에 맞지 않음"
        time.sleep(0.02)


def test_full_flow_auth_audio_partial_final_matched_followup(client, container):
    mic = connect(client, audio=True)
    ready = recv(mic)
    assert ready == {"type": "ready", "roomId": ROOM, "audio": True, "sttModel": "fake-whisper",
                     "llmModel": "fake-llm", "lastSeq": 0}
    status = recv(mic)
    assert status == {"type": "status", "stt": "OK", "llm": "OK", "queueDepth": 0, "audioConnected": True}
    listener = connect(client, audio=False)  # 듣기만 하는 다른 면접관
    assert recv(listener)["audio"] is False

    send_audio(mic, [(2.0, 1.0), (4.0, 1.0), (2.0, 1.0), (4.0, 2.0)])  # 질문·답·질문·답
    events = recv_until(mic, lambda e: e["type"] == "followup.suggested" and e["basedOnSeq"] == 4)
    by_type: dict[str, list[dict]] = {}
    for e in events:
        by_type.setdefault(e["type"], []).append(e)

    finals = by_type["transcript.final"]
    assert [f["seq"] for f in finals] == [1, 2, 3, 4]
    assert [f["speaker"] for f in finals] == [s for s, _ in DEMO_SCRIPT[:4]]
    assert [f["rawText"] for f in finals] == [t for _, t in DEMO_SCRIPT[:4]]
    assert finals[3]["refinedText"].startswith("캡스톤에서는 제가 펌웨어를") and finals[3]["refinedText"].endswith(".")
    assert finals[2]["speakerMethod"] == "QUESTION_MATCH" and finals[1]["speakerMethod"] == "RULE"
    assert all(0 <= f["startMs"] < f["endMs"] for f in finals) and finals[0]["endMs"] <= finals[1]["startMs"]
    # partial은 같은 seq의 final보다 먼저 온다
    order = [(e["type"], e["seq"]) for e in events if e["type"].startswith("transcript.")]
    for seq in (1, 2, 3, 4):
        assert order.index(("transcript.partial", seq)) < order.index(("transcript.final", seq))
    assert by_type["transcript.partial"][0] == {"type": "transcript.partial", "seq": 1, "text": DEMO_SCRIPT[0][1]}
    # 준비된 질문(7001)과 자동 매칭
    matched = by_type["question.matched"]
    assert len(matched) == 1 and matched[0]["seq"] == 3 and matched[0]["questionId"] == 7001
    assert 0.55 <= matched[0]["similarity"] <= 1.0
    # 꼬리질문
    followup = events[-1]
    assert 2 <= len(followup["items"]) <= 3 and followup["items"][0]["id"] == "f-4-1"
    assert set(followup["items"][0]) == {"id", "text", "rationale"}

    # 다른 단말도 같은 이벤트를 받는다
    heard = recv_until(listener, lambda e: e["type"] == "followup.suggested" and e["basedOnSeq"] == 4)
    assert [e["seq"] for e in heard if e["type"] == "transcript.final"] == [1, 2, 3, 4]

    # 백엔드 저장(5장)
    wait_for(lambda: len(container.backend.of("follow-ups", ROOM)) >= 1)
    saved = [b["items"][0] for b in container.backend.of("utterances", ROOM)]
    assert [u["seq"] for u in saved] == [1, 2, 3, 4] and saved[2]["matchedQuestionId"] == 7001
    assert set(saved[0]) == {"seq", "speaker", "speakerMethod", "rawText", "refinedText", "startMs", "endMs",
                             "matchedQuestionId"}
    fu = container.backend.of("follow-ups", ROOM)[-1]
    assert fu["basedOnSeq"] == 4 and fu["items"][0]["clientId"] == "f-4-1"

    # 재접속하면 lastSeq로 빈 구간을 안다
    late = connect(client, audio=False)
    assert recv(late)["lastSeq"] == 4
    for ws in (late, listener, mic):
        ws.close()


def test_spoken_followup_is_matched_once(client, container):
    """제안된 꼬리질문을 면접관이 비슷하게 말하면 followup.matched가 한 번 온다(누르지 않아도 채택)."""
    from app.matching.matcher import KIND_FOLLOWUP, MatchCandidate

    mic = connect(client, audio=True)
    recv_until(mic, lambda e: e["type"] == "status")
    send_audio(mic, [(2.0, 1.0), (4.0, 2.0)])  # 질문·답 → 꼬리질문 제안
    recv_until(mic, lambda e: e["type"] == "followup.suggested")
    # 대본의 다음 면접관 말(3번째 줄)과 조사만 다른 꼬리질문이 제안돼 있었다고 둔다
    session = container.rooms.sessions[ROOM]
    session.matcher.add([MatchCandidate(KIND_FOLLOWUP, "캡스톤 프로젝트에서 맡으신 역할은 무엇이었나요?", ref="f-2-9")])
    # 준비된 질문(7001)도 이 말과 비슷하다. 꼬리질문 쪽이 더 비슷할 때만 꼬리질문으로 잡히는지 보려고 뺀다
    session.matcher.replace([], keep_kinds=(KIND_FOLLOWUP,))

    send_audio(mic, [(2.0, 1.0)])
    events = recv_until(mic, lambda e: e["type"] == "followup.matched")
    matched = events[-1]
    assert set(matched) == {"type", "seq", "id", "similarity"}
    assert matched["seq"] == 3 and matched["id"] == "f-2-9" and 0.55 <= matched["similarity"] <= 1.0
    assert not [e for e in events if e["type"] == "question.matched"]
    final = [e for e in events if e["type"] == "transcript.final"][-1]
    assert final["speaker"] == "INTERVIEWER"
    assert session.followups.matched == {"f-2-9"}
    mic.close()


def test_client_commands_role_fix_asked_request_ping(client, container):
    mic = connect(client, audio=True)
    recv_until(mic, lambda e: e["type"] == "status")
    mic.send_json({"type": "ping"})
    assert recv(mic) == {"type": "pong"}

    mic.send_json({"type": "role", "role": "CANDIDATE"})  # 지금부터의 말은 지원자
    send_audio(mic, [(2.0, 1.5)])
    final = recv_until(mic, lambda e: e["type"] == "transcript.final")[-1]
    assert (final["speaker"], final["speakerMethod"]) == ("CANDIDATE", "MANUAL")  # 대본으로는 면접관 질문이지만 지정이 이긴다

    mic.send_json({"type": "utterance.fix", "seq": 1, "speaker": "INTERVIEWER"})
    updated = recv_until(mic, lambda e: e["type"] == "transcript.updated")[-1]
    assert updated == {"type": "transcript.updated", "seq": 1, "speaker": "INTERVIEWER", "speakerMethod": "MANUAL"}
    wait_for(lambda: container.backend.of("utterances", ROOM)[-1]["items"][0]["speaker"] == "INTERVIEWER")

    mic.send_json({"type": "role", "role": "AUTO"})
    mic.send_json({"type": "question.asked", "questionId": 7002})
    wait_for(lambda: len(container.backend.of("events", ROOM)) == 1)
    assert container.backend.of("events", ROOM)[0] == {
        "type": "QUESTION_ASKED", "message": "면접관이 질문을 했다고 표시함", "data": {"questionId": 7002, "seq": 1}}

    mic.send_json({"type": "followup.request"})  # 지원자 답이 없어도 바로 요청할 수 있다
    followup = recv_until(mic, lambda e: e["type"] == "followup.suggested")[-1]
    assert followup["basedOnSeq"] == 1 and followup["items"]
    mic.send_json({"type": "nonsense"})
    mic.send_text("JSON이 아님")
    mic.send_json({"type": "ping"})
    assert recv_until(mic, lambda e: e["type"] == "pong")  # 모르는 메시지는 무시하고 연결은 유지된다
    mic.close()


@pytest.mark.parametrize("ticket,code", [
    ("not-a-jwt", "TICKET_INVALID"),
    (make_ticket("wrong-secret-wrong-secret-wrong-secret!!", room_id=ROOM, audio=True), "TICKET_INVALID"),
    (make_ticket(SECRET, room_id=ROOM, audio=True, ttl_s=-10), "TICKET_EXPIRED"),
    (make_ticket(SECRET, room_id=1234, audio=True), "TICKET_INVALID"),  # 다른 방의 입장권
])
def test_auth_failure_closes_with_4401(client, ticket, code):
    ws = connect(client, audio=True, ticket=ticket)
    error = recv(ws)
    assert error["type"] == "error" and error["code"] == code and error["retryable"] is False
    with pytest.raises(WebSocketDisconnect) as exc:
        recv(ws)
    assert exc.value.code == 4401


def test_first_message_must_be_auth(client):
    with client.websocket_connect(f"/ws/v1/rooms/{ROOM}") as ws:
        ws.send_json({"type": "ping"})
        assert ws.receive_json()["code"] == "TICKET_INVALID"
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
        assert exc.value.code == 4401


def test_second_microphone_is_rejected_with_4409_then_allowed_after_release(client):
    mic = connect(client, audio=True)
    recv_until(mic, lambda e: e["type"] == "status")
    second = connect(client, audio=True)
    error = recv(second)
    assert error["code"] == "AUDIO_ALREADY_CONNECTED"
    with pytest.raises(WebSocketDisconnect) as exc:
        recv(second)
    assert exc.value.code == 4409
    other_room = connect(client, audio=True, room=9002)  # 다른 방은 상관없다
    assert recv(other_room)["type"] == "ready"
    mic.close()
    time.sleep(0.2)
    again = connect(client, audio=True)  # 마이크 단말이 나가면 다시 붙을 수 있다
    assert recv(again)["type"] == "ready"
    again.close()
    other_room.close()


def test_room_limit_closes_with_4429(client, container):
    container.settings.live.max_rooms = 1
    first = connect(client, audio=True, room=1)
    assert recv(first)["type"] == "ready"
    second = connect(client, audio=True, room=2)
    assert recv(second)["code"] == "OVERLOADED"
    with pytest.raises(WebSocketDisconnect) as exc:
        recv(second)
    assert exc.value.code == 4429
    first.close()


def test_finalize_flushes_then_post_stt_calls_back_and_discards_audio(client, container):
    mic = connect(client, audio=True)
    recv_until(mic, lambda e: e["type"] == "status")
    send_audio(mic, [(2.0, 1.0), (4.0, 1.0), (2.0, 1.0)])
    recv_until(mic, lambda e: e["type"] == "transcript.final" and e["seq"] == 3)
    mic.send_json({"type": "utterance.fix", "seq": 2, "speaker": "INTERVIEWER"})  # 사람이 고친 화자는 보정본에도 남아야 한다
    recv_until(mic, lambda e: e["type"] == "transcript.updated")
    for frame in synth.frames(synth.speech(3.0, seed=9)):  # 말이 끝나기 전에 방이 끝난다
        mic.send_bytes(frame)
    wait_for(lambda: container.audio_store.total_bytes() >= 32000 * 12)

    r = client.post(f"/internal/v1/rooms/{ROOM}/finalize", json={"postStt": True}, headers=INTERNAL)
    assert r.status_code == 202
    assert r.json() == {"flushed": True, "postSttScheduled": True, "audioRetention": "MEMORY_ONLY"}
    # flush: 끝나지 않은 마지막 말도 실시간 전사로 저장된 뒤에 응답이 온다
    assert [b["items"][0]["seq"] for b in container.backend.of("utterances", ROOM)][-1] == 4
    # 방이 끝나면 단말은 1000으로 닫힌다
    with pytest.raises(WebSocketDisconnect) as exc:
        for _ in range(50):
            recv(mic)
    assert exc.value.code == 1000

    wait_for(lambda: len(container.backend.of("refined-transcript", ROOM)) == 1)
    body = container.backend.of("refined-transcript", ROOM)[0]
    assert body["sttModel"] == "fake-whisper"
    items = body["items"]
    assert [i["seq"] for i in items] == [1, 2, 3, 4]
    assert [i["rawText"] for i in items] == [t for _, t in DEMO_SCRIPT[:4]]
    assert (items[1]["speaker"], items[1]["speakerMethod"]) == ("INTERVIEWER", "MANUAL")
    assert items[2]["matchedQuestionId"] == 7001 and items[2]["speakerMethod"] == "QUESTION_MATCH"
    assert set(items[0]) == {"seq", "speaker", "speakerMethod", "rawText", "refinedText", "startMs", "endMs",
                             "matchedQuestionId"}
    # 사후 STT가 끝나면 음성과 방 상태를 버린다
    wait_for(lambda: container.audio_store.total_bytes() == 0 and ROOM not in container.rooms.sessions)
    assert container.backend.of("events", ROOM) == []  # POST_STT_FAILED 없음


def test_finalize_without_post_stt_discards_audio_immediately(client, container):
    mic = connect(client, audio=True)
    recv_until(mic, lambda e: e["type"] == "status")
    send_audio(mic, [(2.0, 1.0)])
    recv_until(mic, lambda e: e["type"] == "transcript.final")
    r = client.post(f"/internal/v1/rooms/{ROOM}/finalize", json={"postStt": False}, headers=INTERNAL)
    assert r.json() == {"flushed": True, "postSttScheduled": False, "audioRetention": "MEMORY_ONLY"}
    assert container.audio_store.total_bytes() == 0 and container.backend.of("refined-transcript") == []


def test_post_stt_failure_holds_audio_and_retries_until_model_recovers(client, container):
    live = container.settings.live
    live.post_stt_max_attempts, live.post_stt_hold_minutes, live.post_stt_retry_interval_s = 1, 1, 0.3
    stt = container.providers.stt_post
    stt.config.max_retries, stt.config.cooldown_s = 0, 0.05
    mic = connect(client, audio=True)
    recv_until(mic, lambda e: e["type"] == "status")
    send_audio(mic, [(2.0, 1.0)])
    recv_until(mic, lambda e: e["type"] == "transcript.final")
    stt.config.options["fail_every"] = 1  # 면접이 끝날 때 Whisper가 죽어 있다
    r = client.post(f"/internal/v1/rooms/{ROOM}/finalize", json={"postStt": True}, headers=INTERNAL)
    assert r.json()["postSttScheduled"] is True
    wait_for(lambda: len(container.backend.of("events", ROOM)) == 1, timeout=15)
    event = container.backend.of("events", ROOM)[0]
    assert event["type"] == "POST_STT_FAILED" and event["data"]["willRetry"] is True and event["data"]["holdMinutes"] == 1
    assert container.audio_store.total_bytes() > 0  # 음성을 버리지 않고 붙잡고 있다
    stt.config.options["fail_every"] = 0  # 모델이 살아났다
    wait_for(lambda: len(container.backend.of("refined-transcript")) == 1, timeout=15)
    wait_for(lambda: container.audio_store.total_bytes() == 0)  # 성공하면 놓아 준다
    assert len(container.backend.of("events", ROOM)) == 1


def test_post_stt_gives_up_after_hold_time_and_discards_audio(client, container):
    live = container.settings.live
    live.post_stt_max_attempts, live.post_stt_hold_minutes, live.post_stt_retry_interval_s = 1, 1, 0.2
    stt = container.providers.stt_post
    stt.config.max_retries, stt.config.cooldown_s = 0, 0.05
    mic = connect(client, audio=True)
    recv_until(mic, lambda e: e["type"] == "status")
    send_audio(mic, [(2.0, 1.0)])
    recv_until(mic, lambda e: e["type"] == "transcript.final")
    stt.config.options["fail_every"] = 1
    client.post(f"/internal/v1/rooms/{ROOM}/finalize", json={"postStt": True}, headers=INTERNAL)
    wait_for(lambda: len(container.backend.of("events", ROOM)) == 1, timeout=15)
    live.post_stt_hold_minutes = 0  # 설정은 시작할 때 읽었으므로 이 값으로는 안 끝난다. 아래에서 시각을 당긴다
    import app.live.post_stt as post_stt

    real = post_stt.time.monotonic
    post_stt.time.monotonic = lambda: real() + 120  # 붙잡는 시간이 지난 것으로
    try:
        wait_for(lambda: len(container.backend.of("events", ROOM)) == 2, timeout=15)
    finally:
        post_stt.time.monotonic = real
    assert container.backend.of("events", ROOM)[1]["data"]["willRetry"] is False
    wait_for(lambda: container.audio_store.total_bytes() == 0)
    assert container.backend.of("refined-transcript") == []


def test_post_stt_failure_reports_event_and_still_discards_audio(client, container):
    container.settings.live.post_stt_max_attempts = 1
    container.settings.live.post_stt_hold_minutes = 0  # 붙잡지 않는 설정
    mic = connect(client, audio=True)
    recv_until(mic, lambda e: e["type"] == "status")
    send_audio(mic, [(2.0, 1.0)])
    recv_until(mic, lambda e: e["type"] == "transcript.final")
    container.providers.stt_post.config.options["fail_every"] = 1  # 사후 STT가 계속 실패
    container.providers.stt_post.config.max_retries = 0
    r = client.post(f"/internal/v1/rooms/{ROOM}/finalize", json={"postStt": True}, headers=INTERNAL)
    assert r.json()["postSttScheduled"] is True
    wait_for(lambda: len(container.backend.of("events", ROOM)) == 1, timeout=15)
    assert container.backend.of("events", ROOM)[0]["type"] == "POST_STT_FAILED"
    wait_for(lambda: container.audio_store.total_bytes() == 0)
    assert container.backend.of("refined-transcript") == []


def test_stt_failure_emits_error_and_flow_continues(client, container):
    stt = container.providers.stt_live
    stt.config.max_retries, stt.config.backoff_base_s = 0, 0.001
    stt.config.options["fail_every"] = 2  # 두 번째 청크만 실패
    mic = connect(client, audio=True)
    recv_until(mic, lambda e: e["type"] == "status")
    send_audio(mic, [(2.0, 1.0), (2.0, 1.0), (2.0, 1.5)])
    events = recv_until(mic, lambda e: e["type"] == "transcript.final" and e["seq"] == 2)
    errors = [e for e in events if e["type"] == "error"]
    assert errors and errors[0]["code"] == "STT_FAILED" and errors[0]["retryable"] is True
    assert [e["seq"] for e in events if e["type"] == "transcript.final"] == [1, 2]  # 실패한 청크는 건너뛰고 이어 간다
    mic.close()


def test_backend_down_does_not_stop_transcription(client, container):
    container.backend.fail = True
    mic = connect(client, audio=True)
    events = recv_until(mic, lambda e: e["type"] == "status")
    assert [e["code"] for e in events if e["type"] == "error"] == ["BACKEND_UNAVAILABLE"]
    send_audio(mic, [(2.0, 1.5)])
    final = recv_until(mic, lambda e: e["type"] == "transcript.final")[-1]
    assert final["seq"] == 1 and final["speaker"] == "INTERVIEWER"
    assert container.outbox.pending >= 1  # 저장은 재시도 큐에서 기다린다
    container.backend.fail = False
    wait_for(lambda: len(container.backend.of("utterances", ROOM)) == 1)
    mic.close()


def test_slow_refine_falls_back_to_raw(client, container):
    container.settings.live.refine_timeout_s = 0.05
    container.providers.llm_fast.chat_model().latency_ms = 400  # 다듬기가 제한 시간보다 느리다
    container.settings.speaker.chain = "manual,question_match,rule"
    mic = connect(client, audio=True)
    recv_until(mic, lambda e: e["type"] == "status")
    send_audio(mic, [(2.0, 1.0), (4.0, 1.5)])
    events = recv_until(mic, lambda e: e["type"] == "transcript.final" and e["seq"] == 2)
    final = events[-1]
    assert final["refinedText"] == final["rawText"] == DEMO_SCRIPT[1][1]
    assert not [e for e in events if e["type"] == "transcript.updated"]  # 나중에 고쳐 보내지 않는다
    mic.close()


def drain(ws) -> list[dict]:
    """지금까지 온 이벤트를 모두 받는다(ping을 보내고 pong이 올 때까지)."""
    ws.send_json({"type": "ping"})
    return recv_until(ws, lambda e: e["type"] == "pong")[:-1]


def test_backpressure_drops_oldest_chunk_and_reports_overloaded(client, container):
    live = container.settings.live
    live.stt_queue_soft, live.stt_queue_hard = 1, 2
    container.providers.stt_live.config.options["latency_ms"] = 300  # STT가 느려 큐가 찬다
    mic = connect(client, audio=True)
    recv_until(mic, lambda e: e["type"] == "status")
    send_audio(mic, [(1.0, 0.8)] * 7)  # 실시간보다 훨씬 빠르게 7개 발화를 밀어 넣는다
    session = container.rooms.sessions[ROOM]
    wait_for(lambda: session.dropped_chunks > 0 and session.queue_depth == 0)
    events = drain(mic)
    errors = [e for e in events if e["type"] == "error"]
    assert errors and errors[0]["code"] == "OVERLOADED" and errors[0]["retryable"] is True
    finals = [e for e in events if e["type"] == "transcript.final"]
    assert 1 <= len(finals) < 7 and len(finals) + session.dropped_chunks == 7  # 버린 만큼만 빠진다
    assert [f["seq"] for f in finals] == list(range(1, len(finals) + 1))  # seq는 빈 번호 없이 이어진다
    assert any(s["queueDepth"] >= 2 for s in events if s["type"] == "status")  # 밀린 것을 status로 알린다
    partial_seqs = {e["seq"] for e in events if e["type"] == "transcript.partial"}
    assert len(partial_seqs) < len(finals)  # 밀린 동안에는 partial을 건너뛴다
    # 밀린 뒤에도 방은 계속 돈다
    send_audio(mic, [(1.0, 1.0)])
    assert recv_until(mic, lambda e: e["type"] == "transcript.final")[-1]["seq"] == len(finals) + 1
    mic.close()


def test_stale_chunks_are_skipped(client, container):
    container.settings.live.max_chunk_wait_s = 0.25
    container.providers.stt_live.config.options["latency_ms"] = 500
    mic = connect(client, audio=True)
    recv_until(mic, lambda e: e["type"] == "status")
    send_audio(mic, [(1.0, 0.8)] * 3)
    session = container.rooms.sessions[ROOM]
    wait_for(lambda: session.queue_depth == 0 and len(session.utterances) + session.dropped_chunks == 3)
    events = drain(mic)
    assert [e["seq"] for e in events if e["type"] == "transcript.final"] == [1]
    assert session.dropped_chunks == 2  # 첫 청크를 전사하는 동안 오래 기다린 두 청크는 건너뛴다
    assert [e["code"] for e in events if e["type"] == "error"] == ["OVERLOADED"]
    assert container.rooms.stats()["roomStats"][0]["droppedChunks"] == 2
    mic.close()


def test_audio_over_memory_cap_is_dropped_and_refined_transcript_keeps_live_part(client, container):
    container.audio_store.max_bytes = 60 * 32000  # 방 음성 상한을 60초로 줄인다
    mic = connect(client, audio=True)
    recv_until(mic, lambda e: e["type"] == "status")
    send_audio(mic, [(2.0, 1.0), (4.0, 1.0)])  # 앞부분: 발화 2개 (약 8초)
    for frame in synth.frames(synth.silence(55.0)):
        mic.send_bytes(frame)
    send_audio(mic, [(2.0, 1.0), (4.0, 2.0)])  # 뒷부분: 발화 2개. 전체 약 73초 → 앞의 약 13초를 버린다
    finals = [e for e in recv_until(mic, lambda e: e["type"] == "transcript.final" and e["seq"] == 4)
              if e["type"] == "transcript.final"]
    wait_for(lambda: container.backend.of("events", ROOM) != [])
    dropped = container.backend.of("events", ROOM)[0]
    assert dropped["type"] == "AUDIO_DROPPED" and dropped["data"]["droppedMs"] > 0

    r = client.post(f"/internal/v1/rooms/{ROOM}/finalize", json={"postStt": True}, headers=INTERNAL)
    assert r.json()["postSttScheduled"] is True
    wait_for(lambda: len(container.backend.of("refined-transcript", ROOM)) == 1)
    items = container.backend.of("refined-transcript", ROOM)[0]["items"]
    assert [i["seq"] for i in items] == [1, 2, 3, 4]
    # 버린 구간의 발화 2개는 실시간 전사를 그대로 쓰고, 남은 음성의 발화 2개는 다시 전사한다
    assert [(i["rawText"], i["startMs"], i["endMs"]) for i in items[:2]] == \
           [(f["rawText"], f["startMs"], f["endMs"]) for f in finals[:2]]
    assert all(a["endMs"] <= b["startMs"] for a, b in zip(items, items[1:]))  # 시각은 방 처음 기준으로 이어진다
    assert abs(items[2]["startMs"] - finals[2]["startMs"]) <= 100 and abs(items[3]["endMs"] - finals[3]["endMs"]) <= 100
