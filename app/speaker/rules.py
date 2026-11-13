"""규칙 기반 화자 판정(가벼운 휴리스틱). LLM 없이 즉시 답한다.

점수가 양수면 면접관, 음수면 지원자. 근거가 약하면(|점수|가 작으면) 판정하지 않고 다음 판정기로 넘긴다.
가중치는 tests/fixtures/speaker_dialogue.json(합성 대화)으로 맞춘 값이다: 실제 면접 녹음으로 다시 재야 한다.
"""

from __future__ import annotations

import re

from app.schemas.common import Speaker, SpeakerMethod
from app.speaker.base import SpeakerContext, SpeakerDecision, SpeakerJudge

_TAIL = r"[\s.?!~]*$"
# 묻는 말로 끝남
_QUESTION_END = re.compile(
    r"(\?|까요|나요|가요|습니까|입니까|는지요|던가요|을까요|셨어요|셨죠|었죠|였죠|했죠|됐죠|(?<!안녕하)세요|고요|다고요)" + _TAIL)
# 의문사. 평서형 어미(-습니다)로 끝나지 않으면 묻는 말로 본다("어떻게 측정한 거예요")
_WH = re.compile(r"(어떻게|왜 |무엇|뭐|뭘|몇 |어느|어떤|언제|누가|얼마)")
_FORMAL_STATEMENT_END = re.compile(r"(습니다|합니다|입니다|됩니다|ㅂ니다)" + _TAIL)
# 시키는 말로 끝남
_REQUEST_END = re.compile(
    r"(주세요|주십시오|주시겠어요|주시겠습니까|주실 수 있나요|주실래요|부탁드립니다|부탁드려요|부탁합니다|보세요|보시겠어요|볼까요|주시죠|보시죠)"
    + _TAIL)
# 듣는 사람을 높이는 말(면접관이 지원자에게)
_ADDRESSEE = re.compile(r"([가-힣]셨|하신|으신|시나요|시겠|하시는|하시면|하시고|으시면|맡으신|말씀하신|지원자|본인이|본인의|본인 )")
_INTERVIEWER_PHRASE = re.compile(
    r"(다음 질문|넘어가겠|잘 들었습니다|질문\s*(드리|하)겠|이력서에|자기소개서에|자소서에|시간 관계상|마지막으로 (하고 싶은|궁금한)|수고하셨습니다|면접은 여기까지)")
_FIRST_PERSON = re.compile(r"(^|[\s,.])(저는|제가|저희|저도|제 |저의|저한테|저에게)")
_STATEMENT_END = re.compile(r"(습니다|ㅂ니다|입니다|했어요|었어요|았어요|거든요|같아요|합니다|됩니다|있어요|였어요|네요|이에요|예요)" + _TAIL)
_CANDIDATE_ASK = re.compile(r"(궁금합니다|궁금했습니다|여쭤|여쭙|질문드려도|질문 드려도|알고 싶습니다)")
# 면접관이 지원자에게 질문을 청하는 말. 그 뒤(최근 발화 안)에는 묻는 쪽과 답하는 쪽이 뒤바뀐다
_INVITES_QUESTION = re.compile(r"((궁금한 (점|것|거)|질문)\s*(이|은)?\s*있으(시|세|신)|물어보고 싶은 (것|거|점))")
_GREETING = re.compile(r"^(네[,.\s]*)?안녕하세요" + _TAIL)

LONG_CHARS = 80


def _reverse_phase(ctx: SpeakerContext) -> bool:
    """최근 발화 안에 면접관이 질문을 청한 말이 있으면 지원자가 묻고 면접관이 답하는 구간으로 본다."""
    return any(r.speaker == Speaker.INTERVIEWER.value and _INVITES_QUESTION.search(r.text) for r in ctx.recent)


def rule_score(ctx: SpeakerContext) -> tuple[float, list[str]]:
    """(점수, 근거 목록). 양수 = 면접관."""
    text = ctx.text.strip()
    score, why = 0.0, []
    is_question = bool(_QUESTION_END.search(text)) and not _GREETING.search(text)
    if not is_question and _WH.search(text) and not _FORMAL_STATEMENT_END.search(text):
        is_question = True
    is_request = bool(_REQUEST_END.search(text))
    asks = is_question or is_request
    prev = ctx.recent[-1] if ctx.recent else None

    if _reverse_phase(ctx):
        # 마무리 문답: 묻는 말은 지원자, 설명하는 말은 면접관
        if _INTERVIEWER_PHRASE.search(text):
            return 2.5, ["진행 표현"]
        if asks or _CANDIDATE_ASK.search(text):
            return -2.5, ["질문을 청한 뒤의 묻는 말"]
        if len(text) > 20 and _STATEMENT_END.search(text) and prev is not None                 and prev.speaker == Speaker.CANDIDATE.value:
            return 2.0, ["지원자의 물음 뒤 설명"]
        return 0.0, []

    if is_question:
        score += 2.0
        why.append("의문형")
    if is_request:
        score += 2.0
        why.append("요청형 어미")
    if _ADDRESSEE.search(text):
        score += 1.0
        why.append("상대 높임")
    if _INTERVIEWER_PHRASE.search(text):
        score += 2.5
        why.append("진행 표현")
    if _FIRST_PERSON.search(text):
        score -= 1.5
        why.append("1인칭")
    if _STATEMENT_END.search(text) and not asks:
        score -= 1.0
        why.append("평서형 어미")
    if len(text) > LONG_CHARS:
        score -= 1.0
        why.append("긴 발화")
    if _CANDIDATE_ASK.search(text):
        score -= 2.0
        why.append("지원자의 되묻기 표현")
    if prev is not None:
        if prev.speaker == Speaker.INTERVIEWER.value and not asks:
            score -= 0.8
            why.append("직전이 면접관")
        elif prev.speaker == Speaker.CANDIDATE.value and asks:
            score += 0.5
            why.append("답 뒤의 질문")
    return score, why


class RuleJudge(SpeakerJudge):
    name = "rule"

    def __init__(self, min_confidence: float = 0.6) -> None:
        self.min_confidence = min_confidence

    async def judge(self, ctx: SpeakerContext) -> SpeakerDecision | None:
        if not ctx.text.strip():
            return None
        score, why = rule_score(ctx)
        confidence = min(0.95, 0.5 + abs(score) * 0.15)
        if score == 0 or confidence < self.min_confidence:
            return None
        speaker = Speaker.INTERVIEWER if score > 0 else Speaker.CANDIDATE
        return SpeakerDecision(speaker, SpeakerMethod.RULE, confidence, ", ".join(why))
