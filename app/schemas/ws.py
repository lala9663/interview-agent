"""WebSocket 이벤트 (계약 3장). 서버 → 클라이언트 이벤트는 dict로 만들어 EventBus에 싣는다."""

from __future__ import annotations

from typing import Any

# 오류 코드 (3.2)
TICKET_INVALID = "TICKET_INVALID"
TICKET_EXPIRED = "TICKET_EXPIRED"
AUDIO_ALREADY_CONNECTED = "AUDIO_ALREADY_CONNECTED"
ROOM_NOT_LIVE = "ROOM_NOT_LIVE"
STT_FAILED = "STT_FAILED"
LLM_FAILED = "LLM_FAILED"
BACKEND_UNAVAILABLE = "BACKEND_UNAVAILABLE"
OVERLOADED = "OVERLOADED"

# 닫힘 코드
CLOSE_NORMAL = 1000
CLOSE_AUTH = 4401
CLOSE_AUDIO_DUP = 4409
CLOSE_OVERLOADED = 4429

CLIENT_ROLES = ("INTERVIEWER", "CANDIDATE", "AUTO")


def ready(room_id: int, audio: bool, stt_model: str, llm_model: str, last_seq: int) -> dict[str, Any]:
    return {"type": "ready", "roomId": room_id, "audio": audio, "sttModel": stt_model,
            "llmModel": llm_model, "lastSeq": last_seq}


def transcript_partial(seq: int, text: str) -> dict[str, Any]:
    return {"type": "transcript.partial", "seq": seq, "text": text}


def transcript_final(seq: int, speaker: str, speaker_method: str, raw_text: str, refined_text: str,
                     start_ms: int, end_ms: int) -> dict[str, Any]:
    return {"type": "transcript.final", "seq": seq, "speaker": speaker, "speakerMethod": speaker_method,
            "rawText": raw_text, "refinedText": refined_text, "startMs": start_ms, "endMs": end_ms}


def transcript_updated(seq: int, speaker: str, speaker_method: str) -> dict[str, Any]:
    return {"type": "transcript.updated", "seq": seq, "speaker": speaker, "speakerMethod": speaker_method}


def question_matched(seq: int, question_id: int, similarity: float) -> dict[str, Any]:
    return {"type": "question.matched", "seq": seq, "questionId": question_id,
            "similarity": round(float(similarity), 2)}


def followup_suggested(based_on_seq: int, items: list[dict[str, str]]) -> dict[str, Any]:
    return {"type": "followup.suggested", "basedOnSeq": based_on_seq, "items": items}


def status(stt: str, llm: str, queue_depth: int, audio_connected: bool) -> dict[str, Any]:
    return {"type": "status", "stt": stt, "llm": llm, "queueDepth": queue_depth,
            "audioConnected": audio_connected}


def error(code: str, message: str, retryable: bool = False) -> dict[str, Any]:
    return {"type": "error", "code": code, "message": message, "retryable": retryable}


def pong() -> dict[str, Any]:
    return {"type": "pong"}
