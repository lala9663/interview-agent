"""질문 매칭: 발화가 준비된 질문·질문 은행·방금 제안한 꼬리질문 중 어느 것과 비슷한지 찾는다.

기본은 문자 n-gram TF-IDF. `EmbeddingProvider`가 설정돼 있으면 임베딩 코사인을 쓴다.
발화가 "네 잘 들었습니다. 그럼 ~은 무엇인가요?"처럼 앞말이 붙은 경우를 위해, 전체와 문장별 유사도 중 큰 값을 쓴다.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from app.matching.tfidf import NgramTfidfIndex, split_sentences
from app.providers.base import EmbeddingProvider

KIND_PREPARED = "PREPARED"  # 이 면접에 준비된 질문(id가 Question id)
KIND_BANK = "BANK"  # 질문 은행(id가 은행 id, question.matched로는 내보내지 않는다)
KIND_FOLLOWUP = "FOLLOWUP"  # 방금 제안한 꼬리질문(id 없음)


@dataclass(frozen=True)
class MatchCandidate:
    kind: str
    text: str
    question_id: int | None = None
    ref: str | None = None  # 꼬리질문 clientId 등


@dataclass(frozen=True)
class Match:
    candidate: MatchCandidate
    similarity: float


class QuestionMatcher:
    def __init__(self, candidates: list[MatchCandidate] | None = None, *, n_min: int = 2, n_max: int = 3,
                 embedding: EmbeddingProvider | None = None) -> None:
        self._n = (n_min, n_max)
        self._embedding = embedding
        self._candidates: list[MatchCandidate] = []
        self._index: NgramTfidfIndex | None = None
        self._matrix: np.ndarray | None = None
        self._dirty = True
        if candidates:
            self.add(candidates)

    def __len__(self) -> int:
        return len(self._candidates)

    @property
    def candidates(self) -> list[MatchCandidate]:
        return list(self._candidates)

    def add(self, candidates: list[MatchCandidate]) -> None:
        known = {(c.kind, c.text) for c in self._candidates}
        fresh = [c for c in candidates if c.text.strip() and (c.kind, c.text) not in known]
        if fresh:
            self._candidates.extend(fresh)
            self._dirty = True

    def replace(self, candidates: list[MatchCandidate], *, keep_kinds: tuple[str, ...] = ()) -> None:
        kept = [c for c in self._candidates if c.kind in keep_kinds]
        self._candidates = []
        self._dirty = True
        self.add(kept + candidates)

    async def _scores(self, queries: list[str]) -> list[list[float]]:
        if self._embedding is not None:
            if self._dirty or self._matrix is None:
                self._matrix = _unit(np.array(await self._embedding.embed([c.text for c in self._candidates])))
                self._dirty = False
            q = _unit(np.array(await self._embedding.embed(queries)))
            return (q @ self._matrix.T).tolist()
        if self._dirty or self._index is None:
            self._index = NgramTfidfIndex([c.text for c in self._candidates], *self._n)
            self._dirty = False
        return [self._index.similarities(q) for q in queries]

    async def best(self, text: str, *, kinds: tuple[str, ...] | None = None) -> Match | None:
        """가장 비슷한 후보. 후보가 없으면 None. 임계값 비교는 부르는 쪽이 한다."""
        if not self._candidates or not text.strip():
            return None
        queries = [text] + [s for s in split_sentences(text) if s != text.strip()]
        rows = await self._scores(queries)
        best_i, best_s = -1, -1.0
        for row in rows:
            for i, score in enumerate(row):
                if kinds is not None and self._candidates[i].kind not in kinds:
                    continue
                if score > best_s:
                    best_i, best_s = i, score
        if best_i < 0:
            return None
        return Match(self._candidates[best_i], float(max(0.0, min(1.0, best_s))))


def _unit(matrix: np.ndarray) -> np.ndarray:
    if matrix.ndim == 1:
        matrix = matrix.reshape(1, -1)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms
