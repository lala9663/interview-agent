"""개발용 방 입장권을 만든다(실제 발급은 백엔드의 POST /rooms/{id}/ticket).

예) python scripts/make_ticket.py --room 9001 --audio
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.live.ticket import make_ticket  # noqa: E402
from app.settings import SecuritySettings  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser(description="개발용 방 입장권(JWT) 발급")
    p.add_argument("--room", type=int, default=9001)
    p.add_argument("--audio", action="store_true", help="마이크를 보내는 단말")
    p.add_argument("--user", default="1")
    p.add_argument("--name", default="개발자")
    p.add_argument("--ttl", type=int, default=300, help="유효 시간(초)")
    p.add_argument("--secret", default=None, help="비우면 ROOM_TICKET_SECRET(또는 개발 기본값)")
    a = p.parse_args()
    secret = a.secret or SecuritySettings().room_ticket_secret
    print(make_ticket(secret, room_id=a.room, audio=a.audio, user_id=a.user, name=a.name, ttl_s=a.ttl))


if __name__ == "__main__":
    main()
