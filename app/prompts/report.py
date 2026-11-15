"""면접 후 보고서 프롬프트. BRIEF는 한 번, DETAILED는 단계별로 부른다."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import BaseMessage

from app.prompts.common import FAIRNESS, JSON_ONLY, build_messages, small_talk_rule, tag, transcript_block

BRIEF_TASK = "report.brief"
OVERVIEW_TASK = "report.detailed.overview"
COMPETENCY_TASK = "report.detailed.competencies"
CAREER_TASK = "report.detailed.career"
NEXT_TASK = "report.detailed.next"
VERSION = "v2"  # v2: 인성 면접이 아니면 사담은 보고서에 옮기지 않는다

_COMMON = f"""
너는 면접 보고서를 쓴다. 읽는 사람은 채용 담당자와 다음 차수 면접관이다.
- <transcript>(면접 대화)에 있는 내용만 쓴다. 대괄호 안의 숫자는 발화 번호(seq)다.
- 평가는 면접관의 몫이다. 단정하지 말고 관찰한 사실과 근거를 쓴다.
- {FAIRNESS}
{JSON_ONLY}
"""

_OVERVIEW_FORMAT = (
    '형식: {"headline": "", "overallComment": "", "strengths": [""], "concerns": [""], '
    '"qaHighlights": [{"question": "", "answerSummary": "", "seqs": [1, 2]}]}'
)

_BRIEF = _COMMON + f"""
짧은 보고서(BRIEF)를 쓴다. headline은 한 줄 총평, overallComment는 3~5문장, strengths와 concerns는 각각 2~4개,
qaHighlights는 중요한 문답 3~5개(질문, 답 요약, 해당 seq)다.
{_OVERVIEW_FORMAT}
"""

_OVERVIEW = _COMMON + f"""
상세 보고서(DETAILED)의 총평 부분을 쓴다. overallComment는 6~10문장, qaHighlights는 중요한 문답 5~8개다.
{_OVERVIEW_FORMAT}
"""

_COMPETENCY = _COMMON + """
상세 보고서의 역량별 분석을 쓴다. JD와 <ai_evaluation>의 평가 항목을 기준으로 역량 3~6개를 고르고,
역량마다 assessment(관찰한 수준, 2~4문장)와 evidence(근거가 된 지원자 말을 짧게 옮긴 것, seq 표시)를 쓴다.
형식: {"competencies": [{"name": "", "assessment": "", "evidence": ["[12] ..."]}]}
"""

_CAREER = _COMMON + """
상세 보고서의 이력 검증을 쓴다. <briefing_summary>(이력서 요약)의 주장마다 면접에서 무엇이 확인됐는지 적는다.
claim은 이력서의 주장, finding은 면접에서 확인된 내용이다. 면접에서 다루지 않은 주장은 finding에 "면접에서 다루지 않음"이라고 쓴다.
형식: {"careerChecks": [{"claim": "", "finding": ""}]}
"""

_NEXT = _COMMON + """
상세 보고서의 마지막 부분을 쓴다. nextRoundChecks는 다음 차수에서 확인할 점(이번에 답이 얕았거나 다루지 못한 것),
risks는 채용 결정에서 살펴야 할 위험이다. <previous_rounds>에서 넘어온 확인점이 이번에 해소됐는지도 반영한다.
형식: {"nextRoundChecks": [""], "risks": [""]}
"""


def _with_rule(instructions: str, ctx: dict[str, Any]) -> str:
    return instructions + small_talk_rule(ctx.get("interview_type", ""), "그런 발화는 문답·강점·우려·근거로 옮기지 않는다.")


def _base_parts(ctx: dict[str, Any]) -> list[str]:
    return [
        tag("track", ctx.get("track", "")), tag("interview_type", ctx.get("interview_type", "")),
        tag("basis", ctx.get("basis", "")), tag("job_title", ctx.get("job_title", "")),
        tag("jd", ctx.get("jd_text", "")), tag("briefing_summary", ctx.get("briefing_summary") or {}),
        tag("ai_evaluation", ctx.get("ai_evaluation") or {}),
        tag("previous_rounds", ctx.get("previous_rounds") or []),
        tag("transcript", transcript_block(ctx.get("transcript") or [])),
    ]


def build_brief(ctx: dict[str, Any]) -> list[BaseMessage]:
    return build_messages(BRIEF_TASK, VERSION, _with_rule(_BRIEF, ctx), *_base_parts(ctx))


def build_overview(ctx: dict[str, Any]) -> list[BaseMessage]:
    return build_messages(OVERVIEW_TASK, VERSION, _with_rule(_OVERVIEW, ctx), *_base_parts(ctx))


def build_competencies(ctx: dict[str, Any]) -> list[BaseMessage]:
    return build_messages(COMPETENCY_TASK, VERSION, _with_rule(_COMPETENCY, ctx), *_base_parts(ctx))


def build_career(ctx: dict[str, Any]) -> list[BaseMessage]:
    return build_messages(CAREER_TASK, VERSION, _with_rule(_CAREER, ctx), *_base_parts(ctx))


def build_next(ctx: dict[str, Any], overview: dict[str, Any]) -> list[BaseMessage]:
    return build_messages(NEXT_TASK, VERSION, _with_rule(_NEXT, ctx), *_base_parts(ctx), tag("overview", overview))
