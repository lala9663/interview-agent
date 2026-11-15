"""Provider 인터페이스.

모델을 부르는 코드는 모두 이 인터페이스를 거친다(그래프·방 파이프라인이 httpx나 SDK를 직접 부르지 않는다).
동시 실행 제한(프로세스 전역 세마포어), 제한 시간, 재시도(지수 백오프), 차단기는 여기서 한 번만 구현한다.

차단기: 연속으로 `DOWN_AFTER`번 실패하면(health = DOWN) `cooldown_s` 동안 모델을 부르지 않고 바로 `ProviderDown`을 낸다.
쉬는 시간이 지나면 호출 하나만 시험 삼아 보낸다(재시도 없이). 성공하면 다시 열고, 실패하면 또 쉰다.
죽은 모델을 청크마다 제한 시간까지 기다리며 두드리면 방의 큐만 밀리기 때문이다.
"""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, TypeVar

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage

log = logging.getLogger(__name__)
T = TypeVar("T")


class ProviderError(Exception):
    """재시도를 다 쓰고도 실패했을 때."""


class ProviderBusy(ProviderError):
    """동시 실행 자리를 `queue_timeout_s` 안에 얻지 못했을 때(모델이 밀려 있다). 재시도하지 않는다."""


class ProviderDown(ProviderError):
    """차단기가 열려 있어 모델을 부르지 않았을 때(연속 실패 뒤 쉬는 중). 재시도하지 않는다."""


DOWN_AFTER = 3  # 연속 실패가 이만큼이면 DOWN


@dataclass
class RoleConfig:
    """config/providers.yaml의 역할 하나(llm.fast 등)."""

    role: str
    impl: str
    model: str = ""
    max_concurrency: int = 8
    timeout_s: float = 30.0
    max_retries: int = 2
    backoff_base_s: float = 0.5
    cooldown_s: float = 30.0  # DOWN이 되면 이만큼 호출을 쉰다. 0이면 차단기를 끈다
    options: dict[str, Any] = field(default_factory=dict)


class _Limited:
    """세마포어 + 제한 시간 + 재시도 + 차단기 + 상태 기록."""

    def __init__(self, config: RoleConfig) -> None:
        self.config = config
        self._sem: asyncio.Semaphore | None = None
        self._consecutive_failures = 0
        self._open_until = 0.0  # 차단기가 열려 있는 시각(monotonic). 0이면 닫힘
        self._probing = False  # 쉬는 시간이 끝나 시험 호출이 나가 있다
        self.calls = 0
        self.failures = 0
        self.skipped = 0  # 차단기가 막아 부르지 않은 횟수
        self.waiting = 0
        self.last_latency_s = 0.0

    @property
    def impl(self) -> str:
        return self.config.impl

    @property
    def model(self) -> str:
        return self.config.model or self.config.impl

    @property
    def health(self) -> str:
        """status 이벤트에 싣는 값: OK | DEGRADED | DOWN."""
        if self._consecutive_failures >= DOWN_AFTER:
            return "DOWN"
        if self._consecutive_failures >= 1:
            return "DEGRADED"
        return "OK"

    def _semaphore(self) -> asyncio.Semaphore:
        if self._sem is None:
            self._sem = asyncio.Semaphore(max(1, self.config.max_concurrency))
        return self._sem

    def _enter(self) -> bool:
        """차단기를 지난다. 시험 호출이면 True. 막혀 있으면 ProviderDown."""
        if not self._open_until:
            return False
        if self._probing or time.monotonic() < self._open_until:
            self.skipped += 1
            raise ProviderDown(f"{self.config.role}({self.config.impl}): 연속 실패로 호출을 쉬는 중")
        self._probing = True
        return True

    def _record_failure(self) -> None:
        self.failures += 1
        self._consecutive_failures += 1
        if self.config.cooldown_s > 0 and self._consecutive_failures >= DOWN_AFTER:
            if not self._open_until:
                log.warning("provider %s 차단: %.0f초 쉰 뒤 한 번 시험한다", self.config.role, self.config.cooldown_s)
            self._open_until = time.monotonic() + self.config.cooldown_s

    async def _run(self, call: Callable[[], Awaitable[T]], *, timeout_s: float | None = None,
                   max_retries: int | None = None, queue_timeout_s: float | None = None) -> T:
        probe = self._enter()
        try:
            return await self._attempts(call, timeout_s=timeout_s, queue_timeout_s=queue_timeout_s,
                                        max_retries=0 if probe else max_retries)
        finally:
            if probe:
                self._probing = False

    async def _attempts(self, call: Callable[[], Awaitable[T]], *, timeout_s: float | None,
                        max_retries: int | None, queue_timeout_s: float | None) -> T:
        timeout = timeout_s if timeout_s is not None else self.config.timeout_s
        retries = self.config.max_retries if max_retries is None else max_retries
        last: Exception | None = None
        for attempt in range(retries + 1):
            try:
                self.waiting += 1
                try:
                    if queue_timeout_s is None:
                        await self._semaphore().acquire()
                    else:
                        await asyncio.wait_for(self._semaphore().acquire(), timeout=max(0.0, queue_timeout_s))
                except asyncio.TimeoutError:
                    raise ProviderBusy(f"{self.config.role}: 동시 실행 자리를 얻지 못함") from None
                finally:
                    self.waiting -= 1
                started = time.monotonic()
                try:
                    result = await asyncio.wait_for(call(), timeout=timeout)
                finally:
                    self.last_latency_s = time.monotonic() - started
                    self._semaphore().release()
                self.calls += 1
                if self._open_until:
                    log.warning("provider %s 복구: 다시 부른다", self.config.role)
                self._consecutive_failures = 0
                self._open_until = 0.0
                return result
            except (asyncio.CancelledError, ProviderBusy):
                raise
            except Exception as exc:  # noqa: BLE001 - provider 종류와 무관하게 재시도
                last = exc
                self._record_failure()
                log.warning("provider %s 실패 (%d/%d): %r", self.config.role, attempt + 1, retries + 1, exc)
                if self._open_until:  # 방금 차단됐다: 남은 재시도는 하지 않는다
                    break
                if attempt < retries:
                    await asyncio.sleep(self.config.backoff_base_s * (2 ** attempt))
        raise ProviderError(f"{self.config.role}({self.config.impl}) 호출 실패: {last!r}") from last


class LLMProvider(_Limited, ABC):
    """LLM. 구현은 LangChain ChatModel만 돌려주면 된다."""

    @abstractmethod
    def chat_model(self) -> BaseChatModel:
        """LangChain ChatModel. 그래프는 이것을 `complete()`를 통해 쓴다."""

    async def complete(self, messages: list[BaseMessage], *, timeout_s: float | None = None,
                       max_retries: int | None = None) -> str:
        model = self.chat_model()

        async def call() -> str:
            result = await model.ainvoke(messages)
            content = result.content
            if isinstance(content, list):  # 일부 모델은 조각 목록으로 준다
                content = "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in content)
            return str(content)

        return await self._run(call, timeout_s=timeout_s, max_retries=max_retries)


@dataclass
class SttRequest:
    pcm: bytes  # PCM16 LE 모노
    sample_rate: int = 16000
    language: str = "ko"
    prompt: str | None = None  # 고유명사 힌트(Whisper initial prompt)
    session: str | None = None  # 방 구분(fake가 대본 위치를 기억하는 데 쓴다)


@dataclass
class SttResult:
    text: str
    raw: Any = None


class STTProvider(_Limited, ABC):
    @abstractmethod
    async def _transcribe(self, request: SttRequest) -> SttResult:
        """실제 호출. 제한·재시도는 `transcribe()`가 한다."""

    async def transcribe(self, request: SttRequest, *, timeout_s: float | None = None,
                         max_retries: int | None = None, queue_timeout_s: float | None = None) -> SttResult:
        return await self._run(lambda: self._transcribe(request), timeout_s=timeout_s, max_retries=max_retries,
                               queue_timeout_s=queue_timeout_s)

    def reset_session(self, session: str) -> None:  # noqa: B027 - 필요한 구현만 덮어쓴다
        """방이 끝났을 때 세션별 상태를 지운다."""


class EmbeddingProvider(_Limited, ABC):
    @abstractmethod
    async def _embed(self, texts: list[str]) -> list[list[float]]:
        ...

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return await self._run(lambda: self._embed(texts))
