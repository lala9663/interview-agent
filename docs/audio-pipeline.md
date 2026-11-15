# 오디오 파이프라인

> 다루는 것: 음성이 들어와 전사가 나가기까지의 단계, 청크를 자르는 기준, 지연 예산, 메모리 추정, 녹음 저장 방식 전환.
>
> 상태: 합성 PCM(말소리 흉내)과 fake STT로 확인했다. **실제 면접 음성과 실제 Whisper로는 확인하지 못했다.** VAD 임계값과 지연 숫자는 실제 환경에서 다시 맞춰야 한다.

## 1. 입력

- 브라우저가 WebSocket 바이너리 프레임으로 보낸다: **PCM 16비트 리틀엔디언, 모노, 16kHz**. 프레임 길이는 자유(권장 100ms = 3200바이트). 홀수 바이트나 프레임 경계가 어긋나도 서버가 이어 붙인다.
- `audio: true` 입장권을 가진 단말(방마다 하나)만 보낼 수 있다. 다른 단말이 보낸 바이너리는 버린다.
- 방 상태가 `LIVE`가 아니면 받지 않고 `ROOM_NOT_LIVE`를 알린다(2초마다 백엔드에 상태를 다시 묻는다).

## 2. 단계

```
프레임 ─▶ AudioStore.append ─────────────────────────────────────────▶ (면접 뒤) 사후 STT
       └▶ VAD(20ms 프레임마다 말소리?) ─▶ 발화 청크 ─▶ stt_queue ─▶ STT ─▶ partial ─▶ 다듬기 ∥ 화자 판정 ─▶ final
```

Whisper large-v3는 실시간(스트리밍) 모델이 아니다. 그래서 **말이 끝날 때까지 메모리에 모았다가** 청크 하나를 한 번에 보낸다. 글은 말이 끝난 뒤에 뜬다.

## 3. 청크를 자르는 기준

`app/audio/vad.py`(말소리 검출), `app/audio/chunker.py`(청크 분할). 설정은 `AUDIO_` 환경변수(`app/settings/sections.py`의 `AudioSettings`).

| 설정 | 기본 | 뜻 |
| --- | ---: | --- |
| `AUDIO_VAD` | `energy` | VAD 구현 이름. 인터페이스(`Vad`)를 구현해 바꿀 수 있다 |
| `AUDIO_VAD_FRAME_MS` | 20 | 말소리 판정 단위 |
| `AUDIO_VAD_THRESHOLD_DB` | -40 | 프레임 RMS가 이 값(dBFS)보다 크면 말소리 |
| `AUDIO_VAD_ADAPTIVE` / `AUDIO_VAD_NOISE_MARGIN_DB` | true / 8 | 바닥 잡음(말소리가 아닌 프레임의 이동 평균)보다 이만큼 커야 말소리. 일정한 소음(에어컨)에 대응 |
| `AUDIO_MIN_SPEECH_MS` | 120 | 말소리가 이만큼 이어져야 발화 시작으로 본다(짧은 소리는 버린다) |
| `AUDIO_MIN_SILENCE_MS` | 700 | 조용한 구간이 이만큼 이어지면 발화가 끝난 것으로 본다 |
| `AUDIO_PAD_MS` | 200 | 청크 앞뒤에 붙이는 여유(첫·끝 음절이 잘리지 않게) |
| `AUDIO_MAX_CHUNK_MS` | 15000 | 발화가 이보다 길면 강제로 자른다 |
| `AUDIO_POST_MAX_CHUNK_MS` | 28000 | 사후 STT의 청크 상한(Whisper의 창 30초 안) |

긴 발화의 처리:

- 최대 길이 3초 전부터 짧은 쉼(160ms)이 보이면 거기서 자른다(낱말 중간을 피한다). 쉼이 없으면 최대 길이에서 자른다.
- 강제로 잘린 청크의 글은 `transcript.partial`로 먼저 나가고, 다음 청크의 글이 **같은 seq에 이어 붙는다.** 말이 끝나면 이어 붙인 전체가 `transcript.final`로 한 번 나간다.

에너지 VAD의 한계: 말소리와 크기가 비슷한 소리(문 닫는 소리, 기침, 타자 소리)를 구분하지 못한다. 그런 청크는 Whisper가 빈 글이나 지어낸 문장을 낸다. 빈 글은 버리고, 지어낸 문장은 `whisper_http`의 `drop_phrases`로 거른다.

## 4. partial과 final

| 이벤트 | 언제 | 내용 |
| --- | --- | --- |
| `transcript.partial` | 청크의 STT가 끝났을 때 | 다듬기 전의 글. 강제 분할이면 지금까지 이어 붙인 글 |
| `transcript.final` | 다듬기와 화자 판정이 끝났을 때 | `rawText`(STT 그대로), `refinedText`(다듬은 글), `speaker`, `speakerMethod`, `startMs`, `endMs` |

- `startMs`/`endMs`는 방 음성의 처음부터 센 위치다(받은 표본 수 기준, 벽시계가 아니다).
- 다듬기(`app/graphs/refine.py`)는 군말·반복을 지우고 문장 부호를 고친다. 결과가 원문보다 1.3배 넘게 길거나 0.4배보다 짧으면 뜻이 바뀐 것으로 보고 원문을 쓴다.
- 다듬기가 `LIVE_REFINE_TIMEOUT_S`(4초)를 넘거나 실패하면 raw를 그대로 final로 낸다. 나중에 고쳐 보내지 않는다(면접 뒤 보정 전사에서 다듬어진다).
- STT 결과가 빈 글이면 seq를 쓰지 않는다. seq는 빈 번호 없이 이어진다.

## 5. 지연 예산

말이 끝난 뒤 `transcript.final`이 뜨기까지:

| 단계 | 시간 | 근거 |
| --- | ---: | --- |
| 침묵 대기 | 0.7초 | `AUDIO_MIN_SILENCE_MS` |
| STT | 모델에 달림 | 부하 테스트 가정: 1.2초 + 오디오 1초당 0.06초 |
| 다듬기 ∥ 화자 판정 | 모델에 달림(둘은 동시에 돈다) | 부하 테스트 가정: 0.6초. 상한 `LIVE_REFINE_TIMEOUT_S` 4초 |
| 대기(다른 방과 겹칠 때) | 0 ~ | provider 세마포어에서 기다리는 시간 |

`transcript.partial`은 STT가 끝나면 바로 나간다(다듬기를 기다리지 않는다).

꼬리질문은 그 뒤에 `LIVE_FOLLOWUP_DEBOUNCE_S`(1.5초) 동안 새 말이 없을 때 만들기 시작한다. 그 사이 면접관이 다음 질문을 시작하면 만들지 않는다.

fake 모델에 위 가정 지연을 넣어 방 20개를 돌린 측정값은 [concurrency.md](concurrency.md)에 있다. **실제 Whisper·LLM의 응답 시간은 재지 못했다.** 붙인 뒤 그 숫자로 이 표를 다시 채운다.

지연을 줄이는 방법과 대가:

| 방법 | 대가 |
| --- | --- |
| `AUDIO_MIN_SILENCE_MS`를 줄인다(예: 500) | 말 중간의 쉼에서 발화가 쪼개진다 |
| `AUDIO_MAX_CHUNK_MS`를 줄인다(예: 8000) | 긴 답의 partial이 일찍 뜬다. STT 호출 수가 늘고 문맥이 짧아져 정확도가 떨어질 수 있다 |
| 다듬기를 끈다(`LIVE_REFINE_TIMEOUT_S`를 아주 작게) | 군말이 그대로 보인다 |
| 화자 판정에서 LLM을 뺀다(`SPEAKER_CHAIN`에서 `llm` 제거) | UNKNOWN이 늘어난다 |

## 6. 메모리 추정

방 전체 음성을 사후 STT 때까지 메모리에 둔다(기본 `MemoryAudioStore`).

16kHz × 2바이트 = 초당 32,000바이트 = **분당 1.92MB**(침묵 구간도 그대로 모은다).

| 면접 길이 | 방 1개 | 방 20개 | 방 30개 |
| ---: | ---: | ---: | ---: |
| 30분 | 58MB | 1.15GB | 1.73GB |
| 60분 | 115MB | 2.30GB | 3.46GB |
| 90분(상한) | 173MB | 3.46GB | 5.18GB |

- 상한: `AUDIO_MAX_MINUTES_PER_ROOM`(기본 90분). 넘으면 **가장 오래된 구간부터 버리고** 백엔드에 `AUDIO_DROPPED` 이벤트를 보낸다(1분에 한 번, 누적 `droppedMs`). 버린 구간은 보정 전사에서 실시간 전사로 대신한다.
- 사후 STT를 도는 동안 그 방의 음성은 잠깐 약 3배가 된다(모아 둔 조각 + 이어 붙인 사본 + 청크 사본). 여러 방이 한꺼번에 끝나도 `LIVE_POST_STT_CONCURRENT_ROOMS`(기본 2) 방씩만 돌리므로 추가분은 최대 2방 × 2배다. 예: 30분 면접 20개가 동시에 끝나면 1.15GB + 약 0.23GB.
- 그 밖의 방당 메모리(청크 버퍼 최대 15초 ≈ 0.5MB, 대화 문맥, 질문 색인)는 음성에 비해 작다.
- 위 표는 계산값이다. 프로세스의 실제 메모리 사용량은 재지 않았다.

서버 메모리가 모자라면: (1) `AUDIO_MAX_MINUTES_PER_ROOM`을 면접 최대 길이에 맞춰 낮춘다, (2) `AUDIO_STORE=file`로 디스크를 임시 공간으로 쓴다(아래), (3) 인스턴스를 늘린다.

## 7. 녹음 저장 방식 전환

**기본은 저장하지 않는다.** 방 음성은 메모리에만 있고 사후 STT가 끝나면(실패해도) 지운다.

| `AUDIO_STORE` | `AUDIO_RETENTION` | 동작 | finalize 응답의 `audioRetention` |
| --- | --- | --- | --- |
| `memory` | `MEMORY_ONLY` | **기본.** 메모리에만 두고 끝나면 삭제 | `MEMORY_ONLY` |
| `file` | `MEMORY_ONLY` | 면접 중 디스크의 임시 파일(`room-<id>.pcm`)에 모으고 끝나면 삭제. 메모리 절약용 | `MEMORY_ONLY` |
| `file` | `KEEP` | 끝나면 `room-<id>-<시각>.wav`로 남기고 `AUDIO_TTL_HOURS`(기본 72) 뒤 삭제 | `KEEP` |
| `memory` | `KEEP` | 시작할 때 오류(메모리는 보관할 수 없다) | - |

저장으로 바꾸는 절차:

1. 정책 확인이 먼저다: 녹음 보관에 대한 지원자 동의, 보관 기간, 접근 권한, 파기 방법.
2. 환경변수: `AUDIO_STORE=file`, `AUDIO_RETENTION=KEEP`, `AUDIO_FILE_DIR=<경로>`, `AUDIO_TTL_HOURS=<시간>`.
3. 암호화: `FileAudioStore`에 `encrypt(bytes) -> bytes` 자리가 있다. `app/container.py`의 `Container(audio_encrypt=...)`에 함수를 넘기면 WAV를 그 함수에 통과시켜 `.wav.enc`로 쓴다. 지금은 연결된 함수가 없다(자리만 있다). 면접 중의 임시 `.pcm`은 암호화되지 않으므로 `AUDIO_FILE_DIR`을 암호화된 볼륨에 둔다.
4. 보관 기간이 지난 파일은 서버 시작 때와 방이 끝날 때마다 지운다(주기적인 작업은 없다. 방이 한동안 없으면 지워지는 시점이 늦어진다).
5. 파일이 아닌 저장소(사내 오브젝트 스토리지 등)가 필요하면 `app/audio/store.py`의 `AudioStore`를 구현하고 `build_audio_store`에 분기를 더한다.
6. 서버가 여러 대면 파일은 방 주인 인스턴스의 디스크에 남는다. 공유 저장소가 필요하면 5번으로 간다.

`FileAudioStore`는 테스트(`tests/test_audio.py`)로 확인했다: 덧붙이기, WAV 변환, 암호화 자리, 보관 기간 삭제, `MEMORY_ONLY`일 때 남는 파일 없음.

## 8. 사후 STT

방이 끝나면(`finalize`) 모아 둔 음성 전체를 다시 전사한다(`app/live/post_stt.py`).

- 같은 VAD로 다시 자르되 청크를 최대 28초까지 길게 잡는다(문맥이 넓어진다).
- `stt.post` 역할로 부른다(실시간과 다른 모델·동시 실행 수를 줄 수 있다. 기본 동시 2).
- 실시간에서 밀려서 건너뛴 구간, STT가 실패한 구간도 여기서 채워진다.
- 다듬기와 화자 판정을 다시 한다. 실시간 전사에서 사람이 고친 화자(MANUAL)는 시간이 절반 넘게 겹치는 발화에 물려준다.
- 결과를 `PUT refined-transcript`로 보낸다. seq는 1부터 다시 매긴다(실시간 전사의 seq와 같지 않을 수 있다).
- 2번 시도해 실패하면 `POST_STT_FAILED` 이벤트를 보내고(`data.willRetry: true`), 음성을 버리지 않고 `LIVE_POST_STT_HOLD_MINUTES`(기본 30분) 동안 붙잡은 채 `LIVE_POST_STT_RETRY_INTERVAL_S`(기본 60초)마다 다시 시도한다. 성공하면 평소처럼 `PUT refined-transcript`를 보낸다(백엔드는 늦게 와도 받는다).
  - 붙잡는 곳은 AudioStore 그대로다. `MEMORY_ONLY`면 메모리에만 있고 디스크에 쓰지 않는다. 서버가 내려가면 사라진다.
  - 그 시간이 지나도 안 되면 `POST_STT_FAILED`를 한 번 더 보내고(`data.willRetry: false`) 음성을 놓아 준다. 이때 보고서는 실시간 전사 기준(`LIVE`)으로 남는다.
  - 기다리는 동안에는 동시 실행 자리(`LIVE_POST_STT_CONCURRENT_ROOMS`)를 쥐지 않는다. 다만 음성은 메모리에 남으므로, Whisper가 오래 죽어 있으면 끝난 방의 음성이 최대 30분치 쌓인다(30분 면접 한 방에 약 58MB). 메모리가 빠듯하면 값을 줄이거나 `AUDIO_STORE=file`을 쓴다.
  - `LIVE_POST_STT_HOLD_MINUTES=0`이면 붙잡지 않고 실패 즉시 놓아 준다.
- 어느 경우든 끝나면 음성을 놓아 준다.
- `finalize`가 오지 않은 방은 마이크가 끊긴 뒤 30분(`LIVE_IDLE_CLOSE_MINUTES`)이 지나면 같은 절차로 정리한다.
