"""문자 n-gram TF-IDF 코사인 유사도.

한국어는 조사·어미가 바뀌어도("맡은 역할은" / "맡으신 역할이") 글자 조각이 많이 겹친다. 형태소 분석기 없이
문자 n-gram만으로 비교하면 의존성이 없고 조사 변화에 강하다.
"""

from __future__ import annotations

import math
import re
from collections import Counter

_KEEP = re.compile(r"[^0-9a-z가-힣]+")
_SENT_SPLIT = re.compile(r"(?<=[.?!])\s+|\n+")


def normalize(text: str) -> str:
    """소문자로 바꾸고 한글·영문·숫자만 남긴다(띄어쓰기도 지운다: 전사는 띄어쓰기가 일정하지 않다)."""
    return _KEEP.sub("", text.lower())


def ngrams(text: str, n_min: int = 2, n_max: int = 3) -> Counter[str]:
    compact = normalize(text)
    grams: Counter[str] = Counter()
    for n in range(n_min, n_max + 1):
        for i in range(len(compact) - n + 1):
            grams[compact[i:i + n]] += 1
    return grams


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT_SPLIT.split(text) if s.strip()]


class NgramTfidfIndex:
    """작은 말뭉치(질문 수십~수백 개)용. 순수 파이썬 희소 벡터."""

    def __init__(self, texts: list[str], n_min: int = 2, n_max: int = 3) -> None:
        self.n_min, self.n_max = n_min, n_max
        self.size = len(texts)
        counts = [ngrams(t, n_min, n_max) for t in texts]
        df: Counter[str] = Counter()
        for c in counts:
            df.update(c.keys())
        self._idf = {g: math.log((1 + self.size) / (1 + d)) + 1.0 for g, d in df.items()}
        self._default_idf = math.log(1 + self.size) + 1.0  # 말뭉치에 없는 조각
        self._vectors = [self._weigh(c) for c in counts]

    def _weigh(self, counts: Counter[str]) -> dict[str, float]:
        vec = {g: (1.0 + math.log(tf)) * self._idf.get(g, self._default_idf) for g, tf in counts.items()}
        norm = math.sqrt(sum(v * v for v in vec.values()))
        return {g: v / norm for g, v in vec.items()} if norm else {}

    def similarities(self, text: str) -> list[float]:
        q = self._weigh(ngrams(text, self.n_min, self.n_max))
        if not q:
            return [0.0] * self.size
        return [sum(w * doc.get(g, 0.0) for g, w in q.items()) for doc in self._vectors]


def pair_similarity(a: str, b: str, n_min: int = 2, n_max: int = 3) -> float:
    """말뭉치 없이 두 글만 비교(꼬리질문 중복 제거용). IDF 없이 TF만 쓴다."""
    ca, cb = ngrams(a, n_min, n_max), ngrams(b, n_min, n_max)
    if not ca or not cb:
        return 0.0
    dot = sum(v * cb.get(g, 0) for g, v in ca.items())
    return dot / (math.sqrt(sum(v * v for v in ca.values())) * math.sqrt(sum(v * v for v in cb.values())))
