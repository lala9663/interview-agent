"""블라인드 필터.

- 입력 필터: 모델에 넣기 전에 프로필에서 금지 키를 지우고, 글에서 금지 항목이 있는 문장을 가린다.
- 출력 검사: 모델이 낸 요약·질문·꼬리질문·보고서에 금지 항목이 있는지 본다. 그래프는 이 검사를 반드시 거친다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.blind.rules import BlindRules, Topic

_SENTENCE = re.compile(r"[^.!?\n]+[.!?]*\s*|\n+")


@dataclass(frozen=True)
class Violation:
    topic: str
    label: str
    matched: str
    rule_type: str  # keyword | pattern


class BlindFilter:
    def __init__(self, rules: BlindRules, mask_text: str = "[블라인드 처리됨]") -> None:
        self.rules = rules
        self.mask = mask_text
        self._key_to_topic: dict[str, str] = {k: t.code for t in rules.topics for k in t.profile_keys}

    # ---------- 검사 ----------
    def _topic_hits(self, topic: Topic, text: str) -> list[Violation]:
        allowed = [(m.start(), m.end()) for a in topic.allow for m in re.finditer(re.escape(a), text)]

        def ok(start: int, end: int) -> bool:
            return not any(s < end and start < e for s, e in allowed)

        hits: list[Violation] = []
        for kw in topic.keywords:
            for m in re.finditer(re.escape(kw), text):
                if ok(m.start(), m.end()):
                    hits.append(Violation(topic.code, topic.label, kw, "keyword"))
                    break
        for pat in topic.patterns:
            for m in pat.finditer(text):
                if ok(m.start(), m.end()):
                    hits.append(Violation(topic.code, topic.label, m.group(0), "pattern"))
                    break
        return hits

    def check_text(self, text: str) -> list[Violation]:
        if not text or self.mask and text.strip() == self.mask:
            return []
        clean = text.replace(self.mask, " ") if self.mask else text
        return [v for topic in self.rules.topics for v in self._topic_hits(topic, clean)]

    def check_texts(self, texts: list[str]) -> list[list[Violation]]:
        return [self.check_text(t) for t in texts]

    def check_obj(self, obj: Any) -> list[Violation]:
        """dict·list 안의 모든 문자열을 검사한다."""
        if isinstance(obj, str):
            return self.check_text(obj)
        if isinstance(obj, dict):
            return [v for value in obj.values() for v in self.check_obj(value)]
        if isinstance(obj, (list, tuple)):
            return [v for value in obj for v in self.check_obj(value)]
        return []

    def is_clean(self, text: str) -> bool:
        return not self.check_text(text)

    # ---------- 가리기 ----------
    def mask_sentences(self, text: str) -> tuple[str, list[str]]:
        """금지 항목이 있는 문장을 통째로 가린다. (가린 글, 걸린 topic code 목록)"""
        if not text:
            return text, []
        codes: list[str] = []
        out: list[str] = []
        for m in _SENTENCE.finditer(text):
            sentence = m.group(0)
            hits = self.check_text(sentence)
            if hits:
                codes.extend(v.topic for v in hits)
                tail = sentence[len(sentence.rstrip()):]
                if not out or out[-1].strip() != self.mask:  # 이어진 문장은 한 번만 표시
                    out.append(self.mask + (tail or " "))
            else:
                out.append(sentence)
        return "".join(out).strip() if codes else text, _unique(codes)

    def scrub(self, obj: Any) -> tuple[Any, list[str]]:
        """dict·list 안의 모든 문자열에 `mask_sentences`를 적용한다(출력의 마지막 안전망)."""
        codes: list[str] = []

        def walk(node: Any) -> Any:
            if isinstance(node, str):
                masked, found = self.mask_sentences(node)
                codes.extend(found)
                return masked
            if isinstance(node, dict):
                return {k: walk(v) for k, v in node.items()}
            if isinstance(node, list):
                return [walk(v) for v in node]
            return node

        return walk(obj), _unique(codes)

    def filter_profile(self, profile: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        """프로필에서 금지 키를 지우고 글 속 금지 문장을 가린다. (걸러진 프로필, 제외된 topic code)"""
        codes: list[str] = []

        def walk(node: Any) -> Any:
            if isinstance(node, dict):
                out = {}
                for key, value in node.items():
                    topic = self._key_to_topic.get(str(key).lower())
                    if topic:
                        codes.append(topic)
                        continue
                    out[key] = walk(value)
                return out
            if isinstance(node, list):
                return [walk(v) for v in node]
            if isinstance(node, str):
                masked, found = self.mask_sentences(node)
                codes.extend(found)
                return masked
            return node

        return walk(profile), _unique(codes)


def _unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))
