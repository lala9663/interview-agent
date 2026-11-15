"""AI 참고 평가 프롬프트."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import BaseMessage

from app.prompts.common import FAIRNESS, JSON_ONLY, build_messages, feedback_block, small_talk_rule, tag, transcript_block

TASK = "evaluation"
VERSION = "v2"  # v2: 인성 면접이 아니면 사담은 근거로 쓰지 않는다

_INSTRUCTIONS = f"""
너는 면접 평가를 돕는다. 결과는 면접관이 참고만 하는 자료이며 합격·불합격을 정하지 않는다.
<transcript>(면접 대화)를 읽고 <criteria>의 항목마다 점수를 매긴다.
- 점수는 1부터 각 항목의 maxScore까지의 정수다.
- 항목마다 근거가 된 지원자 발화의 번호(대괄호 안의 seq)를 evidenceSeqs에 적는다. 면접관 발화는 근거가 아니다.
- 대화에 근거가 없는 항목은 점수를 내지 않는다: score를 null로, evidenceSeqs를 빈 배열로 둔다. 짐작으로 채우지 않는다.
- rationale은 2~3문장으로, 지원자가 실제로 한 말에 기대어 쓴다. 말투·억양·말의 유창함만으로 평가하지 않는다.
- {FAIRNESS}
{JSON_ONLY}
형식: {{"scores": [{{"criterionCode": "", "score": 3, "rationale": "", "evidenceSeqs": [1]}}], "overallComment": ""}}
"""


def build(*, track: str, interview_type: str, job_title: str, jd_text: str, criteria: list[dict[str, Any]],
          transcript: list[dict[str, Any]], briefing_summary: dict[str, Any] | None,
          violations: list[str] | None = None) -> list[BaseMessage]:
    return build_messages(
        TASK, VERSION,
        _INSTRUCTIONS + small_talk_rule(interview_type, "그런 발화는 점수의 근거(evidenceSeqs)로 쓰지 않는다."),
        tag("track", track), tag("interview_type", interview_type), tag("job_title", job_title),
        tag("jd", jd_text), tag("briefing_summary", briefing_summary or {}), tag("criteria", criteria),
        tag("transcript", transcript_block(transcript)), feedback_block(violations or []),
    )
