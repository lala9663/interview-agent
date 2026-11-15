# 가정으로 구현한 것, 확인하지 못한 것

> 다루는 것: 정해지지 않아 가정으로 만든 부분과 그것이 바뀌면 고칠 위치, 실제로 확인하지 못한 것, API 명세에 제안할 것.
>
> 정해지면 이 문서에서 해당 줄을 지우고 코드·문서를 고친다.

## 1. 확인하지 못한 것과 확인한 것

| 항목 | 지금 상태 | 처음 확인할 때 볼 문서 |
| --- | --- | --- |
| 사내 LLM 실제 호출 | 못 했다. `openai_http`는 가짜 서버로 요청 모양만 테스트했다. `openai_compatible`(ChatOpenAI)은 객체가 만들어지는 것까지만 확인했다 | [providers.md](providers.md) 5장 |
| DS API Hub Whisper 실제 호출 | 못 했다. 규격을 모른다. `whisper_http`는 가짜 서버로 multipart 모양만 테스트했다 | [providers.md](providers.md) 4장 |
| 실제 Redis (pub/sub, 잠금, Sentinel) | 못 했다(Redis 없음). `RedisRoomStateStore`, `RedisEventBus`는 가짜 Redis 객체로 코드 경로만 돌려 봤고, `build_redis_client`(연결·Sentinel)는 실행해 본 적이 없다. 인스턴스 두 대의 동작은 메모리 구현을 공유시켜 흉내 낸 테스트로만 확인했다 | [setup.md](setup.md) 5장 |
| 실제 백엔드와의 연동 | **확인했다(2026-11-13, 로컬).** 백엔드(local, H2)와 함께 띄워 context 조회 → 전사·꼬리질문 저장 → finalize → 사후 STT → refined-transcript 콜백 → 백엔드의 AI 평가·보고서(LIVE, REFINED)까지 돌렸다. 모델은 fake였다 | API 명세 5장 |
| 실제 음성(VAD 임계값, 청크 경계) | 못 했다. 합성 소리로만 확인했다 | [audio-pipeline.md](audio-pipeline.md) |
| 실제 면접 대화의 화자 판정 정확도 | 못 했다. 합성 대화 수치만 있다 | [speaker-separation.md](speaker-separation.md) |
| 프롬프트 품질 | 못 봤다. fake LLM으로 흐름만 확인했다 | [prompts.md](prompts.md) 4장 |
| 프로세스 메모리 사용량 | 재지 않았다. 음성 크기의 계산값만 있다 | [audio-pipeline.md](audio-pipeline.md) 6장 |
| 실제 모델 지연에서의 동시 20개 방 | fake 지연(가정값)으로만 쟀다 | [concurrency.md](concurrency.md) |
| 리눅스 서버에서의 실행 | Windows 개발 PC에서만 돌렸다 | [setup.md](setup.md) |

## 2. 가정한 것과 바꿀 위치

| 가정 | 바뀌면 고칠 곳 |
| --- | --- |
| 사내 LLM은 OpenAI 형식(`/chat/completions`)이다 | `config/providers.yaml`의 options. 많이 다르면 `app/providers/openai_http.py`의 `_payload`, `_extract` |
| DS API Hub Whisper는 OpenAI 형식 multipart(`/v1/audio/transcriptions`)이고 청크마다 WAV 한 개를 동기로 받는다 | options. 다르면 `app/providers/whisper_http.py`의 `_build_request`, `_extract_text`, `_transcribe` |
| Whisper는 화자를 나눠 주지 않는다 | 준다면 `app/speaker/chain.py`의 `ChannelJudge`에 잇는다(`SpeakerContext.channel_speaker`) |
| Whisper는 문장 부호를 붙여 준다(질문 매칭의 문장 분리가 부호에 기댄다) | 안 붙으면 `app/matching/tfidf.py`의 `split_sentences`를 어미 기준으로 바꾸거나, 다듬은 글로 매칭한다 |
| 경력(화상, Knox Teams)도 브라우저가 PCM을 보낸다 | 트랙이 나뉘어 오면 `app/live/ws.py`(수신), `ChannelJudge`. Teams가 서버로 직접 보내면 수신부가 새로 필요하다 |
| 방마다 마이크 단말은 하나, 모노 한 채널이다 | 마이크 2개·2채널이면 API 명세 3.1(프레임 형식)과 `app/audio/` |
| 입장권은 HS256, 클레임 `sub`·`name`·`roomId`·`audio`·`exp` | `app/live/ticket.py` |
| 방 상태가 `LIVE`일 때만 오디오를 받는다. `CREATED`면 2초마다 백엔드 context를 다시 읽어 확인한다 | `app/live/session.py`의 `accepting_audio` |
| `finalize` 뒤 방의 단말을 닫힘 코드 1000으로 닫는다 | `app/live/manager.py`의 `_finalize_local` |
| `finalize` 응답은 남은 음성 처리(최대 20초)와 백엔드 저장 내보내기(그 뒤 최대 5초)가 끝난 뒤 돌려준다. 백엔드가 응답을 받고 바로 LIVE 기준 평가를 시작하기 때문이다 | `LIVE_FINALIZE_FLUSH_TIMEOUT_S` |
| 보정 전사의 seq는 1부터 다시 매긴다(실시간 전사의 seq와 다를 수 있다). 백엔드는 전사를 통째로 바꾸고 AI 평가를 보정 전사로 다시 한다(API 명세 5장) | `app/live/post_stt.py`의 `build_refined_transcript` |
| 사후 STT가 실패하면 음성을 30분 동안 메모리에 붙잡고 다시 시도한 뒤 지운다(`MEMORY_ONLY`, 디스크에 쓰지 않음). "녹음을 저장하지 않는다"는 원칙에서 이 시간이 허용되는지 현업·보안 확인이 필요하다 | `LIVE_POST_STT_HOLD_MINUTES`(0이면 즉시 지움), `app/live/post_stt.py`의 `run_post_stt` |
| 꼬리질문은 지원자 말이 끝나고 1.5초 조용하면 만든다. 그 전에 다음 말이 시작되면 만들지 않는다. 이어진 지원자 말이 20자보다 짧으면(맞장구 등) 만들지 않는다 | `LIVE_FOLLOWUP_DEBOUNCE_S`, `LIVE_FOLLOWUP_MIN_CHARS`, `app/live/followups.py` |
| 꼬리질문 id는 `f-<기준 seq>-<번호>` | `app/live/followups.py` |
| 60분 면접 전사 전체가 LLM 문맥에 한 번에 들어간다 | 안 들어가면 `app/graphs/evaluation.py`, `report.py`에 나누어 요약하는 단계 |
| 평가 근거는 면접관이 아닌 발화(`CANDIDATE`, `UNKNOWN`)만 인정한다 | `app/graphs/evaluation.py`의 `validate` |
| 브리핑의 JD 질문은 질문 은행 문장을 그대로 쓰면 `source: BANK`, 새로 만들면 `JD` | `app/graphs/briefing.py`, `app/prompts/briefing.py` |
| 블라인드 기준은 임시(일반 원칙) | `config/blind_rules.yaml` ([blind-rules.md](blind-rules.md)) |
| 실시간·보정 전사는 블라인드로 가리지 않는다(기록이므로) | 가려야 하면 `app/live/session.py`, `post_stt.py`에 단계 추가 |
| 방의 음성과 대화 문맥은 주인 인스턴스의 메모리에만 둔다(인스턴스가 죽으면 그 방의 사후 STT는 못 한다) | 견뎌야 하면 `AudioStore`를 공유 저장소 구현으로, 문맥은 백엔드의 `/rooms/{id}/utterances`에서 복구 |
| 백엔드 저장 재시도 큐는 메모리에 있다(6회, 최대 2000건) | `app/backend/outbox.py`, `BACKEND_RETRY_*` |
| 녹음은 저장하지 않는다 | [audio-pipeline.md](audio-pipeline.md) 7장 |

## 3. API 명세에 없어 이 레포가 정한 것

아래 가운데 `blind/check` 응답, `status`의 `stt`·`llm` 값과 `queueDepth`, `OVERLOADED` 오류의 뜻, 닫힘 코드 4429, 근거 없는 평가 항목, 보정 전사의 seq, `/internal/v1/stats`는 API 명세 3·4·5장에 반영됐다.

| 항목 | 정한 내용 |
| --- | --- |
| `POST /internal/v1/blind/check` 응답 | `{ clean, rulesVersion, results: [{ index, clean, violations: [{ topic, label, matched, ruleType }] }] }` |
| `status` 이벤트의 `stt`·`llm` 값 | `OK` \| `DEGRADED`(직전 호출 실패) \| `DOWN`(연속 3회 실패). API 명세 3.2에 세 값이 모두 있다 |
| `status.queueDepth` | 그 방에서 아직 끝나지 않은 발화 조각 수(기다리는 것 + 처리 중인 것) |
| `GET /internal/v1/stats` | 운영·부하 테스트용 추가 경로(`X-Internal-Key`). 방 수, 큐 깊이, provider 호출 수 |
| 평가에서 근거가 없는 항목 | `scores`에서 **뺀다.** `overallComment` 끝에 "근거가 부족해 점수를 내지 않은 항목: …"을 붙인다 |
| `ROOM_NOT_LIVE` | 방이 `ENDED`면 오류 뒤 닫힘 코드 1000. `CREATED`면 연결은 유지하고 오디오만 받지 않는다 |
| 닫힘 코드 4429 | 인스턴스가 받는 방 수(`LIVE_MAX_ROOMS`)를 넘는 마이크 단말이 붙을 때 |
| `OVERLOADED` 오류 이벤트 | 방의 큐가 밀려 청크를 건너뛸 때(연결은 유지, 5초에 한 번) |

## 4. API 변경 제안 (합의 전, 구현하지 않았다)

| 제안 | 이유 | 지금의 우회 |
| --- | --- | --- |
| 평가 응답의 `score`를 `null` 허용 | "근거가 없어 점수를 내지 않음"을 항목별로 표시할 수 있다 | 항목을 빼고 총평에 적는다 |
| `question.matched`에 질문 은행 매칭도 싣기(예: `bankQuestionId`) | 준비된 질문은 `question.matched`, 제안한 꼬리질문은 `followup.matched`로 알린다. 은행 질문과의 매칭은 화자 판정에만 쓰이고 화면에 못 알린다 | 알리지 않는다 |
| 면접 중 추가·채택된 질문을 백엔드가 Agent에 알리는 방법(백엔드 → Agent 알림, 또는 주기적 context 재조회 규칙) | 프런트를 거치지 않고 더해진 질문의 id를 Agent가 모른다 | 프런트가 `question.added`를 보내거나 `question.asked`로 모르는 id가 오면 context를 다시 읽는다 |
| 방이 끝났음을 알리는 이벤트(예: `room.ended`) | 지금은 닫힘 코드 1000으로만 안다 | 닫힘 코드 |
| 2채널 오디오(면접관 마이크 / 지원자 소리) 프레임 형식 | 화자를 채널로 정할 수 있다([speaker-separation.md](speaker-separation.md) 5장) | 모노만 받는다 |

## 5. 현업·다른 팀에 확인할 것

| 누구에게 | 무엇을 |
| --- | --- |
| DS API Hub 담당 | Whisper API 규격, 동시 요청 한도, 응답 시간, 최대 길이, 화자 정보·단어 시각 제공 여부 |
| 사내 LLM 담당 | API 형식, 문맥 길이, 동시 요청 한도, 응답 시간, JSON 모드 지원 |
| People팀(현업) | 블라인드·금지 질문 기준, 녹음 보관 정책, 면접실 마이크 구성, 화자 오류 허용 수준, 그룹 면접 여부 |
| Knox Teams 담당 | 화상 면접 음성을 받는 방법(참가자별 트랙) |
| 인프라 | Redis(Sentinel) 접속 정보, 로드밸런서의 WebSocket·경로 해시 지원, 서버 메모리 |
