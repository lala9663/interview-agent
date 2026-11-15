---
name: change-api-contract
description: REST 경로·요청·응답 필드, WebSocket 이벤트, 열거형, 오류 코드처럼 다른 레포(frontend, backend)와 맞춰야 하는 것을 바꾸거나 더해야 할 때 쓴다. 혼자 바꾸지 않고 제안 → 합의 → 세 레포 동시 반영으로 가는 절차와 이 레포에서 고칠 파일을 담고 있다.
---

# API 명세 바꾸기

API 명세 원본은 세 레포 공통 문서의 `03-api-contract.md`다. 이 레포의 `docs/contract/api-contract.md`는 복사본이다.
이 레포의 몫은 3장(WebSocket), 4장(백엔드 → Agent), 5장(Agent → 백엔드)이다.

## 1. 정말 API 변경인가

| 하려는 일 | API 변경? |
| --- | --- |
| 응답에 선택 필드를 더한다 | 예(더하기). 받는 쪽은 모르는 필드를 무시하므로 깨지지 않지만 문서에는 있어야 한다 |
| 필드 이름·타입을 바꾸거나 지운다, 열거형 값을 지운다 | 예(깨는 변경) |
| 열거형 값을 더한다 | 예(깨질 수 있음). 받는 쪽이 모르는 값을 견디는지 확인한다 |
| 새 WebSocket 이벤트·오류 코드·닫힘 코드 | 예 |
| 프롬프트·모델·임계값·내부 구조를 바꾼다 | 아니오 |
| API 명세에 모양이 안 적힌 것을 이 레포가 정한다 | API 명세에 더해 달라고 제안하고, 그때까지 `docs/open-questions.md`에 적는다(`blind/check` 응답과 `/internal/v1/stats`가 이렇게 API 명세에 들어갔다) |

## 2. 절차

1. **제안을 쓴다**(이슈나 PR 설명): 무엇을, 왜, 바뀌는 JSON 예시(전·후), 영향받는 레포와 화면·기능, 깨는 변경이면 옮겨 가는 순서.
2. **세 레포 담당이 합의한다.** 합의 전에는 `app/schemas/`의 API 명세 타입을 고치지 않는다. 급하면 기존 API 명세를 지키는 범위에서만 구현한다(예: 새 열거형 값이 필요해도 합의 전에는 API 명세에 있는 값으로 바꿔 내보낸다. 화자 판정의 규칙 방법이 API 명세에 들어가기 전에 `SPEAKER_RULE_WIRE_METHOD`로 그렇게 했다).
3. **같은 날 세 레포에 반영한다.** 원본을 고치고 세 레포의 `docs/contract/api-contract.md`를 같은 내용으로 덮어쓴다.
4. **이 레포에서 한 PR로 고친다.**
   - `docs/contract/api-contract.md` (복사본)
   - `app/schemas/` — `internal.py`(4장), `backend.py`(5장), `ws.py`(3장 이벤트 만들기 함수), `common.py`(열거형)
   - 구현: `app/api/internal.py`, `app/live/`, `app/backend/client.py`(fake 포함), `app/demo/data.py`(데모 context)
   - 테스트: `tests/test_api_backend.py`(REST 필드 이름), `tests/test_ws.py`(이벤트 모양), 백엔드 요청 모양
   - `docs/open-questions.md`에서 해결된 항목 지우기
5. 배포 순서는 아래 "배포 순서"를 따른다.

## 배포 순서 (세 레포 공통)

이 문단은 세 레포의 `change-api-contract` skill에 같은 내용으로 들어 있다. 고칠 때는 셋을 함께 고친다.

- **더하는 변경**(필드·경로·이벤트·오류 코드 추가): 어느 쪽이 먼저 나가도 되게 만든다. 받는 쪽은 그 값이 **없어도** 동작하고, **모르는 값이 와도** 무시한다. 그래도 순서를 정해야 하면 내보내는 쪽이 먼저다.
- **깨는 변경**(이름 변경, 삭제, 타입 변경, 필수화, 뜻 변경): 한 번에 바꾸지 않는다. 추가 → 옮기기 → 삭제 세 단계로 한다. 받는 쪽이 먼저 옛것과 새것을 모두 받게 하고, 그다음 보내는 쪽이 새것으로 바꾸고, 마지막에 옛것을 지운다.
- **열거형 값 추가**는 깨질 수 있다. 받는 쪽이 모르는 값을 받아도 죽지 않는지 먼저 확인한다.
- API 명세(원본과 세 복사본)는 같은 날 고친다. 코드는 위 순서를 지키면 같은 날이 아니어도 된다.

## 3. 이 레포의 규칙

- JSON은 camelCase. 타입은 `CamelModel`을 상속한다(파이썬 필드는 snake_case, alias가 자동으로 붙는다).
- 응답은 `response_model`로 지정한다. dict를 손으로 만들지 않는다(WebSocket 이벤트는 `app/schemas/ws.py`의 함수로만 만든다).
- 오류는 `{ "code", "message", "details" }` 형식이다(`app/api/errors.py`). 새 오류 코드는 API 명세 표에 있어야 한다.
- 요청에서 모르는 필드는 무시한다(`extra="ignore"`). 그래서 상대가 필드를 **더하는** 것은 우리 쪽 수정 없이 통과한다.
- 운영용 경로(`/internal/v1/stats`처럼 화면이 직접 쓰지 않는 것)도 `X-Internal-Key` 뒤에 두고 API 명세 4장에 적는다. 백엔드가 그 응답 모양에 기대기 때문이다.

## 확인 목록

- [ ] 합의된 제안이 있다(링크)
- [ ] 세 레포의 API 명세 복사본이 같다
- [ ] 스키마·구현·fake·테스트를 한 PR에서 고쳤다
- [ ] `pytest` 통과, `scripts/ws_demo_client.py`로 이벤트 모양 확인
- [ ] 깨는 변경이면 배포 순서를 PR에 적었다
