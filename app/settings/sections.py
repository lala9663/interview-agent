"""설정 섹션. 섹션마다 환경변수 접두사가 다르다(README의 환경변수 표와 같아야 한다)."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


def _cfg(prefix: str) -> SettingsConfigDict:
    return SettingsConfigDict(env_prefix=prefix, env_file=".env", env_file_encoding="utf-8", extra="ignore")


class ServerSettings(BaseSettings):
    model_config = _cfg("APP_")
    env: str = "local"
    cors_origins: str = "http://localhost:5173"
    log_level: str = "INFO"
    instance_id: str = ""  # 비우면 시작할 때 무작위로 정한다

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


class SecuritySettings(BaseSettings):
    model_config = _cfg("")
    room_ticket_secret: str = "dev-room-ticket-secret-change-me-32bytes!!"
    internal_api_key: str = "dev-internal-key"
    ws_auth_timeout_s: float = 5.0


class BackendSettings(BaseSettings):
    model_config = _cfg("BACKEND_")
    mode: Literal["http", "fake"] = "fake"
    base_url: str = "http://localhost:8080"
    timeout_s: float = 5.0
    retry_max_attempts: int = 6
    retry_base_delay_s: float = 1.0
    retry_queue_size: int = 2000


class ProviderSettings(BaseSettings):
    model_config = _cfg("")
    providers_file: str = str(REPO_ROOT / "config" / "providers.yaml")


class AudioSettings(BaseSettings):
    model_config = _cfg("AUDIO_")
    sample_rate: int = 16000
    # --- VAD / 청크 ---
    vad: str = "energy"
    vad_frame_ms: int = 20
    vad_threshold_db: float = -40.0  # dBFS. 이보다 크면 말소리
    vad_noise_margin_db: float = 8.0  # 추정한 바닥 잡음보다 이만큼 커야 말소리
    vad_adaptive: bool = True
    min_speech_ms: int = 120  # 이보다 짧은 소리는 버린다
    min_silence_ms: int = 700  # 이만큼 조용하면 발화가 끝난 것으로 본다
    max_chunk_ms: int = 15000  # 넘으면 강제로 자르고 다음 청크와 이어 붙인다
    pad_ms: int = 200  # 앞뒤 여유
    post_max_chunk_ms: int = 28000  # 사후 STT 청크 상한(Whisper 창 30초)
    # --- 보관 ---
    retention: Literal["MEMORY_ONLY", "KEEP"] = "MEMORY_ONLY"
    store: Literal["memory", "file"] = "memory"
    max_minutes_per_room: int = 90
    file_dir: str = str(REPO_ROOT / "data" / "audio")
    ttl_hours: int = 72


class LiveSettings(BaseSettings):
    model_config = _cfg("LIVE_")
    max_rooms: int = 40  # 이 인스턴스가 받는 마이크 방 수 상한(넘으면 4429)
    stt_queue_soft: int = 3  # 넘으면 partial과 다듬기를 건너뛴다
    stt_queue_hard: int = 8  # 넘으면 가장 오래된 청크를 버리고 OVERLOADED
    max_chunk_wait_s: float = 20.0  # 방의 큐와 STT 자리를 합쳐 이보다 오래 기다린 청크는 실시간 전사를 건너뛴다(사후 STT가 채운다)
    refine_timeout_s: float = 4.0  # 넘으면 raw 그대로 final
    speaker_llm_timeout_s: float = 3.0
    followup_debounce_s: float = 1.5  # 지원자 말이 끝나고 이만큼 조용하면 꼬리질문
    followup_min_chars: int = 20
    followup_timeout_s: float = 20.0
    followup_context_utterances: int = 8
    finalize_flush_timeout_s: float = 20.0
    idle_close_minutes: int = 30  # 마이크가 끊긴 뒤 이만큼 지나면 방을 정리
    status_interval_s: float = 5.0
    post_stt_refine: bool = True
    post_stt_max_attempts: int = 2
    post_stt_hold_minutes: int = 30  # 사후 STT가 실패하면 음성을 이만큼 붙잡고 다시 시도한다. 0이면 바로 버린다
    post_stt_retry_interval_s: float = 60.0  # 붙잡고 있는 동안 다시 시도하는 간격
    post_stt_concurrent_rooms: int = 2  # 사후 STT를 동시에 돌리는 방 수(방 음성 복사본이 그만큼만 생긴다)
    audio_lock_ttl_s: int = 30


class SpeakerSettings(BaseSettings):
    model_config = _cfg("SPEAKER_")
    chain: str = "manual,channel,question_match,rule,llm"
    question_match_threshold: float = 0.55
    rule_min_confidence: float = 0.6
    llm_min_confidence: float = 0.6
    rule_wire_method: str = "RULE"  # 규칙 판정이 밖으로 나가는 값(API 명세의 SpeakerMethod.RULE)


class MatchingSettings(BaseSettings):
    model_config = _cfg("MATCHING_")
    threshold: float = 0.55  # question.matched를 내는 기준
    ngram_min: int = 2
    ngram_max: int = 3
    dedupe_threshold: float = 0.7  # 꼬리질문 중복 판정


class BlindSettings(BaseSettings):
    model_config = _cfg("BLIND_")
    rules_file: str = str(REPO_ROOT / "config" / "blind_rules.yaml")
    mask_text: str = "[블라인드 처리됨]"


class StateSettings(BaseSettings):
    model_config = _cfg("STATE_")
    store: Literal["memory", "redis"] = "memory"
    event_bus: Literal["memory", "redis"] = "memory"
    redis_url: str = "redis://localhost:6379/0"
    redis_sentinels: str = ""  # "host1:26379,host2:26379" 이면 Sentinel로 붙는다
    redis_sentinel_master: str = "mymaster"
    redis_password: str = ""
    redis_db: int = 0
    key_prefix: str = "ia"
    room_ttl_hours: int = 12


class Settings:
    """섹션을 묶은 전역 설정. 기능을 더할 때는 해당 섹션만 고친다."""

    def __init__(self, **overrides: BaseSettings) -> None:
        self.server: ServerSettings = overrides.get("server") or ServerSettings()
        self.security: SecuritySettings = overrides.get("security") or SecuritySettings()
        self.backend: BackendSettings = overrides.get("backend") or BackendSettings()
        self.providers: ProviderSettings = overrides.get("providers") or ProviderSettings()
        self.audio: AudioSettings = overrides.get("audio") or AudioSettings()
        self.live: LiveSettings = overrides.get("live") or LiveSettings()
        self.speaker: SpeakerSettings = overrides.get("speaker") or SpeakerSettings()
        self.matching: MatchingSettings = overrides.get("matching") or MatchingSettings()
        self.blind: BlindSettings = overrides.get("blind") or BlindSettings()
        self.state: StateSettings = overrides.get("state") or StateSettings()
        if self.audio.retention == "KEEP" and self.audio.store != "file":
            raise ValueError("AUDIO_RETENTION=KEEP 이면 AUDIO_STORE=file 이어야 한다(메모리는 보관할 수 없다).")

