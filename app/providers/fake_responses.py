"""fake LLM의 작업별 응답 규칙.

품질이 아니라 흐름 확인용이다: 프롬프트의 `<task>`를 보고, 입력 태그에서 재료를 뽑아 모양이 맞는 결과를 낸다.
같은 입력에는 항상 같은 출력이 나온다. 새 작업을 더하면 여기에 `@responder("작업이름")` 함수를 하나 더한다.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable

Responder = Callable[[dict[str, str]], str]
RESPONDERS: dict[str, Responder] = {}

_LINE_RE = re.compile(r"^\[(\d+)\]\s+(\w+):\s?(.*)$")
# 군말. "그"는 다른 군말 바로 뒤에 올 때만 지운다("어 그 캡스톤" → "캡스톤", "그 프로젝트"는 그대로)
_FILLERS = re.compile(r"(?<![가-힣])((어|음|으음|아|저기|뭐랄까)(?![가-힣])[\s,.…]*)+(그(?![가-힣])\s*)?")
_QUESTION_END = re.compile(r"(\?|까요|나요|가요|습니까|주세요|주시겠어요|을까요|던가요|셨어요|었죠|였죠)[\s.?!]*$")


def responder(task: str) -> Callable[[Responder], Responder]:
    def deco(fn: Responder) -> Responder:
        RESPONDERS[task] = fn
        return fn
    return deco


def _json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


def _load(tags: dict[str, str], name: str, default: Any) -> Any:
    try:
        return json.loads(tags.get(name, "") or "null") or default
    except json.JSONDecodeError:
        return default


def _lines(text: str) -> list[dict[str, Any]]:
    out = []
    for ln in text.splitlines():
        m = _LINE_RE.match(ln.strip())
        if m:
            out.append({"seq": int(m.group(1)), "speaker": m.group(2), "text": m.group(3)})
    return out


def _first_sentence(text: str, limit: int = 60) -> str:
    s = re.split(r"(?<=[.!?。])\s+|\n", text.strip())[0] if text.strip() else ""
    return s[:limit]


def _short(text: str, limit: int = 50) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


# ---------- 브리핑 ----------
@responder("briefing.summary")
def _briefing_summary(tags: dict[str, str]) -> str:
    profile = _load(tags, "profile", {})
    job = tags.get("job_title", "") or "해당 직무"
    projects = profile.get("projects") or []
    careers = profile.get("careers") or []
    essays = profile.get("essays") or []
    edu = profile.get("education") or []
    key_points: list[str] = []
    for e in edu[:1]:
        key_points.append(f"{e.get('major', '')} 전공 ({e.get('degree', '')})".strip())
    key_points += [f"프로젝트: {p.get('title', '')}" for p in projects[:2]]
    key_points += [f"경력: {c.get('company', '')} {c.get('role', '')} ({c.get('period', '')})" for c in careers[:2]]
    key_points += [f"{e.get('title', '')}: {_first_sentence(e.get('text', ''))}" for e in essays[:2]]
    focus = (projects[0].get("title") if projects else None) or (careers[0].get("role") if careers else None)
    headline = f"{focus} 경험이 있는 {job} 지원자" if focus else f"{job} 지원자"
    strengths = [f"자격: {c}" for c in (profile.get("certificates") or [])[:2]]
    strengths += [f"수강 과목: {', '.join((profile.get('courses') or [])[:3])}"] if profile.get("courses") else []
    checks = [f"'{p.get('title', '')}'에서 본인이 맡은 범위 확인 필요" for p in projects[:2]]
    checks += [f"{c.get('company', '')} 재직 중 실제 담당 업무 확인 필요" for c in careers[:1]]
    timeline = [{"period": c.get("period", ""), "text": f"{c.get('company', '')} {c.get('role', '')}".strip()}
                for c in careers if c.get("period")]
    return _json({"headline": headline, "keyPoints": [k for k in key_points if k.strip()] or ["자료가 적음"],
                  "strengths": strengths or ["자료에서 뚜렷한 강점을 찾지 못함"],
                  "checkPoints": checks or ["지원 동기와 직무 이해도 확인 필요"], "timeline": timeline})


def _count(tags: dict[str, str], default: int) -> int:
    try:
        return max(0, int(tags.get("count", default)))
    except ValueError:
        return default


@responder("briefing.jd_questions")
def _briefing_jd(tags: dict[str, str]) -> str:
    n = _count(tags, 4)
    phrases = [p.strip(" -•·\t") for p in re.split(r"[,.\n/;]", tags.get("jd", ""))]
    phrases = [p for p in phrases if 2 <= len(p) <= 30]
    questions: list[dict[str, str]] = []
    for p in phrases[:n]:
        questions.append({"source": "JD", "category": "직무", "intent": f"JD 요건 '{_short(p, 20)}' 확인",
                          "text": f"{p}와 관련해 직접 해 본 경험을 구체적으로 설명해 주세요."})
    for b in _load(tags, "question_bank", []):
        if len(questions) >= n:
            break
        questions.append({"source": "BANK", "category": b.get("category", ""), "text": b.get("text", ""),
                          "intent": "질문 은행에서 고름"})
    while len(questions) < n:
        questions.append({"source": "JD", "category": "직무", "intent": "직무 이해도 확인",
                          "text": f"{tags.get('job_title', '이')} 직무에서 중요하다고 보는 역량 {len(questions) + 1}가지를 설명해 주세요."})
    return _json({"questions": questions[:n]})


@responder("briefing.resume_questions")
def _briefing_resume(tags: dict[str, str]) -> str:
    n = _count(tags, 6)
    profile = _load(tags, "profile", {})
    questions: list[dict[str, str]] = []
    for p in profile.get("projects") or []:
        questions.append({"source": "RESUME", "category": "프로젝트", "intent": "기여도 확인",
                          "text": f"'{p.get('title', '')}'에서 본인이 맡은 역할과 기여를 설명해 주세요."})
    for c in profile.get("careers") or []:
        questions.append({"source": "RESUME", "category": "경력", "intent": "담당 업무 확인",
                          "text": f"{c.get('company', '')}에서 {c.get('role', '')}로 일하며 가장 어려웠던 일은 무엇이었나요?"})
    for e in profile.get("essays") or []:
        questions.append({"source": "RESUME", "category": "자기소개서", "intent": "서술 내용 확인",
                          "text": f"자기소개서의 '{e.get('title', '')}'에 쓴 내용을 사례를 들어 설명해 주세요."})
    for course in profile.get("courses") or []:
        questions.append({"source": "RESUME", "category": "전공", "intent": "기초 지식 확인",
                          "text": f"{course} 과목에서 배운 것 중 실제로 써 본 내용을 설명해 주세요."})
    while len(questions) < n:
        questions.append({"source": "RESUME", "category": "공통", "intent": "지원 동기 확인",
                          "text": f"지원하신 이유를 경험과 연결해 설명해 주세요. ({len(questions) + 1})"})
    return _json({"questions": questions[:n]})


# ---------- 면접 중 ----------
def refine_text(text: str) -> str:
    out = _FILLERS.sub("", text)
    out = re.sub(r"\b(\S+)(\s+\1\b)+", r"\1", out)  # 같은 낱말 반복
    out = re.sub(r"\s+", " ", out).strip()
    if out and out[-1] not in ".?!":
        out += "?" if _QUESTION_END.search(out) else "."
    return out or text.strip()


@responder("refine")
def _refine(tags: dict[str, str]) -> str:
    return refine_text(tags.get("utterance", ""))


@responder("speaker.judge")
def _speaker(tags: dict[str, str]) -> str:
    text = tags.get("utterance", "").strip()
    recent = _lines(tags.get("recent", ""))
    if _QUESTION_END.search(text):
        return _json({"speaker": "INTERVIEWER", "confidence": 0.7})
    if recent and recent[-1]["speaker"] == "INTERVIEWER":
        return _json({"speaker": "CANDIDATE", "confidence": 0.7})
    return _json({"speaker": "UNKNOWN", "confidence": 0.3})


_ENDING = re.compile(r"(니다|어요|아요|세요|는데|지만|으며|면서|해서|하고)$")
_PARTICLE = re.compile(r"(에서는|에서|으로|에게|은|는|이|가|을|를|의|도|로|와|과|에)$")
_FOLLOWUP_TEMPLATES = [
    ("'{kw}'은(는) 구체적으로 어떻게 진행하셨나요?", "'{kw}'의 실제 수행 내용 확인"),
    ("'{kw}'에서 본인이 직접 결정한 것은 무엇이었나요?", "'{kw}'에서의 본인 기여 확인"),
    ("'{kw}'의 결과는 어떤 기준으로 확인하셨나요?", "'{kw}'의 검증 방법 확인"),
    ("'{kw}' 때문에 가장 어려웠던 점은 무엇이었나요?", "'{kw}'에서의 문제 해결 확인"),
]


def _keywords(text: str, k: int = 2) -> list[str]:
    """글에서 긴 낱말 k개(서술어는 빼고 조사는 뗀다)."""
    words = [_PARTICLE.sub("", w) for w in re.findall(r"[가-힣A-Za-z0-9]{2,}", text) if not _ENDING.search(w)]
    seen: list[str] = []
    for w in sorted(set(words), key=lambda w: (-len(w), words.index(w))):
        if len(w) >= 2 and w not in seen:
            seen.append(w)
    return seen[:k]


@responder("followup")
def _followup(tags: dict[str, str]) -> str:
    lines = _lines(tags.get("qa", ""))
    answers = [ln for ln in lines if ln["speaker"] == "CANDIDATE"]
    last = answers[-1] if answers else {"seq": 0, "text": ""}
    items = []
    for i, kw in enumerate(_keywords(last["text"], 3)):
        text, why = _FOLLOWUP_TEMPLATES[(last["seq"] + i) % len(_FOLLOWUP_TEMPLATES)]
        items.append({"text": text.format(kw=kw), "rationale": why.format(kw=kw)})
    if len(items) < 2:
        items.append({"text": "그 과정에서 가장 어려웠던 점과 해결한 방법은 무엇이었나요?", "rationale": "문제 해결 과정 확인"})
    return _json({"items": items[:3]})


# ---------- 평가 ----------
@responder("evaluation")
def _evaluation(tags: dict[str, str]) -> str:
    criteria = _load(tags, "criteria", [])
    answers = [ln for ln in _lines(tags.get("transcript", "")) if ln["speaker"] == "CANDIDATE"]
    scores = []
    for i, c in enumerate(criteria):
        if not answers:
            scores.append({"criterionCode": c.get("code", ""), "score": None, "rationale": "근거가 되는 지원자 발화가 없음",
                           "evidenceSeqs": []})
            continue
        picked = [answers[(i + j) % len(answers)] for j in range(min(2, len(answers)))]
        length = sum(len(a["text"]) for a in picked)
        score = min(int(c.get("maxScore", 5)), 3 + (1 if length > 80 else 0))
        scores.append({"criterionCode": c.get("code", ""), "score": score,
                       "rationale": f"{c.get('name', '')} 항목은 \"{_short(picked[0]['text'], 40)}\" 등의 답변을 근거로 보았습니다.",
                       "evidenceSeqs": sorted({a["seq"] for a in picked})})
    comment = f"지원자 발화 {len(answers)}건을 기준으로 한 참고 평가입니다." if answers else "평가할 지원자 발화가 없습니다."
    return _json({"scores": scores, "overallComment": comment})


# ---------- 보고서 ----------
def _qa_pairs(lines: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    pairs = []
    for i, ln in enumerate(lines[:-1]):
        nxt = lines[i + 1]
        if ln["speaker"] == "INTERVIEWER" and nxt["speaker"] == "CANDIDATE":
            pairs.append({"question": _short(ln["text"], 80), "answerSummary": _short(nxt["text"], 100),
                          "seqs": [ln["seq"], nxt["seq"]]})
    return pairs[:limit]


def _overview(tags: dict[str, str], detailed: bool) -> str:
    lines = _lines(tags.get("transcript", ""))
    answers = [ln for ln in lines if ln["speaker"] == "CANDIDATE"]
    job = tags.get("job_title", "") or "해당 직무"
    sentences = [f"{job} 면접 대화 {len(lines)}건(지원자 발화 {len(answers)}건)을 바탕으로 한 보고서입니다.",
                 "지원자는 질문에 자신의 경험을 들어 답했습니다.",
                 "아래 문답과 근거 발화 번호를 함께 확인해 주세요."]
    if detailed:
        sentences += ["역량별 분석과 이력 검증은 별도 항목에 정리했습니다.", "평가는 면접관의 판단을 돕는 참고 자료입니다.",
                      "근거가 부족한 항목은 다음 차수 확인점으로 넘겼습니다."]
    return _json({
        "headline": f"{job} 면접 요약 (전사 기준: {tags.get('basis', 'LIVE')})",
        "overallComment": " ".join(sentences),
        "strengths": [f"경험을 구체적으로 설명함: \"{_short(a['text'], 40)}\"" for a in answers[:2]] or ["확인된 강점 없음"],
        "concerns": ["답이 짧았던 질문은 추가 확인이 필요합니다."],
        "qaHighlights": _qa_pairs(lines, 8 if detailed else 5),
    })


@responder("report.brief")
def _report_brief(tags: dict[str, str]) -> str:
    return _overview(tags, detailed=False)


@responder("report.detailed.overview")
def _report_overview(tags: dict[str, str]) -> str:
    return _overview(tags, detailed=True)


@responder("report.detailed.competencies")
def _report_competencies(tags: dict[str, str]) -> str:
    answers = [ln for ln in _lines(tags.get("transcript", "")) if ln["speaker"] == "CANDIDATE"]
    scores = (_load(tags, "ai_evaluation", {}) or {}).get("scores") or [{"criterionCode": "직무 역량"}]
    out = []
    for i, s in enumerate(scores[:6]):
        ev = [f"[{a['seq']}] {_short(a['text'], 60)}" for a in answers[i:i + 2]]
        out.append({"name": s.get("criterionName") or s.get("criterionCode", "역량"),
                    "assessment": s.get("rationale") or "면접 답변에서 관찰한 내용을 근거와 함께 정리했습니다.",
                    "evidence": ev})
    return _json({"competencies": out})


@responder("report.detailed.career")
def _report_career(tags: dict[str, str]) -> str:
    summary = _load(tags, "briefing_summary", {})
    transcript = tags.get("transcript", "")
    checks = []
    for claim in (summary.get("keyPoints") or [])[:5]:
        hit = any(k in transcript for k in _keywords(claim, 2))
        checks.append({"claim": claim, "finding": "면접에서 관련 답변이 있었음" if hit else "면접에서 다루지 않음"})
    return _json({"careerChecks": checks})


@responder("report.detailed.next")
def _report_next(tags: dict[str, str]) -> str:
    prev = _load(tags, "previous_rounds", [])
    checks = [f"이전 차수({r.get('type', '')}) 확인점 재확인: {cp}" for r in prev for cp in (r.get("checkPoints") or [])[:2]]
    checks.append("답이 얕았던 주제를 사례 중심으로 다시 확인")
    return _json({"nextRoundChecks": checks, "risks": ["전사 기반 자동 분석이므로 면접관 확인이 필요합니다."]})
