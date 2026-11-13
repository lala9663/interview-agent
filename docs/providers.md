# 모델 연결 (provider)

> 다루는 것: 역할과 구현, 사내 LLM·Whisper를 붙이는 절차, 붙일 때 확인할 항목.
>
> 상태: fake 구현으로만 실행해 봤다. **사내 LLM과 DS API Hub Whisper는 접근할 수 없어 실제 호출을 확인하지 못했다.** HTTP 구현은 가짜 서버(httpx MockTransport)로 요청·응답 모양만 테스트했다.

## 1. 역할

`config/providers.yaml`에 역할별로 적는다. 역할마다 모델, 동시 실행 수, 제한 시간, 재시도를 따로 둔다.

| 역할 | 쓰는 곳 | 기본 동시 실행 | 기본 제한 시간 | 재시도 |
| --- | --- | ---: | ---: | ---: |
| `llm.fast` | 다듬기, 화자 판정, 꼬리질문 (면접 중) | 16 | 10초 | 1 |
| `llm.quality` | 브리핑, 평가, 보고서 | 4 | 600초 | 2 |
| `stt.live` | 면접 중 청크 전사 | 8 | 20초 | 2 |
| `stt.post` | 면접 뒤 방 전체 재전사 | 2 | 120초 | 2 |
| `embedding.default` | 질문 매칭(선택, 기본 `none`) | - | - | - |

- 동시 실행 수는 **프로세스 전역** 세마포어다(방이 몇 개든 그 역할의 호출은 이 수를 넘지 않는다). 사내 모델 서버가 받을 수 있는 동시 요청 수에 맞춘다.
- 재시도는 지수 백오프(`backoff_base_s` × 2ⁿ)다. 다 실패하면 `ProviderError`.
- `llm.fast`와 `llm.quality`에 같은 모델을 써도 된다. 나눈 이유는 면접 중 호출이 보고서 생성에 밀리지 않게 하기 위해서다.

## 2. 구현

| 종류 | impl | 파일 | 설명 |
| --- | --- | --- | --- |
| LLM | `fake` | `fake_llm.py`, `fake_responses.py` | 프롬프트의 `<task>`를 보고 규칙으로 답한다. `options.latency_ms`로 지연 흉내 |
| LLM | `openai_compatible` | `openai_compatible.py` | langchain-openai의 `ChatOpenAI`. OpenAI 형식 API |
| LLM | `openai_http` | `openai_http.py` | 같은 형식을 httpx로 직접 부른다(openai SDK 불필요). 경로·인증 헤더·응답 위치를 options로 바꿀 수 있다 |
| STT | `fake` | `fake_stt.py` | 데모 대본(`app/demo/script.py`)을 청크마다 한 줄씩 |
| STT | `whisper_http` | `whisper_http.py` | OpenAI 형식 `/v1/audio/transcriptions` multipart. 경로·필드 이름·인증 헤더·응답 위치를 options로 바꿀 수 있다 |
| 임베딩 | `fake`, `openai_compatible` | `embedding.py` | 해시 임베딩(확인용), OpenAI 형식 `/embeddings` |

모든 LLM 구현은 LangChain `BaseChatModel`을 돌려주고(`chat_model()`), 그래프는 `LLMProvider.complete()`로 부른다.

## 3. 설정 방법

YAML을 고치거나 환경변수로 덮어쓴다. 환경변수 이름은 `PROVIDERS__<종류>__<역할>__<키>[__<하위 키>]`이고 값은 YAML 규칙으로 읽는다(숫자·불리언 인식).

```bash
# 사내 LLM (OpenAI 형식)
PROVIDERS__LLM__FAST__IMPL=openai_compatible          # 또는 openai_http
PROVIDERS__LLM__FAST__MODEL=<모델 이름>
PROVIDERS__LLM__FAST__OPTIONS__BASE_URL=http://<host>/v1
PROVIDERS__LLM__QUALITY__IMPL=openai_compatible
PROVIDERS__LLM__QUALITY__MODEL=<모델 이름>
PROVIDERS__LLM__QUALITY__OPTIONS__BASE_URL=http://<host>/v1
LLM_API_KEY=<키>

# Whisper (DS API Hub)
PROVIDERS__STT__LIVE__IMPL=whisper_http
PROVIDERS__STT__LIVE__MODEL=whisper-large-v3
PROVIDERS__STT__LIVE__OPTIONS__BASE_URL=http://<host>
PROVIDERS__STT__POST__IMPL=whisper_http
PROVIDERS__STT__POST__MODEL=whisper-large-v3
PROVIDERS__STT__POST__OPTIONS__BASE_URL=http://<host>
STT_API_KEY=<키>
```

키는 환경변수에만 둔다. YAML에는 변수 이름(`options.api_key_env`)만 쓴다.
YAML의 역할 키에 오타가 있으면 시작할 때 오류가 난다(모르는 키 거부).

### `openai_compatible` / `openai_http` options

| 키 | 기본 | 설명 |
| --- | --- | --- |
| `base_url` | (필수) | 예: `http://llm.internal/v1` |
| `api_key_env` | `LLM_API_KEY` | 키가 든 환경변수 이름 |
| `headers` | - | 요청마다 붙일 헤더 |
| `temperature` | `0.2` | |
| `max_tokens` | - | |
| `extra_body` | - | 게이트웨이가 요구하는 추가 본문 |
| `path` (`openai_http`만) | `/chat/completions` | |
| `auth_header`, `auth_prefix` (`openai_http`만) | `Authorization`, `Bearer ` | |
| `response_text_path` (`openai_http`만) | `choices.0.message.content` | 응답에서 글자가 있는 위치 |
| `verify_ssl` (`openai_http`만) | `true` | 사설 인증서면 `false` 또는 CA 파일 경로 |

### `whisper_http` options

| 키 | 기본 | 설명 |
| --- | --- | --- |
| `base_url` | (필수) | |
| `path` | `/v1/audio/transcriptions` | |
| `api_key_env` | `STT_API_KEY` | |
| `auth_header`, `auth_prefix` | `Authorization`, `Bearer ` | |
| `headers` | - | 추가 헤더 |
| `file_field`, `model_field`, `language_field`, `prompt_field` | `file`, `model`, `language`, `prompt` | multipart 필드 이름. 빈 문자열이면 보내지 않는다(`file_field` 제외) |
| `extra_fields` | - | 그대로 보낼 필드. 예: `{response_format: json, temperature: 0}` |
| `response_text_path` | `text` | 응답 JSON에서 글자가 있는 위치(점으로 구분). 응답이 글자 그대로면 그대로 쓴다 |
| `drop_phrases` | 유튜브 맺음말 3종 | 무음에서 Whisper가 지어내는 문장. 결과가 이것과 같으면 빈 글자로 본다 |
| `verify_ssl` | `true` | |

보내는 오디오는 청크마다 WAV 한 개다(PCM16, 16kHz, 모노, 최대 15초·사후 28초).

## 4. DS API Hub Whisper를 붙일 때

**실제 규격을 확인하지 못했다.** OpenAI 형식이라고 가정하고 만들었고, 다른 부분은 아래 순서로 맞춘다.

1. Hub 문서에서 확인한다: 주소와 경로, 인증 방식(헤더 이름·형식), 요청이 multipart인지 JSON(base64)인지, 필드 이름, 응답 JSON 모양, 받는 오디오 형식(WAV/PCM, 표본화율), 한 요청의 최대 길이·크기, 동시 요청 한도, 응답 시간.
2. multipart이고 이름만 다르면 → `options`만 바꾼다(코드 수정 없음).
3. 본문이 JSON(base64)이거나 응답이 비동기(작업 id를 받고 조회)면 → **`app/providers/whisper_http.py` 한 파일만** 고친다: `_build_request`(요청 만들기), `_extract_text`(응답에서 글자 꺼내기), 필요하면 `_transcribe`. 인터페이스(`SttRequest` → `SttResult`)는 그대로 둔다.
4. 확인한다.

| 확인 항목 | 방법 | 맞추는 곳 |
| --- | --- | --- |
| 한국어로 전사되는가 | `ws_demo_client.py` 대신 실제 마이크(프론트)로 | `language_field`, `extra_fields` |
| 청크 하나(10초)의 응답 시간 | 로그의 지연, `/internal/v1/stats` | `stt.live.timeout_s`, [concurrency.md](concurrency.md)의 계산 |
| 동시 요청 한도 | Hub 담당 확인 | `stt.live.max_concurrency`, `stt.post.max_concurrency` |
| 고유명사 힌트(`prompt`)를 받는가 | 직무 용어가 든 문장으로 | `prompt_field`(안 받으면 빈 문자열) |
| 무음·잡음에서 지어내는 문장 | 조용한 방에서 1분 | `drop_phrases`, `AUDIO_VAD_THRESHOLD_DB` |
| 문장 부호가 붙는가 | 결과 확인 | 안 붙으면 다듬기에 더 의존한다. 질문 매칭의 문장 분리는 부호가 있을 때만 동작한다 |
| 28초 청크를 받는가 | 사후 STT 실행 | `AUDIO_POST_MAX_CHUNK_MS` |
| 화자 정보를 주는가 | 응답 확인 | 준다면 `ChannelJudge`에 연결할 수 있다([speaker-separation.md](speaker-separation.md)) |

## 5. 사내 LLM을 붙일 때

| 확인 항목 | 맞추는 곳 |
| --- | --- |
| OpenAI 형식 `/chat/completions`인가, 경로가 다른가 | `openai_http`의 `path` |
| 인증 헤더 형식 | `auth_header`, `auth_prefix`, `headers` |
| system 메시지를 받는가 | 안 받으면 `openai_http.py`의 `_payload`에서 system을 user 앞에 합친다 |
| 응답 시간(다듬기 한 문장, 보고서 한 건) | `llm.fast.timeout_s`, `LIVE_REFINE_TIMEOUT_S`, 백엔드의 호출 제한 시간 |
| 동시 요청 한도 | `max_concurrency` |
| 문맥 길이 | 60분 면접 전사 전체를 한 번에 넣는다(나누지 않는다). 안 들어가면 `app/graphs/report.py`, `evaluation.py`에 나누는 단계가 필요하다([open-questions.md](open-questions.md)) |
| JSON을 잘 지키는가 | `call_structured`가 검증하고 한 번 다시 시킨다. 자주 실패하면 프롬프트를 고치거나 `extra_body`로 JSON 모드를 켠다 |
| 추론 과정(thinking)을 본문에 섞어 내는가 | JSON 추출은 첫 JSON 객체를 찾으므로 대개 통과한다. 다듬기는 글 전체를 쓰므로 섞이면 안 된다(`refine` 그래프의 길이 검사에서 걸러져 raw가 쓰인다) |

붙인 뒤에는 `python scripts/eval_speaker.py`를 다시 돌린다. `llm.fast`가 fake가 아니면 LLM 화자 판정(⑤)까지 포함해 잰다.

## 6. 새 구현을 더할 때

`.opencode/skills/add-provider/SKILL.md`를 따른다. 요약: 파일 하나 + `app/providers/registry.py`의 표 한 줄 + 테스트.
