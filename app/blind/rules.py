"""블라인드/금지 항목 규칙을 YAML(config/blind_rules.yaml)에서 읽는다."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class Topic:
    code: str
    label: str
    description: str = ""
    profile_keys: frozenset[str] = frozenset()
    keywords: tuple[str, ...] = ()
    patterns: tuple[re.Pattern[str], ...] = ()
    allow: tuple[str, ...] = ()


@dataclass
class BlindRules:
    version: str = "none"
    topics: list[Topic] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> "BlindRules":
        topics = []
        for raw in data.get("topics") or []:
            if not raw.get("code") or not raw.get("label"):
                raise ValueError(f"blind_rules: topic에는 code와 label이 필요하다: {raw}")
            try:
                patterns = tuple(re.compile(p) for p in raw.get("patterns") or [])
            except re.error as exc:
                raise ValueError(f"blind_rules: topic '{raw['code']}'의 정규식 오류: {exc}") from exc
            topics.append(Topic(
                code=str(raw["code"]), label=str(raw["label"]), description=str(raw.get("description", "")),
                profile_keys=frozenset(str(k).lower() for k in raw.get("profile_keys") or []),
                keywords=tuple(str(k) for k in raw.get("keywords") or []),
                patterns=patterns, allow=tuple(str(a) for a in raw.get("allow") or []),
            ))
        codes = [t.code for t in topics]
        if len(codes) != len(set(codes)):
            raise ValueError("blind_rules: topic code가 겹친다.")
        return cls(version=str(data.get("version", "none")), topics=topics)

    @classmethod
    def load(cls, path: str | Path) -> "BlindRules":
        return cls.from_dict(yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {})
