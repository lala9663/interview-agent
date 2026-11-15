# AGENTS.md — interview-agent

면접 지원 서비스의 Agent(AI) 서버. 이 파일은 AI 코딩 도구(OpenCode)와 사람이 작업 전에 읽는 규칙이다.
답변·주석·문서·프롬프트는 한국어, 코드 식별자는 영어로 쓴다.

## 1. 이 레포의 일

| 레포 | 일 | 이 레포와의 관계 |
| --- | --- | --- |
| `interview-frontend` (Vue) | 화면 | WebSocket `/ws/v1/rooms/{roomId}`로 직접 붙는다(API 명세 3장) |
| `interview-backend` (Spring, Oracle) | 기록의 주인, 권한, 작업(AiJob) 관리 | 우리를 REST로 부르고(4장), 우리가 저장을 부탁한다(5장) |
| `interview-agent` (여기) | AI와 음성: 요약·질문·전사·화자 판정·꼬리질문·평가·보고서 | |

스택: Python 3.12, FastAPI, LangChain / LangGraph, pydantic v2, httpx, redis, numpy, pytest.
모델: 사내 LLM(OpenAI 형식 API 가정), 사내 DS API Hub의 Whisper large-v3. 기본 설정은 fake 구현이다.

## 2. 하지 않는 것 (금지)

- **DB에 직접 닿지 않는다.** DB 드라이버·ORM을 넣지 않는다. 저장은 `app/backend/`를 거쳐 백엔드에 맡긴다.
- **provider 인터페이스를 건너뛰고 모델을 부르지 않는다.** 그래프·방 파이프라인에서 httpx·SDK로 LLM·STT를 직접 호출하지 않는다. 동시 실행 제한·제한 시간·재시도가 `app/providers/base.py`에 있기 때문이다.
- **블라인드 검사를 우회하지 않는다.** 지원자 자료를 모델에 넣기 전에 `BlindFilter.filter_profile`, 모델이 낸 글(요약·질문·꼬리질문·보고서)을 내보내기 전에 `check_obj` / `scrub`를 거친다. 새 그래프도 같다.
- **API 명세를 혼자 바꾸지 않는다.** 경로·필드 이름·열거형은 `docs/contract/api-contract.md`와 같아야 한다(6장 절차).
- **녹음을 임의로 저장하지 않는다.** 기본은 `AUDIO_RETENTION=MEMORY_ONLY`다. 디버깅용으로도 PCM·WAV를 디스크에 쓰는 코드를 넣지 않는다. 저장은 `AudioStore` 구현으로만 한다.
- **지원자 자료·전사 내용을 로그에 길게 남기지 않는다.** 로그에는 방 번호, seq, 길이, 오류만 쓴다(fake 백엔드의 데모 로그는 예외).
- 프롬프트에 입력을 그대로 이어 붙이지 않는다. `app/prompts/common.py`의 `tag()`로 감싼다.
- 무거운 의존성(torch, transformers, 로컬 모델 런타임)을 넣지 않는다. 새 패키지는 사내 pip 미러에 반입해야 하므로 PR 설명에 이유를 쓰고 `requirements.txt`, `pyproject.toml`, `requirements.lock.txt`를 함께 고친다.
- 한 파일이 400줄을 넘으면 나눈다.
- 비밀값(API 키, 입장권 비밀키)을 코드·YAML·문서에 쓰지 않는다. 환경변수 이름만 쓴다.

## 3. 폴더 규칙

기능 단위 폴더다. 기능을 더하거나 고칠 때 손대는 파일이 그 폴더 안에 모이게 한다.

| 폴더 | 담는 것 | 다른 폴더가 쓰는 창구 |
| --- | --- | --- |
| `app/schemas/` | API 명세 타입. JSON은 camelCase(`CamelModel`) | `common`, `internal`(4장), `backend`(5장), `ws`(3장) |
| `app/settings/` | 섹션별 설정 클래스(섹션마다 환경변수 접두사) | `Settings` |
| `app/providers/` | 모델 인터페이스·레지스트리·구현 | `ProviderSet`, `LLMProvider.complete`, `STTProvider.transcribe` |
| `app/prompts/` | 작업별 프롬프트(작업 이름·버전) | `build_*()` |
| `app/graphs/` | LangGraph 그래프 | `GraphRunner` (API와 방 파이프라인은 이것만 부른다) |
| `app/blind/` | 금지 항목 규칙·필터 | `BlindFilter` |
| `app/audio/` | VAD, 청크 분할, 음성 보관 | `SpeechChunker`, `AudioStore` |
| `app/speaker/` | 화자 판정 체인 | `SpeakerChain.decide` |
| `app/matching/` | 질문 유사도 | `QuestionMatcher.best` |
| `app/live/` | WebSocket, 방 파이프라인, 꼬리질문 시점, 사후 STT | `RoomManager`, `LocalHub` |
| `app/state/` | 방 상태·이벤트 중계(memory \| redis) | `RoomStateStore`, `EventBus` |
| `app/backend/` | 백엔드 호출(http \| fake), 재시도 큐 | `BackendClient`, `BackendOutbox` |
| `app/api/` | 내부 REST와 오류 형식 | - |
| `app/demo/` | fake용 대본·데모 데이터·합성 PCM | - |
| `config/` | `providers.yaml`, `blind_rules.yaml` | - |

- 객체 조립은 `app/container.py` 한 곳에서 한다. 다른 곳에서 전역 싱글턴을 만들지 않는다.
- 의존 방향: `api`·`live` → `graphs` → `prompts`·`providers`·`blind`. 아래 폴더가 위 폴더를 import하지 않는다.
- 설정값을 더할 때는 해당 섹션 클래스에만 넣고, README의 환경변수 표와 관련 문서를 같이 고친다.

## 4. 기능 추가 순서

1. API 명세에 있는 일인가? 없으면 6장 절차부터.
2. 타입(`app/schemas/`) → 3. 프롬프트(`app/prompts/`) → 4. 그래프(`app/graphs/`, 블라인드 검사 포함) → 5. fake 응답(`app/providers/fake_responses.py`) → 6. `GraphRunner`와 API(또는 방 파이프라인)에 연결 → 7. 테스트 → 8. 문서.
3. 자주 하는 작업은 skill에 절차가 있다(`.opencode/skills/<이름>/SKILL.md`).

| skill | 언제 |
| --- | --- |
| `add-provider` | LLM·STT·임베딩 구현을 더하거나 사내 모델로 바꿀 때 |
| `add-graph` | 새 AI 작업(프롬프트 + 그래프)을 더할 때 |
| `change-api-contract` | 경로·필드·이벤트·열거형을 바꿔야 할 때 |
| `tune-audio-pipeline` | 청크 기준, 지연, 큐 한계, 음성 보관 방식을 바꿀 때 |
| `update-blind-rules` | 금지 항목 기준이 바뀔 때 |
| `port-legacy-feature` | 기존 사내 Python/Vue 코드의 기능을 옮길 때 |

## 5. 명령어

```bash
.venv/Scripts/python -m pytest                          # 전체 테스트(외부 연결 없음, 수 초)
.venv/Scripts/python -m pytest tests/test_ws.py -k flow # 일부만
.venv/Scripts/python -m uvicorn app.main:app --port 8000 --reload
.venv/Scripts/python scripts/ws_demo_client.py --turns 6 --finalize
.venv/Scripts/python scripts/load_test.py --rooms 20 30 # 부하 테스트(서버를 따로 띄운다, 방 수마다 약 75초)
.venv/Scripts/python scripts/eval_speaker.py            # 화자 판정 정확도 표
.venv/Scripts/python scripts/make_ticket.py --room 9001 --audio
```

리눅스에서는 `.venv/bin/python`. 작업을 마치기 전에 `pytest`가 통과해야 한다.
오디오·큐·provider 제한을 건드렸으면 `load_test.py`를, 화자 판정·매칭을 건드렸으면 `eval_speaker.py`를 돌리고
숫자가 바뀌었으면 `docs/concurrency.md` / `docs/speaker-separation.md`의 표를 고친다.

## 6. API 변경 절차

1. 원본은 세 레포 공통 문서의 `03-api-contract.md`이고, 이 레포의 `docs/contract/api-contract.md`는 복사본이다.
2. 바꿔야 하면 먼저 제안을 올린다(무엇을, 왜, 어느 레포가 영향을 받는지). 세 레포 담당이 합의한 뒤에 고친다.
3. 합의되면 API 명세 네 개(원본과 세 레포의 복사본)를 **같은 날** 같은 내용으로, 문서만 담은 PR로 먼저 올린다(제목에 `[API 변경]`, 세 명 모두 확인). 이 레포의 코드(`app/schemas/`, 구현, 테스트: `tests/test_api_backend.py`, `tests/test_ws.py`의 모양 검사)는 그 뒤에 따로 올리고, 다른 레포와 같은 날이 아니어도 된다.
4. 필드를 더하는 것은 기능 플래그 없이 가능하다(받는 쪽이 모르는 필드는 무시한다: `CamelModel`의 `extra="ignore"`). 이름을 바꾸거나 지우는 것은 받는 쪽이 먼저 둘 다 받게 한 뒤에 한다.
5. API 명세에 없는데 이 레포가 정해서 쓰는 것은 `docs/open-questions.md`에 적고 API 명세에 더해 달라고 제안한다. (`blind/check` 응답 모양과 `/internal/v1/stats`는 그렇게 API 명세 4장에 들어갔다.)

## 7. 브랜치 규칙 (3명이 동시에 작업, `dev` + `main`)

- 길게 사는 브랜치는 둘이다. `dev`는 작업을 모으는 곳이고, `main`은 매주 화요일에 올리는 확인된 지점이다.
- 작업 브랜치는 `dev`에서 따서 하루 이틀 안에 PR로 `dev`에 합친다(스쿼시 병합: 작업 하나가 커밋 하나로 남는다). 오래 사는 작업 브랜치를 두지 않는다.
- `main`에는 직접 커밋하지 않는다. 화요일에 병합 담당이 세 서비스를 함께 띄워 확인한 뒤 `dev`를 `main`으로 올리고(일반 병합) 태그를 붙인다(예: `2026-w47`). 세 레포는 같은 날 같은 태그로 올린다.
- `dev`는 항상 빌드되고 테스트가 통과해야 한다. 깨졌으면 새 작업보다 복구가 먼저다.
- 작업 브랜치 이름은 `feat/<짧은-설명>`, `fix/<짧은-설명>`.
- PR은 작게 올린다(리뷰하는 사람이 15분 안에 읽을 크기, 한 가지 목적). 보통 한 기능 폴더 + 테스트 + 문서다. 여러 폴더를 건드리는 리팩터링은 따로 PR로 먼저 올린다.
- 덜 끝난 기능은 설정 플래그 뒤에 두고 합친다. 예: 새 화자 판정기는 구현을 넣되 `SPEAKER_CHAIN` 기본값에는 넣지 않는다. 새 provider는 `providers.yaml` 기본값을 바꾸지 않는다. 새 AudioStore는 `AUDIO_STORE` 기본값을 바꾸지 않는다.
- 작업 중에 범위 밖의 아이디어나 개선점이 나오면 그 자리에서 만들지 않고 세 레포 공통 문서의 `09-backlog.md`(할 목록)에 적는다. 적는 법은 그 문서 맨 위에 있다.
- 충돌이 잦은 파일: `app/container.py`, `app/providers/registry.py`(impl 표), `app/providers/fake_responses.py`, `app/settings/sections.py`, `README.md`의 환경변수 표. 이 파일들은 줄을 **더하기만** 하고 기존 줄의 순서·정렬을 바꾸지 않는다.
- 통과의 기준은 `pytest`다. PR에 그 결과를 붙인다.
- 커밋 메시지(= PR 제목)는 `<영역>: <무엇을 왜>` 한 줄, 한국어. 예: `speaker: 되묻기 구간 규칙 추가`. 영역은 기능 폴더나 패키지 이름이다. API 명세를 바꾸면 앞에 `[API 변경]`을 붙인다. `dev`에는 스쿼시로 합치므로 PR 제목이 그대로 커밋 한 줄이 된다.
- 리뷰는 1명, 영역이 다른 사람이 본다. API 명세를 바꾸는 PR은 세 명 모두 확인한다.

## 8. 테스트 규칙

- 모든 테스트는 fake provider·fake 백엔드·메모리 상태로 돈다. 외부 연결(사내 모델, Redis, 백엔드)이 필요한 테스트를 기본 실행에 넣지 않는다.
- 모델 출력을 정해서 시험할 때는 `tests/conftest.py`의 `ScriptedLLM`을 쓴다.
- 새 REST 응답·WebSocket 이벤트는 필드 이름까지 검사한다(camelCase).
- 블라인드를 거치는 출력은 "금지 항목이 출력에 없다"를 검사한다(`blind.check_obj(...) == []`).
- WebSocket 테스트는 `tests/test_ws.py`의 `recv` / `recv_until`을 쓴다(제한 시간이 있어 멈춰 서지 않는다).

## 9. 지금 알아야 할 상태

- **확인하지 못한 것**: 사내 LLM·Whisper 실제 호출, 실제 Redis(pub/sub·Sentinel), 실제 면접 음성의 VAD·화자 판정 정확도. 처음 붙일 때 `docs/providers.md`와 `docs/open-questions.md`의 확인 항목을 따른다. 백엔드와의 연동은 로컬에서 fake 모델로 한 번 끝까지 확인했다(2026-11-13, `docs/open-questions.md` 1장).
- 화자 판정의 규칙 판정은 API 명세의 `SpeakerMethod.RULE`로 나간다(`SPEAKER_RULE_WIRE_METHOD`로 바꿀 수 있다).
- 경력(화상, Knox Teams)은 API가 정해지지 않아 신입과 같이 브라우저가 음성을 보낸다고 가정했다. 트랙이 나뉘어 오게 되면 `app/speaker/chain.py`의 `ChannelJudge`와 오디오 수신부를 잇는다.

## 10. 문서 위치

| 알고 싶은 것 | 문서 |
| --- | --- |
| 전체 구조와 흐름 | `docs/architecture.md` |
| 설치·실행·반입 | `docs/setup.md` |
| 사내 모델 붙이기 | `docs/providers.md` |
| 청크 기준·지연·메모리·음성 보관 | `docs/audio-pipeline.md` |
| 화자 판정 방법과 측정값 | `docs/speaker-separation.md` |
| 동시 방 수·부하 테스트·Redis | `docs/concurrency.md` |
| 금지 항목 규칙 | `docs/blind-rules.md` |
| 프롬프트 목록·규칙 | `docs/prompts.md` |
| 가정한 것과 바꿀 위치 | `docs/open-questions.md` |
| API 명세 | `docs/contract/api-contract.md` |

문서는 사실만 짧게 쓰고, 코드·측정값과 다르면 문서를 고친다. 코드를 바꾸면 같은 PR에서 해당 문서를 고친다.
