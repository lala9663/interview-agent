"""방 입장권: 백엔드가 발급한 HS256 JWT를 검증한다. 클레임: sub, name, roomId, audio, exp (API 명세 2.7)."""

from __future__ import annotations

import time
from dataclasses import dataclass

import jwt

ALGORITHM = "HS256"


class TicketError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Ticket:
    user_id: str
    name: str
    room_id: int
    audio: bool


def verify_ticket(token: str, secret: str, room_id: int) -> Ticket:
    try:
        claims = jwt.decode(token, secret, algorithms=[ALGORITHM], options={"require": ["exp", "roomId"]})
    except jwt.ExpiredSignatureError as exc:
        raise TicketError("TICKET_EXPIRED", "입장권이 만료되었습니다.") from exc
    except jwt.PyJWTError as exc:
        raise TicketError("TICKET_INVALID", "입장권이 올바르지 않습니다.") from exc
    try:
        ticket_room = int(claims["roomId"])
    except (TypeError, ValueError) as exc:
        raise TicketError("TICKET_INVALID", "입장권의 방 번호가 올바르지 않습니다.") from exc
    if ticket_room != int(room_id):
        raise TicketError("TICKET_INVALID", "다른 방의 입장권입니다.")
    return Ticket(user_id=str(claims.get("sub", "")), name=str(claims.get("name", "")), room_id=ticket_room,
                  audio=claims.get("audio") is True)


def make_ticket(secret: str, *, room_id: int, audio: bool, user_id: str = "1", name: str = "개발자",
                ttl_s: int = 300) -> str:
    """개발·테스트용 발급(실제 발급은 백엔드가 한다)."""
    return jwt.encode({"sub": str(user_id), "name": name, "roomId": int(room_id), "audio": bool(audio),
                       "exp": int(time.time()) + ttl_s}, secret, algorithm=ALGORITHM)
