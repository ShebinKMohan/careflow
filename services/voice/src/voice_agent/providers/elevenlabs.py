from __future__ import annotations

from collections.abc import AsyncIterator
from urllib.parse import quote

import httpx

from voice_agent.providers._retry import stream_with_retry
from voice_agent.telephony.plivo import PlivoAudioFormat


class ElevenLabsTextToSpeechError(RuntimeError):
    """Raised when ElevenLabs returns an unusable text-to-speech response."""


DEFAULT_MAX_AUDIO_BYTES = 512 * 1024


class ElevenLabsTextToSpeech:
    def __init__(
        self,
        *,
        api_key: str,
        voice_id: str,
        model_id: str,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 30.0,
        max_audio_bytes: int = DEFAULT_MAX_AUDIO_BYTES,
        retry_max_attempts: int = 1,
        retry_backoff_seconds: float = 0.25,
        retry_max_backoff_seconds: float = 2.0,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive.")
        if max_audio_bytes <= 0:
            raise ValueError("max_audio_bytes must be positive.")
        if retry_max_attempts < 1:
            raise ValueError("retry_max_attempts must be at least 1.")
        self._api_key = _required_config_string(api_key, "api_key")
        self._voice_id = _required_config_string(voice_id, "voice_id")
        self._model_id = _required_config_string(model_id, "model_id")
        self._timeout_seconds = timeout_seconds
        self._max_audio_bytes = max_audio_bytes
        self._retry_max_attempts = retry_max_attempts
        self._retry_backoff_seconds = retry_backoff_seconds
        self._retry_max_backoff_seconds = retry_max_backoff_seconds
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)
        self._owns_client = client is None

    async def synthesize(
        self,
        *,
        text: str,
        audio_format: PlivoAudioFormat,
    ) -> AsyncIterator[bytes]:
        if audio_format is not PlivoAudioFormat.L16_16KHZ:
            raise ValueError("ElevenLabs adapter is configured for PCM16 16kHz Plivo streams.")
        clean_text = text.strip()
        if not clean_text:
            raise ElevenLabsTextToSpeechError("ElevenLabs text must not be blank.")

        escaped_voice_id = quote(self._voice_id, safe="")
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{escaped_voice_id}/stream"
        async with stream_with_retry(
            self._client,
            "POST",
            url,
            params={"output_format": "pcm_16000"},
            headers={
                "accept": "audio/pcm",
                "content-type": "application/json",
                "xi-api-key": self._api_key,
            },
            json={"text": clean_text, "model_id": self._model_id},
            max_attempts=self._retry_max_attempts,
            backoff_seconds=self._retry_backoff_seconds,
            max_backoff_seconds=self._retry_max_backoff_seconds,
        ) as response:
            response.raise_for_status()
            _reject_non_audio_content_type(response)
            _reject_oversized_content_length(
                response,
                max_audio_bytes=self._max_audio_bytes,
            )
            has_audio = False
            pending = b""
            total_audio_bytes = 0
            async for chunk in response.aiter_bytes():
                if not chunk:
                    continue
                total_audio_bytes += len(chunk)
                if total_audio_bytes > self._max_audio_bytes:
                    raise _audio_too_large()
                audio = pending + chunk
                complete_sample_length = len(audio) - (len(audio) % 2)
                if complete_sample_length:
                    has_audio = True
                    yield audio[:complete_sample_length]
                pending = audio[complete_sample_length:]
            if pending:
                raise ElevenLabsTextToSpeechError(
                    "ElevenLabs returned an incomplete PCM16 sample."
                )
            if not has_audio:
                raise ElevenLabsTextToSpeechError("ElevenLabs returned no audio chunks.")

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _required_config_string(value: str, name: str) -> str:
    clean_value = value.strip()
    if not clean_value:
        raise ValueError(f"{name} must not be blank.")
    return clean_value


def _reject_oversized_content_length(
    response: httpx.Response,
    *,
    max_audio_bytes: int,
) -> None:
    content_length = response.headers.get("content-length")
    if content_length is None:
        return
    try:
        audio_size = int(content_length)
    except ValueError as exc:
        raise ElevenLabsTextToSpeechError(
            "ElevenLabs response Content-Length was not an integer."
        ) from exc
    if audio_size <= 0:
        raise ElevenLabsTextToSpeechError(
            "ElevenLabs response Content-Length must be positive."
        )
    if audio_size > max_audio_bytes:
        raise _audio_too_large()


def _reject_non_audio_content_type(response: httpx.Response) -> None:
    content_type = response.headers.get("content-type")
    if content_type is None:
        return
    media_type = content_type.split(";", maxsplit=1)[0].strip().lower()
    if media_type.startswith("audio/") or media_type == "application/octet-stream":
        return
    raise ElevenLabsTextToSpeechError(
        f"ElevenLabs returned a non-audio response content type: {media_type}."
    )


def _audio_too_large() -> ElevenLabsTextToSpeechError:
    return ElevenLabsTextToSpeechError(
        "ElevenLabs audio response exceeded configured audio size limit."
    )
