"""부하 테스트: fake provider에 지연을 주고 방 여러 개를 동시에 돌려 지연과 큐 깊이를 표로 낸다.

  python scripts/load_test.py --rooms 20 30
  python scripts/load_test.py --rooms 20 --stt-latency-ms 2500 --stt-concurrency 4   # STT가 느리고 좁을 때

방마다 마이크 단말 하나가 실제 WebSocket으로 붙어, 합성 PCM(질문 3초 → 답 6초 …)을 실시간 속도로 보낸다.
서버는 이 스크립트가 따로 띄운다(uvicorn, 빈 포트). --url 을 주면 이미 떠 있는 서버에 붙는다.

재는 값(클라이언트에서 잰다):
  말 끝 → final     : 마지막 말소리 프레임을 보낸 때부터 그 발화의 transcript.final 을 받을 때까지
                      (VAD가 침묵을 기다리는 시간 min_silence_ms 포함)
  답 끝 → 꼬리질문  : 답의 마지막 말소리 프레임을 보낸 때부터 followup.suggested 를 받을 때까지
                      (followup_debounce_s 포함)
  큐 깊이           : status.queueDepth 와 /internal/v1/stats 의 최댓값

fake 모델의 지연은 가정값이다(--stt-latency-ms 등). 실제 사내 모델의 응답 시간을 재서 넣어야 의미가 있다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import socket
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402
import websockets  # noqa: E402

from app.demo import synth  # noqa: E402
from app.live.ticket import make_ticket  # noqa: E402
from app.settings import SecuritySettings  # noqa: E402

QUESTION = (3.0, 1.5)  # (말하는 초, 쉬는 초)
ANSWER = (6.0, 5.5)
KEY = {"X-Internal-Key": "dev-internal-key"}


@dataclass
class RoomResult:
    room_id: int
    expected: int = 0
    final_latency: list[float] = field(default_factory=list)
    followup_latency: list[float] = field(default_factory=list)
    max_queue_depth: int = 0
    errors: dict[str, int] = field(default_factory=dict)
    finals: int = 0
    followups: int = 0
    failed: str = ""


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_server(args: argparse.Namespace, port: int) -> subprocess.Popen:
    env = os.environ.copy()
    env.update({
        "APP_LOG_LEVEL": "WARNING", "BACKEND_MODE": "fake", "LIVE_MAX_ROOMS": "200",
        "PROVIDERS__STT__LIVE__OPTIONS__LATENCY_MS": str(args.stt_latency_ms),
        "PROVIDERS__STT__LIVE__OPTIONS__LATENCY_PER_AUDIO_S_MS": str(args.stt_latency_per_s_ms),
        "PROVIDERS__STT__LIVE__MAX_CONCURRENCY": str(args.stt_concurrency),
        "PROVIDERS__LLM__FAST__OPTIONS__LATENCY_MS": str(args.llm_latency_ms),
        "PROVIDERS__LLM__FAST__MAX_CONCURRENCY": str(args.llm_concurrency),
        "PYTHONIOENCODING": "utf-8",
    })
    return subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port),
                             "--log-level", "warning"], cwd=str(ROOT), env=env)


async def wait_healthy(base: str, timeout_s: float = 30.0) -> None:
    deadline = time.monotonic() + timeout_s
    async with httpx.AsyncClient() as http:
        while time.monotonic() < deadline:
            try:
                if (await http.get(f"{base}/health", timeout=2)).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.3)
    raise RuntimeError("서버가 뜨지 않았다.")


async def run_room(ws_base: str, room_id: int, pairs: int, secret: str, start_delay: float) -> RoomResult:
    result = RoomResult(room_id, expected=pairs * 2)
    turns = [QUESTION, ANSWER] * pairs
    speech_end: list[float] = []  # 발화별 말 끝 시각
    speech_end_pos: list[float] = []  # 발화별 말 끝의 오디오 위치(ms)
    final_seq_turn: dict[int, int] = {}  # seq → 몇 번째 발화인지

    def turn_of(end_ms: int) -> int | None:
        """final의 endMs(오디오 위치)로 어느 발화인지 찾는다. 청크가 버려져도 어긋나지 않는다."""
        best = min(range(len(speech_end_pos)), key=lambda i: abs(speech_end_pos[i] - end_ms), default=None)
        return best if best is not None and abs(speech_end_pos[best] - end_ms) <= 1500 else None

    await asyncio.sleep(start_delay)
    try:
        async with websockets.connect(f"{ws_base}/ws/v1/rooms/{room_id}", max_size=None) as ws:
            await ws.send(json.dumps({"type": "auth", "ticket": make_ticket(secret, room_id=room_id, audio=True)}))

            async def reader() -> None:
                async for raw in ws:
                    now = time.monotonic()
                    ev = json.loads(raw)
                    kind = ev.get("type")
                    if kind == "transcript.final":
                        result.finals += 1
                        turn = turn_of(ev["endMs"])
                        if turn is not None:
                            final_seq_turn[ev["seq"]] = turn
                            result.final_latency.append(now - speech_end[turn])
                    elif kind == "followup.suggested":
                        result.followups += 1
                        turn = final_seq_turn.get(ev["basedOnSeq"])
                        if turn is not None:
                            result.followup_latency.append(now - speech_end[turn])
                    elif kind == "status":
                        result.max_queue_depth = max(result.max_queue_depth, ev["queueDepth"])
                    elif kind == "error":
                        result.errors[ev["code"]] = result.errors.get(ev["code"], 0) + 1

            reading = asyncio.create_task(reader())
            next_at = time.monotonic()
            sent_ms = 0.0

            async def send(pcm: bytes) -> None:
                nonlocal next_at, sent_ms
                sent_ms += len(pcm) / 32
                for frame in synth.frames(pcm):
                    await ws.send(frame)
                    next_at += synth.FRAME_MS / 1000
                    await asyncio.sleep(max(0.0, next_at - time.monotonic()))

            await send(synth.silence(0.5, seed=room_id))
            for i, (talk, rest) in enumerate(turns):
                await send(synth.speech(talk, seed=room_id * 100 + i))
                speech_end.append(time.monotonic())
                speech_end_pos.append(sent_ms)
                await send(synth.silence(rest, seed=room_id * 100 + i))
            await asyncio.sleep(4.0)  # 마지막 이벤트를 기다린다
            reading.cancel()
    except Exception as exc:  # noqa: BLE001
        result.failed = repr(exc)
    return result


async def watch_stats(base: str, stop: asyncio.Event, peak: dict[str, int]) -> None:
    async with httpx.AsyncClient() as http:
        while not stop.is_set():
            try:
                s = (await http.get(f"{base}/internal/v1/stats", headers=KEY, timeout=2)).json()
                peak["rooms"] = max(peak.get("rooms", 0), s["rooms"])
                peak["queue"] = max([peak.get("queue", 0)] + [r["maxQueueDepth"] for r in s["roomStats"]])
                peak["stt_wait"] = max(peak.get("stt_wait", 0), s["providers"]["stt.live"]["waiting"])
                peak["llm_wait"] = max(peak.get("llm_wait", 0), s["providers"]["llm.fast"]["waiting"])
                peak["dropped"] = max([peak.get("dropped", 0)] + [r["droppedChunks"] for r in s["roomStats"]])
            except Exception:  # noqa: BLE001
                pass
            await asyncio.sleep(0.5)


def pct(values: list[float], p: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(p * (len(ordered) - 1))))]


async def run_once(args: argparse.Namespace, rooms: int) -> dict:
    proc = None
    if args.url:
        base = args.url.rstrip("/")
    else:
        port = free_port()
        base = f"http://127.0.0.1:{port}"
        proc = start_server(args, port)
    ws_base = base.replace("http://", "ws://").replace("https://", "wss://")
    try:
        await wait_healthy(base)
        secret = SecuritySettings().room_ticket_secret
        stop, peak = asyncio.Event(), {}
        watcher = asyncio.create_task(watch_stats(base, stop, peak))
        rng = random.Random(1)
        started = time.monotonic()
        results = await asyncio.gather(*(run_room(ws_base, 20000 + i, args.pairs, secret, rng.uniform(0, 3.0))
                                         for i in range(rooms)))
        elapsed = time.monotonic() - started
        stop.set()
        await watcher
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
    finals = [x for r in results for x in r.final_latency]
    followups = [x for r in results for x in r.followup_latency]
    errors: dict[str, int] = {}
    for r in results:
        for code, n in r.errors.items():
            errors[code] = errors.get(code, 0) + n
    return {
        "rooms": rooms, "elapsed": elapsed, "failed_rooms": [r.failed for r in results if r.failed],
        "finals": sum(r.finals for r in results), "expected_finals": sum(r.expected for r in results),
        "followups": sum(r.followups for r in results), "expected_followups": rooms * args.pairs,
        "final_p50": pct(finals, 0.5), "final_p95": pct(finals, 0.95), "final_max": max(finals, default=float("nan")),
        "final_mean": statistics.fmean(finals) if finals else float("nan"),
        "fu_p50": pct(followups, 0.5), "fu_p95": pct(followups, 0.95), "fu_max": max(followups, default=float("nan")),
        "queue_status": max((r.max_queue_depth for r in results), default=0), "peak": peak, "errors": errors,
    }


def print_table(args: argparse.Namespace, rows: list[dict]) -> None:
    print()
    print(f"조건: STT {args.stt_latency_ms}ms + 오디오 1초당 {args.stt_latency_per_s_ms}ms (동시 {args.stt_concurrency}), "
          f"LLM {args.llm_latency_ms}ms (동시 {args.llm_concurrency}), 방마다 질문·답 {args.pairs}쌍 "
          f"(질문 {QUESTION[0]}초, 답 {ANSWER[0]}초)")
    print()
    print("| 방 수 | final 받음 | 말 끝→final p50 | p95 | 최대 | 꼬리질문 받음 | 답 끝→꼬리질문 p50 | p95 | 최대 "
          "| 방별 큐 깊이 최대 | STT 대기 최대 | LLM 대기 최대 | 방별 건너뛴 청크 최대 | 오류 이벤트 |")
    print("| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |")
    for r in rows:
        peak = r["peak"]
        errors = ", ".join(f"{k}×{v}" for k, v in r["errors"].items()) or "없음"
        if r["failed_rooms"]:
            errors += f" / 접속 실패 {len(r['failed_rooms'])}방"
        def sec(key: str) -> str:
            return "-" if r[key] != r[key] else f"{r[key]:.2f}s"  # NaN이면 잰 값이 없다

        print(f"| {r['rooms']} | {r['finals']}/{r['expected_finals']} | {sec('final_p50')} | {sec('final_p95')} "
              f"| {sec('final_max')} | {r['followups']}/{r['expected_followups']} | {sec('fu_p50')} "
              f"| {sec('fu_p95')} | {sec('fu_max')} | {max(r['queue_status'], peak.get('queue', 0))} "
              f"| {peak.get('stt_wait', 0)} | {peak.get('llm_wait', 0)} | {peak.get('dropped', 0)} | {errors} |")
    print()


def main() -> None:
    p = argparse.ArgumentParser(description="동시 방 부하 테스트(fake provider)")
    p.add_argument("--rooms", type=int, nargs="+", default=[20], help="동시 방 수(여러 개면 차례로 돌린다)")
    p.add_argument("--pairs", type=int, default=4, help="방마다 질문·답 쌍 수")
    p.add_argument("--stt-latency-ms", type=int, default=1200)
    p.add_argument("--stt-latency-per-s-ms", type=int, default=60)
    p.add_argument("--stt-concurrency", type=int, default=8)
    p.add_argument("--llm-latency-ms", type=int, default=600)
    p.add_argument("--llm-concurrency", type=int, default=16)
    p.add_argument("--url", default="", help="이미 떠 있는 서버(http://host:port). 주면 서버를 띄우지 않는다")
    args = p.parse_args()
    rows = []
    for rooms in args.rooms:
        print(f"방 {rooms}개 실행 중 … (약 {int(args.pairs * (sum(QUESTION) + sum(ANSWER)) + 10)}초)", flush=True)
        rows.append(asyncio.run(run_once(args, rooms)))
    print_table(args, rows)
    sys.exit(1 if any(r["failed_rooms"] or r["finals"] == 0 for r in rows) else 0)


if __name__ == "__main__":
    main()
