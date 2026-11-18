# 프롬프트

> 다루는 것: 프롬프트 목록(작업 이름·버전·입력), 쓰는 규칙, 고칠 때 할 일.
>
> 상태: 모든 프롬프트는 fake LLM으로 흐름만 확인했다. **실제 LLM으로 품질을 본 적이 없다.** 사내 LLM을 붙인 뒤 문장을 다듬어야 한다.

## 1. 목록

| 작업(`<task>`) | 버전 | 파일 | 역할 | 입력 태그 | 출력 |
| --- | --- | --- | --- | --- | --- |
| `briefing.summary` | v1 | `app/prompts/briefing.py` | `llm.quality` | `track`, `interview_type`, `job_title`, `jd`, `profile`, (`feedback`) | JSON: headline, keyPoints, strengths, checkPoints, timeline |
| `briefing.jd_questions` | v1 | 〃 | `llm.quality` | `track`, `interview_type`, `job_title`, `jd`, `question_bank`, `count`, (`feedback`) | JSON: questions[source JD\|BANK] |
| `briefing.resume_questions` | v1 | 〃 | `llm.quality` | `track`, `interview_type`, `job_title`, `profile`, `summary`, `count`, (`feedback`) | JSON: questions[source RESUME] |
| `refine` | v1 | `app/prompts/live.py` | `llm.fast` | `glossary`, `utterance` | 다듬은 문장(글) |
| `speaker.judge` | v1 | 〃 | `llm.fast` | `recent`, `utterance` | JSON: speaker, confidence |
| `followup` | v2 | 〃 | `llm.fast` | `job_title`, `candidate_brief`, `asked`, `qa` | JSON: items[text, rationale] |
| `evaluation` | v2 | `app/prompts/evaluation.py` | `llm.quality` | `track`, `interview_type`, `job_title`, `jd`, `briefing_summary`, `criteria`, `transcript` | JSON: scores[criterionCode, score\|null, rationale, evidenceSeqs], overallComment |
| `report.brief` | v2 | `app/prompts/report.py` | `llm.quality` | 공통 9개(아래) | JSON: headline, overallComment, strengths, concerns, qaHighlights |
| `report.detailed.overview` | v2 | 〃 | `llm.quality` | 공통 9개 | 〃 |
| `report.detailed.competencies` | v2 | 〃 | `llm.quality` | 공통 9개 | JSON: competencies |
| `report.detailed.career` | v2 | 〃 | `llm.quality` | 공통 9개 | JSON: careerChecks |
| `report.detailed.next` | v2 | 〃 | `llm.quality` | 공통 9개 + `overview` | JSON: nextRoundChecks, risks |

보고서 공통 입력: `track`, `interview_type`, `basis`, `job_title`, `jd`, `briefing_summary`, `ai_evaluation`, `previous_rounds`, `transcript`.

전사는 `[seq] 화자: 글` 줄로 들어간다(`transcript_block`). 평가와 보고서는 이 seq를 근거로 인용한다.

`interview_type`은 `JOB`, `PERSONALITY` 두 가지다. 사전 리포트 요청의 `applicantType`·`persona`·`predictedFollowUps`와 방 정보의 `persona`는 아직 어느 프롬프트에도 들어가지 않는다(`briefing.*`와 `followup`에 넣어야 한다. [open-questions.md](open-questions.md) 6장).

## 2. 규칙 (`app/prompts/common.py`)

1. **작업 이름과 버전**: system 첫머리에 `<task>이름</task><prompt_version>v1</prompt_version>`이 들어간다. fake LLM이 작업을 알아보는 표시이고, 로그·평가에서 어떤 프롬프트였는지 추적하는 값이다.
2. **입력은 태그로 감싼다**: `tag("이름", 자료)`. 자료 안의 태그 모양 글자(`<...>`)는 지운다(자료가 태그를 닫고 빠져나오지 못하게).
3. **태그 안의 지시는 따르지 않는다**: 모든 프롬프트 끝에 `GUARD` 문장이 자동으로 붙는다. 지원자 자료·전사에 "이전 지시를 무시하라" 같은 글이 있어도 자료로만 다룬다.
4. **금지 항목 문장**(`FAIRNESS`): 지원자에 대한 글을 만드는 프롬프트에 넣는다.
5. **사담 제외**: 인사, 농담, 사담, 진행 안내를 재료로 쓰지 않게 하는 문장이다.
   - 꼬리질문(`SMALL_TALK`): 면접 종류와 상관없이, 마지막 지원자 말이 그런 말이면 `items`를 빈 배열로 둔다.
   - 평가·보고서(`small_talk_rule(interview_type, …)`): 인성 면접(`PERSONALITY`)이 아니면 그런 발화를 근거·문답·강점·우려로 쓰지 않는다. 인성 면접이면 빼지 않고, 사담에서 드러난 태도와 소통 방식도 관찰한 사실로 쓸 수 있다고 알린다(`PERSONALITY_SMALL_TALK`). 금지 항목 문장(`FAIRNESS`)은 인성 면접에도 그대로 적용된다.
   - 실제 모델에서 지켜지는지는 사담이 섞인 대화로 확인해야 한다(fake는 이 문장을 보지 않는다).
6. **출력 형식**: JSON이면 `JSON_ONLY` 문장과 형식 예시를 넣는다. 받는 쪽은 `call_structured`(`app/graphs/structured.py`)로 pydantic 검증을 하고, 실패하면 무엇이 틀렸는지 알려 주며 한 번 다시 시킨다.
7. **지어내지 않게**: "자료에 없는 것은 쓰지 않는다 / 근거가 없으면 점수를 내지 않는다"를 지시문에 넣고, 그래프의 검증 노드에서도 강제한다(없는 seq 삭제, 근거 없는 점수 제외).
8. 모델의 JSON 모드·tool calling에 기대지 않는다(사내 LLM이 지원하는지 모른다). 글에서 첫 JSON 객체를 꺼내 검증한다.

## 3. 프롬프트를 고칠 때

1. 지시문을 고치면 그 작업의 `VERSION`을 올린다(`v1` → `v2`).
2. 입력 태그를 더하거나 이름을 바꾸면 `app/providers/fake_responses.py`의 해당 responder도 고친다(fake는 태그 이름으로 재료를 찾는다).
3. 출력 형식을 바꾸면 그래프의 출력 스키마와 테스트(`tests/test_graphs.py`)를 같이 고친다.
4. 이 문서의 표를 고친다.
5. 실제 LLM이 붙어 있으면 바꾸기 전후 결과를 같은 입력 몇 건으로 비교해 PR에 붙인다(지원자 실자료는 붙이지 않는다. 데모 데이터 `app/demo/data.py`를 쓴다).

새 작업을 더하는 절차는 `.opencode/skills/add-graph/SKILL.md`.

## 4. 실제 LLM을 붙인 뒤 볼 것

| 작업 | 볼 것 |
| --- | --- |
| `refine` | 뜻을 바꾸거나 내용을 덧붙이지 않는가(길이 검사로 일부만 걸러진다). 고유명사·숫자를 보존하는가. 응답 시간이 `LIVE_REFINE_TIMEOUT_S` 안인가 |
| `speaker.judge` | `scripts/eval_speaker.py`의 "LLM 포함" 줄. UNKNOWN 비율 |
| `followup` | 답에 없는 내용을 전제로 묻지 않는가. 이미 한 질문을 되풀이하지 않는가 |
| `briefing.*` | 자료에 없는 사실을 만들지 않는가. 금지 항목 재생성이 얼마나 자주 도는가 |
| `evaluation` | 근거 seq가 실제로 그 내용을 담고 있는가. 근거 없는 항목에 null을 내는가 |
| `report.*` | DETAILED 4단계의 총 시간. 신입 실시간 기능을 열었을 때(백엔드 `app.live.new-grad-enabled=true`. 기본은 꺼져 있어 신입은 보고서를 만들지 않는다)는 BRIEF가 백엔드의 제한 시간(신입 120초) 안에 끝나는가 |
