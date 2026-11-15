"""provider 레지스트리, 설정 교체, 제한·재시도, HTTP 어댑터의 요청 모양."""

from __future__ import annotations

import json

import httpx
import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from app.audio.wav import wav_to_pcm16
from app.providers import registry
from app.providers.base import ProviderError, RoleConfig, SttRequest
from app.providers.fake_llm import FakeLLMProvider, parse_prompt
from app.providers.fake_stt import FakeSTTProvider
from app.providers.whisper_http import WhisperHttpSTTProvider
from app.prompts import live as live_prompts
from tests.conftest import ROOT

YAML = ROOT / "config" / "providers.yaml"


def test_default_config_is_all_fake():
    p = registry.build_providers(YAML, env={})
    assert (p.llm_fast.impl, p.llm_quality.impl, p.stt_live.impl, p.stt_post.impl) == ("fake",) * 4
    assert p.embedding is None
    assert p.llm_fast.config.max_concurrency == 16 and p.stt_post.config.max_concurrency == 2


def test_env_overrides_yaml(monkeypatch):
    env = {
        "PROVIDERS__LLM__FAST__IMPL": "openai_compatible",
        "PROVIDERS__LLM__FAST__MODEL": "inhouse-chat",
        "PROVIDERS__LLM__FAST__OPTIONS__BASE_URL": "http://llm.internal/v1",
        "PROVIDERS__LLM__FAST__MAX_CONCURRENCY": "3",
        "PROVIDERS__STT__LIVE__IMPL": "whisper_http",
        "PROVIDERS__STT__LIVE__OPTIONS__BASE_URL": "http://hub.internal",
        "PROVIDERS__EMBEDDING__DEFAULT__IMPL": "fake",
        "UNRELATED": "x",
    }
    p = registry.build_providers(YAML, env=env)
    assert p.llm_fast.impl == "openai_compatible" and p.llm_fast.model == "inhouse-chat"
    assert p.llm_fast.config.max_concurrency == 3  # 숫자로 읽힌다
    assert p.llm_quality.impl == "fake"  # 다른 역할은 그대로
    assert isinstance(p.stt_live, WhisperHttpSTTProvider)
    assert p.embedding is not None


def test_openai_compatible_wraps_chat_openai(monkeypatch):
    p = registry.build_providers(YAML, env={
        "PROVIDERS__LLM__FAST__IMPL": "openai_compatible", "PROVIDERS__LLM__FAST__MODEL": "inhouse-chat",
        "PROVIDERS__LLM__FAST__OPTIONS__BASE_URL": "http://llm.internal/v1"})
    monkeypatch.setenv("LLM_API_KEY", "k")
    try:
        chat = p.llm_fast.chat_model()  # 네트워크 호출 없음
    except ImportError as exc:  # 이 PC의 실행 정책이 openai SDK의 네이티브 모듈(jiter)을 막는 경우
        pytest.skip(f"openai SDK를 불러올 수 없는 환경: {exc}")
    assert type(chat).__name__ == "ChatOpenAI" and "llm.internal" in str(chat.openai_api_base)


async def test_openai_http_calls_chat_completions(monkeypatch):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"], seen["auth"], seen["dept"] = str(request.url), request.headers["authorization"], request.headers["x-dept"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "다듬은 문장."}}]})

    monkeypatch.setenv("LLM_API_KEY", "k1")
    p = registry.build_providers(YAML, env={
        "PROVIDERS__LLM__FAST__IMPL": "openai_http", "PROVIDERS__LLM__FAST__MODEL": "inhouse-chat",
        "PROVIDERS__LLM__FAST__OPTIONS__BASE_URL": "http://llm.internal/v1",
        "PROVIDERS__LLM__FAST__OPTIONS__HEADERS__X-DEPT": "people",
        "PROVIDERS__LLM__FAST__OPTIONS__MAX_TOKENS": "256"})
    p.llm_fast.chat_model().transport = httpx.MockTransport(handler)
    text = await p.llm_fast.complete(live_prompts.build_refine(utterance="어 네 맞습니다"))
    assert text == "다듬은 문장."
    assert seen["url"] == "http://llm.internal/v1/chat/completions" and seen["auth"] == "Bearer k1"
    assert seen["dept"] == "people"
    body = seen["body"]
    assert body["model"] == "inhouse-chat" and body["max_tokens"] == 256 and body["stream"] is False
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    assert "<task>refine</task>" in body["messages"][0]["content"]


def test_unknown_impl_and_unknown_key_fail_loudly(tmp_path):
    with pytest.raises(ValueError, match="알 수 없는 llm impl"):
        registry.build_providers(YAML, env={"PROVIDERS__LLM__FAST__IMPL": "nope"})
    bad = tmp_path / "p.yaml"
    bad.write_text("llm:\n  fast: {impl: fake, modle: typo}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="모르는 키"):
        registry.build_providers(bad, env={})


def test_register_custom_impl():
    registry.register("stt", "custom_for_test", "app.providers.fake_stt:FakeSTTProvider")
    try:
        p = registry.build_providers(YAML, env={"PROVIDERS__STT__POST__IMPL": "custom_for_test"})
        assert isinstance(p.stt_post, FakeSTTProvider)
    finally:
        registry.STT_IMPLS.pop("custom_for_test")


async def test_fake_llm_answers_by_task_tag_and_is_deterministic():
    llm = FakeLLMProvider(RoleConfig(role="llm.fast", impl="fake", model="fake-llm"))
    messages = live_prompts.build_refine(utterance="어 그 캡스톤에서는 제가 제가 펌웨어를 맡았습니다")
    task, tags = parse_prompt(messages)
    assert task == "refine" and "캡스톤" in tags["utterance"]
    first, second = await llm.complete(messages), await llm.complete(messages)
    assert first == second == "캡스톤에서는 제가 펌웨어를 맡았습니다."
    unknown = await llm.complete([SystemMessage(content="<task>nope</task>"), HumanMessage(content="x")])
    assert "응답 규칙이 없습니다" in unknown


async def test_fake_stt_walks_script_per_session():
    stt = FakeSTTProvider(RoleConfig(role="stt.live", impl="fake"))
    a1 = await stt.transcribe(SttRequest(pcm=b"\0" * 3200, session="a"))
    b1 = await stt.transcribe(SttRequest(pcm=b"\0" * 3200, session="b"))
    a2 = await stt.transcribe(SttRequest(pcm=b"\0" * 3200, session="a"))
    assert a1.text == b1.text and a1.text != a2.text
    stt.reset_session("a")
    assert (await stt.transcribe(SttRequest(pcm=b"\0" * 3200, session="a"))).text == a1.text


async def test_retry_then_success_and_final_failure():
    cfg = RoleConfig(role="stt.live", impl="fake", max_retries=2, backoff_base_s=0.001, options={"fail_every": 2})
    stt = FakeSTTProvider(cfg)
    await stt.transcribe(SttRequest(pcm=b"\0\0"))  # 1번째 성공
    result = await stt.transcribe(SttRequest(pcm=b"\0\0"))  # 2번째 실패 → 재시도로 성공
    assert result.text and stt.failures == 1 and stt.health == "OK"
    always = FakeSTTProvider(RoleConfig(role="stt.live", impl="fake", max_retries=1, backoff_base_s=0.001,
                                        options={"fail_every": 1}))
    with pytest.raises(ProviderError):
        await always.transcribe(SttRequest(pcm=b"\0\0"))
    assert always.failures == 2 and always.health == "DEGRADED"


async def test_breaker_stops_calling_dead_model_then_probes_and_recovers():
    import asyncio

    from app.providers.base import ProviderDown

    stt = FakeSTTProvider(RoleConfig(role="stt.live", impl="fake", max_retries=5, backoff_base_s=0.001, cooldown_s=0.15,
                                     options={"fail_every": 1}))
    with pytest.raises(ProviderError):
        await stt.transcribe(SttRequest(pcm=b"\0\0"))
    assert stt.failures == 3 and stt.health == "DOWN"  # 세 번 실패하면 남은 재시도를 하지 않는다
    with pytest.raises(ProviderDown):  # 쉬는 동안에는 부르지 않고 바로 실패한다
        await stt.transcribe(SttRequest(pcm=b"\0\0"))
    assert stt.failures == 3 and stt.skipped == 1

    await asyncio.sleep(0.2)  # 쉬는 시간이 지나면 한 번만 시험한다(재시도 없이). 실패하면 다시 쉰다
    with pytest.raises(ProviderError):
        await stt.transcribe(SttRequest(pcm=b"\0\0"))
    assert stt.failures == 4
    with pytest.raises(ProviderDown):
        await stt.transcribe(SttRequest(pcm=b"\0\0"))

    stt.config.options["fail_every"] = 0  # 모델이 살아났다
    await asyncio.sleep(0.2)
    assert (await stt.transcribe(SttRequest(pcm=b"\0\0"))).text
    assert stt.health == "OK"
    assert (await stt.transcribe(SttRequest(pcm=b"\0\0"))).text  # 다시 평소처럼 부른다


async def test_breaker_lets_only_one_probe_through():
    import asyncio

    from app.providers.base import ProviderDown

    stt = FakeSTTProvider(RoleConfig(role="stt.live", impl="fake", max_retries=2, backoff_base_s=0.001, cooldown_s=0.05,
                                     options={"fail_every": 1}))
    with pytest.raises(ProviderError):
        await stt.transcribe(SttRequest(pcm=b"\0\0"))
    stt.config.options.update(fail_every=0, latency_ms=100)
    await asyncio.sleep(0.1)
    probe = asyncio.create_task(stt.transcribe(SttRequest(pcm=b"\0\0", session="a")))
    await asyncio.sleep(0.01)
    with pytest.raises(ProviderDown):  # 시험 호출이 나가 있는 동안 다른 호출은 기다리지 않고 실패한다
        await stt.transcribe(SttRequest(pcm=b"\0\0", session="b"))
    assert (await probe).text and stt.health == "OK"


async def test_breaker_can_be_turned_off():
    stt = FakeSTTProvider(RoleConfig(role="stt.live", impl="fake", max_retries=4, backoff_base_s=0.001, cooldown_s=0,
                                     options={"fail_every": 1}))
    with pytest.raises(ProviderError):
        await stt.transcribe(SttRequest(pcm=b"\0\0"))
    assert stt.failures == 5 and stt.skipped == 0


async def test_semaphore_limits_concurrency():
    import asyncio

    llm = FakeLLMProvider(RoleConfig(role="llm.fast", impl="fake", max_concurrency=2, options={"latency_ms": 30}))
    messages = live_prompts.build_refine(utterance="네 맞습니다")
    peak = 0

    async def watch() -> None:
        nonlocal peak
        for _ in range(20):
            peak = max(peak, llm.waiting)
            await asyncio.sleep(0.005)

    await asyncio.gather(watch(), *(llm.complete(messages) for _ in range(6)))
    assert peak >= 3 and llm.calls == 6  # 6개 중 2개만 동시에 돌고 나머지는 기다렸다


async def test_whisper_http_request_shape_is_configurable(monkeypatch):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("x-api-key")
        seen["body"] = request.content
        return httpx.Response(200, json={"result": {"text": " 안녕하세요 "}})

    monkeypatch.setenv("HUB_KEY", "secret")
    stt = WhisperHttpSTTProvider(RoleConfig(role="stt.live", impl="whisper_http", model="whisper-large-v3", options={
        "base_url": "http://hub.internal/", "path": "/stt/v2/transcribe", "api_key_env": "HUB_KEY",
        "auth_header": "X-Api-Key", "auth_prefix": "", "file_field": "audio", "model_field": "model_id",
        "extra_fields": {"response_format": "json"}, "response_text_path": "result.text"}))
    stt._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result = await stt.transcribe(SttRequest(pcm=b"\x01\x00" * 1600, prompt="STM32"))
    assert result.text == "안녕하세요"
    assert seen["url"] == "http://hub.internal/stt/v2/transcribe" and seen["auth"] == "secret"
    body = seen["body"]
    assert b'name="audio"; filename="chunk.wav"' in body and b'name="model_id"' in body and b"whisper-large-v3" in body
    assert b'name="language"' in body and b'name="prompt"' in body and b"RIFF" in body
    wav = body[body.index(b"RIFF"):]
    assert wav_to_pcm16(wav[:44 + 3200])[1] == 16000


async def test_whisper_http_drops_known_hallucination(monkeypatch):
    stt = WhisperHttpSTTProvider(RoleConfig(role="stt.live", impl="whisper_http", model="m",
                                            options={"base_url": "http://x"}))
    stt._client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, content=json.dumps({"text": "시청해주셔서 감사합니다"}).encode(),
                                 headers={"content-type": "application/json"})))
    assert (await stt.transcribe(SttRequest(pcm=b"\0\0"))).text == ""


async def test_queue_timeout_raises_busy_without_retry():
    import asyncio

    from app.providers.base import ProviderBusy

    stt = FakeSTTProvider(RoleConfig(role="stt.live", impl="fake", max_concurrency=1, max_retries=3,
                                     options={"latency_ms": 200}))
    slow = asyncio.create_task(stt.transcribe(SttRequest(pcm=b"\0\0", session="a")))
    await asyncio.sleep(0.01)
    with pytest.raises(ProviderBusy):
        await stt.transcribe(SttRequest(pcm=b"\0\0", session="b"), queue_timeout_s=0.05)  # 자리가 안 난다
    assert (await slow).text and stt.failures == 0 and stt.waiting == 0  # 밀린 것은 실패로 세지 않는다
    assert (await stt.transcribe(SttRequest(pcm=b"\0\0", session="b"), queue_timeout_s=1.0)).text  # 자리가 나면 된다
