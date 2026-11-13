---
name: change-api-contract
description: REST 경로·요청·응답 필드, WebSocket 이벤트, 열거형, 오류 코드처럼 다른 레포(frontend, backend)와 맞춰야 하는 것을 바꾸거나 더해야 할 때 쓴다. 혼자 바꾸지 않고 제안 → 합의 → 세 레포 동시 반영으로 가는 절차와 이 레포에서 고칠 파일을 담고 있다.
---

# API 계약 바꾸기

계약 원본은 세 레포 공통 문서의 `03-api-contract.md`다. 이 레포의 `docs/contract/api-contract.md`는 복사본이다.
이 레포의 몫은 3장(WebSocket), 4장(백엔드 → Agent), 5장(Agent → 백엔드)이다.

## 1. 정말 계약 변경인가

| 하려는 일 | 계약 변경? |
| --- | --- |
| 응답에 선택 필드를 더한다 | 예(더하기). 받는 쪽은 모르는 필드를 무시하므로 깨지지 않지만 문서에는 있어야 한다 |
| 필드 이름·타입을 바꾸거나 지운다, 열거형 값을 더하거나 지운다 | 예(깨는 변경) |
| 새 WebSocket 이벤트·오류 코드·닫힘 코드 | 예 |
| 프롬프트·모델·임계값·내부 구조를 바꾼다 | 아니오 |
| 계약에 모양이 안 적힌 것을 이 레포가 정한다(예: `blind/check` 응답) | 계약에 더해 달라고 제안하고, 그때까지 `docs/open-questions.md`에 적는다 |

## 2. 절차

1. **제안을 쓴다**(이슈나 PR 설명): 무엇을, 왜, 바뀌는 JSON 예시(전·후), 영향받는 레포와 화면·기능, 깨는 변경이면 옮겨 가는 순서.
2. **세 레포 담당이 합의한다.** 합의 전에는 `app/schemas/`의 계약 타입을 고치지 않는다. 급하면 기존 계약을 지키는 범위에서만 구현한다(예: 계약에 없는 `RULE` 화자 방법은 밖으로 `NONE`으로 내보낸다).
3. **같은 날 세 레포에 반영한다.** 원본을 고치고 세 레포의 `docs/contract/api-contract.md`를 같은 내용으로 덮어쓴다.
4. **이 레포에서 한 PR로 고친다.**
   - `docs/contract/api-contract.md` (복사본)
   - `app/schemas/` — `internal.py`(4장), `backend.py`(5장), `ws.py`(3장 이벤트 만들기 함수), `common.py`(열거형)
   - 구현: `app/api/internal.py`, `app/live/`, `app/backend/client.py`(fake 포함), `app/demo/data.py`(데모 context)
   - 테스트: `tests/test_api_backend.py`(REST 필드 이름), `tests/test_ws.py`(이벤트 모양), 백엔드 요청 모양
   - `docs/open-questions.md`에서 해결된 항목 지우기
5. **깨는 변경의 순서**: 받는 쪽이 먼저 옛것·새것을 모두 받게 배포 → 보내는 쪽이 새것으로 바꿈 → 받는 쪽이 옛것을 지움. 한 번에 바꾸지 않는다.

## 3. 이 레포의 규칙

- JSON은 camelCase. 타입은 `CamelModel`을 상속한다(파이썬 필드는 snake_case, alias가 자동으로 붙는다).
- 응답은 `response_model`로 지정한다. dict를 손으로 만들지 않는다(WebSocket 이벤트는 `app/schemas/ws.py`의 함수로만 만든다).
- 오류는 `{ "code", "message", "details" }` 형식이다(`app/api/errors.py`). 새 오류 코드는 계약 표에 있어야 한다.
- 요청에서 모르는 필드는 무시한다(`extra="ignore"`). 그래서 상대가 필드를 **더하는** 것은 우리 쪽 수정 없이 통과한다.
- 계약에 없는 운영용 경로(`/internal/v1/stats`)는 `X-Internal-Key` 뒤에 두고 `docs/open-questions.md`에 적는다.

## 확인 목록

- [ ] 합의된 제안이 있다(링크)
- [ ] 세 레포의 계약 복사본이 같다
- [ ] 스키마·구현·fake·테스트를 한 PR에서 고쳤다
- [ ] `pytest` 통과, `scripts/ws_demo_client.py`로 이벤트 모양 확인
- [ ] 깨는 변경이면 배포 순서를 PR에 적었다
