"""VAD 청크 분할과 AudioStore 두 구현."""

from __future__ import annotations

import numpy as np
import pytest

from app.audio.chunker import SpeechChunker
from app.audio.store import FileAudioStore, MemoryAudioStore, build_audio_store
from app.audio.vad import EnergyVad, frame_db
from app.audio.wav import wav_to_pcm16
from app.demo import synth
from app.settings import AudioSettings, Settings


def _chunker(**kw) -> SpeechChunker:
    args = dict(min_speech_ms=120, min_silence_ms=700, max_chunk_ms=15000, pad_ms=200)
    args.update(kw)
    return SpeechChunker(EnergyVad(), **args)


def _feed(chunker: SpeechChunker, pcm: bytes, frame_bytes: int = 3200):
    out = []
    for frame in synth.frames(pcm, frame_bytes):
        out += chunker.feed(frame)
    return out


def test_energy_vad_separates_speech_from_noise_floor():
    vad = EnergyVad()
    quiet = np.frombuffer(synth.silence(0.02), dtype="<i2")
    loud = np.frombuffer(synth.speech(0.5)[:640], dtype="<i2")
    assert frame_db(quiet) < -55 and not vad.is_speech(quiet)
    assert vad.is_speech(loud)
    assert frame_db(np.zeros(320, dtype="<i2")) == -100.0


def test_two_utterances_become_two_chunks_with_padding():
    pcm = synth.conversation([(2.0, 1.5), (3.0, 1.5)], lead_silence=1.0)
    chunks = _feed(_chunker(), pcm)
    assert len(chunks) == 2 and not any(c.forced or c.continuation for c in chunks)
    first, second = chunks
    assert 700 <= first.start_ms <= 1000  # 말 시작 1000ms에서 앞 여유(200ms + 검출 지연)만큼 앞
    assert 3000 <= first.end_ms <= 3300  # 말 끝 3000ms + 뒤 여유
    assert first.duration_ms == len(first.pcm) // 32
    assert 2900 <= second.duration_ms <= 3500


def test_frame_size_does_not_matter():
    pcm = synth.conversation([(1.0, 1.0), (1.0, 1.0)])
    a = _feed(_chunker(), pcm, 3200)
    b = _feed(_chunker(), pcm, 1001)  # 홀수 바이트 프레임도 이어 붙여 처리한다
    assert [(c.start_ms, c.end_ms) for c in a] == [(c.start_ms, c.end_ms) for c in b]


def test_short_noise_is_ignored():
    pcm = synth.silence(1.0) + synth.speech(0.06) + synth.silence(1.5)
    assert _feed(_chunker(), pcm) == []


def test_long_speech_is_force_cut_and_marked_as_continuation():
    pcm = synth.silence(0.5) + synth.speech(10.0) + synth.silence(1.5)
    chunks = _feed(_chunker(max_chunk_ms=4000), pcm)
    assert len(chunks) >= 3
    assert chunks[0].forced and not chunks[0].continuation
    assert all(c.continuation for c in chunks[1:]) and not chunks[-1].forced
    assert all(c.duration_ms <= 4000 for c in chunks)
    assert sum(c.duration_ms for c in chunks) == pytest.approx(10400, abs=300)  # 빠진 구간이 없다


def test_flush_emits_unfinished_speech():
    chunker = _chunker()
    assert _feed(chunker, synth.silence(0.5) + synth.speech(2.0)) == []
    assert chunker.in_speech
    tail = chunker.flush()
    assert len(tail) == 1 and tail[0].duration_ms >= 2000 and not chunker.in_speech


async def test_memory_store_keeps_then_releases():
    store = MemoryAudioStore(max_minutes_per_room=1)
    assert await store.append(1, b"\x01\x00" * 16000) == 0
    await store.append(2, b"\x02\x00" * 8000)
    assert await store.duration_ms(1) == 1000 and store.total_bytes() == 48000
    audio = await store.read_all(1)
    assert len(audio.pcm) == 32000 and audio.offset_ms == 0
    await store.release(1)
    assert await store.duration_ms(1) == 0 and (await store.read_all(1)).pcm == b""
    assert store.retention == "MEMORY_ONLY"


async def test_memory_store_drops_oldest_over_cap():
    store = MemoryAudioStore(max_minutes_per_room=1)  # 60초 = 1,920,000바이트
    dropped = 0
    for _ in range(70):
        dropped += await store.append(1, b"\0" * 32000)  # 1초씩
    assert dropped == 10_000 and await store.duration_ms(1) == 60_000
    audio = await store.read_all(1)
    assert audio.offset_ms == 10_000 and audio.dropped_ms == 10_000


async def test_file_store_keep_writes_wav_with_encrypt_hook_and_ttl(tmp_path):
    store = FileAudioStore(tmp_path, retention="KEEP", ttl_hours=1, encrypt=lambda b: b[::-1])
    await store.append(7, b"\x01\x00" * 16000)
    await store.append(7, b"\x02\x00" * 16000)
    assert await store.duration_ms(7) == 2000 and len((await store.read_all(7)).pcm) == 64000
    await store.release(7)
    files = list(tmp_path.iterdir())
    assert len(files) == 1 and files[0].name.endswith(".wav.enc")  # 임시 .pcm은 지워지고 암호화된 WAV만 남는다
    pcm, rate = wav_to_pcm16(files[0].read_bytes()[::-1])
    assert rate == 16000 and len(pcm) == 64000
    import os
    import time

    old = time.time() - 2 * 3600
    os.utime(files[0], (old, old))
    assert await store.purge_expired() == 1 and list(tmp_path.iterdir()) == []


async def test_file_store_memory_only_leaves_nothing(tmp_path):
    store = FileAudioStore(tmp_path, retention="MEMORY_ONLY")
    await store.append(3, b"\0" * 3200)
    await store.release(3)
    assert list(tmp_path.iterdir()) == []


def test_store_selection_and_invalid_combination(tmp_path):
    assert isinstance(build_audio_store(AudioSettings()), MemoryAudioStore)
    keep = AudioSettings(store="file", retention="KEEP", file_dir=str(tmp_path))
    assert isinstance(build_audio_store(keep), FileAudioStore) and build_audio_store(keep).retention == "KEEP"
    with pytest.raises(ValueError, match="AUDIO_STORE=file"):
        Settings(audio=AudioSettings(store="memory", retention="KEEP"))
