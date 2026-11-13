"""PCM 프레임을 말소리 기준으로 발화 청크로 자른다.

Whisper는 실시간 모델이 아니라서, 말이 끝날 때까지(또는 최대 길이까지) 메모리에 모았다가 한 번에 보낸다.
- 조용한 구간이 `min_silence_ms` 이어지면 청크를 끝낸다.
- `max_chunk_ms`를 넘으면 강제로 자른다(forced=True). 그 다음 청크는 continuation=True 이고, 방 파이프라인이
  두 청크의 글을 한 발화로 이어 붙인다. 최대 길이 3초 전부터는 짧은 쉼(160ms)이 보이면 거기서 자른다(낱말 중간을 피함).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np

from app.audio.vad import Vad, build_vad
from app.settings import AudioSettings

_SOFT_WINDOW_MS = 3000
_SOFT_PAUSE_MS = 160


@dataclass
class AudioChunk:
    pcm: bytes  # 비어 있으면 "앞의 강제 분할 발화가 끝났다"는 표시
    start_ms: int
    end_ms: int
    forced: bool = False  # 최대 길이에서 잘림 → 다음 청크와 이어진다
    continuation: bool = False  # 앞 청크(forced)에 이어지는 청크

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms


class SpeechChunker:
    def __init__(self, vad: Vad, *, sample_rate: int = 16000, frame_ms: int = 20, min_speech_ms: int = 120,
                 min_silence_ms: int = 700, max_chunk_ms: int = 15000, pad_ms: int = 200) -> None:
        self.vad = vad
        self.sample_rate = sample_rate
        self.frame_ms = frame_ms
        self.frame_bytes = sample_rate * frame_ms // 1000 * 2
        self.min_speech_frames = max(1, min_speech_ms // frame_ms)
        self.min_silence_frames = max(1, min_silence_ms // frame_ms)
        self.max_frames = max(self.min_speech_frames + 1, max_chunk_ms // frame_ms)
        self.pad_frames = max(0, pad_ms // frame_ms)
        self._soft_frames = max(1, self.max_frames - _SOFT_WINDOW_MS // frame_ms)
        self._soft_pause_frames = max(1, _SOFT_PAUSE_MS // frame_ms)
        self._remainder = b""
        self._frame_index = 0  # 지금까지 처리한 프레임 수
        self._ring: deque[bytes] = deque(maxlen=self.pad_frames + self.min_speech_frames)
        self._speech_run = 0
        self._in_speech = False
        self._buf: list[bytes] = []
        self._buf_start = 0  # 청크 첫 프레임의 index
        self._silence_run = 0
        self._speech_frames = 0  # 현재 청크 안의 말소리 프레임 수
        self._continuation = False

    @classmethod
    def from_settings(cls, settings: AudioSettings, *, post: bool = False) -> "SpeechChunker":
        return cls(build_vad(settings), sample_rate=settings.sample_rate, frame_ms=settings.vad_frame_ms,
                   min_speech_ms=settings.min_speech_ms, min_silence_ms=settings.min_silence_ms,
                   max_chunk_ms=settings.post_max_chunk_ms if post else settings.max_chunk_ms,
                   pad_ms=settings.pad_ms)

    @property
    def in_speech(self) -> bool:
        return self._in_speech

    @property
    def position_ms(self) -> int:
        return self._frame_index * self.frame_ms

    def feed(self, pcm: bytes) -> list[AudioChunk]:
        data = self._remainder + pcm if self._remainder else pcm  # 사후 STT의 큰 입력을 복사하지 않게
        usable = len(data) - len(data) % self.frame_bytes
        self._remainder = data[usable:]
        chunks: list[AudioChunk] = []
        for offset in range(0, usable, self.frame_bytes):
            frame = data[offset:offset + self.frame_bytes]
            out = self._step(frame)
            if out is not None:
                chunks.append(out)
        return chunks

    def flush(self) -> list[AudioChunk]:
        """남은 말소리를 청크로 내보낸다(마이크가 끊기거나 방이 끝날 때)."""
        self._remainder = b""
        if not self._in_speech:
            return []
        chunk = self._close(forced=False, keep_tail_frames=self._silence_run)
        return [chunk] if chunk is not None else []

    # ---- 내부 ----
    def _step(self, frame: bytes) -> AudioChunk | None:
        speech = self.vad.is_speech(np.frombuffer(frame, dtype="<i2"))
        self._frame_index += 1
        if not self._in_speech:
            self._ring.append(frame)
            self._speech_run = self._speech_run + 1 if speech else 0
            if self._speech_run >= self.min_speech_frames:
                self._in_speech = True
                self._buf = list(self._ring)
                self._buf_start = self._frame_index - len(self._buf)
                self._speech_frames = self._speech_run
                self._silence_run = 0
                self._ring.clear()
                self._speech_run = 0
            return None

        self._buf.append(frame)
        if speech:
            self._speech_frames += 1
            self._silence_run = 0
        else:
            self._silence_run += 1
        if self._silence_run >= self.min_silence_frames:
            return self._close(forced=False, keep_tail_frames=self._silence_run)
        length = len(self._buf)
        if length >= self.max_frames or (length >= self._soft_frames and self._silence_run >= self._soft_pause_frames):
            return self._close(forced=True, keep_tail_frames=0)
        return None

    def _close(self, *, forced: bool, keep_tail_frames: int) -> AudioChunk | None:
        """현재 청크를 끝낸다. 뒤쪽 침묵은 pad만큼만 남긴다."""
        frames = self._buf
        drop = max(0, keep_tail_frames - self.pad_frames)
        if drop:
            frames = frames[:-drop]
        start_ms = self._buf_start * self.frame_ms
        end_ms = (self._buf_start + len(frames)) * self.frame_ms
        has_speech = self._speech_frames >= self.min_speech_frames
        was_continuation = self._continuation
        self._buf = []
        self._speech_frames = 0
        self._silence_run = 0
        if forced:
            self._buf_start = self._frame_index
            self._continuation = True  # 말하는 중이므로 그대로 이어서 모은다
        else:
            self._in_speech = False
            self._continuation = False
        if not has_speech:
            if was_continuation and not forced:
                # 강제로 자른 직후 말이 끝났다: 앞 발화를 확정하라는 빈 표시
                return AudioChunk(b"", start_ms, start_ms, forced=False, continuation=True)
            if forced and not was_continuation:
                self._continuation = False
            return None
        return AudioChunk(b"".join(frames), start_ms, end_ms, forced=forced, continuation=was_continuation)
