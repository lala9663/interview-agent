"""Agent → 백엔드 내부 REST 스키마 (API 명세 5장)."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from app.schemas.common import CamelModel, InterviewMode, InterviewType, RoomStatus, Speaker, Track
from app.schemas.internal import BankQuestion


class ContextQuestion(CamelModel):
    id: int
    source: str = ""
    category: str = ""
    text: str


class RoomContext(CamelModel):
    room_id: int
    interview_id: int | None = None
    status: RoomStatus = RoomStatus.LIVE
    track: Track = Track.NEW_GRAD
    interview_type: InterviewType = InterviewType.JOB
    mode: InterviewMode = InterviewMode.OFFLINE
    job_title: str = ""
    jd_text: str = ""
    candidate_brief: str = ""
    questions: list[ContextQuestion] = Field(default_factory=list)
    question_bank: list[BankQuestion] = Field(default_factory=list)


class UtteranceItem(CamelModel):
    seq: int
    speaker: Speaker
    speaker_method: str  # API 명세의 SpeakerMethod 문자열(RULE은 나가기 전에 바뀐다)
    raw_text: str
    refined_text: str
    start_ms: int
    end_ms: int
    matched_question_id: int | None = None


class UtterancesRequest(CamelModel):
    items: list[UtteranceItem]


class FollowUpItem(CamelModel):
    client_id: str
    text: str
    rationale: str = ""


class FollowUpsRequest(CamelModel):
    based_on_seq: int
    items: list[FollowUpItem]


class RefinedTranscriptRequest(CamelModel):
    stt_model: str
    items: list[UtteranceItem]


class BackendEvent(CamelModel):
    type: str  # POST_STT_FAILED | AUDIO_DROPPED | QUESTION_ASKED
    message: str = ""
    data: dict[str, Any] = Field(default_factory=dict)
