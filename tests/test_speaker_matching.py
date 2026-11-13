"""질문 매칭과 화자 판정 체인."""

from __future__ import annotations

import json

import pytest

import eval_speaker
from app.matching.matcher import KIND_BANK, KIND_FOLLOWUP, KIND_PREPARED, MatchCandidate, QuestionMatcher
from app.matching.tfidf import pair_similarity
from app.providers.base import RoleConfig
from app.providers.embedding import HashingEmbeddingProvider
from app.schemas.common import Speaker, SpeakerMethod, wire_speaker_method
from app.settings import SpeakerSettings
from app.speaker.base import RecentUtterance, SpeakerContext
from app.speaker.chain import build_speaker_chain
from tests.conftest import ScriptedLLM

QUESTIONS = [
    MatchCandidate(KIND_PREPARED, "캡스톤 프로젝트에서 맡은 역할은 무엇이었나요?", question_id=7001),
    MatchCandidate(KIND_PREPARED, "RTOS 환경에서 태스크 간 동기화는 어떻게 처리하나요?", question_id=7003),
    MatchCandidate(KIND_BANK, "프로세스와 스레드의 차이를 설명해 주세요.", question_id=31),
    MatchCandidate(KIND_BANK, "협업 중 의견 충돌을 어떻게 해결했나요?", question_id=33),
]


async def test_matches_question_despite_particle_and_ending_changes():
    matcher = QuestionMatcher(QUESTIONS)
    m = await matcher.best("캡스톤 프로젝트에서 맡으신 역할이 무엇이었나요")
    assert m.candidate.question_id == 7001 and m.similarity > 0.55
    m = await matcher.best("네 잘 들었습니다. 그럼 프로세스와 스레드의 차이를 설명해 주시겠어요?")
    assert m.candidate.question_id == 31 and m.similarity > 0.6  # 앞말이 붙어도 문장 단위로 비교한다


async def test_answer_is_not_mistaken_for_the_question():
    matcher = QuestionMatcher(QUESTIONS)
    m = await matcher.best("프로세스는 독립된 메모리 공간을 가지는 실행 단위이고 스레드는 그 안에서 메모리를 공유합니다")
    assert m.similarity < 0.55
    assert (await matcher.best("오늘 점심은 김치찌개를 먹었습니다")).similarity < 0.2


async def test_kind_filter_and_incremental_add():
    matcher = QuestionMatcher(QUESTIONS)
    text = "프로세스와 스레드의 차이를 설명해 주세요"
    assert (await matcher.best(text)).candidate.kind == KIND_BANK
    assert (await matcher.best(text, kinds=(KIND_PREPARED,))).similarity < 0.3
    matcher.add([MatchCandidate(KIND_FOLLOWUP, "경합 조건을 어떻게 찾아내셨나요?", ref="f-6-1")])
    m = await matcher.best("경합 조건은 어떻게 찾아내셨어요")
    assert m.candidate.ref == "f-6-1" and m.similarity > 0.55
    assert await QuestionMatcher().best("아무 말") is None


async def test_embedding_provider_is_used_when_configured():
    emb = HashingEmbeddingProvider(RoleConfig(role="embedding.default", impl="fake"))
    matcher = QuestionMatcher(QUESTIONS, embedding=emb)
    m = await matcher.best("캡스톤 프로젝트에서 맡으신 역할이 무엇이었나요")
    assert m.candidate.question_id == 7001 and emb.calls == 2  # 후보 1번 + 질의 1번


def test_pair_similarity_for_dedupe():
    assert pair_similarity("그 결과를 어떻게 검증하셨나요?", "그 결과는 어떻게 검증하셨나요") > 0.7
    assert pair_similarity("그 결과를 어떻게 검증하셨나요?", "팀원은 몇 명이었나요?") < 0.4


def _chain(names: str = "manual,channel,question_match,rule", llm=None):
    return build_speaker_chain(SpeakerSettings(chain=names), llm)


async def test_chain_order_manual_beats_everything():
    matcher = QuestionMatcher(QUESTIONS)
    text = "캡스톤 프로젝트에서 맡으신 역할이 무엇이었나요"
    match = await matcher.best(text)
    d = await _chain().decide(SpeakerContext(text=text, role_override="CANDIDATE", match=match))
    assert (d.speaker, d.method) == (Speaker.CANDIDATE, SpeakerMethod.MANUAL)
    d = await _chain().decide(SpeakerContext(text=text, channel_speaker="CANDIDATE", match=match))
    assert d.method == SpeakerMethod.CHANNEL
    d = await _chain().decide(SpeakerContext(text=text, match=match))
    assert (d.speaker, d.method) == (Speaker.INTERVIEWER, SpeakerMethod.QUESTION_MATCH)


@pytest.mark.parametrize("text,recent,expected", [
    ("그때 팀원은 몇 명이었죠", [("CANDIDATE", "네 맞습니다")], Speaker.INTERVIEWER),
    ("그 부분을 조금 더 자세히 설명해 주세요", [], Speaker.INTERVIEWER),
    ("저는 그때 펌웨어를 맡았고 통신 모듈을 직접 구현했습니다", [("INTERVIEWER", "역할이 무엇이었나요")], Speaker.CANDIDATE),
    ("네 세 명이었습니다", [("INTERVIEWER", "팀원은 몇 명이었죠")], Speaker.CANDIDATE),
    ("신입 교육 기간은 얼마나 되나요", [("INTERVIEWER", "마지막으로 궁금한 점 있으시면 말씀해 주세요")], Speaker.CANDIDATE),
])
async def test_rule_judge(text, recent, expected):
    ctx = SpeakerContext(text=text, recent=[RecentUtterance(i, s, t) for i, (s, t) in enumerate(recent, 1)])
    d = await _chain("rule").decide(ctx)
    assert d.speaker == expected and d.method == SpeakerMethod.RULE
    assert wire_speaker_method(d.method) == "RULE"


async def test_weak_evidence_falls_through_to_unknown():
    d = await _chain("rule").decide(SpeakerContext(text="조금만 더 구체적으로요"))
    assert (d.speaker, d.method) == (Speaker.UNKNOWN, SpeakerMethod.NONE)


async def test_llm_judge_confidence_gate_and_failure():
    ctx = SpeakerContext(text="조금만 더 구체적으로요", recent=[RecentUtterance(1, "CANDIDATE", "네 그렇습니다")])
    sure = ScriptedLLM([json.dumps({"speaker": "INTERVIEWER", "confidence": 0.9})])
    d = await _chain("rule,llm", sure).decide(ctx)
    assert (d.speaker, d.method) == (Speaker.INTERVIEWER, SpeakerMethod.LLM)
    assert "<utterance>" in sure.chat.seen[0][1].content and "[1] CANDIDATE" in sure.chat.seen[0][1].content
    unsure = ScriptedLLM([json.dumps({"speaker": "INTERVIEWER", "confidence": 0.3})])
    d = await _chain("rule,llm", unsure).decide(ctx)
    assert (d.speaker, d.method) == (Speaker.UNKNOWN, SpeakerMethod.LLM)
    broken = ScriptedLLM(["JSON이 아닌 답"])
    d = await _chain("rule,llm", broken).decide(ctx)
    assert (d.speaker, d.method) == (Speaker.UNKNOWN, SpeakerMethod.NONE)  # 실패해도 전사는 계속된다


def test_unknown_judge_name_is_rejected():
    with pytest.raises(ValueError, match="알 수 없는 화자 판정기"):
        _chain("manual,voiceprint")


async def test_fixture_accuracy_does_not_regress():
    """합성 대화(125발화) 기준 회귀 방지선. 실제 수치는 docs/speaker-separation.md."""
    data = eval_speaker.load_fixture()
    assert len(data["utterances"]) >= 100 and len(data["bank"]) == 100
    auto = eval_speaker.summarize(await eval_speaker.run_chain(data["utterances"], data["bank"], "question_match,rule"))
    assert auto["accuracy"] >= 0.90 and auto["precision"] >= 0.93
    only_match = eval_speaker.summarize(await eval_speaker.run_chain(data["utterances"], data["bank"], "question_match"))
    assert only_match["precision"] == 1.0  # 질문 유사도로 면접관이라 한 것은 모두 맞아야 한다
    small = eval_speaker.summarize(await eval_speaker.run_chain(data["utterances"], data["bank"][:20], "question_match"))
    assert small["interviewer_recall"] < only_match["interviewer_recall"]  # 은행이 클수록 더 많이 잡는다
