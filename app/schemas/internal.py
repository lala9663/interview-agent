"""백엔드 → Agent 내부 REST 스키마 (API 명세 4장)."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from app.schemas.common import (
    AudioRetention,
    CamelModel,
    InterviewType,
    QuestionSource,
    ReportBasis,
    ReportDetail,
    Speaker,
    Track,
)


# ---------- 공통 조각 ----------
class TimelineItem(CamelModel):
    period: str = ""
    text: str = ""


class BriefingSummary(CamelModel):
    headline: str = ""
    key_points: list[str] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    check_points: list[str] = Field(default_factory=list)
    timeline: list[TimelineItem] = Field(default_factory=list)


class BankQuestion(CamelModel):
    id: int
    category: str = ""
    text: str


class TranscriptLine(CamelModel):
    seq: int
    speaker: Speaker = Speaker.UNKNOWN
    text: str = ""


# ---------- 4.1 briefings ----------
class CandidateInput(CamelModel):
    profile: dict[str, Any] = Field(default_factory=dict)


class QuestionCount(CamelModel):
    jd: int = Field(default=4, ge=0, le=20)
    resume: int = Field(default=6, ge=0, le=20)


class BriefingRequest(CamelModel):
    track: Track
    interview_type: InterviewType
    job_title: str = ""
    jd_text: str = ""
    candidate: CandidateInput = Field(default_factory=CandidateInput)
    question_bank: list[BankQuestion] = Field(default_factory=list)
    question_count: QuestionCount = Field(default_factory=QuestionCount)


class GeneratedQuestion(CamelModel):
    source: QuestionSource
    category: str = ""
    text: str
    intent: str = ""


class BriefingResponse(CamelModel):
    summary: BriefingSummary
    questions: list[GeneratedQuestion]
    excluded: list[str] = Field(default_factory=list)
    model: str


# ---------- 4.2 evaluations ----------
class Criterion(CamelModel):
    code: str
    name: str = ""
    description: str = ""
    max_score: int = Field(default=5, ge=1)


class EvaluationRequest(CamelModel):
    track: Track
    interview_type: InterviewType
    job_title: str = ""
    jd_text: str = ""
    criteria: list[Criterion] = Field(default_factory=list)
    transcript: list[TranscriptLine] = Field(default_factory=list)
    briefing_summary: dict[str, Any] | None = None


class CriterionScore(CamelModel):
    criterion_code: str
    score: int
    rationale: str = ""
    evidence_seqs: list[int] = Field(default_factory=list)


class EvaluationResponse(CamelModel):
    scores: list[CriterionScore]
    overall_comment: str = ""
    model: str


# ---------- 4.3 reports ----------
class AiEvaluationInput(CamelModel):
    scores: list[dict[str, Any]] = Field(default_factory=list)
    overall_comment: str = ""


class PreviousRound(CamelModel):
    type: str = ""
    headline: str = ""
    check_points: list[str] = Field(default_factory=list)


class ReportRequest(CamelModel):
    track: Track
    detail: ReportDetail
    basis: ReportBasis = ReportBasis.LIVE
    interview_type: InterviewType
    job_title: str = ""
    jd_text: str = ""
    transcript: list[TranscriptLine] = Field(default_factory=list)
    briefing_summary: dict[str, Any] | None = None
    ai_evaluation: AiEvaluationInput | None = None
    previous_rounds: list[PreviousRound] = Field(default_factory=list)


class QaHighlight(CamelModel):
    question: str = ""
    answer_summary: str = ""
    seqs: list[int] = Field(default_factory=list)


class Competency(CamelModel):
    name: str = ""
    assessment: str = ""
    evidence: list[str] = Field(default_factory=list)


class CareerCheck(CamelModel):
    claim: str = ""
    finding: str = ""


class ReportContent(CamelModel):
    headline: str = ""
    overall_comment: str = ""
    strengths: list[str] = Field(default_factory=list)
    concerns: list[str] = Field(default_factory=list)
    qa_highlights: list[QaHighlight] = Field(default_factory=list)
    # 아래는 DETAILED에서만 채운다. BRIEF에서는 빈 배열
    competencies: list[Competency] = Field(default_factory=list)
    career_checks: list[CareerCheck] = Field(default_factory=list)
    next_round_checks: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)


class ReportResponse(CamelModel):
    content: ReportContent
    model: str


# ---------- 4.4 finalize ----------
class FinalizeRequest(CamelModel):
    post_stt: bool = True


class FinalizeResponse(CamelModel):
    flushed: bool
    post_stt_scheduled: bool
    audio_retention: AudioRetention


# ---------- blind/check (API 명세 4장. 처음에는 이 레포에서 정했다: docs/open-questions.md) ----------
class BlindCheckRequest(CamelModel):
    texts: list[str] = Field(default_factory=list)


class BlindViolationOut(CamelModel):
    topic: str
    label: str
    matched: str
    rule_type: str


class BlindCheckItem(CamelModel):
    index: int
    clean: bool
    violations: list[BlindViolationOut] = Field(default_factory=list)


class BlindCheckResponse(CamelModel):
    clean: bool
    rules_version: str
    results: list[BlindCheckItem]


class HealthResponse(CamelModel):
    status: str = "UP"
    llm: str
    stt: str
