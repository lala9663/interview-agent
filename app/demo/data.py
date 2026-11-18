"""데모 데이터: 백엔드 없이(fake backend) 방을 돌릴 때 쓰는 context와 브리핑 요청 예시."""

from __future__ import annotations

from typing import Any

DEMO_JD = (
    "임베디드 SW 개발, C/C++ 펌웨어 설계와 구현, RTOS 기반 태스크 설계, 디바이스 드라이버 개발, "
    "코드 리뷰와 단위 테스트, 하드웨어 팀과의 협업"
)

DEMO_QUESTIONS: list[dict[str, Any]] = [
    {"id": 7001, "source": "RESUME", "category": "프로젝트", "text": "캡스톤 프로젝트에서 맡은 역할은 무엇이었나요?"},
    {"id": 7002, "source": "RESUME", "category": "프로젝트", "text": "프로젝트에서 가장 어려웠던 문제와 해결 방법을 설명해 주세요."},
    {"id": 7003, "source": "JD", "category": "직무", "text": "RTOS 환경에서 태스크 간 동기화는 어떻게 처리하나요?"},
    {"id": 7004, "source": "JD", "category": "직무", "text": "코드 리뷰에서 받은 피드백 중 기억에 남는 것은 무엇인가요?"},
    {"id": 7005, "source": "RESUME", "category": "경력", "text": "인턴 기간에 맡았던 업무를 소개해 주세요."},
]

DEMO_BANK: list[dict[str, Any]] = [
    {"id": 31, "category": "CS기초", "text": "프로세스와 스레드의 차이를 설명해 주세요."},
    {"id": 32, "category": "CS기초", "text": "교착 상태가 생기는 조건과 예방 방법은 무엇인가요?"},
    {"id": 33, "category": "협업", "text": "협업 중 의견 충돌을 어떻게 해결했나요?"},
    {"id": 34, "category": "CS기초", "text": "인터럽트와 폴링의 차이와 각각 알맞은 상황을 설명해 주세요."},
    {"id": 35, "category": "태도", "text": "새로운 기술을 배울 때 어떤 방법으로 익히나요?"},
]

DEMO_BRIEF = (
    "임베디드 프로젝트 경험이 뚜렷한 SW 지원자. 캡스톤에서 펌웨어 담당, STM32 기반 센서 수집 모듈 개발, "
    "반도체 장비 업체 인턴 2개월."
)

DEMO_PROFILE: dict[str, Any] = {
    "education": [{"school": "한국대학교", "major": "전자공학", "degree": "학사", "gpa": "3.9/4.5"}],
    "courses": ["자료구조", "운영체제", "임베디드시스템"],
    "essays": [{"title": "지원동기", "text": "하드웨어와 맞닿은 소프트웨어를 만들고 싶어 지원했습니다. 동아리에서 3년간 펌웨어를 개발했습니다."}],
    "projects": [{"title": "스마트 팜 센서 노드 캡스톤", "text": "STM32 기반 센서 수집 펌웨어와 통신 프로토콜을 설계했습니다."}],
    "careers": [{"company": "에이비씨테크", "role": "펌웨어 인턴", "period": "2025.07~2025.08", "text": "장비 제어 보드의 로그 수집 기능을 개발했습니다."}],
    "certificates": ["정보처리기사"],
    "extra": {},
}


def demo_context(room_id: int) -> dict[str, Any]:
    return {
        "roomId": room_id, "interviewId": 501, "status": "LIVE", "track": "NEW_GRAD", "interviewType": "JOB",
        "mode": "OFFLINE", "jobTitle": "SW개발", "jdText": DEMO_JD, "candidateBrief": DEMO_BRIEF,
        "questions": DEMO_QUESTIONS, "questionBank": DEMO_BANK,
    }


def demo_briefing_request() -> dict[str, Any]:
    return {
        "track": "NEW_GRAD", "interviewType": "JOB", "jobTitle": "SW개발", "jdText": DEMO_JD,
        "candidate": {"profile": DEMO_PROFILE}, "questionBank": DEMO_BANK, "questionCount": {"jd": 4, "resume": 6},
    }
