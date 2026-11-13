"""화자 판정 인터페이스.

Whisper는 화자를 나누지 못한다고 가정한다. 발화마다 판정기를 순서대로 돌려, 처음으로 답을 낸 판정기의 결과를 쓴다.
새 방법(예: 마이크 2개의 크기 비교, 화자분리 모델)을 넣으려면 `SpeakerJudge`를 구현하고
app/speaker/chain.py의 `build_speaker_chain` 안 표에 이름을 더한 뒤 `SPEAKER_CHAIN`에 넣는다.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from app.matching.matcher import Match
from app.schemas.common import Speaker, SpeakerMethod


@dataclass
class RecentUtterance:
    seq: int
    speaker: str
    text: str


@dataclass
class SpeakerContext:
    text: str
    role_override: str | None = None  # 클라이언트 role 메시지: INTERVIEWER | CANDIDATE | None(AUTO)
    channel_speaker: str | None = None  # 마이크·트랙이 나뉘어 있을 때 채널에서 온 화자(지금은 항상 None)
    match: Match | None = None  # 준비된 질문·질문 은행·제안한 꼬리질문과의 최고 유사도
    recent: list[RecentUtterance] = field(default_factory=list)  # 앞선 발화(오래된 것부터)
    duration_ms: int = 0


@dataclass
class SpeakerDecision:
    speaker: Speaker
    method: SpeakerMethod
    confidence: float = 1.0
    reason: str = ""


class SpeakerJudge(ABC):
    name: str = ""

    @abstractmethod
    async def judge(self, ctx: SpeakerContext) -> SpeakerDecision | None:
        """판정하지 못하면 None(다음 판정기로 넘어간다)."""
