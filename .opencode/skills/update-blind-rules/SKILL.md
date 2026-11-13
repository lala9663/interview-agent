---
name: update-blind-rules
description: 블라인드 채용 기준이나 금지 질문 기준이 바뀌었을 때(현업·People팀이 새 기준을 줌, 금지 주제 추가·삭제, 오탐·미탐 신고) 쓴다. config/blind_rules.yaml만 고쳐 반영하는 절차와 검증 방법을 담고 있다.
---

# 블라인드/금지 항목 규칙 바꾸기

규칙은 **`config/blind_rules.yaml` 한 파일**에 있다. 코드는 고치지 않는다.
이 규칙은 두 곳에서 쓰인다(`app/blind/filter.py`).

- 입력 필터: 지원자 프로필에서 금지 키를 지우고, 글에서 금지 항목이 있는 문장을 가린 뒤 모델에 넣는다.
- 출력 검사: 모델이 낸 요약·질문·꼬리질문·평가·보고서에 금지 항목이 있으면 다시 만들거나, 문장을 가리거나, 항목을 뺀다.

## 1. 파일 모양

```yaml
version: "2026-12-people-v1"     # 바꿀 때마다 올린다. /blind/check 응답의 rulesVersion으로 나간다
topics:
  - code: family                 # 응답의 excluded·violations에 나가는 값(영문)
    label: 가족                  # 화면에 보이는 이름
    description: 가족 관계, 가족의 직업·학력·재산
    profile_keys: [family, 가족관계]      # 프로필 JSON에서 이 이름의 키는 값과 함께 지운다(대소문자 무시)
    keywords: [부모님, 아버지, 어머니]    # 글에 이 낱말이 있으면 걸린다
    patterns: ["가족\\s*(구성|사항)"]     # 정규식(파이썬 re). 낱말만으로는 오탐이 많을 때
    allow: [가족 같은]                    # 이 표현과 겹치는 부분은 무시한다
```

## 2. 절차

1. 받은 기준을 주제(topic)별로 나눈다. 새 주제면 `code`(영문 소문자), `label`을 정한다. **이미 쓰이는 `code`는 바꾸지 않는다**(백엔드·프론트가 `excluded` 값으로 쓴다. 바꿔야 하면 `change-api-contract` 절차).
2. 낱말을 `keywords`에 넣기 전에 오탐을 생각한다. 다른 낱말 안에 들어 있을 수 있으면 `patterns`로 쓴다.
   - 예: `여자` → "참여자"에 걸린다 → `(?<![가-힣])(남자|여자)`
   - 예: `재산` → "지식재산권" → `(?<![가-힣])재산(?!권)`
   - 예: `자가` → "지원자가" → `(?<![가-힣])자가\s*(주택|소유)`
3. `version`을 올린다.
4. 확인한다.
   ```bash
   .venv/Scripts/python -m pytest tests/test_blind.py
   # 서버를 띄운 뒤, 기준에 딸린 예시 문장으로:
   curl -X POST localhost:8000/internal/v1/blind/check -H "X-Internal-Key: dev-internal-key" \
        -H "Content-Type: application/json" -d '{"texts": ["부모님은 어떤 일을 하시나요?", "가족 같은 팀이었습니다."]}'
   ```
5. `tests/test_blind.py`에 예시를 더한다: 걸려야 하는 문장은 `test_forbidden_questions_are_detected`, 걸리면 안 되는 직무 문장은 `test_ordinary_job_talk_is_clean`의 목록에 한 줄씩.
6. `docs/blind-rules.md`의 주제 표와 "기준 출처"를 고친다(누가, 언제 준 기준인지).
7. 서버는 시작할 때 규칙을 읽는다. 반영하려면 다시 시작한다.

## 3. 판단 기준

- **빠뜨리는 것(미탐)이 잘못 거르는 것(오탐)보다 나쁘다.** 다만 오탐이 많으면 요약·질문이 `[블라인드 처리됨]`투성이가 되어 못 쓰게 된다. 직무 용어와 겹치면 `patterns`나 `allow`로 좁힌다.
- 낱말 규칙은 돌려 말한 표현("댁에서는 뭐라고 하세요?")을 못 잡는다. 프롬프트에도 금지 원칙(`app/prompts/common.py`의 `FAIRNESS`)이 들어가 있으니, 새 주제가 생기면 그 문장도 같이 고치고 프롬프트 버전을 올린다.
- 직무상 필요한 예외(예: 해외 근무 직무의 비자 문제)는 규칙 파일로 표현하지 못한다. 그런 요구가 오면 현업에 확인할 사항으로 `docs/open-questions.md`에 적고 사용자에게 알린다.
- 면접 중 **지원자가 스스로 말한** 가족·결혼 이야기는 실시간 전사에는 그대로 남는다(전사는 기록이다). 걸러지는 것은 모델이 만든 글(꼬리질문·평가·보고서)이다. 전사도 가려야 한다는 기준이 오면 `app/live/session.py`에 단계가 필요하므로 코드 변경이다.

## 확인 목록

- [ ] `version`을 올렸다
- [ ] 기준에 딸린 예시가 모두 걸린다 / 직무 문장은 안 걸린다(테스트에 추가)
- [ ] 기존 `code`를 바꾸지 않았다
- [ ] `docs/blind-rules.md`를 고쳤다
- [ ] 필요하면 `FAIRNESS` 문장과 프롬프트 버전을 고쳤다
