"""WebSocket `/ws/v1/rooms/{roomId}` (계약 3장).

연결 → 5초 안에 `auth` → `ready` → (마이크 단말이면 바이너리 오디오) / 텍스트 명령.
이벤트 방송은 hub가, 방 파이프라인은 RoomSession이 맡는다. 여기서는 연결의 수명만 다룬다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from fastapi import APIRouter, WebSocket
from starlette.websockets import WebSocketDisconnect

from app.container import Container
from app.live.hub import Connection
from app.live.session import RoomSession
from app.live.ticket import Ticket, TicketError, verify_ticket
from app.schemas import ws as events

log = logging.getLogger(__name__)
router = APIRouter()
_CONTROL_TYPES = {"role", "question.asked", "followup.request", "utterance.fix"}


async def _reject(ws: WebSocket, code: str, message: str, close_code: int) -> None:
    try:
        await ws.send_json(events.error(code, message, retryable=False))
        await ws.close(code=close_code, reason=code)
    except Exception:  # noqa: BLE001 - 이미 끊긴 연결
        pass


async def _authenticate(ws: WebSocket, c: Container, room_id: int) -> Ticket | None:
    try:
        raw = await asyncio.wait_for(ws.receive_text(), timeout=c.settings.security.ws_auth_timeout_s)
        msg = json.loads(raw)
        if not isinstance(msg, dict) or msg.get("type") != "auth" or not isinstance(msg.get("ticket"), str):
            raise TicketError(events.TICKET_INVALID, "첫 메시지는 auth여야 합니다.")
        return verify_ticket(msg["ticket"], c.settings.security.room_ticket_secret, room_id)
    except TicketError as exc:
        await _reject(ws, exc.code, exc.message, events.CLOSE_AUTH)
    except asyncio.TimeoutError:
        await _reject(ws, events.TICKET_INVALID, "제한 시간 안에 auth가 오지 않았습니다.", events.CLOSE_AUTH)
    except (json.JSONDecodeError, KeyError, RuntimeError):  # RuntimeError: 텍스트가 아닌 프레임
        await _reject(ws, events.TICKET_INVALID, "auth 메시지 형식이 올바르지 않습니다.", events.CLOSE_AUTH)
    except WebSocketDisconnect:
        pass
    return None


@router.websocket("/ws/v1/rooms/{room_id}")
async def room_socket(ws: WebSocket, room_id: int) -> None:
    c: Container = ws.app.state.container
    await ws.accept()
    ticket = await _authenticate(ws, c, room_id)
    if ticket is None:
        return

    conn = Connection(ws, audio=ticket.audio, user_id=ticket.user_id)
    holder = f"{c.instance_id}:{conn.id}"
    lock_ttl = c.settings.live.audio_lock_ttl_s
    session: RoomSession | None = None

    if ticket.audio:
        if not c.rooms.can_accept_room(room_id):
            await _reject(ws, events.OVERLOADED, "서버가 받을 수 있는 방 수를 넘었습니다.", events.CLOSE_OVERLOADED)
            return
        if not await c.state.acquire_audio(room_id, holder, lock_ttl):
            await _reject(ws, events.AUDIO_ALREADY_CONNECTED, "이 방에는 이미 마이크 단말이 있습니다.",
                          events.CLOSE_AUDIO_DUP)
            return
        try:
            session, context_ok = await c.rooms.open_session(room_id)
        except Exception:  # noqa: BLE001
            await c.state.release_audio(room_id, holder)
            raise
        if session.context.status == "ENDED" or session.closed:
            await c.state.release_audio(room_id, holder)
            await _reject(ws, events.ROOM_NOT_LIVE, "이미 끝난 방입니다.", events.CLOSE_NORMAL)
            return

    await c.hub.join(room_id, conn)
    try:
        await conn.send(events.ready(room_id, ticket.audio, c.providers.stt_live.model, c.providers.llm_fast.model,
                                     await c.state.last_seq(room_id)))
        if session is not None:
            if not context_ok:
                await conn.send(events.error(events.BACKEND_UNAVAILABLE,
                                             "백엔드에서 방 정보를 읽지 못했습니다. 전사는 계속됩니다.", True))
            await session.set_audio_connected(True)
        else:
            local = c.rooms.sessions.get(room_id)
            if local is not None:
                await conn.send(local.status_event())
        await _receive_loop(ws, c, conn, room_id, session, holder, lock_ttl)
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        log.exception("room %s WebSocket 오류", room_id)
    finally:
        await c.hub.leave(room_id, conn)
        if ticket.audio:
            try:
                await c.state.release_audio(room_id, holder)
            except Exception:  # noqa: BLE001 - 풀지 못해도 TTL(30초)이 지나면 풀린다
                log.warning("room %s 마이크 잠금 해제 실패", room_id)
            if session is not None and not session.closed:
                await session.set_audio_connected(False)


async def _receive_loop(ws: WebSocket, c: Container, conn: Connection, room_id: int,
                        session: RoomSession | None, holder: str, lock_ttl: int) -> None:
    last_refresh = time.monotonic()
    while True:
        message = await ws.receive()
        if message["type"] == "websocket.disconnect":
            return
        data = message.get("bytes")
        if data is not None:
            if session is None or session.closed:
                continue  # 마이크 단말이 아니면 오디오를 받지 않는다
            now = time.monotonic()
            if now - last_refresh > lock_ttl / 3:
                last_refresh = now
                try:
                    await c.state.refresh_audio(room_id, holder, lock_ttl)
                except Exception:  # noqa: BLE001 - 상태 저장소가 잠깐 끊겨도 오디오는 계속 받는다
                    log.warning("room %s 마이크 잠금 갱신 실패", room_id)
            if await session.accepting_audio():
                await session.feed(data)
            continue
        text = message.get("text")
        if text is None:
            continue
        try:
            msg: Any = json.loads(text)
        except json.JSONDecodeError:
            continue
        if not isinstance(msg, dict):
            continue
        kind = msg.get("type")
        if kind == "ping":
            await conn.send(events.pong())
        elif kind in _CONTROL_TYPES:
            # 방의 주인 인스턴스가 다를 수 있으므로 버스로 보낸다(한 대일 때는 바로 처리된다)
            await c.rooms.send_control(room_id, msg)
