"""테스트 공통 준비물. 모든 테스트는 fake provider·fake backend·메모리 상태로 돈다(외부 연결 없음)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from app.blind.filter import BlindFilter  # noqa: E402
from app.blind.rules import BlindRules  # noqa: E402
from app.container import Container  # noqa: E402
from app.main import create_app  # noqa: E402
from app.providers.base import LLMProvider, RoleConfig  # noqa: E402
from app.providers.registry import ProviderSet, build_providers  # noqa: E402
from app.settings import AudioSettings, BackendSettings, LiveSettings, Settings  # noqa: E402

INTERNAL = {"X-Internal-Key": "dev-internal-key"}


class ScriptedChatModel(BaseChatModel):
    """정해 둔 응답을 차례로 돌려주는 모델. 응답이 떨어지면 fake 규칙으로 답한다."""

    responses: list[str] = []
    seen: list[list[BaseMessage]] = []

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def _generate(self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None,
                  **kwargs: Any) -> ChatResult:
        from app.providers.fake_llm import fake_answer

        self.seen.append(messages)
        text = self.responses.pop(0) if self.responses else fake_answer(messages)
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=text))])


class ScriptedLLM(LLMProvider):
    def __init__(self, responses: list[str]) -> None:
        super().__init__(RoleConfig(role="llm.test", impl="scripted", model="scripted-llm", max_retries=0))
        self.chat = ScriptedChatModel(responses=list(responses), seen=[])

    def chat_model(self) -> BaseChatModel:
        return self.chat


@pytest.fixture
def providers() -> ProviderSet:
    return build_providers(ROOT / "config" / "providers.yaml", env={})


@pytest.fixture
def blind() -> BlindFilter:
    return BlindFilter(BlindRules.load(ROOT / "config" / "blind_rules.yaml"))


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        backend=BackendSettings(mode="fake", retry_base_delay_s=0.01),
        live=LiveSettings(followup_debounce_s=0.05, status_interval_s=0.5),
        audio=AudioSettings(min_silence_ms=400, file_dir=str(tmp_path / "audio")),
    )


@pytest.fixture
def container(settings: Settings, providers: ProviderSet) -> Container:
    return Container(settings, providers=providers)


@pytest.fixture
def client(container: Container):
    with TestClient(create_app(container=container)) as c:
        yield c
