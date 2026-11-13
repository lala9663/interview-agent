"""백엔드 저장 재시도 큐(outbox).

저장(전사·꼬리질문·이벤트·보정 전사)이 실패해도 면접 흐름은 멈추지 않는다: 호출은 큐에 넣고 바로 돌아오며,
뒤에서 순서대로 보내고 실패하면 지수 백오프로 다시 보낸다. 횟수를 다 쓰면 버리고 로그를 남긴다.
큐는 프로세스 메모리에 있다(인스턴스가 죽으면 사라진다: 전사는 사후 STT 콜백으로 다시 채워진다).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable

from app.backend.client import BackendClient
from app.schemas.backend import BackendEvent, FollowUpsRequest, RefinedTranscriptRequest, UtterancesRequest
from app.settings import BackendSettings

log = logging.getLogger(__name__)


@dataclass
class _Job:
    name: str
    room_id: int
    send: Callable[[], Awaitable[None]]
    attempts: int = 0


class BackendOutbox:
    def __init__(self, client: BackendClient, settings: BackendSettings) -> None:
        self.client = client
        self.settings = settings
        self._queue: asyncio.Queue[_Job] = asyncio.Queue(maxsize=settings.retry_queue_size)
        self._task: asyncio.Task | None = None
        self._inflight = 0
        self._delayed = 0  # 재시도를 기다리는 작업 수
        self.dropped = 0
        self.healthy = True

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="backend-outbox")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    @property
    def pending(self) -> int:
        return self._queue.qsize() + self._inflight + self._delayed

    def _put(self, job: _Job) -> None:
        self.start()
        try:
            self._queue.put_nowait(job)
        except asyncio.QueueFull:
            self.dropped += 1
            log.error("백엔드 재시도 큐가 가득 차 %s(room=%s)를 버렸다.", job.name, job.room_id)

    # ---- 저장 요청(바로 돌아온다) ----
    def save_utterances(self, room_id: int, body: UtterancesRequest) -> None:
        self._put(_Job("utterances", room_id, lambda: self.client.save_utterances(room_id, body)))

    def save_followups(self, room_id: int, body: FollowUpsRequest) -> None:
        self._put(_Job("follow-ups", room_id, lambda: self.client.save_followups(room_id, body)))

    def put_refined_transcript(self, room_id: int, body: RefinedTranscriptRequest) -> None:
        self._put(_Job("refined-transcript", room_id, lambda: self.client.put_refined_transcript(room_id, body)))

    def post_event(self, room_id: int, body: BackendEvent) -> None:
        self._put(_Job("events", room_id, lambda: self.client.post_event(room_id, body)))

    async def drain(self, timeout_s: float = 10.0) -> bool:
        """큐가 빌 때까지 기다린다(방 마무리·테스트용). 시간 안에 비면 True."""
        deadline = asyncio.get_running_loop().time() + timeout_s
        while self.pending:
            if asyncio.get_running_loop().time() > deadline:
                return False
            await asyncio.sleep(0.01)
        return True

    async def _run(self) -> None:
        while True:
            job = await self._queue.get()
            self._inflight += 1
            try:
                await job.send()
                self.healthy = True
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self.healthy = False
                job.attempts += 1
                if job.attempts >= self.settings.retry_max_attempts:
                    self.dropped += 1
                    log.error("백엔드 저장 포기: %s room=%s (%d회 실패) %r", job.name, job.room_id, job.attempts, exc)
                else:
                    delay = min(30.0, self.settings.retry_base_delay_s * (2 ** (job.attempts - 1)))
                    log.warning("백엔드 저장 실패, %.1f초 뒤 재시도: %s room=%s %r", delay, job.name, job.room_id, exc)
                    self._delayed += 1
                    asyncio.get_running_loop().call_later(delay, self._requeue, job)
            finally:
                self._inflight -= 1

    def _requeue(self, job: _Job) -> None:
        self._delayed -= 1
        try:
            self._queue.put_nowait(job)
        except asyncio.QueueFull:
            self.dropped += 1
