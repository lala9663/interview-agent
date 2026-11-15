"""면접 중 프롬프트: 전사 다듬기, 화자 판정, 꼬리질문. 모두 llm.fast 역할로 부른다."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import BaseMessage

from app.prompts.common import FAIRNESS, JSON_ONLY, SMALL_TALK, build_messages, tag, transcript_block

REFINE_TASK = "refine"
REFINE_VERSION = "v1"
SPEAKER_TASK = "speaker.judge"
SPEAKER_VERSION = "v1"
FOLLOWUP_TASK = "followup"
FOLLOWUP_VERSION = "v2"  # v2: 사담이면 제안하지 않는다

_REFINE = """
너는 면접 음성 전사를 읽기 좋게 다듬는다. <utterance>는 음성 인식 결과 그대로다.
- 뜻을 바꾸지 않는다. 내용을 더하거나 요약하지 않는다.
- 군말(어, 음, 그, 저기, 뭐랄까)과 같은 말의 반복, 말을 고쳐 한 부분의 앞쪽을 지운다.
- 고유명사, 숫자, 기술 용어는 들린 대로 둔다. <glossary>에 있는 낱말로 잘못 들린 것이 분명하면 그 낱말로 고친다.
- 띄어쓰기와 문장 부호를 바로잡는다. 말투(존댓말)는 그대로 둔다.
출력은 다듬은 문장만 쓴다. 설명이나 따옴표를 붙이지 않는다.
"""

_SPEAKER = f"""
면접 전사에서 <utterance>를 말한 사람이 면접관(INTERVIEWER)인지 지원자(CANDIDATE)인지 판정한다.
- <recent>는 바로 앞의 대화다. 흐름(질문 뒤에는 답이 온다)과 말의 내용(묻는 말인가, 자기 경험을 말하는가)으로 판단한다.
- 지원자도 되물을 수 있고 면접관도 설명할 수 있다. 확실하지 않으면 UNKNOWN으로 하고 confidence를 낮게 준다.
{JSON_ONLY}
형식: {{"speaker": "INTERVIEWER|CANDIDATE|UNKNOWN", "confidence": 0.0}}
"""

_FOLLOWUP = f"""
너는 면접관 옆에서 꼬리질문을 제안한다. <qa>는 방금까지의 질문과 답이다. 마지막 지원자 답을 더 깊이 확인하는 질문을 2~3개 만든다.
- 답에서 나온 구체적인 말(수치, 역할, 선택의 이유, 어려웠던 점)을 짚는다. 답에 없는 내용을 전제로 하지 않는다.
- <asked>에 있는 질문과 같은 것을 다시 묻지 않는다. 서로 다른 각도의 질문을 낸다.
- 한 질문은 한 문장, 한 가지만 묻는다. rationale에는 왜 묻는지 짧게 적는다.
- {SMALL_TALK} 마지막 지원자 말이 그런 말이면 질문을 만들지 않고 items를 빈 배열로 둔다.
- {FAIRNESS}
{JSON_ONLY}
형식: {{"items": [{{"text": "", "rationale": ""}}]}}
"""


def build_refine(*, utterance: str, glossary: list[str] | None = None) -> list[BaseMessage]:
    return build_messages(REFINE_TASK, REFINE_VERSION, _REFINE,
                          tag("glossary", ", ".join(glossary or [])), tag("utterance", utterance))


def build_speaker(*, utterance: str, recent: list[dict[str, Any]]) -> list[BaseMessage]:
    return build_messages(SPEAKER_TASK, SPEAKER_VERSION, _SPEAKER,
                          tag("recent", transcript_block(recent)), tag("utterance", utterance))


def build_followup(*, qa: list[dict[str, Any]], candidate_brief: str, asked: list[str],
                   job_title: str = "") -> list[BaseMessage]:
    return build_messages(
        FOLLOWUP_TASK, FOLLOWUP_VERSION, _FOLLOWUP,
        tag("job_title", job_title), tag("candidate_brief", candidate_brief),
        tag("asked", "\n".join(f"- {q}" for q in asked)), tag("qa", transcript_block(qa)),
    )
