"""HTTP로 부르는 Whisper STT.

기본은 OpenAI 형식 `POST {base_url}/v1/audio/transcriptions` (multipart: file, model, language, prompt).
**사내 DS API Hub의 실제 규격은 아직 확인하지 못했다.** 경로·필드 이름·인증 헤더·응답에서 글자를 꺼내는 위치를
options로 바꿀 수 있게 했고, 그것으로 안 되면 이 파일의 `_build_request` / `_extract_text` 만 고치면 된다.
(확인할 항목: docs/providers.md)

options:
  base_url (필수), path(기본 /v1/audio/transcriptions)
  api_key_env(기본 STT_API_KEY), auth_header(기본 Authorization), auth_prefix(기본 "Bearer ")
  headers: 추가 헤더
  file_field(file), model_field(model), language_field(language), prompt_field(prompt)
  extra_fields: 그대로 보낼 폼 필드(dict). 예) {response_format: json, temperature: 0}
  response_text_path: 응답 JSON에서 글자가 있는 위치(점으로 구분, 기본 text). 예) result.text
  drop_phrases: 무음에서 Whisper가 지어내는 문장 목록. 결과가 이것과 같으면 빈 글자로 본다
  verify_ssl: 사설 인증서면 false 또는 CA 파일 경로
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from app.audio.wav import pcm16_to_wav
from app.providers.base import RoleConfig, STTProvider, SttRequest, SttResult

DEFAULT_DROP_PHRASES = ["시청해주셔서 감사합니다", "시청해 주셔서 감사합니다", "구독과 좋아요 부탁드립니다"]


class WhisperHttpSTTProvider(STTProvider):
    def __init__(self, config: RoleConfig) -> None:
        super().__init__(config)
        if not config.options.get("base_url"):
            raise ValueError(f"{config.role}: options.base_url 이 필요하다.")
        self._client: httpx.AsyncClient | None = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.config.timeout_s,
                                             verify=self.config.options.get("verify_ssl", True))
        return self._client

    def _build_request(self, request: SttRequest) -> dict[str, Any]:
        """httpx.post에 넘길 인자를 만든다. 규격이 다르면 여기를 고친다."""
        o = self.config.options
        headers = dict(o.get("headers") or {})
        key = os.environ.get(o.get("api_key_env", "STT_API_KEY"), "")
        if key:
            headers[o.get("auth_header", "Authorization")] = f"{o.get('auth_prefix', 'Bearer ')}{key}"
        data: dict[str, str] = {str(k): str(v) for k, v in (o.get("extra_fields") or {}).items()}
        if o.get("model_field", "model"):
            data[o.get("model_field", "model")] = self.config.model
        if o.get("language_field", "language") and request.language:
            data[o.get("language_field", "language")] = request.language
        if request.prompt and o.get("prompt_field", "prompt"):
            data[o.get("prompt_field", "prompt")] = request.prompt
        wav = pcm16_to_wav(request.pcm, request.sample_rate)
        return {
            "url": str(o["base_url"]).rstrip("/") + str(o.get("path", "/v1/audio/transcriptions")),
            "headers": headers,
            "data": data,
            "files": {o.get("file_field", "file"): ("chunk.wav", wav, "audio/wav")},
        }

    def _extract_text(self, body: Any) -> str:
        """응답에서 글자를 꺼낸다. 규격이 다르면 여기를 고친다."""
        if isinstance(body, str):
            return body.strip()
        node: Any = body
        for key in str(self.config.options.get("response_text_path", "text")).split("."):
            if isinstance(node, list):
                node = node[int(key)]
            else:
                node = node[key]
        return str(node).strip()

    async def _transcribe(self, request: SttRequest) -> SttResult:
        response = await self._http().post(**self._build_request(request))
        response.raise_for_status()
        ctype = response.headers.get("content-type", "")
        body: Any = response.json() if "json" in ctype else response.text
        text = self._extract_text(body)
        drops = self.config.options.get("drop_phrases", DEFAULT_DROP_PHRASES)
        if text in drops:
            text = ""
        return SttResult(text=text, raw=body)
