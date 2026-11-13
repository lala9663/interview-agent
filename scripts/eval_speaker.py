"""화자 판정 방법별 정확도를 잰다(합성 대화: tests/fixtures/speaker_dialogue.json).

  python scripts/eval_speaker.py                 # 질문 은행 0 / 20 / 100개
  python scripts/eval_speaker.py --bank 0 50 100 --threshold 0.5

직전 화자 문맥에는 정답이 아니라 **판정 결과**를 넣는다(실제 동작과 같게: 한 번 틀리면 다음에도 영향을 준다).
LLM 판정(⑤)은 llm.fast가 fake가 아닐 때만 잰다. fake LLM의 판정은 규칙 흉내라서 재는 의미가 없다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.matching.matcher import KIND_BANK, MatchCandidate, QuestionMatcher  # noqa: E402
from app.providers.registry import build_providers  # noqa: E402
from app.schemas.common import Speaker  # noqa: E402
from app.settings import ProviderSettings, SpeakerSettings  # noqa: E402
from app.speaker.base import RecentUtterance, SpeakerContext  # noqa: E402
from app.speaker.chain import build_speaker_chain  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "speaker_dialogue.json"


def load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


async def run_chain(utterances: list[dict], bank: list[dict], chain_names: str, *, threshold: float = 0.55,
                    llm=None) -> list[dict]:
    """대화를 처음부터 차례로 판정한다. 결과: [{speaker(정답), kind, predicted, method}]"""
    settings = SpeakerSettings(chain=chain_names, question_match_threshold=threshold)
    chain = build_speaker_chain(settings, llm)
    matcher = QuestionMatcher([MatchCandidate(KIND_BANK, q["text"], question_id=q["id"]) for q in bank])
    recent: list[RecentUtterance] = []
    results = []
    for i, u in enumerate(utterances, 1):
        match = await matcher.best(u["text"]) if len(matcher) else None
        decision = await chain.decide(SpeakerContext(text=u["text"], match=match, recent=recent[-8:]))
        recent.append(RecentUtterance(i, decision.speaker.value, u["text"]))
        results.append({"speaker": u["speaker"], "kind": u["kind"], "predicted": decision.speaker.value,
                        "method": decision.method.value})
    return results


def simulate_manual(utterances: list[dict], forget_rate: float, seed: int = 3) -> list[dict]:
    """MANUAL(역할 전환 버튼): 화자가 바뀔 때 `forget_rate` 확률로 전환을 잊는다고 가정한다."""
    rng = random.Random(seed)
    role = utterances[0]["speaker"]
    results = []
    for u in utterances:
        if u["speaker"] != role and rng.random() >= forget_rate:
            role = u["speaker"]
        results.append({"speaker": u["speaker"], "kind": u["kind"], "predicted": role, "method": "MANUAL"})
    return results


def summarize(results: list[dict]) -> dict:
    n = len(results)
    correct = sum(r["predicted"] == r["speaker"] for r in results)
    unknown = sum(r["predicted"] == Speaker.UNKNOWN.value for r in results)
    decided = n - unknown

    def recall(speaker: str) -> float:
        rows = [r for r in results if r["speaker"] == speaker]
        return sum(r["predicted"] == speaker for r in rows) / len(rows) if rows else 0.0

    return {"n": n, "accuracy": correct / n, "unknown": unknown / n,
            "precision": (correct / decided) if decided else 0.0,
            "interviewer_recall": recall("INTERVIEWER"), "candidate_recall": recall("CANDIDATE")}


def by_kind(results: list[dict]) -> dict[str, tuple[int, int]]:
    out: dict[str, list[int]] = {}
    for r in results:
        row = out.setdefault(r["kind"], [0, 0])
        row[0] += r["predicted"] == r["speaker"]
        row[1] += 1
    return {k: (v[0], v[1]) for k, v in out.items()}


def _pct(x: float) -> str:
    return f"{x * 100:5.1f}%"


async def main_async(args: argparse.Namespace) -> None:
    data = load_fixture()
    utterances, bank_all = data["utterances"], data["bank"]
    providers = build_providers(ProviderSettings().providers_file)
    llm = providers.llm_fast if providers.llm_fast.impl != "fake" else None
    n_i = sum(u["speaker"] == "INTERVIEWER" for u in utterances)
    print(f"발화 {len(utterances)}개 (면접관 {n_i}, 지원자 {len(utterances) - n_i}), 질문 은행 전체 {len(bank_all)}개, "
          f"유사도 임계값 {args.threshold}")
    print("LLM 판정(⑤): " + (f"포함 ({providers.llm_fast.model})" if llm else "제외 (llm.fast가 fake) → ①~④만 측정"))
    print()
    header = f"| {'방법':<34} | 은행 | 정확도 | 판정한 것 중 정확 | UNKNOWN | 면접관 재현 | 지원자 재현 |"
    print(header)
    print("| " + "-" * 34 + " | ---: | ---: | ---: | ---: | ---: | ---: |")

    def row(name: str, bank_size: str, s: dict) -> None:
        print(f"| {name:<34} | {bank_size:>4} | {_pct(s['accuracy'])} | {_pct(s['precision'])} | {_pct(s['unknown'])} "
              f"| {_pct(s['interviewer_recall'])} | {_pct(s['candidate_recall'])} |")

    row("① MANUAL (전환을 잊지 않음)", "-", summarize(simulate_manual(utterances, 0.0)))
    row("① MANUAL (전환 10% 잊음)", "-", summarize(simulate_manual(utterances, 0.10)))
    row("④ 규칙만", "-", summarize(await run_chain(utterances, [], "rule")))
    last_chain: list[dict] = []
    for size in args.bank:
        bank = bank_all[:size]
        row("③ QUESTION_MATCH만", str(size), summarize(await run_chain(utterances, bank, "question_match",
                                                                      threshold=args.threshold)))
        last_chain = await run_chain(utterances, bank, "question_match,rule", threshold=args.threshold)
        row("③+④ 자동 체인", str(size), summarize(last_chain))
        if llm is not None:
            row("③+④+⑤ 자동 체인(LLM 포함)", str(size), summarize(
                await run_chain(utterances, bank, "question_match,rule,llm", threshold=args.threshold, llm=llm)))
    print()
    print(f"발화 종류별 정확도 (③+④ 자동 체인, 은행 {args.bank[-1]}개):")
    print("| 종류 | 맞음/전체 | 정확도 |")
    print("| --- | ---: | ---: |")
    for kind, (ok, total) in sorted(by_kind(last_chain).items()):
        print(f"| {kind} | {ok}/{total} | {_pct(ok / total)} |")


def main() -> None:
    p = argparse.ArgumentParser(description="화자 판정 정확도 측정")
    p.add_argument("--bank", type=int, nargs="+", default=[0, 20, 100], help="질문 은행 크기들")
    p.add_argument("--threshold", type=float, default=0.55, help="QUESTION_MATCH 유사도 임계값")
    asyncio.run(main_async(p.parse_args()))


if __name__ == "__main__":
    main()
