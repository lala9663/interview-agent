# 구조

> 다루는 것: Agent 서버의 구성 요소, 요청·음성·이벤트가 흐르는 길, 서버가 여러 대일 때의 동작.

## 1. 한눈에

```
백엔드 ──REST /internal/v1 (X-Internal-Key)──▶ app/api ──▶ GraphRunner ──▶ LangGraph 그래프 ──▶ LLMProvider ──▶ 사내 LLM
                                                               ▲                    │
브라우저 ──WebSocket /ws/v1/rooms/{id} (입장권)──▶ app/live ───┘                    └─ BlindFilter (입력 필터, 출력 검사)
            │  오디오(PCM16 16kHz)                    │
            │                                         ├─ AudioStore (방 전체 음성, 기본은 메모리)
            │                                         ├─ SpeechChunker (VAD) ─▶ STTProvider ─▶ Whisper
            │                                         ├─ SpeakerChain, QuestionMatcher
            ◀── 이벤트 ── LocalHub ◀── EventBus ◀─────┤
                                    (memory | redis)   └─ BackendOutbox ──REST──▶ 백엔드 (전사·꼬리질문·보정 전사 저장)
```

- REST(브리핑·평가·보고서·블라인드 검사)는 상태가 없다. 입력을 받아 결과를 돌려준다.
- 상태가 있는 것은 실시간 방뿐이다. 여러 인스턴스가 같아야 하는 값(seq, 마이크 잠금, 방 주인)은 `RoomStateStore`에, 방의 대화 문맥과 음성은 방 주인 인스턴스의 메모리에 있다.
- 객체는 `app/container.py`에서 한 번 조립한다.

## 2. 폴더와 책임

| 폴더 | 책임 | 주요 타입 |
| --- | --- | --- |
| `app/api/` | 내부 REST(API 명세 4장), 오류 형식, `X-Internal-Key` 검사 | `router`, `ApiError` |
| `app/live/` | WebSocket(API 명세 3장), 방 파이프라인, 꼬리질문 시점, 사후 STT | `RoomManager`, `RoomSession`, `LocalHub`, `FollowUpScheduler` |
| `app/graphs/` | LangGraph 그래프 5종과 구조화 출력 | `GraphRunner`, `call_structured` |
| `app/prompts/` | 작업별 프롬프트 | `build_*` |
| `app/providers/` | 모델 인터페이스, 레지스트리, 구현 | `LLMProvider`, `STTProvider`, `ProviderSet` |
| `app/blind/` | 금지 항목 규칙과 필터 | `BlindFilter` |
| `app/audio/` | VAD, 청크, 음성 보관 | `SpeechChunker`, `AudioStore` |
| `app/speaker/` | 화자 판정 체인 | `SpeakerChain` |
| `app/matching/` | 질문 유사도 | `QuestionMatcher` |
| `app/state/` | 방 상태, 이벤트 중계 | `RoomStateStore`, `EventBus` |
| `app/backend/` | 백엔드 호출(API 명세 5장)과 재시도 큐 | `BackendClient`, `BackendOutbox` |

## 3. 면접 전: 브리핑

`POST /internal/v1/briefings` → `GraphRunner.briefing` → 그래프(`app/graphs/briefing.py`):

```
blind_filter ─▶ summarize ─▶ jd_questions ─▶ resume_questions ─▶ blind_check ─┬─▶ finalize
   (프로필에서 금지 키 삭제,                                       (출력에 금지 항목?) │
    금지 문장 가림)                    ▲                                             │
                                       └──────────── 있으면 1회 다시 생성 ◀──────────┘
```

`finalize`는 재생성 뒤에도 남은 금지 항목을 지운다(요약은 문장을 가리고, 질문은 통째로 뺀다). 응답의 `excluded`는 입력에서 걸러진 주제 코드다.

요청의 `applicantType`·`persona`·`predictedFollowUps`는 지금 무시하고, 응답의 `questions[].followUps`·`summary.checkItems`는 아직 주지 않는다([open-questions.md](open-questions.md) 6장).

## 4. 면접 중: 방 파이프라인

`app/live/session.py`의 `RoomSession`이 방 하나를 맡는다. 방 정보(context)의 `persona`는 아직 쓰지 않는다([open-questions.md](open-questions.md) 6장).

```
feed(PCM) ─▶ AudioStore.append            (방 전체 음성을 모은다: 사후 STT용)
          └▶ SpeechChunker.feed ─▶ 청크 ─▶ stt_queue ─▶ [STT 작업자] ─▶ transcript.partial
                                                              │ (강제로 잘린 청크는 같은 seq에 이어 붙인다)
                                                              ▼
                                                        final_queue ─▶ [확정 작업자]
                                                                          ├─ 다듬기(refine 그래프) ∥ 화자 판정(SpeakerChain)
                                                                          ├─ transcript.final
                                                                          ├─ 면접관 말이면 준비된 질문·제안한 꼬리질문과 매칭 → question.matched 또는 followup.matched
                                                                          ├─ 백엔드 저장(BackendOutbox, 기다리지 않음)
                                                                          └─ 지원자 말이면 꼬리질문 타이머 시작
```

- 작업자는 방마다 두 개(STT 단계, 확정 단계)이고 순서를 지킨다. 방끼리는 서로 기다리지 않는다. 모델 호출 수는 provider의 프로세스 전역 세마포어로 제한된다.
- 꼬리질문: 지원자 발화가 확정되고 1.5초(`LIVE_FOLLOWUP_DEBOUNCE_S`) 동안 새 말소리가 없으면 만든다. 그 사이에 다음 말이 시작되면 취소한다. 이어진 지원자 말이 20자(`LIVE_FOLLOWUP_MIN_CHARS`)보다 짧으면 만들지 않는다. `followup.request`가 오면 바로 만든다.
- 면접 흐름을 멈추지 않는 장치(큐 역압, 다듬기 건너뛰기, 저장 재시도)는 [concurrency.md](concurrency.md).

### 클라이언트 명령

| 메시지 | 처리 |
| --- | --- |
| `role` | 그 뒤에 만들어지는 청크의 화자를 지정(MANUAL). `AUTO`면 자동 판정 |
| `utterance.fix` | 발화의 화자를 고치고 `transcript.updated` 방송, 백엔드에 같은 seq로 다시 저장 |
| `question.asked` | 한 질문으로 표시, 백엔드에 `QUESTION_ASKED` 이벤트. 모르는 질문 id면 context를 다시 읽는다 |
| `question.added` | context를 다시 읽어 면접 중에 더한 질문을 비교 대상에 넣는다 |
| `followup.request` | 꼬리질문을 바로 만든다 |
| `ping` | 그 단말에만 `pong` |

명령은 EventBus의 control 채널로 방 주인 인스턴스에 전달된다(한 대일 때도 같은 경로).

## 5. 면접 후: 마무리와 사후 STT

`POST /internal/v1/rooms/{roomId}/finalize` (`app/live/manager.py`):

1. 남은 음성을 끝까지 처리한다: 청크 분할기를 비우고, 두 큐가 빌 때까지 기다리고(제한 20초, `LIVE_FINALIZE_FLUSH_TIMEOUT_S`), 백엔드 저장 큐를 비운다(그 뒤 최대 5초). 여기까지가 `flushed`.
2. 방의 모든 단말을 닫힘 코드 1000으로 닫는다.
3. `postStt: true`이고 모아 둔 음성이 있으면 사후 STT를 백그라운드로 시작하고 `202`를 돌려준다.
4. 사후 STT(`app/live/post_stt.py`): 방 전체 음성을 다시 청크로 자르고(최대 28초) → `stt.post`로 전사 → 다듬기 → 화자 판정 → `PUT /internal/v1/rooms/{roomId}/refined-transcript`. 실시간 전사에서 사람이 고친 화자(MANUAL)는 시간이 겹치는 발화에 물려준다. seq는 1부터 다시 매긴다.
5. 끝나면 `AudioStore.release`: `MEMORY_ONLY`면 음성을 지운다. 실패하면 `POST_STT_FAILED` 이벤트를 백엔드에 보내고, 음성을 `LIVE_POST_STT_HOLD_MINUTES`(기본 30분) 동안 붙잡은 채 다시 시도한다. 그래도 안 되면 음성을 지운다(`docs/audio-pipeline.md`).

백엔드는 `finalize` 응답을 받은 뒤 실시간 전사 기준(LIVE)으로 평가·보고서를 만들고, `refined-transcript`를 받으면 보정본(REFINED) 보고서를 만든다.

실시간 기능을 쓰지 않는 면접(신입은 백엔드 기본 설정)은 4장과 5장의 호출(방 정보 조회, WebSocket, `finalize`)이 오지 않고 3장의 사전 리포트 요청만 온다.

## 6. 서버가 여러 대일 때

- **오디오 WebSocket은 한 인스턴스에 붙는다.** 그 인스턴스가 방의 주인이 되어 `RoomSession`(청크·큐·대화 문맥)과 방 음성(메모리)을 가진다.
- **이벤트는 Redis로 중계된다.** 주인 인스턴스가 만든 이벤트는 `EventBus`(Redis pub/sub)의 방 채널에 실리고, 모든 인스턴스의 `LocalHub`가 구독해 자기에게 붙은 단말에 보낸다. 구독 단말은 어느 인스턴스에 붙어도 같은 이벤트를 받는다.
- 다른 인스턴스에 붙은 단말의 명령과, 다른 인스턴스로 들어온 `finalize`는 control 채널로 주인 인스턴스에 간다. `finalize`는 주인의 답을 기다렸다가 응답한다.
- 마이크 단말 중복은 `RoomStateStore`의 잠금(Redis `SET NX EX`)으로 인스턴스를 넘어 막는다. seq도 Redis `INCR`로 센다.

| 키 / 채널 | 내용 |
| --- | --- |
| `ia:room:{id}:seq` | 발화 번호 카운터 |
| `ia:room:{id}:audio` | 마이크 잠금(단말 id, TTL 30초, 오디오가 오는 동안 갱신) |
| `ia:room:{id}:owner` | 방 주인 인스턴스 id |
| `ia:room:{id}:events` (pub/sub) | 서버 → 클라이언트 이벤트 |
| `ia:room:{id}:control` (pub/sub) | 명령, finalize |

한계(지금 구현):

- 방의 음성과 대화 문맥은 주인 인스턴스의 메모리에만 있다. 그 인스턴스가 죽으면 그 방의 사후 STT는 할 수 없다(실시간 전사는 이미 백엔드에 저장돼 있다).
- 마이크 단말이 끊겼다가 **다른 인스턴스**로 다시 붙으면 새 인스턴스가 새 세션을 만든다. 앞부분 음성은 이전 인스턴스에 남는다. 로드밸런서에서 `/ws/v1/rooms/{roomId}` 경로로 같은 방을 같은 인스턴스에 보내도록(경로 해시) 설정하는 것을 권한다.
- Redis 구현(`RedisRoomStateStore`, `RedisEventBus`, Sentinel 연결)은 이 개발 환경에 Redis가 없어 **실제 Redis로 검증하지 못했다.** 인스턴스 두 대의 동작(이벤트 중계, 명령 전달, 다른 인스턴스로 온 finalize, 마이크 잠금)은 메모리 구현을 두 컨테이너가 함께 쓰게 한 테스트(`tests/test_state.py`)로 확인했고, Redis 구현은 가짜 Redis 객체로 코드 경로만 돌려 봤다.

## 7. 실패했을 때

| 무엇이 | 동작 |
| --- | --- |
| STT 호출 실패(재시도 뒤) | 그 청크를 건너뛰고 `error STT_FAILED`(retryable). 사후 STT가 채운다 |
| 다듬기가 느리거나 실패 | raw 그대로 `transcript.final`. 나중에 고쳐 보내지 않는다 |
| LLM 화자 판정 실패 | `UNKNOWN` / `NONE` |
| 꼬리질문 생성 실패 | `error LLM_FAILED`(retryable). 전사는 계속 |
| 백엔드 저장 실패 | 재시도 큐(지수 백오프, 6회). 면접 흐름은 계속 |
| 백엔드 context 읽기 실패 | 빈 context로 시작(질문 매칭 없음), `error BACKEND_UNAVAILABLE`, 뒤에서 다시 읽는다 |
| 이벤트 버스·상태 저장소(Redis) 끊김 | 전사·백엔드 저장·음성 모으기는 계속. 단말로 가는 이벤트만 빠진다(로그). seq는 세션 카운터로 잇는다 |
| 큐가 밀림 | 3개 이상 기다리면 partial과 다듬기 생략. 8개가 차거나 20초 넘게 기다린 청크는 실시간 전사를 건너뛰고 `error OVERLOADED`(사후 STT가 채운다) |
| REST에서 모델 실패 | `502 MODEL_FAILED` (백엔드가 작업을 FAILED로 두고 다시 시도) |
