"""fake LLM: 사내 LLM 없이 전체 흐름을 돌리기 위한 구현.

options:
  latency_ms: 호출마다 기다리는 시간(부하 테스트에서 실제 지연을 흉내 낸다)
  fail_every: N이면 N번째 호출마다 실패(재시도 경로 확인용). 0이면 끔
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from app.providers.base import LLMProvider, RoleConfig
from app.providers.fake_responses import RESPONDERS

_TASK_RE = re.compile(r"<task>\s*([\w.]+)\s*</task>")
_BLOCK_RE = re.compile(r"<([A-Za-z_][\w.-]*)>\n(.*?)\n</\1>", re.DOTALL)


def parse_prompt(messages: list[BaseMessage]) -> tuple[str, dict[str, str]]:
    """(작업 이름, {태그: 내용}). 작업은 system에서, 자료 태그는 그 뒤 메시지에서 읽는다."""
    system = "\n".join(str(m.content) for m in messages if m.type == "system")
    user = "\n".join(str(m.content) for m in messages if m.type != "system")
    m = _TASK_RE.search(system)
    return (m.group(1) if m else ""), {name: body.strip() for name, body in _BLOCK_RE.findall(user)}


def fake_answer(messages: list[BaseMessage]) -> str:
    task, tags = parse_prompt(messages)
    fn = RESPONDERS.get(task)
    if fn is None:
        return f"(fake-llm: 작업 '{task}'의 응답 규칙이 없습니다. app/providers/fake_responses.py에 더하세요.)"
    return fn(tags)


class FakeChatModel(BaseChatModel):
    latency_ms: int = 0
    fail_every: int = 0
    model_name: str = "fake-llm"
    calls: int = 0

    @property
    def _llm_type(self) -> str:
        return "fake-interview-llm"

    def _answer(self, messages: list[BaseMessage]) -> ChatResult:
        self.calls += 1
        if self.fail_every and self.calls % self.fail_every == 0:
            raise RuntimeError("fake-llm: 설정된 실패")
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=fake_answer(messages)))])

    def _generate(self, messages: list[BaseMessage], stop: list[str] | None = None,
                  run_manager: CallbackManagerForLLMRun | None = None, **kwargs: Any) -> ChatResult:
        return self._answer(messages)

    async def _agenerate(self, messages: list[BaseMessage], stop: list[str] | None = None,
                         run_manager: AsyncCallbackManagerForLLMRun | None = None, **kwargs: Any) -> ChatResult:
        if self.latency_ms:
            await asyncio.sleep(self.latency_ms / 1000)
        return self._answer(messages)


class FakeLLMProvider(LLMProvider):
    def __init__(self, config: RoleConfig) -> None:
        super().__init__(config)
        self._model = FakeChatModel(
            latency_ms=int(config.options.get("latency_ms", 0)),
            fail_every=int(config.options.get("fail_every", 0)),
            model_name=config.model or "fake-llm",
        )

    def chat_model(self) -> BaseChatModel:
        return self._model
