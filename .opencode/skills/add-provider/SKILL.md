---
name: add-provider
description: LLM·STT·임베딩 provider를 새로 더하거나, fake에서 사내 모델(사내 LLM, DS API Hub Whisper)로 바꿀 때 쓴다. 설정만 바꾸는 경우와 새 구현 파일을 쓰는 경우의 절차, 확인 방법을 담고 있다.
---

# provider 더하기 / 바꾸기

모델 호출은 모두 `app/providers/`의 인터페이스를 거친다. 역할은 네 가지다.

| 역할 | 쓰는 곳 | 인터페이스 |
| --- | --- | --- |
| `llm.fast` | 다듬기, 화자 판정, 꼬리질문(면접 중) | `LLMProvider` |
| `llm.quality` | 브리핑, 평가, 보고서 | `LLMProvider` |
| `stt.live` | 면접 중 청크 전사 | `STTProvider` |
| `stt.post` | 면접 뒤 방 전체 재전사 | `STTProvider` |
| `embedding.default` (선택) | 질문 매칭 | `EmbeddingProvider` |

## A. 설정만 바꾸면 되는 경우 (먼저 시도)

이미 있는 구현: `fake`, `openai_compatible`(langchain-openai), `openai_http`(httpx로 직접), `whisper_http`.

1. `config/providers.yaml`의 해당 역할을 고치거나 환경변수로 덮어쓴다.
   ```
   PROVIDERS__LLM__FAST__IMPL=openai_compatible
   PROVIDERS__LLM__FAST__MODEL=<모델 이름>
   PROVIDERS__LLM__FAST__OPTIONS__BASE_URL=http://<host>/v1
   LLM_API_KEY=<키>            # 키는 환경변수에만. YAML에는 api_key_env(변수 이름)만 쓴다
   ```
2. 경로·필드 이름·인증 헤더·응답 위치가 다르면 `options`로 바꾼다(각 구현 파일 맨 위 주석에 목록이 있다).
3. `GET /health`의 `llm`, `stt` 값이 바뀐 impl 이름인지 본다.
4. `scripts/ws_demo_client.py`로 실제 전사가 오는지, `POST /internal/v1/briefings`로 요약이 오는지 본다.
5. `max_concurrency`, `timeout_s`를 실제 응답 시간에 맞추고 `scripts/load_test.py`가 아니라 **실제 모델로** 방 몇 개를 돌려 본다(부하 테스트 스크립트는 fake 지연만 흉내 낸다).

기본값(`providers.yaml`의 fake)은 바꾸지 않는다. 배포 환경의 환경변수나 배포용 YAML(`PROVIDERS_FILE`)로 바꾼다.

## B. 새 구현을 쓰는 경우

규격이 OpenAI 형식과 많이 달라 options로 안 될 때만 한다.

1. `app/providers/<이름>.py`를 만든다.
   - LLM: `LLMProvider`를 상속하고 `chat_model()`이 LangChain `BaseChatModel`을 돌려주게 한다. 직접 구현할 때는 `app/providers/openai_http.py`를 본뜬다(`_payload`, `_extract`, `_agenerate`).
   - STT: `STTProvider`를 상속하고 `async _transcribe(request: SttRequest) -> SttResult`만 쓴다. `app/providers/whisper_http.py`를 본뜬다.
   - 생성자는 `RoleConfig` 하나를 받는다. 필요한 값은 `config.options`에서 읽고, 없으면 `ValueError`로 바로 알린다.
   - 재시도·세마포어·제한 시간을 구현 안에 넣지 않는다(`base.py`의 `_run`이 한다). SDK 자체 재시도는 끈다.
2. `app/providers/registry.py`의 표에 한 줄 더한다: `"<이름>": "app.providers.<이름>:<클래스>"`. (기존 줄은 건드리지 않는다.)
3. `tests/test_providers.py`에 테스트를 더한다: `httpx.MockTransport`로 요청 모양(URL, 헤더, 본문)과 응답 해석을 확인한다. 실제 서버를 부르는 테스트는 넣지 않는다.
4. `config/providers.yaml` 아래쪽 예시 주석과 `docs/providers.md`의 구현 표에 더한다.
5. 새 패키지가 필요하면 AGENTS.md 2장의 의존성 규칙을 따른다.

## 확인 목록

- [ ] `pytest` 통과
- [ ] `GET /health`에 새 impl 이름이 보인다
- [ ] 키가 코드·YAML·로그에 없다
- [ ] 실패했을 때 `ProviderError`로 올라오고(REST는 502 `MODEL_FAILED`, WebSocket은 `STT_FAILED` / `LLM_FAILED`) 면접 흐름은 이어진다
- [ ] STT를 바꿨으면 `ready.sttModel`과 `refined-transcript`의 `sttModel`이 기대한 이름이다
- [ ] 한국어 고유명사 힌트(`SttRequest.prompt`)를 모델이 받는지, 무음에서 지어내는 문장이 있으면 `drop_phrases`에 넣었는지
