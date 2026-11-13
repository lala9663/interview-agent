"""사전 리포트(브리핑) 프롬프트: 요약, JD 기반 질문, 이력서 기반 질문."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import BaseMessage

from app.prompts.common import FAIRNESS, JSON_ONLY, build_messages, feedback_block, tag

SUMMARY_TASK = "briefing.summary"
SUMMARY_VERSION = "v1"
JD_TASK = "briefing.jd_questions"
JD_VERSION = "v1"
RESUME_TASK = "briefing.resume_questions"
RESUME_VERSION = "v1"

_SUMMARY = f"""
너는 면접관을 돕는 채용 보조자다. 지원자 자료와 직무 기술서(JD)를 읽고, 면접관이 면접 직전에 1분 안에 읽을 요약을 쓴다.
- 면접관의 눈으로 쓴다: 무엇을 했는지, JD와 어디가 맞닿는지, 면접에서 확인해야 할 점이 무엇인지.
- 자료에 없는 사실을 지어내지 않는다. 확인이 안 되는 것은 checkPoints에 "확인 필요"로 적는다.
- {FAIRNESS}
- headline은 한 문장, keyPoints 3~5개, strengths 2~4개, checkPoints 2~4개, timeline은 시기가 드러난 활동만.
{JSON_ONLY}
형식: {{"headline": "", "keyPoints": [""], "strengths": [""], "checkPoints": [""], "timeline": [{{"period": "", "text": ""}}]}}
"""

_JD = f"""
너는 면접 질문을 준비하는 채용 보조자다. 직무 기술서(JD)에서 이 직무에 꼭 필요한 역량을 뽑아, 그것을 확인하는 질문을 만든다.
- 질문 수는 <count>에 적힌 만큼. 한 질문은 한 가지만 묻는다. 예/아니오로 끝나지 않게 묻는다.
- <question_bank>에 알맞은 질문이 있으면 그대로 써도 된다. 그때는 source를 "BANK"로, 새로 만들면 "JD"로 적는다.
- <track>이 NEW_GRAD면 기초와 학습 태도 위주, EXPERIENCED면 실제 경험과 판단 근거 위주로 묻는다.
- {FAIRNESS}
{JSON_ONLY}
형식: {{"questions": [{{"source": "JD", "category": "", "text": "", "intent": ""}}]}}
"""

_RESUME = f"""
너는 면접 질문을 준비하는 채용 보조자다. 지원자 자료(학력·과목·자기소개서·프로젝트·경력)를 읽고, 본인이 직접 한 일과 깊이를 확인하는 질문을 만든다.
- 질문 수는 <count>에 적힌 만큼. 자료의 구체적인 내용(프로젝트 이름, 맡은 역할, 수치)을 짚어서 묻는다.
- 자료에 없는 내용을 전제로 묻지 않는다. intent에는 무엇을 확인하려는지 짧게 적는다.
- {FAIRNESS}
{JSON_ONLY}
형식: {{"questions": [{{"source": "RESUME", "category": "", "text": "", "intent": ""}}]}}
"""


def build_summary(*, track: str, interview_type: str, job_title: str, jd_text: str, profile: dict[str, Any],
                  violations: list[str] | None = None) -> list[BaseMessage]:
    return build_messages(
        SUMMARY_TASK, SUMMARY_VERSION, _SUMMARY,
        tag("track", track), tag("interview_type", interview_type), tag("job_title", job_title),
        tag("jd", jd_text), tag("profile", profile), feedback_block(violations or []),
    )


def build_jd_questions(*, track: str, interview_type: str, job_title: str, jd_text: str,
                       question_bank: list[dict[str, Any]], count: int,
                       violations: list[str] | None = None) -> list[BaseMessage]:
    return build_messages(
        JD_TASK, JD_VERSION, _JD,
        tag("track", track), tag("interview_type", interview_type), tag("job_title", job_title),
        tag("jd", jd_text), tag("question_bank", question_bank), tag("count", str(count)),
        feedback_block(violations or []),
    )


def build_resume_questions(*, track: str, interview_type: str, job_title: str, profile: dict[str, Any],
                           summary: dict[str, Any], count: int,
                           violations: list[str] | None = None) -> list[BaseMessage]:
    return build_messages(
        RESUME_TASK, RESUME_VERSION, _RESUME,
        tag("track", track), tag("interview_type", interview_type), tag("job_title", job_title),
        tag("profile", profile), tag("summary", summary), tag("count", str(count)),
        feedback_block(violations or []),
    )
