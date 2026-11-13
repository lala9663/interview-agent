"""말소리 검출(VAD). 인터페이스 + 에너지 기반 구현(numpy만).

다른 VAD(예: 사내에서 쓸 수 있는 모델 기반)를 쓰려면 `Vad`를 구현하고 `VADS`에 이름을 더한 뒤 `AUDIO_VAD`로 고른다.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np

from app.settings import AudioSettings


class Vad(ABC):
    @abstractmethod
    def is_speech(self, frame: np.ndarray) -> bool:
        """frame: int16 모노 한 프레임(`vad_frame_ms` 길이)."""

    def reset(self) -> None:  # noqa: B027
        """방이 바뀌거나 다시 시작할 때 내부 상태를 지운다."""


def frame_db(frame: np.ndarray) -> float:
    """프레임의 RMS를 dBFS로. 완전한 무음은 -100."""
    if frame.size == 0:
        return -100.0
    rms = float(np.sqrt(np.mean(np.square(frame.astype(np.float32)))))
    return 20.0 * float(np.log10(rms / 32768.0)) if rms > 0 else -100.0


class EnergyVad(Vad):
    """에너지 임계값 VAD.

    말소리 = dBFS가 `threshold_db`보다 크고, (adaptive면) 추정한 바닥 잡음 + `noise_margin_db`보다도 큼.
    바닥 잡음은 말소리가 아닌 프레임의 지수 이동 평균으로 추정한다(에어컨 같은 일정한 소음에 대응).
    """

    def __init__(self, threshold_db: float = -40.0, noise_margin_db: float = 8.0, adaptive: bool = True) -> None:
        self.threshold_db = threshold_db
        self.noise_margin_db = noise_margin_db
        self.adaptive = adaptive
        self._noise_db: float | None = None

    def reset(self) -> None:
        self._noise_db = None

    @property
    def noise_floor_db(self) -> float | None:
        return self._noise_db

    def is_speech(self, frame: np.ndarray) -> bool:
        db = frame_db(frame)
        limit = self.threshold_db
        if self.adaptive and self._noise_db is not None:
            limit = max(limit, self._noise_db + self.noise_margin_db)
        speech = db > limit
        if self.adaptive and not speech:
            self._noise_db = db if self._noise_db is None else 0.95 * self._noise_db + 0.05 * db
        return speech


VADS = {"energy": EnergyVad}


def build_vad(settings: AudioSettings) -> Vad:
    if settings.vad not in VADS:
        raise ValueError(f"알 수 없는 VAD '{settings.vad}'. 등록된 것: {sorted(VADS)}")
    if settings.vad == "energy":
        return EnergyVad(settings.vad_threshold_db, settings.vad_noise_margin_db, settings.vad_adaptive)
    return VADS[settings.vad]()
