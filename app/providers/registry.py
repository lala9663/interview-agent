"""Provider 레지스트리: impl 이름 → 클래스, 그리고 providers.yaml + 환경변수 읽기.

새 구현을 넣을 때 고치는 곳은 (1) 새 파일 하나, (2) 아래 표의 한 줄, (3) config/providers.yaml 이다.
절차는 .opencode/skills/add-provider/SKILL.md.
"""

from __future__ import annotations

import importlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from app.providers.base import EmbeddingProvider, LLMProvider, RoleConfig, STTProvider

# impl 이름 → "모듈:클래스". 문자열이라 쓰지 않는 구현의 의존성은 불러오지 않는다.
LLM_IMPLS: dict[str, str] = {
    "fake": "app.providers.fake_llm:FakeLLMProvider",
    "openai_compatible": "app.providers.openai_compatible:OpenAICompatibleLLMProvider",
    "openai_http": "app.providers.openai_http:OpenAIHttpLLMProvider",  # openai SDK 없이 httpx로 직접
}
STT_IMPLS: dict[str, str] = {
    "fake": "app.providers.fake_stt:FakeSTTProvider",
    "whisper_http": "app.providers.whisper_http:WhisperHttpSTTProvider",
}
EMBEDDING_IMPLS: dict[str, str] = {
    "fake": "app.providers.embedding:HashingEmbeddingProvider",
    "openai_compatible": "app.providers.embedding:OpenAICompatibleEmbeddingProvider",
}

ENV_PREFIX = "PROVIDERS__"
_ROLE_KEYS = {"impl", "model", "max_concurrency", "timeout_s", "max_retries", "backoff_base_s", "options"}


def _load_class(table: dict[str, str], impl: str, kind: str) -> type:
    if impl not in table:
        raise ValueError(f"알 수 없는 {kind} impl '{impl}'. 등록된 것: {sorted(table)}")
    module_name, class_name = table[impl].split(":")
    return getattr(importlib.import_module(module_name), class_name)


def register(kind: str, impl: str, target: str) -> None:
    """실행 중에 구현을 더한다(테스트·플러그인용). target은 "모듈:클래스"."""
    {"llm": LLM_IMPLS, "stt": STT_IMPLS, "embedding": EMBEDDING_IMPLS}[kind][impl] = target


def _set_path(tree: dict[str, Any], path: list[str], value: Any) -> None:
    node = tree
    for key in path[:-1]:
        nxt = node.get(key)
        if not isinstance(nxt, dict):
            nxt = {}
            node[key] = nxt
        node = nxt
    node[path[-1]] = value


def load_provider_config(path: str | Path, env: dict[str, str] | None = None) -> dict[str, Any]:
    """YAML을 읽고 `PROVIDERS__LLM__FAST__MODEL=...` 같은 환경변수로 덮어쓴다.

    환경변수 이름은 `__`로 경로를 나누고 소문자로 바꾼다. 값은 YAML 규칙으로 읽는다(숫자·불리언 인식).
    """
    data: dict[str, Any] = {}
    p = Path(path)
    if p.exists():
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    for name, raw in (env if env is not None else os.environ).items():
        if not name.startswith(ENV_PREFIX):
            continue
        parts = [s.lower() for s in name[len(ENV_PREFIX):].split("__") if s]
        if len(parts) >= 3:
            _set_path(data, parts, yaml.safe_load(raw))
    return data


def role_config(data: dict[str, Any], kind: str, role: str) -> RoleConfig:
    section = (data.get(kind) or {}).get(role) or {}
    unknown = set(section) - _ROLE_KEYS
    if unknown:
        raise ValueError(f"providers.yaml {kind}.{role}에 모르는 키: {sorted(unknown)}")
    return RoleConfig(
        role=f"{kind}.{role}",
        impl=str(section.get("impl", "fake")),
        model=str(section.get("model", "")),
        max_concurrency=int(section.get("max_concurrency", 8)),
        timeout_s=float(section.get("timeout_s", 30.0)),
        max_retries=int(section.get("max_retries", 2)),
        backoff_base_s=float(section.get("backoff_base_s", 0.5)),
        options=dict(section.get("options") or {}),
    )


@dataclass
class ProviderSet:
    llm_fast: LLMProvider
    llm_quality: LLMProvider
    stt_live: STTProvider
    stt_post: STTProvider
    embedding: EmbeddingProvider | None = None

    def llm(self, role: str) -> LLMProvider:
        return {"fast": self.llm_fast, "quality": self.llm_quality}[role]


def build_llm(config: RoleConfig) -> LLMProvider:
    return _load_class(LLM_IMPLS, config.impl, "llm")(config)


def build_stt(config: RoleConfig) -> STTProvider:
    return _load_class(STT_IMPLS, config.impl, "stt")(config)


def build_embedding(config: RoleConfig) -> EmbeddingProvider | None:
    if config.impl in ("none", ""):
        return None
    return _load_class(EMBEDDING_IMPLS, config.impl, "embedding")(config)


def build_providers(path: str | Path, env: dict[str, str] | None = None) -> ProviderSet:
    data = load_provider_config(path, env)
    emb_section = (data.get("embedding") or {}).get("default") or {"impl": "none"}
    data.setdefault("embedding", {})["default"] = emb_section
    emb_cfg = role_config(data, "embedding", "default") if emb_section.get("impl", "none") != "none" else None
    return ProviderSet(
        llm_fast=build_llm(role_config(data, "llm", "fast")),
        llm_quality=build_llm(role_config(data, "llm", "quality")),
        stt_live=build_stt(role_config(data, "stt", "live")),
        stt_post=build_stt(role_config(data, "stt", "post")),
        embedding=build_embedding(emb_cfg) if emb_cfg else None,
    )
