"""방 파이프라인이 쓰는 의존성 묶음과 발화 자료형."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.audio.store import AudioStore
from app.backend.client import BackendClient
from app.backend.outbox import BackendOutbox
from app.blind.filter import BlindFilter
from app.graphs.runner import GraphRunner
from app.providers.registry import ProviderSet
from app.schemas.backend import UtteranceItem
from app.schemas.common import SpeakerMethod, wire_speaker_method
from app.settings import Settings
from app.state.event_bus import EventBus
from app.state.room_store import RoomStateStore


@dataclass
class LiveDeps:
    settings: Settings
    providers: ProviderSet
    graphs: GraphRunner
    blind: BlindFilter
    backend: BackendClient
    outbox: BackendOutbox
    state: RoomStateStore
    bus: EventBus
    audio_store: AudioStore
    instance_id: str


@dataclass
class Utterance:
    seq: int
    speaker: str
    method: SpeakerMethod
    raw_text: str
    refined_text: str
    start_ms: int
    end_ms: int
    matched_question_id: int | None = None

    def wire_method(self, rule_as: str) -> str:
        return wire_speaker_method(self.method, rule_as)

    def to_item(self, rule_as: str) -> UtteranceItem:
        return UtteranceItem(seq=self.seq, speaker=self.speaker, speaker_method=self.wire_method(rule_as),
                             raw_text=self.raw_text, refined_text=self.refined_text, start_ms=self.start_ms,
                             end_ms=self.end_ms, matched_question_id=self.matched_question_id)


@dataclass
class PendingUtterance:
    """STT는 끝났고 다듬기·화자 판정을 기다리는 발화(강제 분할 청크는 여기에 이어 붙는다)."""

    seq: int
    text: str
    start_ms: int
    end_ms: int
    role_override: str | None
    degraded: bool = False  # 큐가 밀려 다듬기를 건너뛴다
    enqueued_at: float = 0.0


def glossary_from_context(context: Any) -> list[str]:
    """다듬기·STT 힌트용 낱말: 직무 이름과 질문에 나온 영문·숫자 섞인 용어."""
    import re

    words: list[str] = [context.job_title] if context.job_title else []
    text = " ".join([context.jd_text, context.candidate_brief] + [q.text for q in context.questions])
    for w in re.findall(r"[A-Za-z][A-Za-z0-9+#./-]{1,}", text):
        if w not in words:
            words.append(w)
    return words[:40]
