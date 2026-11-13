"""구조화 출력: LLM 글에서 JSON을 꺼내 pydantic으로 검증하고, 실패하면 한 번 다시 시킨다.

사내 LLM이 tool calling·JSON mode를 지원하는지 모르므로 프롬프트 + 검증 방식만 쓴다(모델에 기대지 않는다).
"""

from __future__ import annotations

import json
import logging
from typing import TypeVar

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from pydantic import BaseModel, ValidationError

from app.providers.base import LLMProvider

log = logging.getLogger(__name__)
M = TypeVar("M", bound=BaseModel)


class StructuredOutputError(Exception):
    """재시도 뒤에도 형식이 맞지 않을 때."""


def extract_json(text: str) -> object:
    """글에서 첫 JSON 객체(또는 배열)를 꺼낸다. 코드 블록 표시나 앞뒤 설명이 붙어 있어도 된다."""
    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch in "{[":
            try:
                value, _ = decoder.raw_decode(text[i:])
                return value
            except json.JSONDecodeError:
                continue
    raise ValueError("JSON을 찾지 못했다.")


async def call_structured(llm: LLMProvider, messages: list[BaseMessage], schema: type[M], *,
                          retries: int = 1, timeout_s: float | None = None) -> M:
    convo = list(messages)
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        text = await llm.complete(convo, timeout_s=timeout_s)
        try:
            return schema.model_validate(extract_json(text))
        except (ValidationError, ValueError) as exc:
            last_error = exc
            log.warning("구조화 출력 검증 실패 (%s, %d번째): %s", schema.__name__, attempt + 1, str(exc)[:200])
            convo = list(messages) + [
                AIMessage(content=text),
                HumanMessage(content=f"앞의 출력은 형식에 맞지 않았다({str(exc)[:300]}). 지정한 형식의 JSON 하나만 다시 써라."),
            ]
    raise StructuredOutputError(f"{schema.__name__} 형식 검증 실패: {last_error}")
