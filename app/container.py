"""앱이 쓰는 객체를 한곳에서 조립한다. 테스트는 `Settings`를 바꿔 넣어 새 컨테이너를 만든다."""

from __future__ import annotations

import uuid
from typing import Any

from app.audio.store import AudioStore, EncryptHook, build_audio_store
from app.backend.client import BackendClient, build_backend
from app.backend.outbox import BackendOutbox
from app.blind.filter import BlindFilter
from app.blind.rules import BlindRules
from app.graphs.runner import GraphRunner
from app.live.deps import LiveDeps
from app.live.hub import LocalHub
from app.live.manager import RoomManager
from app.providers.registry import ProviderSet, build_providers
from app.settings import Settings
from app.state.event_bus import EventBus
from app.state.factory import build_state
from app.state.room_store import RoomStateStore


class Container:
    def __init__(self, settings: Settings | None = None, *, providers: ProviderSet | None = None,
                 backend: BackendClient | None = None, audio_store: AudioStore | None = None,
                 audio_encrypt: EncryptHook | None = None,
                 shared_state: tuple[RoomStateStore, EventBus] | None = None) -> None:
        self.settings = settings or Settings()
        s = self.settings
        self.instance_id = s.server.instance_id or uuid.uuid4().hex[:8]
        self.providers = providers or build_providers(s.providers.providers_file)
        self.blind = BlindFilter(BlindRules.load(s.blind.rules_file), s.blind.mask_text)
        self.graphs = GraphRunner(self.providers, self.blind, dedupe_threshold=s.matching.dedupe_threshold)
        self.backend = backend or build_backend(s.backend, s.security.internal_api_key)
        self.outbox = BackendOutbox(self.backend, s.backend)
        if shared_state is not None:  # 테스트: 여러 인스턴스가 같은 상태·버스를 쓰는 상황을 한 프로세스에서 흉내 낸다
            self.state, self.bus, self._redis = shared_state[0], shared_state[1], None
        else:
            self.state, self.bus, self._redis = build_state(s.state)
        # 저장 방식으로 바꿀 때 암호화가 필요하면 audio_encrypt에 bytes -> bytes 함수를 넘긴다
        self.audio_store = audio_store or build_audio_store(s.audio, encrypt=audio_encrypt)
        self.hub = LocalHub(self.bus, s.state.key_prefix)
        self.rooms = RoomManager(LiveDeps(
            settings=s, providers=self.providers, graphs=self.graphs, blind=self.blind, backend=self.backend,
            outbox=self.outbox, state=self.state, bus=self.bus, audio_store=self.audio_store,
            instance_id=self.instance_id))

    async def startup(self) -> None:
        self.outbox.start()
        await self.audio_store.purge_expired()

    async def shutdown(self) -> None:
        await self.rooms.shutdown()
        await self.outbox.stop()
        await self.backend.close()
        await self.bus.close()
        await self.state.close()
        redis: Any = self._redis
        if redis is not None:
            await redis.aclose()
