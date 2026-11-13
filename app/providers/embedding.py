"""EmbeddingProvider 구현(선택). 설정하지 않으면(impl: none) 질문 매칭은 TF-IDF를 쓴다."""

from __future__ import annotations

import hashlib
import os

import httpx

from app.providers.base import EmbeddingProvider


class HashingEmbeddingProvider(EmbeddingProvider):
    """fake: 문자 2-gram을 해시해 고정 길이 벡터로 만든다(결정적). 임베딩 경로 확인용."""

    async def _embed(self, texts: list[str]) -> list[list[float]]:
        dim = int(self.config.options.get("dim", 256))
        out = []
        for text in texts:
            vec = [0.0] * dim
            compact = "".join(text.split())
            for i in range(len(compact) - 1):
                h = int.from_bytes(hashlib.md5(compact[i:i + 2].encode()).digest()[:4], "little")
                vec[h % dim] += 1.0
            out.append(vec)
        return out


class OpenAICompatibleEmbeddingProvider(EmbeddingProvider):
    """OpenAI 형식 `POST {base_url}/embeddings`. options: base_url, api_key_env, headers."""

    async def _embed(self, texts: list[str]) -> list[list[float]]:
        o = self.config.options
        headers = dict(o.get("headers") or {})
        key = os.environ.get(o.get("api_key_env", "EMBEDDING_API_KEY"), "")
        if key:
            headers["Authorization"] = f"Bearer {key}"
        async with httpx.AsyncClient(timeout=self.config.timeout_s) as client:
            r = await client.post(str(o["base_url"]).rstrip("/") + "/embeddings", headers=headers,
                                  json={"model": self.config.model, "input": texts})
            r.raise_for_status()
            rows = sorted(r.json()["data"], key=lambda d: d.get("index", 0))
            return [row["embedding"] for row in rows]
