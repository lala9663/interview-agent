"""OpenAI 형식 API를 쓰는 LLM (사내 LLM이 이 형식이라고 가정: docs/open-questions.md).

options:
  base_url: 예) http://llm.internal/v1   (필수)
  api_key_env: API 키가 들어 있는 환경변수 이름(기본 LLM_API_KEY). 키가 없으면 "EMPTY"를 보낸다
  headers: 요청마다 붙일 헤더(dict)
  temperature, max_tokens: 그대로 전달
  extra_body: 사내 게이트웨이가 요구하는 추가 본문(dict)

형식이 다르면 이 파일만 고친다(ChatOpenAI 대신 BaseChatModel을 직접 구현).
"""

from __future__ import annotations

import os

from langchain_core.language_models.chat_models import BaseChatModel

from app.providers.base import LLMProvider, RoleConfig


class OpenAICompatibleLLMProvider(LLMProvider):
    def __init__(self, config: RoleConfig) -> None:
        super().__init__(config)
        opts = config.options
        if not opts.get("base_url"):
            raise ValueError(f"{config.role}: options.base_url 이 필요하다.")
        if not config.model:
            raise ValueError(f"{config.role}: model 이 필요하다.")
        self._model: BaseChatModel | None = None

    def chat_model(self) -> BaseChatModel:
        if self._model is None:
            from langchain_openai import ChatOpenAI  # 쓰는 경우에만 불러온다

            opts = self.config.options
            kwargs: dict = {
                "model": self.config.model,
                "base_url": opts["base_url"],
                "api_key": os.environ.get(opts.get("api_key_env", "LLM_API_KEY"), "") or "EMPTY",
                "default_headers": dict(opts.get("headers") or {}) or None,
                "temperature": float(opts.get("temperature", 0.2)),
                "timeout": self.config.timeout_s,
                "max_retries": 0,  # 재시도는 app/providers/base.py가 한다
            }
            if opts.get("max_tokens"):
                kwargs["max_tokens"] = int(opts["max_tokens"])
            if opts.get("extra_body"):
                kwargs["extra_body"] = dict(opts["extra_body"])
            self._model = ChatOpenAI(**kwargs)
        return self._model
