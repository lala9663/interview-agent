---
name: tune-audio-pipeline
description: 실시간 전사가 늦다, 말이 중간에 잘린다, 잡음이 전사된다, 방이 많을 때 밀린다, 메모리가 모자란다, 녹음을 저장하도록 바꿔야 한다 같은 요청에 쓴다. 청크 기준(VAD), 큐 역압, provider 동시 실행 수, 음성 보관 방식(AudioStore)을 어디서 어떻게 바꾸고 무엇으로 확인하는지 담고 있다.
---

# 오디오 파이프라인 조정

흐름: `PCM 프레임 → AudioStore에 모음 → VAD 청크 → stt_queue → STT → partial → final_queue → 다듬기 ∥ 화자 판정 → final`.
코드: `app/audio/`(VAD·청크·보관), `app/live/session.py`(큐·역압), `app/providers/base.py`(동시 실행·재시도).
배경과 숫자는 `docs/audio-pipeline.md`, `docs/concurrency.md`.

## 1. 증상별로 볼 설정

| 증상 | 먼저 볼 것 | 설정 |
| --- | --- | --- |
| 말이 끝나고 글이 늦게 뜬다 | 침묵 대기 시간, STT 응답 시간 | `AUDIO_MIN_SILENCE_MS`(기본 700). 줄이면 빨라지지만 말 중간의 쉼에서 잘린다 |
| 한 문장이 여러 발화로 쪼개진다 | 침묵 대기가 짧다 | `AUDIO_MIN_SILENCE_MS`를 올린다(900~1200) |
| 긴 답이 한참 뒤에 한꺼번에 뜬다 | 최대 청크 길이 | `AUDIO_MAX_CHUNK_MS`(기본 15000). 줄이면 partial이 자주 뜬다(같은 seq로 이어 붙는다) |
| 잡음·숨소리가 전사된다 | VAD 임계값, 최소 말소리 길이 | `AUDIO_VAD_THRESHOLD_DB`(기본 -40, 올리면 둔감), `AUDIO_MIN_SPEECH_MS`(기본 120) |
| 작은 목소리가 빠진다 | VAD 임계값 | `AUDIO_VAD_THRESHOLD_DB`를 내린다(-45~-50). `AUDIO_VAD_NOISE_MARGIN_DB`도 본다 |
| 말 첫 음절·끝 음절이 잘린다 | 앞뒤 여유 | `AUDIO_PAD_MS`(기본 200) |
| 무음인데 "시청해주셔서 감사합니다" 같은 글이 나온다 | Whisper가 지어낸 문장 | `whisper_http`의 `options.drop_phrases`, VAD 임계값 |
| 방이 많으면 밀린다 / `OVERLOADED` | STT 동시 실행 수, 큐 한계 | `providers.yaml`의 `stt.live.max_concurrency`, `LIVE_STT_QUEUE_SOFT/HARD` |
| final의 다듬기가 자주 빠진다(raw 그대로) | LLM이 제한 시간을 넘는다 | `LIVE_REFINE_TIMEOUT_S`(기본 4), `llm.fast.max_concurrency` |
| 꼬리질문이 너무 빨리/늦게 뜬다 | 답이 끝났다고 보는 대기 시간 | `LIVE_FOLLOWUP_DEBOUNCE_S`(기본 1.5) |
| 모델이 죽었다 살아났는데 한동안 전사가 안 나온다 | 차단기가 쉬는 시간 | `providers.yaml`의 `cooldown_s`(기본 30초) |
| 면접이 끝날 때 Whisper가 죽어 있어 보정본이 안 생긴다 | 음성을 붙잡는 시간 | `LIVE_POST_STT_HOLD_MINUTES`(기본 30), `LIVE_POST_STT_RETRY_INTERVAL_S`(기본 60) |
| 서버 메모리가 모자란다 | 방 음성 상한, 보관 방식 | `AUDIO_MAX_MINUTES_PER_ROOM`, `AUDIO_STORE=file`(디스크를 임시 공간으로) |

설정의 원본은 `app/settings/sections.py`의 `AudioSettings`, `LiveSettings`다. 기본값을 바꾸면 README 표와 `docs/audio-pipeline.md`를 같이 고친다.

## 2. 바꾼 뒤 확인

1. `pytest tests/test_audio.py tests/test_ws.py`
2. `python scripts/ws_demo_client.py --turns 6` — partial/final이 발화마다 한 번씩 오는지.
3. `python scripts/load_test.py --rooms 20 30` — "말 끝 → final", "답 끝 → 꼬리질문", 큐 깊이, 버린 청크. 결과 표가 달라졌으면 `docs/concurrency.md`를 고친다.
4. **실제 음성으로 확인한다.** 합성 PCM은 VAD가 잡기 쉬운 소리라 임계값 조정의 근거가 못 된다. 실제 면접실 마이크로 녹음한 구간(조용한 방, 에어컨 소리, 두 사람이 겹쳐 말할 때)을 `SpeechChunker`에 넣어 청크 경계를 본다.

## 3. VAD를 다른 것으로 바꿀 때

`app/audio/vad.py`의 `Vad`를 구현하고(`is_speech(frame) -> bool`, 프레임은 int16 20ms), `VADS`에 이름을 더하고 `build_vad`에 생성 코드를 더한 뒤 `AUDIO_VAD=<이름>`으로 고른다. 기본값(`energy`)은 바꾸지 않고 합친다. 모델 기반 VAD는 의존성 규칙(AGENTS.md 2장)을 먼저 본다.

## 4. 녹음을 저장하도록 바꿀 때

기본은 저장하지 않는다(`AUDIO_RETENTION=MEMORY_ONLY`, `AUDIO_STORE=memory`). 정책이 바뀌어 저장해야 하면:

1. 설정만으로: `AUDIO_STORE=file`, `AUDIO_RETENTION=KEEP`, `AUDIO_FILE_DIR=<경로>`, `AUDIO_TTL_HOURS=<보관 시간>`. finalize 응답의 `audioRetention`이 `KEEP`으로 바뀐다. 방이 끝나면 `room-<id>-<시각>.wav`가 남고 보관 시간이 지나면 지워진다.
2. 암호화가 필요하면 `Container(audio_encrypt=<bytes -> bytes 함수>)`로 넘긴다(`app/container.py`). 그러면 `.wav.enc`로 쓴다. 사내 암호화 모듈·KMS를 이 함수 안에서 부른다.
3. 파일이 아니라 사내 저장소(오브젝트 스토리지 등)에 넣어야 하면 `app/audio/store.py`의 `AudioStore`를 구현하고 `build_audio_store`에 분기를 더한다.
4. 저장하면 개인정보 처리(동의, 보관 기간, 접근 권한, 파기)가 따라온다. 코드보다 먼저 정책 확인이 필요하다고 사용자에게 알린다. 백엔드·프론트의 안내 문구도 바뀌어야 한다(`audioRetention` 값을 본다).
5. `tests/test_audio.py`의 FileAudioStore 테스트를 참고해 새 구현의 테스트를 더한다.

## 5. 하지 말 것

- 디버깅하려고 청크를 WAV로 떨어뜨리는 코드를 넣지 않는다(녹음 저장이 된다). 필요하면 로컬에서만 쓰고 커밋하지 않는다.
- STT·LLM 호출에 자체 세마포어·재시도를 더하지 않는다(`providers.yaml` 값으로 조정).
- 면접 흐름을 기다리게 만드는 변경을 하지 않는다: 밀리면 건너뛰고(부분 전사·다듬기), 사후 STT가 채운다.
