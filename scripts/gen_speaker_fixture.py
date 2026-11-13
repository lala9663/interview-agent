"""화자 판정 평가용 합성 면접 대화를 만든다 → tests/fixtures/speaker_dialogue.json

실제 면접 녹음이 아니라 규칙으로 만든 대화다(시드 고정, 다시 돌려도 같은 파일). 음성 인식 결과처럼 문장 부호가 없다.
발화 종류(kind):
  bank_exact        질문 은행 문장을 거의 그대로 말함
  bank_variant      질문 은행 질문을 다른 말로 바꿔 말함
  improvised        질문 은행에 없는 즉석 질문
  procedural        면접관의 진행 말("다음 질문으로 넘어가겠습니다")
  interviewer_explain 면접관이 지원자의 되물음에 설명함(평서문이라 어렵다)
  answer / short_answer / echo_answer  지원자 답(echo는 질문의 말을 되받아 시작)
  reverse_question  지원자가 되묻는 말(의문문이라 어렵다)

실행: python scripts/gen_speaker_fixture.py
"""

from __future__ import annotations

import json
import random
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "speaker_dialogue.json"

TOPICS = [
    "프로세스와 스레드", "교착 상태", "가상 메모리", "인터럽트 처리", "뮤텍스와 세마포어", "캐시 메모리",
    "TCP와 UDP", "해시 테이블", "정렬 알고리즘", "메모리 누수", "단위 테스트", "코드 리뷰", "버전 관리",
    "디버깅 도구", "RTOS 스케줄링", "디바이스 드라이버", "통신 프로토콜 설계", "저전력 설계", "부트로더",
    "펌웨어 업데이트", "객체 지향 설계", "예외 처리", "로그 수집", "성능 최적화", "동시성 문제",
    "데이터베이스 인덱스", "빌드 자동화", "요구사항 분석", "일정 관리", "기술 문서 작성",
]
BANK_TEMPLATES = [
    ("개념", "{t}에 대해 설명해 주세요."),
    ("경험", "{t} 관련해서 실제로 다뤄 본 경험이 있나요?"),
    ("판단", "{t}에서 주의해야 할 점은 무엇인가요?"),
]
BEHAVIORAL = [
    "협업 중 의견 충돌을 어떻게 해결했나요?", "가장 크게 실패한 경험과 거기서 배운 점은 무엇인가요?",
    "새로운 기술을 배울 때 어떤 방법으로 익히나요?", "마감이 촉박할 때 일의 우선순위를 어떻게 정하나요?",
    "본인의 강점과 약점을 말씀해 주세요.", "우리 회사에 지원한 이유는 무엇인가요?",
    "팀에서 주로 어떤 역할을 맡는 편인가요?", "스트레스를 받을 때 어떻게 관리하나요?",
    "5년 뒤에 어떤 개발자가 되고 싶나요?", "리더와 생각이 다를 때 어떻게 행동하나요?",
]
VARIANT_TEMPLATES = {
    "개념": ["{t}가 뭔지 본인이 이해한 대로 한번 말씀해 보시겠어요", "{t} 이게 어떤 건지 간단하게 설명 한번 해 주실래요"],
    "경험": ["혹시 {t} 쪽은 직접 해 보신 적 있으세요", "{t} 이런 걸 프로젝트에서 실제로 써 보셨나요"],
    "판단": ["{t} 할 때 조심해야 되는 게 뭐라고 생각하세요", "{t} 다룰 때 어떤 점을 특히 신경 써야 할까요"],
}
BEHAVIORAL_VARIANTS = [
    "팀원이랑 생각이 안 맞았을 때 어떻게 푸셨는지 궁금한데요", "실패했던 일 하나만 얘기해 주시고 뭘 배웠는지도 같이요",
    "처음 보는 기술은 보통 어떻게 공부하세요", "일이 몰리면 뭐부터 하시는 편이에요",
    "스스로 생각하는 장점하고 단점은 뭔가요",
]
PREFIXES = ["", "", "네 ", "그럼 ", "네 그러면 ", "좋습니다 "]
IMPROVISED = [
    "방금 말씀하신 부분에서 성능은 얼마나 개선됐나요", "그때 팀원은 몇 명이었죠", "왜 그 방식을 선택하셨어요",
    "조금만 더 구체적으로요", "그 숫자는 어떻게 측정한 거예요", "아 그렇군요 그러면 테스트는 어떻게 하셨습니까",
    "본인이 직접 짠 코드는 어느 정도 분량이었나요", "그 문제를 다시 만나면 다르게 하실 건가요",
    "혹시 그 과정에서 다른 대안도 검토해 보셨어요", "그게 본인 아이디어였나요 아니면 팀에서 정한 건가요",
    "결과물은 지금도 쓰이고 있나요", "그 부분은 이력서에 적으신 내용과 조금 다른데 설명해 주시겠어요",
    "일정이 밀렸을 때는 어떻게 하셨죠", "지금 말씀하신 버퍼 크기는 어떻게 정하셨나요",
    "그 얘기는 처음 듣는데 예를 하나 들어 보시죠", "그래서 최종적으로 오류율이 얼마였다고요",
    "그 툴 말고 다른 건 안 써 보셨고요", "네 그 다음에는요", "지도 교수님 도움은 어느 정도 받으셨어요",
    "말씀 중에 죄송한데 그 보드 사양이 어떻게 됐죠",
]
PROCEDURAL = [
    "네 잘 들었습니다 다음 질문으로 넘어가겠습니다", "네 알겠습니다", "시간 관계상 마지막 질문 드리겠습니다",
    "네 답변 감사합니다", "좋습니다 이어서 질문하겠습니다", "네 편하게 말씀하시면 됩니다",
]
ANSWER_TEMPLATES = [
    "{t}는 제가 프로젝트에서 직접 다뤄 봤습니다 처음에는 개념만 알고 있었는데 실제로 적용하면서 문제를 겪었고 로그를 보면서 원인을 찾았습니다",
    "네 {t} 관련해서는 수업에서 배운 내용을 바탕으로 작은 실험을 해 봤고 그 결과를 정리해서 팀에 공유했습니다",
    "솔직히 {t}는 깊게 해 보지는 못했습니다 다만 기본 원리는 이해하고 있고 입사 후에 빠르게 익히겠습니다",
    "저는 {t}에서 가장 중요한 건 재현 가능한 환경을 먼저 만드는 거라고 생각합니다 그래서 항상 테스트 코드를 먼저 작성하는 편입니다",
    "그때는 팀원 세 명이 같이 했는데 {t} 부분은 제가 맡아서 설계부터 구현까지 했습니다",
    "음 그 부분은 처음에 잘못 판단했었습니다 {t} 문제라고 생각 못 하고 하드웨어를 의심했는데 측정해 보니 소프트웨어 쪽이었습니다",
]
ECHO_TEMPLATES = [
    "{t}에 대해 설명드리면 먼저 기본 개념은 자원을 어떻게 나누고 지키느냐의 문제입니다",
    "{t}에서 주의해야 할 점은 크게 두 가지라고 생각합니다 하나는 경계 조건이고 다른 하나는 타이밍입니다",
]
FOLLOW_ANSWERS = [
    "그리고 그 경험 덕분에 문제를 작게 나눠서 확인하는 습관이 생겼습니다",
    "결과적으로 처리 시간이 삼십 퍼센트 정도 줄었고 발표에서도 좋은 평가를 받았습니다",
    "추가로 말씀드리면 그 내용을 문서로 남겨서 다음 기수가 참고할 수 있게 했습니다",
    "다만 그때 테스트를 충분히 못 한 게 아쉬웠고 지금이라면 자동화를 먼저 했을 것 같습니다",
]
SHORT_ANSWERS = ["네 맞습니다", "음 그건 잘 모르겠습니다", "네 세 명이었습니다", "아니요 그건 제 아이디어였습니다",
                 "네 지금도 동아리에서 쓰고 있습니다", "대략 이천 줄 정도였습니다"]
REVERSE_QUESTIONS = [
    "혹시 입사하면 어떤 팀에 배치되는지 여쭤봐도 될까요", "신입 교육 기간은 얼마나 되나요",
    "팀에서는 코드 리뷰를 어떤 식으로 하시는지 궁금합니다", "야근이 많은 편인가요",
    "죄송한데 질문을 한 번만 다시 말씀해 주시겠어요",
]
INTERVIEWER_EXPLAIN = [
    "저희 팀은 주로 장비 제어 소프트웨어를 만들고 있고 신입은 육 개월 정도 교육을 받습니다",
    "배치는 교육이 끝난 다음에 본인 희망하고 팀 상황을 같이 봐서 정합니다",
    "리뷰는 머지 전에 두 명 이상이 보는 게 원칙이고 저도 매일 리뷰를 합니다",
    "프로젝트 막바지에는 조금 바쁘지만 평소에는 그렇게 많지 않습니다",
    "아 네 방금 질문은 그 프로젝트에서 본인 역할이 뭐였냐는 거였습니다",
]


def build_bank() -> list[dict]:
    bank, next_id = [], 1
    for topic in TOPICS:
        for category, template in BANK_TEMPLATES:
            bank.append({"id": next_id, "category": category, "text": template.format(t=topic), "topic": topic})
            next_id += 1
    for text in BEHAVIORAL:
        bank.append({"id": next_id, "category": "인성", "text": text, "topic": None})
        next_id += 1
    random.Random(11).shuffle(bank)  # 앞에서 N개를 잘라 써도 주제가 고르게 섞이게
    for i, q in enumerate(bank, 1):
        q["id"] = i
    return bank


def spoken(text: str) -> str:
    return text.replace("?", "").replace(".", "").strip()


def build_dialogue(bank: list[dict]) -> list[dict]:
    rng = random.Random(7)
    out: list[dict] = []

    def add(speaker: str, kind: str, text: str, bank_id: int | None = None) -> None:
        out.append({"speaker": speaker, "kind": kind, "text": text, "bankId": bank_id})

    add("INTERVIEWER", "procedural", "안녕하세요 자리에 앉으시고 간단하게 자기소개 부탁드립니다")
    add("CANDIDATE", "answer", "안녕하세요 저는 임베디드 소프트웨어 개발자를 지망하는 지원자입니다 학부에서 전자공학을 전공했고 동아리에서 펌웨어를 삼 년 동안 개발했습니다")
    picks = rng.sample(bank, 46)
    behavioral_variants = iter(BEHAVIORAL_VARIANTS)
    improvised = iter(IMPROVISED)
    for i, q in enumerate(picks):
        topic = q["topic"] or "협업"
        style = ("bank_exact", "bank_variant", "improvised")[i % 3] if i % 7 else "bank_exact"
        if style == "bank_exact":
            add("INTERVIEWER", style, rng.choice(PREFIXES) + spoken(q["text"]), q["id"])
        elif style == "bank_variant":
            if q["topic"] is None:
                text = next(behavioral_variants, None) or spoken(q["text"])
            else:
                text = rng.choice(VARIANT_TEMPLATES[q["category"]]).format(t=topic)
            add("INTERVIEWER", style, rng.choice(PREFIXES) + text, q["id"])
        else:
            add("INTERVIEWER", style, next(improvised))
        # 지원자 답 1~2개
        roll = rng.random()
        if style == "improvised" and roll < 0.35:
            add("CANDIDATE", "short_answer", rng.choice(SHORT_ANSWERS))
        elif style == "bank_exact" and roll < 0.25:
            add("CANDIDATE", "echo_answer", rng.choice(ECHO_TEMPLATES).format(t=topic))
        else:
            add("CANDIDATE", "answer", rng.choice(ANSWER_TEMPLATES).format(t=topic))
        if rng.random() < 0.3:
            add("CANDIDATE", "answer", rng.choice(FOLLOW_ANSWERS))
        if i % 6 == 5:
            add("INTERVIEWER", "procedural", PROCEDURAL[(i // 6) % len(PROCEDURAL)])
    add("INTERVIEWER", "procedural", "마지막으로 궁금한 점 있으시면 말씀해 주세요")
    for question, answer in zip(REVERSE_QUESTIONS, INTERVIEWER_EXPLAIN):
        add("CANDIDATE", "reverse_question", question)
        add("INTERVIEWER", "interviewer_explain", answer)
    add("CANDIDATE", "short_answer", "네 답변 감사합니다")
    add("INTERVIEWER", "procedural", "네 오늘 면접은 여기까지입니다 수고하셨습니다")
    return out


def main() -> None:
    bank = build_bank()
    dialogue = build_dialogue(bank)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "description": "합성 면접 대화(화자 정답 표시). scripts/gen_speaker_fixture.py로 만든 파일이다. 직접 고치지 않는다.",
        "bank": [{"id": q["id"], "category": q["category"], "text": q["text"]} for q in bank],
        "utterances": dialogue,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    kinds: dict[str, int] = {}
    for u in dialogue:
        kinds[u["kind"]] = kinds.get(u["kind"], 0) + 1
    print(f"질문 은행 {len(bank)}개, 발화 {len(dialogue)}개 → {OUT}")
    print(json.dumps(kinds, ensure_ascii=False))


if __name__ == "__main__":
    main()
