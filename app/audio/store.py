"""방 전체 음성을 모아 두는 곳(사후 STT용).

기본은 `MemoryAudioStore`: 파일로 남기지 않고, 사후 STT가 끝나면 버린다.
`FileAudioStore`는 구현해 두었지만 기본은 꺼져 있다. 켜는 법: docs/audio-pipeline.md "저장 방식 전환".

  AUDIO_STORE=memory, AUDIO_RETENTION=MEMORY_ONLY  → 기본. 메모리에만 두고 끝나면 삭제
  AUDIO_STORE=file,   AUDIO_RETENTION=MEMORY_ONLY  → 디스크를 임시 공간으로만 쓰고 끝나면 삭제(메모리 절약)
  AUDIO_STORE=file,   AUDIO_RETENTION=KEEP         → WAV로 남기고 ttl_hours 뒤에 삭제
"""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from app.audio.wav import pcm16_to_wav
from app.settings import AudioSettings

log = logging.getLogger(__name__)
EncryptHook = Callable[[bytes], bytes]


@dataclass
class RoomAudio:
    pcm: bytes
    offset_ms: int  # 앞에서 버린 구간 길이. 타임스탬프에 더한다
    dropped_ms: int


class AudioStore(ABC):
    """방 음성 저장 인터페이스. 모든 구현은 PCM16 모노를 받는다."""

    retention: str = "MEMORY_ONLY"

    @abstractmethod
    async def append(self, room_id: int, pcm: bytes) -> int:
        """음성을 덧붙인다. 상한을 넘어 버린 구간의 길이(ms)를 돌려준다(없으면 0)."""

    @abstractmethod
    async def read_all(self, room_id: int) -> RoomAudio:
        ...

    @abstractmethod
    async def duration_ms(self, room_id: int) -> int:
        ...

    @abstractmethod
    async def release(self, room_id: int) -> None:
        """사후 STT가 끝난 뒤 부른다. MEMORY_ONLY면 지우고, KEEP이면 보관 형태로 바꾼다."""

    async def purge_expired(self) -> int:
        """보관 기간이 지난 것을 지운다. 지운 개수."""
        return 0


class MemoryAudioStore(AudioStore):
    retention = "MEMORY_ONLY"

    def __init__(self, *, sample_rate: int = 16000, max_minutes_per_room: int = 90) -> None:
        self.sample_rate = sample_rate
        self.max_bytes = max_minutes_per_room * 60 * sample_rate * 2
        self._parts: dict[int, deque[bytes]] = {}
        self._size: dict[int, int] = {}
        self._dropped_bytes: dict[int, int] = {}

    def _ms(self, nbytes: int) -> int:
        return nbytes * 1000 // (self.sample_rate * 2)

    async def append(self, room_id: int, pcm: bytes) -> int:
        parts = self._parts.setdefault(room_id, deque())
        parts.append(pcm)
        size = self._size.get(room_id, 0) + len(pcm)
        dropped = 0
        while size > self.max_bytes and len(parts) > 1:  # 가장 오래된 구간부터 버린다
            old = parts.popleft()
            size -= len(old)
            dropped += len(old)
        self._size[room_id] = size
        if dropped:
            self._dropped_bytes[room_id] = self._dropped_bytes.get(room_id, 0) + dropped
        return self._ms(dropped)

    async def read_all(self, room_id: int) -> RoomAudio:
        dropped = self._ms(self._dropped_bytes.get(room_id, 0))
        return RoomAudio(b"".join(self._parts.get(room_id, ())), offset_ms=dropped, dropped_ms=dropped)

    async def duration_ms(self, room_id: int) -> int:
        return self._ms(self._size.get(room_id, 0))

    async def release(self, room_id: int) -> None:
        self._parts.pop(room_id, None)
        self._size.pop(room_id, None)
        self._dropped_bytes.pop(room_id, None)

    def total_bytes(self) -> int:
        return sum(self._size.values())


class FileAudioStore(AudioStore):
    """디스크에 모은다. 면접 중에는 `<room>.pcm`에 덧붙이고, release 때 KEEP이면 `<room>-<시각>.wav`로 바꾼다.

    `encrypt`가 있으면 WAV 바이트를 그 함수에 통과시켜 `.wav.enc`로 쓴다(사내 KMS·암호화 모듈을 여기에 꽂는다).
    면접 중의 임시 `.pcm`은 암호화하지 않는다: 암호화가 필수면 디스크 암호화 볼륨을 `file_dir`로 지정한다.
    """

    def __init__(self, directory: str | Path, *, retention: str = "KEEP", sample_rate: int = 16000,
                 ttl_hours: int = 72, max_minutes_per_room: int = 0, encrypt: EncryptHook | None = None) -> None:
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.retention = retention
        self.sample_rate = sample_rate
        self.ttl_hours = ttl_hours
        self.max_bytes = max_minutes_per_room * 60 * sample_rate * 2  # 0이면 제한 없음
        self.encrypt = encrypt

    def _pcm_path(self, room_id: int) -> Path:
        return self.dir / f"room-{int(room_id)}.pcm"

    def _append_sync(self, room_id: int, pcm: bytes) -> None:
        with self._pcm_path(room_id).open("ab") as f:
            f.write(pcm)

    async def append(self, room_id: int, pcm: bytes) -> int:
        path = self._pcm_path(room_id)
        if self.max_bytes and path.exists() and path.stat().st_size + len(pcm) > self.max_bytes:
            return len(pcm) * 1000 // (self.sample_rate * 2)  # 디스크 상한: 새 구간을 받지 않는다
        await asyncio.to_thread(self._append_sync, room_id, pcm)
        return 0

    async def read_all(self, room_id: int) -> RoomAudio:
        path = self._pcm_path(room_id)
        data = await asyncio.to_thread(path.read_bytes) if path.exists() else b""
        return RoomAudio(data, offset_ms=0, dropped_ms=0)

    async def duration_ms(self, room_id: int) -> int:
        path = self._pcm_path(room_id)
        return path.stat().st_size * 1000 // (self.sample_rate * 2) if path.exists() else 0

    def _release_sync(self, room_id: int) -> None:
        path = self._pcm_path(room_id)
        if not path.exists():
            return
        if self.retention == "KEEP":
            wav = pcm16_to_wav(path.read_bytes(), self.sample_rate)
            stamp = time.strftime("%Y%m%dT%H%M%S")
            if self.encrypt is not None:
                (self.dir / f"room-{int(room_id)}-{stamp}.wav.enc").write_bytes(self.encrypt(wav))
            else:
                (self.dir / f"room-{int(room_id)}-{stamp}.wav").write_bytes(wav)
        path.unlink()

    async def release(self, room_id: int) -> None:
        await asyncio.to_thread(self._release_sync, room_id)
        await self.purge_expired()

    def _purge_sync(self) -> int:
        limit = time.time() - self.ttl_hours * 3600
        removed = 0
        for path in self.dir.glob("room-*"):
            if path.is_file() and path.stat().st_mtime < limit:
                path.unlink()
                removed += 1
        return removed

    async def purge_expired(self) -> int:
        removed = await asyncio.to_thread(self._purge_sync)
        if removed:
            log.info("보관 기간이 지난 음성 파일 %d개 삭제", removed)
        return removed


def build_audio_store(settings: AudioSettings, encrypt: EncryptHook | None = None) -> AudioStore:
    if settings.store == "file":
        return FileAudioStore(settings.file_dir, retention=settings.retention, sample_rate=settings.sample_rate,
                              ttl_hours=settings.ttl_hours, max_minutes_per_room=settings.max_minutes_per_room,
                              encrypt=encrypt)
    return MemoryAudioStore(sample_rate=settings.sample_rate, max_minutes_per_room=settings.max_minutes_per_room)
