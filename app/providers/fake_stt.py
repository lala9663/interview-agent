"""fake STT: 오디오 내용은 읽지 못하므로 데모 대본을 청크마다 한 줄씩 돌려준다.

options:
  latency_ms: 호출마다 기다리는 고정 시간
  latency_per_audio_s_ms: 오디오 1초마다 더 기다리는 시간(긴 청크가 더 오래 걸리는 것을 흉내)
  fail_every: N이면 N번째 호출마다 실패. 0이면 끔
"""

from __future__ import annotations

import asyncio

from app.demo.script import script_line
from app.providers.base import RoleConfig, STTProvider, SttRequest, SttResult


class FakeSTTProvider(STTProvider):
    def __init__(self, config: RoleConfig) -> None:
        super().__init__(config)
        self._cursor: dict[str, int] = {}
        self._count = 0

    async def _transcribe(self, request: SttRequest) -> SttResult:
        opts = self.config.options
        # 대본 위치는 기다리기 전에 잡는다: 동시에 불려도 부른 순서대로 줄이 나간다
        key = request.session or "_"
        index = self._cursor.get(key, 0)
        self._cursor[key] = index + 1
        self._count += 1
        count = self._count
        seconds = len(request.pcm) / 2 / max(1, request.sample_rate)
        delay = float(opts.get("latency_ms", 0)) + float(opts.get("latency_per_audio_s_ms", 0)) * seconds
        if delay:
            await asyncio.sleep(delay / 1000)
        fail_every = int(opts.get("fail_every", 0))
        if fail_every and count % fail_every == 0:
            self._cursor[key] = min(self._cursor.get(key, 0), index)  # 재시도 때 같은 줄을 다시 준다
            raise RuntimeError("fake-stt: 설정된 실패")
        return SttResult(text=script_line(index)[1])

    def reset_session(self, session: str) -> None:
        self._cursor.pop(session, None)
