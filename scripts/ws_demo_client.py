"""WebSocket 데모 클라이언트: 입장권을 직접 만들어 접속하고, 합성 PCM을 실시간 속도로 보내며 받은 이벤트를 출력한다.

먼저 서버를 띄운다:  uvicorn app.main:app --port 8000
그다음:              python scripts/ws_demo_client.py --room 9001 --turns 6
끝에 --finalize 를 붙이면 방을 마무리(사후 STT)까지 시킨다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402
import websockets  # noqa: E402

from app.demo import synth  # noqa: E402
from app.live.ticket import make_ticket  # noqa: E402
from app.settings import SecuritySettings  # noqa: E402


def _show(event: dict, started: float) -> None:
    kind = event.get("type")
    stamp = f"[{time.monotonic() - started:6.1f}s]"
    if kind == "transcript.partial":
        print(f"{stamp} partial  #{event['seq']} {event['text'][:60]}")
    elif kind == "transcript.final":
        print(f"{stamp} final    #{event['seq']} {event['speaker']:<11} ({event['speakerMethod']}) {event['refinedText'][:70]}")
    elif kind == "question.matched":
        print(f"{stamp} matched  #{event['seq']} → 질문 {event['questionId']} (유사도 {event['similarity']})")
    elif kind == "followup.suggested":
        print(f"{stamp} followup (#{event['basedOnSeq']} 기준)")
        for item in event["items"]:
            print(f"           - [{item['id']}] {item['text']}")
    else:
        print(f"{stamp} {json.dumps(event, ensure_ascii=False)}")


async def run(args: argparse.Namespace) -> int:
    secret = args.secret or SecuritySettings().room_ticket_secret
    ticket = make_ticket(secret, room_id=args.room, audio=True)
    url = f"{args.url.rstrip('/')}/ws/v1/rooms/{args.room}"
    counts: dict[str, int] = {}
    started = time.monotonic()
    async with websockets.connect(url, max_size=None) as ws:
        await ws.send(json.dumps({"type": "auth", "ticket": ticket}))

        async def reader() -> None:
            async for raw in ws:
                event = json.loads(raw)
                counts[event.get("type", "?")] = counts.get(event.get("type", "?"), 0) + 1
                _show(event, started)

        reading = asyncio.create_task(reader())
        # 질문(짧게) / 답(길게)이 번갈아 나오는 대화
        turns = [(2.5, 1.5) if i % 2 == 0 else (5.0, 3.5) for i in range(args.turns)]
        pcm = synth.conversation(turns)
        print(f"오디오 {len(pcm) / 32000:.1f}초를 실시간 속도로 보냅니다 ({args.turns}개 발화).")
        next_at = time.monotonic()
        for frame in synth.frames(pcm):
            await ws.send(frame)
            next_at += synth.FRAME_MS / 1000 / args.speed
            await asyncio.sleep(max(0.0, next_at - time.monotonic()))
        await asyncio.sleep(args.tail)
        if args.finalize:
            async with httpx.AsyncClient() as http:
                base = args.url.replace("ws://", "http://").replace("wss://", "https://").rstrip("/")
                r = await http.post(f"{base}/internal/v1/rooms/{args.room}/finalize", json={"postStt": True},
                                    headers={"X-Internal-Key": args.internal_key}, timeout=60)
                print(f"finalize → {r.status_code} {r.text}")
            await asyncio.sleep(1.0)
        reading.cancel()
    print("받은 이벤트:", json.dumps(counts, ensure_ascii=False))
    return 0 if counts.get("transcript.final") else 1


def main() -> None:
    p = argparse.ArgumentParser(description="Agent WebSocket 데모 클라이언트")
    p.add_argument("--url", default="ws://localhost:8000")
    p.add_argument("--room", type=int, default=9001)
    p.add_argument("--turns", type=int, default=6, help="발화 수(질문·답 번갈아)")
    p.add_argument("--speed", type=float, default=1.0, help="1.0이 실시간")
    p.add_argument("--tail", type=float, default=5.0, help="다 보낸 뒤 이벤트를 기다리는 초")
    p.add_argument("--finalize", action="store_true", help="끝에 방 마무리를 부른다")
    p.add_argument("--secret", default=None)
    p.add_argument("--internal-key", default="dev-internal-key")
    sys.exit(asyncio.run(run(p.parse_args())))


if __name__ == "__main__":
    main()
