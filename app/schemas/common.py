"""API 명세 공통 타입: camelCase 모델과 열거형 (docs/contract/api-contract.md 1장)."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    """JSON은 camelCase, 파이썬 코드는 snake_case."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="ignore",
        use_enum_values=True,
    )

    def to_wire(self) -> dict:
        return self.model_dump(by_alias=True, mode="json")


class Track(str, Enum):
    NEW_GRAD = "NEW_GRAD"
    EXPERIENCED = "EXPERIENCED"


class InterviewType(str, Enum):
    PT = "PT"
    PERSONALITY = "PERSONALITY"
    PORTFOLIO = "PORTFOLIO"
    JOB = "JOB"


class InterviewMode(str, Enum):
    OFFLINE = "OFFLINE"
    ONLINE = "ONLINE"


class RoomStatus(str, Enum):
    CREATED = "CREATED"
    LIVE = "LIVE"
    ENDED = "ENDED"


class Speaker(str, Enum):
    INTERVIEWER = "INTERVIEWER"
    CANDIDATE = "CANDIDATE"
    UNKNOWN = "UNKNOWN"


class SpeakerMethod(str, Enum):
    """API 명세의 SpeakerMethod. 규칙 판정(RULE)도 API 명세에 있는 값이다.

    밖으로 나갈 때는 `wire_speaker_method()`가 RULE을 설정값(기본 RULE, API 명세에 있는 값)으로 내보낸다.
    """

    MANUAL = "MANUAL"
    QUESTION_MATCH = "QUESTION_MATCH"
    LLM = "LLM"
    CHANNEL = "CHANNEL"
    NONE = "NONE"
    RULE = "RULE"  # 말투 규칙으로 판정(밖으로 나가는 값은 SPEAKER_RULE_WIRE_METHOD 로 바꿀 수 있다)


def wire_speaker_method(method: SpeakerMethod | str, rule_as: str = "RULE") -> str:
    value = method.value if isinstance(method, SpeakerMethod) else str(method)
    return rule_as if value == SpeakerMethod.RULE.value else value


class QuestionSource(str, Enum):
    JD = "JD"
    RESUME = "RESUME"
    BANK = "BANK"
    FOLLOW_UP = "FOLLOW_UP"
    MANUAL = "MANUAL"


class ReportBasis(str, Enum):
    LIVE = "LIVE"
    REFINED = "REFINED"


class ReportDetail(str, Enum):
    BRIEF = "BRIEF"
    DETAILED = "DETAILED"


class AudioRetention(str, Enum):
    MEMORY_ONLY = "MEMORY_ONLY"
    KEEP = "KEEP"


class ErrorBody(CamelModel):
    code: str
    message: str
    details: dict = {}
