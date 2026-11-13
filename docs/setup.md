# 설치와 실행

> 다루는 것: 개발 PC에서 띄우기, 사내망 반입, 백엔드·프론트와 같이 띄우기, 서버 여러 대.

## 1. 개발 PC (모델·백엔드·Redis 없이)

필요한 것: Python 3.12.

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements-dev.txt      # 리눅스·맥: .venv/bin/python
.venv/Scripts/python -m pytest
.venv/Scripts/python -m uvicorn app.main:app --port 8000 --reload
```

기본 설정은 fake 모델, fake 백엔드, 메모리 상태다. 확인:

```bash
curl http://localhost:8000/health
curl -X POST http://localhost:8000/internal/v1/blind/check -H "X-Internal-Key: dev-internal-key" \
     -H "Content-Type: application/json" -d '{"texts": ["결혼은 하셨나요?"]}'
.venv/Scripts/python scripts/ws_demo_client.py --turns 6 --finalize
```

- 경로에 한글·공백이 있으면 명령의 경로를 따옴표로 감싼다.
- Windows 콘솔에서 한글이 깨지면 `set PYTHONIOENCODING=utf-8`(PowerShell: `$env:PYTHONIOENCODING="utf-8"`).
- Windows의 앱 실행 정책(Smart App Control 등)이 파이썬 네이티브 모듈을 막는 PC에서는 `openai` SDK의 `jiter`를 불러오지 못할 수 있다(이 레포를 만든 PC에서 한 번 발생). fake와 `openai_http` 구현은 이 모듈을 쓰지 않는다.

## 2. 사내망 반입

- 반입할 패키지 목록은 `requirements.lock.txt`(간접 의존성 포함, Python 3.12 기준 `pip freeze`)다. 사내 pip 미러에 이 목록을 올린다. 서버가 리눅스면 리눅스용 wheel을 받아야 한다(`numpy`, `pydantic_core`, `orjson`, `jiter`, `tiktoken`, `xxhash`, `zstandard`, `ormsgpack`, `uuid_utils`, `regex` 등이 네이티브 wheel이다).
- 미러에서 설치: `pip install --index-url <사내 미러> -r requirements.txt`
- `langchain-openai`(와 `openai`, `tiktoken`, `jiter`)를 반입하기 어렵다면 `openai_http` 구현만 쓰면 된다. 그 경우 `requirements.txt`에서 `langchain-openai`를 빼도 fake와 `openai_http`는 돈다(`openai_compatible`만 못 쓴다).
- 반입 뒤에는 외부로 나갈 수 없다. `AGENTS.md`와 `.opencode/skills/`가 작업 규칙과 절차를 담고 있다.

## 3. 백엔드·프론트와 같이 띄우기

| 설정 | 값 |
| --- | --- |
| `BACKEND_MODE` | `http` |
| `BACKEND_BASE_URL` | `http://localhost:8080` (백엔드 주소) |
| `INTERNAL_API_KEY` | 백엔드의 값과 같게 |
| `ROOM_TICKET_SECRET` | 백엔드의 값과 같게(32바이트 이상) |
| `APP_CORS_ORIGINS` | 프론트 주소(기본 `http://localhost:5173`) |

- 포트는 8000이다(계약의 `agentWsUrl`이 `ws://localhost:8000/ws/v1/rooms/{roomId}`).
- 백엔드가 아직 없으면 `BACKEND_MODE=fake`로 두고 `scripts/make_ticket.py --room 9001 --audio`로 입장권을 만들어 프론트에 넣어 볼 수 있다.

## 4. 사내 모델 연결

[providers.md](providers.md)를 따른다. 요약: `config/providers.yaml`(또는 `PROVIDERS__…` 환경변수)에서 `impl`, `model`, `options.base_url`을 바꾸고 키를 환경변수에 넣는다.

## 5. 서버 여러 대

```
STATE_STORE=redis
STATE_EVENT_BUS=redis
STATE_REDIS_URL=redis://<host>:6379/0
# Sentinel이면:
STATE_REDIS_SENTINELS=host1:26379,host2:26379,host3:26379
STATE_REDIS_SENTINEL_MASTER=mymaster
STATE_REDIS_PASSWORD=<비밀번호>
APP_INSTANCE_ID=agent-1          # 인스턴스마다 다르게
```

- 로드밸런서는 WebSocket을 지원해야 하고, `/ws/v1/rooms/{roomId}`는 경로 해시로 같은 방을 같은 인스턴스에 보내는 것을 권한다([architecture.md](architecture.md) 6장).
- uvicorn 작업자(`--workers`)를 여러 개 띄우는 것도 "서버 여러 대"와 같다: 프로세스마다 메모리가 따로이므로 Redis가 있어야 한다. Redis 없이 한 대로 쓸 때는 `--workers 1`.
- Redis 연결은 실제 Redis로 검증하지 못했다. 처음 붙일 때 확인할 것: 두 인스턴스에 단말을 하나씩 붙여 이벤트가 양쪽에 오는지, 마이크 단말 두 개가 서로 다른 인스턴스에서 4409로 막히는지, 다른 인스턴스로 보낸 `finalize`가 처리되는지.

## 6. 운영에서 볼 것

| 경로 | 내용 |
| --- | --- |
| `GET /health` | `{"status":"UP","llm":"<impl>","stt":"<impl>"}` |
| `GET /internal/v1/stats` (`X-Internal-Key`) | 방 수, 방별 큐 깊이, provider 호출·실패·대기 수, 백엔드 재시도 큐 길이. 계약에 없는 운영용 경로다 |

로그에는 방 번호·seq·오류만 남긴다. 지원자 자료와 전사 내용은 남기지 않는다(fake 백엔드의 데모 로그 제외).
