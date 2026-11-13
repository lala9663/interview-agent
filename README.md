# interview-agent

면접 지원 서비스의 **Agent(AI) 서버**다. 세 레포(frontend · backend · agent) 중 AI와 음성을 맡는다.

- 면접 전: 지원자 프로필(블라인드 제외본)과 JD로 사전 리포트(요약)와 질문을 만든다.
- 면접 중: 브라우저가 보내는 마이크 음성을 말소리 단위로 잘라 Whisper로 전사하고, LLM으로 다듬고, 화자(면접관/지원자)를 판정하고, 준비된 질문과 자동 매칭하고, 꼬리질문을 제안한다.
- 면접 후: AI 참고 평가와 보고서(신입 BRIEF / 경력 DETAILED)를 만들고, 방 전체 음성을 다시 전사한 보정 전사를 백엔드에 돌려준다.

DB에는 닿지 않는다. 저장은 모두 백엔드가 한다. API는 [docs/contract/api-contract.md](docs/contract/api-contract.md)의 3·4·5장을 그대로 따른다.

스택: Python 3.12, FastAPI, LangChain / LangGraph. 모델(사내 LLM, Whisper large-v3)은 `app/providers/` 인터페이스 뒤에 있고, **기본값은 fake 구현**이라 모델·백엔드·Redis 없이 전체 흐름이 돈다.

## 빠른 시작 (fake로)

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements-dev.txt     # 리눅스·맥: .venv/bin/python

.venv/Scripts/python -m pytest                                  # 테스트
.venv/Scripts/python -m uvicorn app.main:app --port 8000        # 서버

curl http://localhost:8000/health                               # {"status":"UP","llm":"fake","stt":"fake"}
.venv/Scripts/python scripts/ws_demo_client.py --turns 6 --finalize   # 합성 음성을 보내고 이벤트를 출력
.venv/Scripts/python scripts/load_test.py --rooms 20            # 방 20개 부하 테스트(서버를 따로 띄운다)
.venv/Scripts/python scripts/eval_speaker.py                    # 화자 판정 정확도 표
```

fake일 때 일어나는 일: fake STT는 음성 내용을 읽지 못하므로 `app/demo/script.py`의 면접 대본을 청크마다 한 줄씩 돌려준다. fake LLM은 프롬프트의 `<task>` 태그를 보고 규칙으로 모양이 맞는 결과를 낸다(품질이 아니라 흐름 확인용). fake 백엔드는 데모 context를 돌려주고 저장 요청은 로그로만 남긴다.

사내 모델을 붙이는 법은 [docs/providers.md](docs/providers.md), 설치와 실행 상세는 [docs/setup.md](docs/setup.md).

## 환경변수

값이 없으면 개발 기본값으로 돈다. `.env` 파일도 읽는다(`.env.example` 참고).

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `ROOM_TICKET_SECRET` | `dev-room-ticket-secret-change-me-32bytes!!` | 방 입장권(JWT HS256) 비밀키. 백엔드와 같은 값 |
| `INTERNAL_API_KEY` | `dev-internal-key` | 내부 API의 `X-Internal-Key`. 백엔드와 같은 값 |
| `APP_CORS_ORIGINS` | `http://localhost:5173` | CORS 허용 출처(쉼표로 구분) |
| `APP_LOG_LEVEL` | `INFO` | 로그 수준 |
| `APP_INSTANCE_ID` | (무작위) | 서버가 여러 대일 때 인스턴스 이름 |
| `BACKEND_MODE` | `fake` | `fake`(단독 실행) \| `http` |
| `BACKEND_BASE_URL` | `http://localhost:8080` | 백엔드 주소(`/internal/v1`은 붙이지 않는다) |
| `PROVIDERS_FILE` | `config/providers.yaml` | 모델 설정 파일 |
| `PROVIDERS__<종류>__<역할>__<키>` | - | 모델 설정 덮어쓰기. 예: `PROVIDERS__LLM__FAST__IMPL=openai_compatible` |
| `LLM_API_KEY`, `STT_API_KEY` | - | 모델 API 키(`options.api_key_env`로 이름을 바꿀 수 있다) |
| `STATE_STORE`, `STATE_EVENT_BUS` | `memory` | `memory`(서버 한 대) \| `redis`(여러 대) |
| `STATE_REDIS_URL` | `redis://localhost:6379/0` | Redis 주소 |
| `STATE_REDIS_SENTINELS`, `STATE_REDIS_SENTINEL_MASTER` | - , `mymaster` | 값이 있으면 Sentinel로 붙는다 |
| `AUDIO_RETENTION` | `MEMORY_ONLY` | `MEMORY_ONLY`(저장 안 함) \| `KEEP`(파일로 보관) |
| `AUDIO_STORE` | `memory` | `memory` \| `file` |
| `AUDIO_MAX_MINUTES_PER_ROOM` | `90` | 방마다 모아 두는 음성 상한(분) |
| `AUDIO_MIN_SILENCE_MS`, `AUDIO_MAX_CHUNK_MS`, `AUDIO_VAD_THRESHOLD_DB` … | `700`, `15000`, `-40` | 청크 기준. 전체는 [docs/audio-pipeline.md](docs/audio-pipeline.md) |
| `LIVE_MAX_ROOMS` | `40` | 인스턴스 하나가 받는 방 수 상한 |
| `LIVE_STT_QUEUE_SOFT`, `LIVE_STT_QUEUE_HARD` | `3`, `8` | 방별 큐 역압 기준. [docs/concurrency.md](docs/concurrency.md) |
| `LIVE_MAX_CHUNK_WAIT_S` | `20` | 이보다 오래 기다린 청크는 실시간 전사를 건너뛴다(사후 STT가 채운다) |
| `LIVE_REFINE_TIMEOUT_S`, `LIVE_FOLLOWUP_DEBOUNCE_S` | `4.0`, `1.5` | 다듬기 제한 시간, 꼬리질문 대기 시간 |
| `LIVE_POST_STT_CONCURRENT_ROOMS` | `2` | 사후 STT를 동시에 돌리는 방 수 |
| `SPEAKER_CHAIN` | `manual,channel,question_match,rule,llm` | 화자 판정 순서. [docs/speaker-separation.md](docs/speaker-separation.md) |
| `SPEAKER_QUESTION_MATCH_THRESHOLD`, `MATCHING_THRESHOLD` | `0.55`, `0.55` | 질문 유사도 기준 |
| `BLIND_RULES_FILE` | `config/blind_rules.yaml` | 금지 항목 규칙. [docs/blind-rules.md](docs/blind-rules.md) |

전체 목록과 기본값의 원본은 `app/settings/sections.py`다.

## 폴더 구조

```
app/
  main.py          FastAPI 앱
  container.py     객체 조립(설정 → provider, 그래프, 방 관리자)
  settings/        섹션별 설정(환경변수)
  schemas/         계약 타입: common(열거형), internal(4장), backend(5장), ws(3장)
  api/             내부 REST(4장)와 오류 형식
  providers/       LLM·STT·Embedding 인터페이스, 레지스트리, 구현(fake, openai_compatible, openai_http, whisper_http)
  prompts/         작업별 프롬프트(버전 포함)
  graphs/          LangGraph 그래프: briefing, refine, followup, evaluation, report
  blind/           블라인드/금지 항목 규칙과 필터
  audio/           VAD, 청크 분할, AudioStore(memory | file)
  speaker/         화자 판정 체인
  matching/        질문 매칭(문자 n-gram TF-IDF, 임베딩 선택)
  live/            WebSocket, 방 파이프라인, 꼬리질문, 사후 STT, 입장권
  state/           RoomStateStore, EventBus (memory | redis)
  backend/         백엔드 클라이언트(http | fake)와 재시도 큐
  demo/            데모 대본·데이터·합성 PCM
config/            providers.yaml, blind_rules.yaml
scripts/           make_ticket, ws_demo_client, load_test, eval_speaker, gen_speaker_fixture
tests/             pytest (fixtures/ 에 화자 평가용 합성 대화)
docs/              설계·운영 문서, contract/ 에 계약 복사본
.opencode/skills/  OpenCode용 작업 절차
AGENTS.md          OpenCode가 읽는 규칙
```

## 문서

| 문서 | 내용 |
| --- | --- |
| [docs/architecture.md](docs/architecture.md) | 구성 요소와 데이터 흐름 |
| [docs/setup.md](docs/setup.md) | 설치, 실행, 반입 |
| [docs/providers.md](docs/providers.md) | 사내 LLM·Whisper 붙이는 절차와 확인 항목 |
| [docs/audio-pipeline.md](docs/audio-pipeline.md) | 청크 기준, 지연 예산, 메모리 추정, 저장 방식 전환 |
| [docs/speaker-separation.md](docs/speaker-separation.md) | 화자분리 없이 화자를 정하는 방법, 측정 결과, 권장 |
| [docs/concurrency.md](docs/concurrency.md) | 동시 20개 방: 설정, 부하 테스트 결과, 증설 기준, Redis |
| [docs/blind-rules.md](docs/blind-rules.md) | 블라인드/금지 질문 규칙과 바꾸는 법 |
| [docs/prompts.md](docs/prompts.md) | 프롬프트 목록과 규칙 |
| [docs/open-questions.md](docs/open-questions.md) | 가정으로 구현한 것과 바꿀 위치 |
| [docs/contract/api-contract.md](docs/contract/api-contract.md) | API 계약(세 레포 공통 원본의 복사본) |
