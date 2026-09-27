from __future__ import annotations

import io
import math
import struct
import wave
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx

from voice_agent.providers._retry import stream_with_retry
from voice_agent.telephony.plivo import PlivoAudioFormat


class LuxTTSTextToSpeechError(RuntimeError):
    """Raised when the LuxTTS sidecar returns unusable audio."""


DEFAULT_MAX_AUDIO_BYTES = 2 * 1024 * 1024
DEFAULT_NUM_STEPS = 4
DEFAULT_T_SHIFT = 0.9
DEFAULT_SPEED = 0.8
MIN_AUDIO_PEAK_RATIO = 0.05
MIN_AUDIO_RMS_DBFS = -45.0
MAX_AUDIO_RMS_DBFS = -3.0
MAX_AUDIO_CLIPPING_RATIO = 0.01
MIN_AUDIO_NON_SILENT_RATIO = 0.10
_AUDIO_SECONDS_HEADER_TOLERANCE = 0.05


class LuxTTSHttpTextToSpeech:
    """HTTP adapter for a LuxTTS GPU/CPU sidecar.

    LuxTTS is intentionally kept out of the Plivo media process: it has heavy model
    dependencies, benefits from a GPU-specific runtime, and can run on Python versions
    that differ from the telephony backend. The sidecar contract is small and strict:
    POST text + voice parameters, return mono PCM16 at the requested Plivo sample rate.
    """

    def __init__(
        self,
        *,
        url: str,
        api_key: str | None = None,
        voice: str | None = None,
        model: str | None = None,
        num_steps: int = DEFAULT_NUM_STEPS,
        t_shift: float = DEFAULT_T_SHIFT,
        speed: float = DEFAULT_SPEED,
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
        if num_steps <= 0:
            raise ValueError("num_steps must be positive.")
        if t_shift <= 0:
            raise ValueError("t_shift must be positive.")
        if speed <= 0:
            raise ValueError("speed must be positive.")
        self._url = _required_config_string(url, "url")
        self._api_key = _required_config_string(api_key, "api_key") if api_key is not None else None
        self._voice = _optional_config_string(voice)
        self._model = _optional_config_string(model)
        self._num_steps = num_steps
        self._t_shift = t_shift
        self._speed = speed
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
            raise ValueError("LuxTTS adapter is configured for PCM16 16kHz Plivo streams.")
        clean_text = text.strip()
        if not clean_text:
            raise LuxTTSTextToSpeechError("LuxTTS text must not be blank.")

        headers = {
            "accept": "audio/pcm, application/octet-stream, audio/wav",
            "content-type": "application/json",
        }
        if self._api_key:
            headers["authorization"] = f"Bearer {self._api_key}"

        payload: dict[str, object] = {
            "text": clean_text,
            "output_format": "pcm_s16le",
            "sample_rate": audio_format.sample_rate,
            "num_steps": self._num_steps,
            "t_shift": self._t_shift,
            "speed": self._speed,
        }
        if self._voice is not None:
            payload["voice"] = self._voice
        if self._model is not None:
            payload["model"] = self._model

        async with stream_with_retry(
            self._client,
            "POST",
            self._url,
            headers=headers,
            json=payload,
            max_attempts=self._retry_max_attempts,
            backoff_seconds=self._retry_backoff_seconds,
            max_backoff_seconds=self._retry_max_backoff_seconds,
        ) as response:
            response.raise_for_status()
            _reject_oversized_content_length(
                response,
                max_audio_bytes=self._max_audio_bytes,
            )
            media_type = _response_media_type(response)
            body = await _read_limited_audio_body(
                response,
                max_audio_bytes=self._max_audio_bytes,
            )

        if media_type == "audio/wav":
            body = _decode_pcm16_wav(body, expected_sample_rate=audio_format.sample_rate)
        elif not _is_raw_pcm_media_type(media_type):
            raise LuxTTSTextToSpeechError(
                f"LuxTTS returned a non-audio response content type: {media_type}."
            )
        _validate_pcm16(body)
        _validate_sidecar_quality_headers(
            response,
            audio=body,
            sample_rate=audio_format.sample_rate,
        )
        _validate_audio_quality(body)
        yield body

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _required_config_string(value: str, name: str) -> str:
    clean_value = value.strip()
    if not clean_value:
        raise ValueError(f"{name} must not be blank.")
    return clean_value


def _optional_config_string(value: str | None) -> str | None:
    if value is None:
        return None
    clean_value = value.strip()
    return clean_value or None


def _response_media_type(response: httpx.Response) -> str:
    content_type = response.headers.get("content-type")
    if content_type is None:
        return "application/octet-stream"
    return str(content_type).split(";", maxsplit=1)[0].strip().lower()


def _is_raw_pcm_media_type(media_type: str) -> bool:
    return media_type in {"audio/pcm", "application/octet-stream"} or media_type.startswith(
        "audio/l16"
    )


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
        raise LuxTTSTextToSpeechError("LuxTTS response Content-Length was not an integer.") from exc
    if audio_size <= 0:
        raise LuxTTSTextToSpeechError("LuxTTS response Content-Length must be positive.")
    if audio_size > max_audio_bytes:
        raise _audio_too_large()


async def _read_limited_audio_body(
    response: httpx.Response,
    *,
    max_audio_bytes: int,
) -> bytes:
    chunks: list[bytes] = []
    total_audio_bytes = 0
    async for chunk in response.aiter_bytes():
        if not chunk:
            continue
        total_audio_bytes += len(chunk)
        if total_audio_bytes > max_audio_bytes:
            raise _audio_too_large()
        chunks.append(chunk)
    body = b"".join(chunks)
    if not body:
        raise LuxTTSTextToSpeechError("LuxTTS returned no audio.")
    return body


def _decode_pcm16_wav(body: bytes, *, expected_sample_rate: int) -> bytes:
    try:
        with wave.open(io.BytesIO(body), "rb") as wav:
            channels = wav.getnchannels()
            sample_width = wav.getsampwidth()
            sample_rate = wav.getframerate()
            compression = wav.getcomptype()
            frames = wav.readframes(wav.getnframes())
    except wave.Error as exc:
        raise LuxTTSTextToSpeechError("LuxTTS WAV response was invalid.") from exc
    if channels != 1:
        raise LuxTTSTextToSpeechError("LuxTTS WAV response must be mono.")
    if sample_width != 2:
        raise LuxTTSTextToSpeechError("LuxTTS WAV response must be 16-bit PCM.")
    if sample_rate != expected_sample_rate:
        raise LuxTTSTextToSpeechError(
            "LuxTTS WAV response must already match the requested sample rate."
        )
    if compression != "NONE":
        raise LuxTTSTextToSpeechError("LuxTTS WAV response must be uncompressed PCM.")
    return frames


def _validate_pcm16(audio: bytes) -> None:
    if not audio:
        raise LuxTTSTextToSpeechError("LuxTTS returned no audio.")
    if len(audio) % 2:
        raise LuxTTSTextToSpeechError("LuxTTS returned an incomplete PCM16 sample.")


def _validate_sidecar_quality_headers(
    response: httpx.Response,
    *,
    audio: bytes,
    sample_rate: int,
) -> None:
    duration_gate = response.headers.get("x-duration-gate-passed")
    if duration_gate is None:
        raise LuxTTSTextToSpeechError(
            "LuxTTS response missing X-Duration-Gate-Passed quality header."
        )
    clean_duration_gate = duration_gate.strip().lower()
    if clean_duration_gate not in {"true", "false"}:
        raise LuxTTSTextToSpeechError("LuxTTS response X-Duration-Gate-Passed was not a boolean.")
    if clean_duration_gate != "true":
        raise LuxTTSTextToSpeechError(
            "LuxTTS sidecar duration gate failed; refusing truncated audio."
        )
    audio_seconds_header = response.headers.get("x-audio-seconds")
    if audio_seconds_header is None:
        raise LuxTTSTextToSpeechError("LuxTTS response missing X-Audio-Seconds quality header.")
    try:
        reported_audio_seconds = float(audio_seconds_header)
    except ValueError as exc:
        raise LuxTTSTextToSpeechError("LuxTTS response X-Audio-Seconds was not numeric.") from exc
    if reported_audio_seconds <= 0:
        raise LuxTTSTextToSpeechError("LuxTTS response X-Audio-Seconds must be positive.")
    actual_audio_seconds = len(audio) / (sample_rate * 2)
    if abs(reported_audio_seconds - actual_audio_seconds) > _AUDIO_SECONDS_HEADER_TOLERANCE:
        raise LuxTTSTextToSpeechError(
            "LuxTTS response X-Audio-Seconds did not match received PCM duration."
        )


@dataclass(frozen=True, slots=True)
class _AudioQualityMetrics:
    peak_ratio: float
    rms_dbfs: float
    clipping_ratio: float
    non_silent_ratio: float


def _validate_audio_quality(audio: bytes) -> None:
    quality = _audio_quality_metrics(audio)
    if quality.peak_ratio < MIN_AUDIO_PEAK_RATIO:
        raise LuxTTSTextToSpeechError(f"LuxTTS audio peak too low: {quality.peak_ratio:.3f}.")
    if quality.rms_dbfs < MIN_AUDIO_RMS_DBFS:
        raise LuxTTSTextToSpeechError(f"LuxTTS audio RMS too low: {quality.rms_dbfs:.1f} dBFS.")
    if quality.rms_dbfs > MAX_AUDIO_RMS_DBFS:
        raise LuxTTSTextToSpeechError(f"LuxTTS audio RMS too hot: {quality.rms_dbfs:.1f} dBFS.")
    if quality.clipping_ratio > MAX_AUDIO_CLIPPING_RATIO:
        raise LuxTTSTextToSpeechError(
            f"LuxTTS audio clipping ratio too high: {quality.clipping_ratio:.3f}."
        )
    if quality.non_silent_ratio < MIN_AUDIO_NON_SILENT_RATIO:
        raise LuxTTSTextToSpeechError(
            f"LuxTTS audio mostly silent: {quality.non_silent_ratio:.3f}."
        )


def _audio_quality_metrics(audio: bytes) -> _AudioQualityMetrics:
    sample_count = len(audio) // 2
    max_abs = 0
    square_sum = 0.0
    clipping_count = 0
    non_silent_count = 0
    for (sample,) in struct.iter_unpack("<h", audio):
        abs_sample = abs(sample)
        max_abs = max(max_abs, abs_sample)
        square_sum += float(sample) * float(sample)
        if abs_sample >= 32760:
            clipping_count += 1
        if abs_sample >= 200:
            non_silent_count += 1
    rms = math.sqrt(square_sum / sample_count)
    return _AudioQualityMetrics(
        peak_ratio=min(1.0, max_abs / 32767.0),
        rms_dbfs=max(-120.0, 20.0 * math.log10(max(rms, 1.0) / 32767.0)),
        clipping_ratio=clipping_count / sample_count,
        non_silent_ratio=non_silent_count / sample_count,
    )


def _audio_too_large() -> LuxTTSTextToSpeechError:
    return LuxTTSTextToSpeechError("LuxTTS audio response exceeded configured audio size limit.")
