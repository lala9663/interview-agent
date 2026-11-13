"""합성 PCM: 말소리 흉내 구간과 침묵. 데모 클라이언트·부하 테스트·테스트가 함께 쓴다.

진짜 말소리가 아니라 VAD가 말소리로 잡을 만한 크기의 소리다(fake STT는 내용을 읽지 않는다).
"""

from __future__ import annotations

import numpy as np

SAMPLE_RATE = 16000
FRAME_MS = 100
FRAME_BYTES = SAMPLE_RATE * FRAME_MS // 1000 * 2


def speech(seconds: float, *, seed: int = 0, level_db: float = -18.0) -> bytes:
    """음절 박자(초당 4번)로 크기가 출렁이는 배음 소리."""
    n = int(SAMPLE_RATE * seconds)
    t = np.arange(n) / SAMPLE_RATE
    rng = np.random.default_rng(seed)
    f0 = 110 + 40 * rng.random()
    wave = sum(np.sin(2 * np.pi * f0 * k * t) / k for k in range(1, 6))
    envelope = 0.55 + 0.45 * np.sin(2 * np.pi * 4 * t)
    signal = wave * envelope + 0.05 * rng.standard_normal(n)
    signal = signal / (np.sqrt(np.mean(signal ** 2)) + 1e-9) * (32768 * 10 ** (level_db / 20))
    return np.clip(signal, -32767, 32767).astype("<i2").tobytes()


def silence(seconds: float, *, seed: int = 0, level_db: float = -65.0) -> bytes:
    """아주 작은 바닥 잡음."""
    n = int(SAMPLE_RATE * seconds)
    noise = np.random.default_rng(seed).standard_normal(n) * (32768 * 10 ** (level_db / 20))
    return noise.astype("<i2").tobytes()


def frames(pcm: bytes, frame_bytes: int = FRAME_BYTES) -> list[bytes]:
    return [pcm[i:i + frame_bytes] for i in range(0, len(pcm), frame_bytes)]


def conversation(turns: list[tuple[float, float]], *, lead_silence: float = 0.5, seed: int = 0) -> bytes:
    """[(말하는 초, 그 뒤 쉬는 초), ...] 로 이어진 PCM."""
    parts = [silence(lead_silence, seed=seed)]
    for i, (talk, rest) in enumerate(turns):
        parts.append(speech(talk, seed=seed + i + 1))
        parts.append(silence(rest, seed=seed + 100 + i))
    return b"".join(parts)
