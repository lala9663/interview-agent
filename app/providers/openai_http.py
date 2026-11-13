"""OpenAI 형식 `/chat/completions`를 httpx로 직접 부르는 LLM (openai SDK 없이).

`openai_compatible`(langchain-openai의 ChatOpenAI)와 같은 일을 한다. 이쪽을 쓰는 경우:
- openai SDK(네이티브 모듈 jiter 포함)를 반입·실행할 수 없는 환경
- 사내 게이트웨이의 경로·본문·응답이 OpenAI 형식에서 조금 벗어나 SDK가 받아들이지 못할 때
  (그때는 이 파일의 `_payload` / `_extract` 만 고친다)

options:
  base_url (필수), path(기본 /chat/completions)
  api_key_env(기본 LLM_API_KEY), auth_header(기본 Authorization), auth_prefix(기본 "Bearer ")
  headers, temperature, max_tokens, extra_body, verify_ssl
  response_text_path: 응답에서 글자가 있는 위치(기본 choices.0.message.content)
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import ConfigDict

from app.providers.base import LLMProvider, RoleConfig

_ROLES = {"system": "system", "human": "user", "ai": "assistant"}


class OpenAIHttpChatModel(BaseChatModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    model_name: str
    url: str
    headers: dict[str, str] = {}
    temperature: float = 0.2
    max_tokens: int | None = None
    extra_body: dict[str, Any] = {}
    response_text_path: str = "choices.0.message.content"
    timeout_s: float = 30.0
    verify_ssl: Any = True
    transport: Any = None  # 테스트에서 httpx.MockTransport를 넣는다

    @property
    def _llm_type(self) -> str:
        return "openai-http"

    def _payload(self, messages: list[BaseMessage]) -> dict[str, Any]:
        """요청 본문. 규격이 다르면 여기를 고친다."""
        body: dict[str, Any] = {
            "model": self.model_name,
            "messages": [{"role": _ROLES.get(m.type, "user"), "content": str(m.content)} for m in messages],
            "temperature": self.temperature,
            "stream": False,
        }
        if self.max_tokens:
            body["max_tokens"] = self.max_tokens
        body.update(self.extra_body)
        return body

    def _extract(self, data: Any) -> str:
        """응답에서 글자를 꺼낸다. 규격이 다르면 여기를 고친다."""
        node = data
        for key in self.response_text_path.split("."):
            node = node[int(key)] if isinstance(node, list) else node[key]
        return str(node or "")

    def _result(self, data: Any) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=self._extract(data)))])

    def _generate(self, messages: list[BaseMessage], stop: list[str] | None = None,
                  run_manager: CallbackManagerForLLMRun | None = None, **kwargs: Any) -> ChatResult:
        with httpx.Client(timeout=self.timeout_s, verify=self.verify_ssl) as client:
            response = client.post(self.url, json=self._payload(messages), headers=self.headers)
            response.raise_for_status()
            return self._result(response.json())

    async def _agenerate(self, messages: list[BaseMessage], stop: list[str] | None = None,
                         run_manager: AsyncCallbackManagerForLLMRun | None = None, **kwargs: Any) -> ChatResult:
        async with httpx.AsyncClient(timeout=self.timeout_s, verify=self.verify_ssl,
                                     transport=self.transport) as client:
            response = await client.post(self.url, json=self._payload(messages), headers=self.headers)
            response.raise_for_status()
            return self._result(response.json())


class OpenAIHttpLLMProvider(LLMProvider):
    def __init__(self, config: RoleConfig) -> None:
        super().__init__(config)
        o = config.options
        if not o.get("base_url") or not config.model:
            raise ValueError(f"{config.role}: model 과 options.base_url 이 필요하다.")
        headers = {str(k): str(v) for k, v in (o.get("headers") or {}).items()}
        key = os.environ.get(o.get("api_key_env", "LLM_API_KEY"), "")
        if key:
            headers[o.get("auth_header", "Authorization")] = f"{o.get('auth_prefix', 'Bearer ')}{key}"
        self._model = OpenAIHttpChatModel(
            model_name=config.model,
            url=str(o["base_url"]).rstrip("/") + str(o.get("path", "/chat/completions")),
            headers=headers, temperature=float(o.get("temperature", 0.2)),
            max_tokens=int(o["max_tokens"]) if o.get("max_tokens") else None,
            extra_body=dict(o.get("extra_body") or {}),
            response_text_path=str(o.get("response_text_path", "choices.0.message.content")),
            timeout_s=config.timeout_s, verify_ssl=o.get("verify_ssl", True),
        )

    def chat_model(self) -> BaseChatModel:
        return self._model
