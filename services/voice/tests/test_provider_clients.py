import asyncio
import base64
import io
import json
import math
import struct
import wave
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from voice_agent.providers.deepgram import DeepgramStreamingSpeechToText
from voice_agent.providers.elevenlabs import ElevenLabsTextToSpeech, ElevenLabsTextToSpeechError
from voice_agent.providers.local import LocalSpeechToText
from voice_agent.providers.luxtts import LuxTTSHttpTextToSpeech, LuxTTSTextToSpeechError
from voice_agent.providers.orchestrator import OrchestratorClientError, OrchestratorLLMClient
from voice_agent.providers.sarvam import (
    SarvamStreamingTextToSpeech,
    SarvamTextToSpeech,
    SarvamTextToSpeechError,
)
from voice_agent.telephony.plivo import PlivoAudioFormat


def _valid_luxtts_pcm(samples: int = 1600, amplitude: int = 6000) -> bytes:
    return b"".join(
        struct.pack("<h", amplitude if index % 2 else -amplitude) for index in range(samples)
    )


def _luxtts_success_headers(
    pcm: bytes,
    *,
    content_type: str = "audio/pcm",
) -> dict[str, str]:
    audio_seconds = len(pcm) / (16_000 * 2)
    return {
        "content-type": content_type,
        "x-duration-gate-passed": "true",
        "x-audio-seconds": f"{audio_seconds:.3f}",
    }


@pytest.mark.anyio
async def test_luxtts_http_tts_posts_plivo_pcm_contract() -> None:
    requests: list[httpx.Request] = []
    pcm = _valid_luxtts_pcm()

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=pcm, headers=_luxtts_success_headers(pcm))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tts = LuxTTSHttpTextToSpeech(
        url="https://luxtts.example.com/tts",
        api_key="secret",
        voice="maya",
        model="lux-v1",
        num_steps=4,
        t_shift=0.9,
        speed=0.8,
        client=client,
    )

    chunks = [
        chunk
        async for chunk in tts.synthesize(
            text=" Hello there. ",
            audio_format=PlivoAudioFormat.L16_16KHZ,
        )
    ]

    assert chunks == [pcm]
    request = requests[0]
    assert request.headers["authorization"] == "Bearer secret"
    assert request.headers["accept"] == "audio/pcm, application/octet-stream, audio/wav"
    assert json.loads(request.content) == {
        "text": "Hello there.",
        "output_format": "pcm_s16le",
        "sample_rate": 16000,
        "num_steps": 4,
        "t_shift": 0.9,
        "speed": 0.8,
        "voice": "maya",
        "model": "lux-v1",
    }
    await client.aclose()


@pytest.mark.anyio
async def test_luxtts_http_tts_accepts_matching_pcm16_wav() -> None:
    pcm = _valid_luxtts_pcm()

    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            content=_wav_bytes(pcm, sample_rate=16000),
            headers=_luxtts_success_headers(pcm, content_type="audio/wav"),
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tts = LuxTTSHttpTextToSpeech(url="https://luxtts.example.com/tts", client=client)

    chunks = [
        chunk
        async for chunk in tts.synthesize(
            text="Hello there.",
            audio_format=PlivoAudioFormat.L16_16KHZ,
        )
    ]

    assert chunks == [pcm]
    await client.aclose()


@pytest.mark.anyio
async def test_luxtts_http_tts_rejects_failed_duration_gate() -> None:
    pcm = _valid_luxtts_pcm()

    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        headers = _luxtts_success_headers(pcm)
        headers["x-duration-gate-passed"] = "false"
        return httpx.Response(200, content=pcm, headers=headers)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tts = LuxTTSHttpTextToSpeech(url="https://luxtts.example.com/tts", client=client)

    with pytest.raises(LuxTTSTextToSpeechError, match="duration gate failed"):
        _ = [
            chunk
            async for chunk in tts.synthesize(
                text="Hello there.",
                audio_format=PlivoAudioFormat.L16_16KHZ,
            )
        ]
    await client.aclose()


@pytest.mark.anyio
async def test_luxtts_http_tts_rejects_missing_quality_header() -> None:
    pcm = _valid_luxtts_pcm()

    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, content=pcm, headers={"content-type": "audio/pcm"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tts = LuxTTSHttpTextToSpeech(url="https://luxtts.example.com/tts", client=client)

    with pytest.raises(LuxTTSTextToSpeechError, match="X-Duration-Gate-Passed"):
        _ = [
            chunk
            async for chunk in tts.synthesize(
                text="Hello there.",
                audio_format=PlivoAudioFormat.L16_16KHZ,
            )
        ]
    await client.aclose()


@pytest.mark.anyio
async def test_luxtts_http_tts_rejects_audio_seconds_header_mismatch() -> None:
    pcm = _valid_luxtts_pcm()

    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        headers = _luxtts_success_headers(pcm)
        headers["x-audio-seconds"] = "3.000"
        return httpx.Response(200, content=pcm, headers=headers)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tts = LuxTTSHttpTextToSpeech(url="https://luxtts.example.com/tts", client=client)

    with pytest.raises(LuxTTSTextToSpeechError, match="received PCM duration"):
        _ = [
            chunk
            async for chunk in tts.synthesize(
                text="Hello there.",
                audio_format=PlivoAudioFormat.L16_16KHZ,
            )
        ]
    await client.aclose()


@pytest.mark.anyio
async def test_luxtts_http_tts_rejects_unusable_audio_quality() -> None:
    pcm = b"\x00\x00" * 1600

    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, content=pcm, headers=_luxtts_success_headers(pcm))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tts = LuxTTSHttpTextToSpeech(url="https://luxtts.example.com/tts", client=client)

    with pytest.raises(LuxTTSTextToSpeechError, match="audio peak too low"):
        _ = [
            chunk
            async for chunk in tts.synthesize(
                text="Hello there.",
                audio_format=PlivoAudioFormat.L16_16KHZ,
            )
        ]
    await client.aclose()


@pytest.mark.anyio
async def test_luxtts_http_tts_rejects_wrong_sample_rate_wav() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            content=_wav_bytes(b"\x01\x00\x02\x00", sample_rate=48000),
            headers={"content-type": "audio/wav"},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tts = LuxTTSHttpTextToSpeech(url="https://luxtts.example.com/tts", client=client)

    with pytest.raises(LuxTTSTextToSpeechError, match="requested sample rate"):
        _ = [
            chunk
            async for chunk in tts.synthesize(
                text="Hello there.",
                audio_format=PlivoAudioFormat.L16_16KHZ,
            )
        ]
    await client.aclose()


@pytest.mark.anyio
async def test_luxtts_http_tts_rejects_incomplete_pcm16_sample() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, content=b"\x01", headers={"content-type": "audio/pcm"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tts = LuxTTSHttpTextToSpeech(url="https://luxtts.example.com/tts", client=client)

    with pytest.raises(LuxTTSTextToSpeechError, match="incomplete PCM16"):
        _ = [
            chunk
            async for chunk in tts.synthesize(
                text="Hello there.",
                audio_format=PlivoAudioFormat.L16_16KHZ,
            )
        ]
    await client.aclose()


@pytest.mark.anyio
async def test_luxtts_http_tts_rejects_non_audio_response() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, json={"error": "not audio"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tts = LuxTTSHttpTextToSpeech(url="https://luxtts.example.com/tts", client=client)

    with pytest.raises(LuxTTSTextToSpeechError, match="non-audio"):
        _ = [
            chunk
            async for chunk in tts.synthesize(
                text="Hello there.",
                audio_format=PlivoAudioFormat.L16_16KHZ,
            )
        ]
    await client.aclose()


def _wav_bytes(pcm: bytes, *, sample_rate: int) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return buffer.getvalue()


@pytest.mark.anyio
async def test_orchestrator_client_accepts_json_reply_contract() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"reply": "Hello from orchestration."})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    llm = OrchestratorLLMClient(
        url="https://orchestrator.example.com/respond",
        api_key="secret",
        client=client,
    )

    chunks = [chunk async for chunk in llm.generate_reply(call_id="call-1", user_text="hello")]

    assert chunks == ["Hello from orchestration."]
    assert requests[0].headers["authorization"] == "Bearer secret"
    assert json.loads(requests[0].content) == {"call_id": "call-1", "input_text": "hello"}
    await client.aclose()


@pytest.mark.anyio
async def test_orchestrator_client_sends_previous_turns_as_conversation_history() -> None:
    requests: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        reply = "First answer." if len(requests) == 1 else "Second answer."
        return httpx.Response(200, json={"reply": reply})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    llm = OrchestratorLLMClient(
        url="https://orchestrator.example.com/respond",
        client=client,
    )

    first_chunks = [
        chunk async for chunk in llm.generate_reply(call_id="call-1", user_text="first question")
    ]
    second_chunks = [
        chunk async for chunk in llm.generate_reply(call_id="call-1", user_text="second question")
    ]

    assert first_chunks == ["First answer."]
    assert second_chunks == ["Second answer."]
    assert requests[0] == {"call_id": "call-1", "input_text": "first question"}
    assert requests[1] == {
        "call_id": "call-1",
        "input_text": "second question",
        "conversation_history": [
            {"role": "user", "content": "first question"},
            {"role": "assistant", "content": "First answer."},
        ],
    }
    await client.aclose()


@pytest.mark.anyio
async def test_orchestrator_client_can_defer_conversation_history_for_speculation() -> None:
    requests: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        replies = ["Speculative answer.", "Real answer.", "Follow-up answer."]
        return httpx.Response(200, json={"reply": replies[len(requests) - 1]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    llm = OrchestratorLLMClient(
        url="https://orchestrator.example.com/respond",
        client=client,
    )

    speculative_chunks = [
        chunk
        async for chunk in llm.generate_reply(
            call_id="call-1",
            user_text="interim guess",
            commit_history=False,
        )
    ]
    real_chunks = [
        chunk
        async for chunk in llm.generate_reply(
            call_id="call-1",
            user_text="real question",
            commit_history=False,
        )
    ]
    llm.remember_turn(
        call_id="call-1",
        user_text="real question",
        assistant_text="Real answer.",
    )
    follow_up_chunks = [
        chunk async for chunk in llm.generate_reply(call_id="call-1", user_text="follow up")
    ]

    assert speculative_chunks == ["Speculative answer."]
    assert real_chunks == ["Real answer."]
    assert follow_up_chunks == ["Follow-up answer."]
    assert requests[0] == {"call_id": "call-1", "input_text": "interim guess"}
    assert requests[1] == {"call_id": "call-1", "input_text": "real question"}
    assert requests[2] == {
        "call_id": "call-1",
        "input_text": "follow up",
        "conversation_history": [
            {"role": "user", "content": "real question"},
            {"role": "assistant", "content": "Real answer."},
        ],
    }
    await client.aclose()


def test_orchestrator_client_rejects_blank_url() -> None:
    with pytest.raises(ValueError, match="url must not be blank"):
        OrchestratorLLMClient(url="   ")


@pytest.mark.parametrize(
    ("url", "match"),
    [
        ("https://", "url must be a valid http or https URL with a host"),
        ("https:///respond", "url must be a valid http or https URL with a host"),
        ("ftp://orchestrator.example.com/respond", "url must use http or https"),
        ("https://user:pass@orchestrator.example.com/respond", "url must not include credentials"),
        (
            "https://orchestrator.example.com/respond?tenant=acme",
            "url must not include a query string or fragment",
        ),
        (
            "https://orchestrator.example.com/respond#voice",
            "url must not include a query string or fragment",
        ),
    ],
)
def test_orchestrator_client_rejects_malformed_url(url: str, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        OrchestratorLLMClient(url=url)


def test_orchestrator_client_rejects_blank_api_key_when_supplied() -> None:
    with pytest.raises(ValueError, match="api_key must not be blank"):
        OrchestratorLLMClient(
            url="https://orchestrator.example.com/respond",
            api_key="   ",
        )


@pytest.mark.anyio
@pytest.mark.parametrize("field_name", ["call_id", "user_text"])
async def test_orchestrator_client_rejects_blank_turn_arguments_without_calling_provider(
    field_name: str,
) -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"reply": "Hello from orchestration."})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    llm = OrchestratorLLMClient(url="https://orchestrator.example.com/respond", client=client)
    call_id = "   " if field_name == "call_id" else "call-1"
    user_text = "   " if field_name == "user_text" else "hello"

    with pytest.raises(OrchestratorClientError, match=field_name):
        _ = [chunk async for chunk in llm.generate_reply(call_id=call_id, user_text=user_text)]

    assert requests == []
    await client.aclose()


@pytest.mark.anyio
async def test_orchestrator_client_rejects_unsafe_call_id_without_calling_provider() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"reply": "Hello from orchestration."})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    llm = OrchestratorLLMClient(url="https://orchestrator.example.com/respond", client=client)

    with pytest.raises(OrchestratorClientError, match="call_id"):
        _ = [
            chunk
            async for chunk in llm.generate_reply(
                call_id="call\nunsafe",
                user_text="hello",
            )
        ]

    assert requests == []
    await client.aclose()


@pytest.mark.anyio
async def test_orchestrator_client_trims_url_api_key_and_turn_arguments() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"reply": "Hello from orchestration."})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    llm = OrchestratorLLMClient(
        url=" https://orchestrator.example.com/respond ",
        api_key=" secret ",
        client=client,
    )

    chunks = [chunk async for chunk in llm.generate_reply(call_id=" call-1 ", user_text=" hello ")]

    assert chunks == ["Hello from orchestration."]
    assert str(requests[0].url) == "https://orchestrator.example.com/respond"
    assert requests[0].headers["authorization"] == "Bearer secret"
    assert json.loads(requests[0].content) == {"call_id": "call-1", "input_text": "hello"}
    await client.aclose()


@pytest.mark.anyio
async def test_orchestrator_client_allows_http_url_for_local_or_test_usage() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"reply": "Hello from local orchestration."})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    llm = OrchestratorLLMClient(
        url=" http://localhost:9000/voice/respond/ ",
        client=client,
    )

    chunks = [chunk async for chunk in llm.generate_reply(call_id="call-1", user_text="hello")]

    assert chunks == ["Hello from local orchestration."]
    assert str(requests[0].url) == "http://localhost:9000/voice/respond"
    await client.aclose()


@pytest.mark.anyio
async def test_orchestrator_client_start_warms_healthz_connection_with_auth() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(204)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    llm = OrchestratorLLMClient(
        url="https://orchestrator.example.com/voice/respond",
        api_key="secret",
        client=client,
    )

    await llm.start()

    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert str(requests[0].url) == "https://orchestrator.example.com/healthz"
    assert requests[0].headers["authorization"] == "Bearer secret"
    await client.aclose()


@pytest.mark.anyio
async def test_orchestrator_client_start_tolerates_missing_healthz_endpoint() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    llm = OrchestratorLLMClient(
        url="https://custom-orchestrator.example.com/respond",
        client=client,
    )

    await llm.start()

    assert len(requests) == 1
    assert str(requests[0].url) == "https://custom-orchestrator.example.com/healthz"
    await client.aclose()


@pytest.mark.anyio
async def test_orchestrator_client_accepts_sse_delta_contract() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=('data: {"delta":"Hello"}\n\ndata: {"delta":" there"}\n\ndata: [DONE]\n\n'),
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    llm = OrchestratorLLMClient(url="https://orchestrator.example.com/respond", client=client)

    chunks = [chunk async for chunk in llm.generate_reply(call_id="call-1", user_text="hello")]

    assert chunks == ["Hello", " there"]
    await client.aclose()


@pytest.mark.anyio
async def test_orchestrator_client_requires_exact_sse_media_type_before_streaming() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            headers={"content-type": "application/not-text/event-stream"},
            json={"reply": "Hello from JSON."},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    llm = OrchestratorLLMClient(url="https://orchestrator.example.com/respond", client=client)

    try:
        chunks = [chunk async for chunk in llm.generate_reply(call_id="call-1", user_text="hello")]
    finally:
        await client.aclose()

    assert chunks == ["Hello from JSON."]


@pytest.mark.anyio
async def test_orchestrator_client_can_require_streaming_sse_response() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, json={"reply": "Hello from JSON."})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    llm = OrchestratorLLMClient(
        url="https://orchestrator.example.com/respond",
        client=client,
        require_streaming_response=True,
    )

    try:
        with pytest.raises(OrchestratorClientError, match="text/event-stream"):
            _ = [
                chunk
                async for chunk in llm.generate_reply(
                    call_id="call-1",
                    user_text="hello",
                )
            ]
    finally:
        await client.aclose()


@pytest.mark.anyio
async def test_orchestrator_client_accepts_sse_media_type_case_and_parameters() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            headers={"content-type": "Text/Event-Stream; charset=utf-8"},
            content='data: {"delta":"Hello"}\n\ndata: [DONE]\n\n',
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    llm = OrchestratorLLMClient(url="https://orchestrator.example.com/respond", client=client)

    try:
        chunks = [chunk async for chunk in llm.generate_reply(call_id="call-1", user_text="hello")]
    finally:
        await client.aclose()

    assert chunks == ["Hello"]


@pytest.mark.anyio
async def test_orchestrator_client_accepts_multiline_sse_data_event() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=('data: {"delta":\ndata: "Hello caller"}\n\ndata: [DONE]\n\n'),
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    llm = OrchestratorLLMClient(url="https://orchestrator.example.com/respond", client=client)

    chunks = [chunk async for chunk in llm.generate_reply(call_id="call-1", user_text="hello")]

    assert chunks == ["Hello caller"]
    await client.aclose()


@pytest.mark.anyio
async def test_orchestrator_client_stops_reading_sse_after_done_event() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=('data: {"delta":"Hello"}\n\ndata: [DONE]\n\ndata: not-json-after-done\n\n'),
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    llm = OrchestratorLLMClient(url="https://orchestrator.example.com/respond", client=client)

    chunks = [chunk async for chunk in llm.generate_reply(call_id="call-1", user_text="hello")]

    assert chunks == ["Hello"]
    await client.aclose()


@pytest.mark.anyio
async def test_orchestrator_client_accepts_openai_compatible_sse_delta_content() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=(
                'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n'
                'data: {"choices":[{"delta":{"content":" caller"}}]}\n\n'
                "data: [DONE]\n\n"
            ),
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    llm = OrchestratorLLMClient(url="https://orchestrator.example.com/respond", client=client)

    chunks = [chunk async for chunk in llm.generate_reply(call_id="call-1", user_text="hello")]

    assert chunks == ["Hello", " caller"]
    await client.aclose()


@pytest.mark.anyio
async def test_orchestrator_client_times_out_before_first_sse_delta() -> None:
    stream = HangingAsyncByteStream()

    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=stream,
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    llm = OrchestratorLLMClient(
        url="https://orchestrator.example.com/respond",
        client=client,
        first_chunk_timeout_seconds=0.01,
    )

    with pytest.raises(OrchestratorClientError, match="first reply chunk timed out"):
        _ = [chunk async for chunk in llm.generate_reply(call_id="call-1", user_text="hello")]

    await client.aclose()


@pytest.mark.anyio
async def test_orchestrator_client_times_out_before_json_reply_body() -> None:
    stream = HangingAsyncByteStream()

    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            stream=stream,
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    llm = OrchestratorLLMClient(
        url="https://orchestrator.example.com/respond",
        client=client,
        first_chunk_timeout_seconds=0.01,
    )

    with pytest.raises(OrchestratorClientError, match="first reply chunk timed out"):
        _ = [chunk async for chunk in llm.generate_reply(call_id="call-1", user_text="hello")]

    await client.aclose()


@pytest.mark.anyio
async def test_orchestrator_client_rejects_malformed_json_response() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, content=b"not-json")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    llm = OrchestratorLLMClient(url="https://orchestrator.example.com/respond", client=client)

    with pytest.raises(OrchestratorClientError):
        _ = [chunk async for chunk in llm.generate_reply(call_id="call-1", user_text="hello")]

    await client.aclose()


@pytest.mark.anyio
async def test_orchestrator_client_rejects_blank_json_reply_text() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, json={"reply": "   "})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    llm = OrchestratorLLMClient(url="https://orchestrator.example.com/respond", client=client)

    with pytest.raises(OrchestratorClientError, match="did not include reply text"):
        _ = [chunk async for chunk in llm.generate_reply(call_id="call-1", user_text="hello")]

    await client.aclose()


@pytest.mark.anyio
async def test_orchestrator_client_rejects_oversized_json_response_while_streaming() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            stream=ChunkedAsyncByteStream([b'{"reply":"', b"x" * 32, b'"}']),
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    llm = OrchestratorLLMClient(
        url="https://orchestrator.example.com/respond",
        client=client,
        max_response_bytes=16,
    )

    with pytest.raises(OrchestratorClientError, match="exceeded configured size limit"):
        _ = [chunk async for chunk in llm.generate_reply(call_id="call-1", user_text="hello")]

    await client.aclose()


@pytest.mark.anyio
async def test_orchestrator_client_rejects_negative_response_content_length() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            headers={"content-length": "-1"},
            stream=ChunkedAsyncByteStream([b'{"reply":"Hello caller"}']),
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    llm = OrchestratorLLMClient(url="https://orchestrator.example.com/respond", client=client)

    with pytest.raises(OrchestratorClientError, match="Content-Length must be positive"):
        _ = [chunk async for chunk in llm.generate_reply(call_id="call-1", user_text="hello")]

    await client.aclose()


@pytest.mark.anyio
async def test_orchestrator_client_rejects_oversized_sse_response() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content='data: {"delta":"xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"}\n\n',
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    llm = OrchestratorLLMClient(
        url="https://orchestrator.example.com/respond",
        client=client,
        max_response_bytes=16,
    )

    with pytest.raises(OrchestratorClientError, match="exceeded configured size limit"):
        _ = [chunk async for chunk in llm.generate_reply(call_id="call-1", user_text="hello")]

    await client.aclose()


@pytest.mark.anyio
async def test_orchestrator_client_rejects_oversized_sse_content_length_before_reading() -> None:
    stream = ChunkedAsyncByteStream([b'data: {"delta":"Hello"}\n\n'])

    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            headers={
                "content-type": "text/event-stream",
                "content-length": "100",
            },
            stream=stream,
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    llm = OrchestratorLLMClient(
        url="https://orchestrator.example.com/respond",
        client=client,
        max_response_bytes=16,
    )

    with pytest.raises(OrchestratorClientError, match="exceeded configured size limit"):
        _ = [chunk async for chunk in llm.generate_reply(call_id="call-1", user_text="hello")]

    assert stream.yield_count == 0
    await client.aclose()


@pytest.mark.anyio
async def test_orchestrator_client_enforces_sse_size_limit_before_full_line_buffer() -> None:
    chunks = [
        b"data: ",
        b'{"delta":"',
        b"xxxxxxxx",
        b"yyyyyyyy",
        b'"}\n\n',
    ]
    stream = ChunkedAsyncByteStream(chunks)

    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=stream,
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    llm = OrchestratorLLMClient(
        url="https://orchestrator.example.com/respond",
        client=client,
        max_response_bytes=20,
    )

    with pytest.raises(OrchestratorClientError, match="exceeded configured size limit"):
        _ = [chunk async for chunk in llm.generate_reply(call_id="call-1", user_text="hello")]

    assert stream.yield_count == 3
    await client.aclose()


@pytest.mark.anyio
async def test_orchestrator_client_rejects_malformed_sse_data() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content="data: not-json\n\n",
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    llm = OrchestratorLLMClient(url="https://orchestrator.example.com/respond", client=client)

    with pytest.raises(OrchestratorClientError):
        _ = [chunk async for chunk in llm.generate_reply(call_id="call-1", user_text="hello")]

    await client.aclose()


@pytest.mark.anyio
async def test_orchestrator_client_rejects_blank_sse_reply_text() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content='data: {"delta":"   "}\n\ndata: [DONE]\n\n',
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    llm = OrchestratorLLMClient(url="https://orchestrator.example.com/respond", client=client)

    with pytest.raises(OrchestratorClientError, match="did not include reply text"):
        _ = [chunk async for chunk in llm.generate_reply(call_id="call-1", user_text="hello")]

    await client.aclose()


@pytest.mark.anyio
async def test_orchestrator_client_rejects_empty_sse_reply() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content="data: [DONE]\n\n",
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    llm = OrchestratorLLMClient(url="https://orchestrator.example.com/respond", client=client)

    with pytest.raises(OrchestratorClientError, match="did not include reply text"):
        _ = [chunk async for chunk in llm.generate_reply(call_id="call-1", user_text="hello")]

    await client.aclose()


@pytest.mark.anyio
async def test_orchestrator_client_closes_only_owned_http_client() -> None:
    owned = OrchestratorLLMClient(url="https://orchestrator.example.com/respond")
    injected_client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200))
    )
    injected = OrchestratorLLMClient(
        url="https://orchestrator.example.com/respond",
        client=injected_client,
    )

    owned_http_client = owned._client
    await owned.close()
    await injected.close()

    assert owned_http_client.is_closed
    assert not injected_client.is_closed
    await injected_client.aclose()


def test_orchestrator_client_rejects_non_positive_timeout() -> None:
    with pytest.raises(ValueError, match="timeout_seconds must be positive"):
        OrchestratorLLMClient(
            url="https://orchestrator.example.com/respond",
            timeout_seconds=0,
        )


def test_orchestrator_client_rejects_non_positive_first_chunk_timeout() -> None:
    with pytest.raises(ValueError, match="first_chunk_timeout_seconds must be positive"):
        OrchestratorLLMClient(
            url="https://orchestrator.example.com/respond",
            first_chunk_timeout_seconds=0,
        )


def test_orchestrator_client_rejects_non_positive_max_response_bytes() -> None:
    with pytest.raises(ValueError, match="max_response_bytes must be positive"):
        OrchestratorLLMClient(
            url="https://orchestrator.example.com/respond",
            max_response_bytes=0,
        )


@pytest.mark.anyio
async def test_deepgram_stt_keeps_transcript_iterator_open_across_reader_reconnect() -> None:
    stt = DeepgramStreamingSpeechToText(api_key="deepgram-secret")
    collector_task = asyncio.create_task(_collect_transcripts_until(stt, count=1))
    first_reader_task = asyncio.create_task(stt._read_transcripts(FakeDeepgramSocket(["not-json"])))

    try:
        await asyncio.wait_for(first_reader_task, timeout=0.2)
        assert not collector_task.done()
        second_reader_task = asyncio.create_task(
            stt._read_transcripts(
                FakeDeepgramSocket(
                    [
                        json.dumps(
                            {
                                "channel": {
                                    "alternatives": [
                                        {"transcript": "I need help", "confidence": 0.93}
                                    ]
                                },
                                "is_final": True,
                            }
                        )
                    ]
                )
            )
        )
        events = await asyncio.wait_for(collector_task, timeout=0.2)
    finally:
        await stt.close()
        if not collector_task.done():
            collector_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await collector_task

    await second_reader_task
    assert len(events) == 1
    assert events[0].text == "I need help"
    assert events[0].is_final
    assert events[0].confidence == 0.93


@pytest.mark.anyio
async def test_deepgram_stt_uses_configured_websocket_timing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connect_kwargs: dict[str, object] = {}

    async def connect(uri: str, **kwargs: object) -> FakeDeepgramSocket:
        connect_kwargs["uri"] = uri
        connect_kwargs.update(kwargs)
        return FakeDeepgramSocket([])

    monkeypatch.setattr("voice_agent.providers.deepgram.websockets.connect", connect)
    stt = DeepgramStreamingSpeechToText(
        api_key="deepgram-secret",
        endpointing_ms=250,
        utterance_end_ms=1200,
        connect_timeout_seconds=4.5,
        ping_interval_seconds=6.5,
        ping_timeout_seconds=7.5,
        transcript_message_max_bytes=8192,
    )

    await stt._ensure_connected()
    await stt.close()

    assert connect_kwargs["uri"] == (
        "wss://api.deepgram.com/v1/listen?"
        "model=nova-3&language=en-IN&encoding=linear16&sample_rate=16000&channels=1"
        "&interim_results=true&endpointing=250"
        "&utterance_end_ms=1200&vad_events=true&punctuate=true"
        "&smart_format=true&filler_words=true"
    )
    assert connect_kwargs["open_timeout"] == 4.5
    assert connect_kwargs["ping_interval"] == 6.5
    assert connect_kwargs["ping_timeout"] == 7.5
    assert connect_kwargs["max_size"] == 8192


@pytest.mark.anyio
async def test_deepgram_stt_start_preconnects_and_reuses_socket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    socket = BlockingDeepgramSocket()
    connect_count = 0

    async def connect(uri: str, **kwargs: object) -> BlockingDeepgramSocket:
        del uri, kwargs
        nonlocal connect_count
        connect_count += 1
        return socket

    monkeypatch.setattr("voice_agent.providers.deepgram.websockets.connect", connect)
    stt = DeepgramStreamingSpeechToText(api_key="deepgram-secret")

    await stt.start()
    await stt.accept_audio(b"first frame")
    await stt.close()

    assert connect_count == 1
    assert socket.sent_messages[0] == b"first frame"
    assert socket.closed is True


@pytest.mark.anyio
async def test_deepgram_stt_accept_audio_does_not_block_on_slow_socket_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    socket = SlowSendDeepgramSocket()

    async def connect(uri: str, **kwargs: object) -> SlowSendDeepgramSocket:
        del uri, kwargs
        return socket

    monkeypatch.setattr("voice_agent.providers.deepgram.websockets.connect", connect)
    stt = DeepgramStreamingSpeechToText(api_key="deepgram-secret")

    await stt.start()
    send_task = asyncio.create_task(stt.accept_audio(b"first frame"))
    await asyncio.wait_for(socket.send_started.wait(), timeout=0.2)
    try:
        done, _pending = await asyncio.wait({send_task}, timeout=0.05)
        assert send_task in done
        assert socket.sent_messages == []

        socket.release_send.set()
        await asyncio.wait_for(socket.send_finished.wait(), timeout=0.2)
    finally:
        socket.release_send.set()
        await stt.close()
        if not send_task.done():
            send_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await send_task

    assert socket.sent_messages[0] == b"first frame"


@pytest.mark.anyio
async def test_deepgram_stt_reconnects_after_reader_exits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sockets = [FakeDeepgramSocket([]), FakeDeepgramSocket([])]
    connect_count = 0

    async def connect(uri: str, **kwargs: object) -> FakeDeepgramSocket:
        del uri, kwargs
        nonlocal connect_count
        socket = sockets[connect_count]
        connect_count += 1
        return socket

    monkeypatch.setattr("voice_agent.providers.deepgram.websockets.connect", connect)
    stt = DeepgramStreamingSpeechToText(api_key="deepgram-secret")

    await stt.accept_audio(b"first frame")
    first_reader_task = stt._reader_task
    assert first_reader_task is not None
    await asyncio.wait_for(first_reader_task, timeout=0.2)
    await stt.accept_audio(b"second frame")
    await stt.close()

    assert connect_count == 2
    assert sockets[0].sent_messages == [b"first frame"]
    assert sockets[1].sent_messages[0] == b"second frame"


@pytest.mark.anyio
async def test_deepgram_stt_bounds_transcript_queue_and_keeps_latest_events() -> None:
    stt = DeepgramStreamingSpeechToText(
        api_key="deepgram-secret",
        transcript_queue_max_size=2,
    )
    reader_task = asyncio.create_task(
        stt._read_transcripts(
            FakeDeepgramSocket(
                [
                    _deepgram_transcript_message("first request"),
                    _deepgram_transcript_message("second request"),
                    _deepgram_transcript_message("third request"),
                ]
            )
        )
    )

    try:
        await asyncio.wait_for(reader_task, timeout=0.2)
        events = await asyncio.wait_for(_collect_transcripts_until(stt, count=2), timeout=0.2)
    finally:
        await stt.close()

    assert [event.text for event in events] == ["second request", "third request"]


@pytest.mark.anyio
async def test_deepgram_stt_close_does_not_block_when_transcript_queue_is_full() -> None:
    stt = DeepgramStreamingSpeechToText(
        api_key="deepgram-secret",
        transcript_queue_max_size=1,
    )

    await asyncio.wait_for(
        stt._read_transcripts(FakeDeepgramSocket([_deepgram_transcript_message("queued request")])),
        timeout=0.2,
    )
    await asyncio.wait_for(stt.close(), timeout=0.2)


def test_deepgram_stt_rejects_non_positive_websocket_timing() -> None:
    with pytest.raises(ValueError, match="connect_timeout_seconds must be positive"):
        DeepgramStreamingSpeechToText(
            api_key="deepgram-secret",
            connect_timeout_seconds=0,
        )
    with pytest.raises(ValueError, match="ping_interval_seconds must be positive"):
        DeepgramStreamingSpeechToText(
            api_key="deepgram-secret",
            ping_interval_seconds=0,
        )
    with pytest.raises(ValueError, match="ping_timeout_seconds must be positive"):
        DeepgramStreamingSpeechToText(
            api_key="deepgram-secret",
            ping_timeout_seconds=0,
        )


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"api_key": "   "}, "api_key must not be blank"),
        ({"model": "   "}, "model must not be blank"),
        ({"language": "   "}, "language must not be blank"),
        ({"sample_rate": 0}, "sample_rate must be positive"),
        ({"endpointing_ms": 0}, "endpointing_ms must be positive"),
        ({"utterance_end_ms": 999}, "utterance_end_ms must be at least 1000"),
        ({"transcript_queue_max_size": 0}, "transcript_queue_max_size must be positive"),
        ({"transcript_message_max_bytes": 0}, "transcript_message_max_bytes must be positive"),
    ],
)
def test_deepgram_stt_rejects_invalid_stream_configuration(
    kwargs: dict[str, object],
    match: str,
) -> None:
    arguments: dict[str, Any] = {"api_key": "deepgram-secret"}
    arguments.update(kwargs)

    with pytest.raises(ValueError, match=match):
        DeepgramStreamingSpeechToText(**arguments)


@pytest.mark.anyio
async def test_deepgram_stt_trims_stream_configuration_before_connect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connect_kwargs: dict[str, object] = {}

    async def connect(uri: str, **kwargs: object) -> FakeDeepgramSocket:
        connect_kwargs["uri"] = uri
        connect_kwargs.update(kwargs)
        return FakeDeepgramSocket([])

    monkeypatch.setattr("voice_agent.providers.deepgram.websockets.connect", connect)
    stt = DeepgramStreamingSpeechToText(
        api_key=" deepgram-secret ",
        model=" nova-3 ",
        language=" en-IN ",
    )

    await stt._ensure_connected()
    await stt.close()

    assert connect_kwargs["uri"] == (
        "wss://api.deepgram.com/v1/listen?"
        "model=nova-3&language=en-IN&encoding=linear16&sample_rate=16000&channels=1"
        "&interim_results=true&endpointing=150"
        "&utterance_end_ms=1000&vad_events=true&punctuate=true"
        "&smart_format=true&filler_words=true"
    )
    assert connect_kwargs["additional_headers"] == {"Authorization": "Token deepgram-secret"}


@pytest.mark.anyio
async def test_local_stt_accepts_non_silent_pcm16_smoke_audio() -> None:
    stt = LocalSpeechToText(trigger_phrase="local caller audio")

    transcript = await stt.accept_audio(_pcm16_tone())

    assert transcript is not None
    assert transcript.text == "local caller audio"
    assert transcript.is_final
    assert transcript.confidence == 1.0
    await stt.close()


@pytest.mark.anyio
async def test_local_stt_ignores_pcm16_silence() -> None:
    stt = LocalSpeechToText(trigger_phrase="local caller audio")

    transcript = await stt.accept_audio(b"\x00\x00" * 320)

    assert transcript is None
    await stt.close()


def _pcm16_tone() -> bytes:
    sample_rate = 16_000
    duration_seconds = 0.04
    frequency_hz = 440.0
    amplitude = 2800
    frame_count = int(sample_rate * duration_seconds)
    samples = bytearray()
    for index in range(frame_count):
        value = int(amplitude * math.sin(2.0 * math.pi * frequency_hz * index / sample_rate))
        samples.extend(struct.pack("<h", value))
    return bytes(samples)


async def _collect_transcripts_until(
    stt: DeepgramStreamingSpeechToText,
    *,
    count: int,
) -> list[Any]:
    events: list[Any] = []
    async for event in stt.transcripts():
        events.append(event)
        if len(events) >= count:
            return events
    return events


def _deepgram_transcript_message(text: str) -> str:
    return json.dumps(
        {
            "channel": {"alternatives": [{"transcript": text, "confidence": 0.93}]},
            "is_final": True,
        }
    )


class FakeDeepgramSocket:
    def __init__(self, messages: list[object]) -> None:
        self._messages = messages
        self.closed = False
        self.sent_messages: list[object] = []

    def __aiter__(self) -> AsyncIterator[object]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[object]:
        for message in self._messages:
            yield message

    async def send(self, message: object) -> None:
        self.sent_messages.append(message)

    async def close(self) -> None:
        self.closed = True


class BlockingDeepgramSocket(FakeDeepgramSocket):
    def __init__(self) -> None:
        super().__init__([])
        self.release = asyncio.Event()

    async def _iterate(self) -> AsyncIterator[object]:
        await self.release.wait()
        for message in self._messages:
            yield message


class SlowSendDeepgramSocket(FakeDeepgramSocket):
    def __init__(self) -> None:
        super().__init__([])
        self.send_started = asyncio.Event()
        self.release_send = asyncio.Event()
        self.send_finished = asyncio.Event()
        self.release_reader = asyncio.Event()

    async def _iterate(self) -> AsyncIterator[object]:
        await self.release_reader.wait()
        return
        yield ""

    async def send(self, message: object) -> None:
        self.send_started.set()
        await self.release_send.wait()
        self.sent_messages.append(message)
        self.send_finished.set()

    async def close(self) -> None:
        self.release_reader.set()
        await super().close()


class ChunkedAsyncByteStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks
        self.yield_count = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            self.yield_count += 1
            yield chunk

    async def aclose(self) -> None:
        return None


class HangingAsyncByteStream(httpx.AsyncByteStream):
    async def __aiter__(self) -> AsyncIterator[bytes]:
        await asyncio.Event().wait()
        yield b""

    async def aclose(self) -> None:
        return None


@pytest.mark.anyio
async def test_elevenlabs_tts_requests_pcm16_16khz_streaming_audio() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=b"\x00\x01\x02\x03")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tts = ElevenLabsTextToSpeech(
        api_key="eleven-secret",
        voice_id="voice-1",
        model_id="eleven_multilingual_v2",
        client=client,
    )

    chunks = [
        chunk
        async for chunk in tts.synthesize(
            text="Hello caller",
            audio_format=PlivoAudioFormat.L16_16KHZ,
        )
    ]

    assert chunks == [b"\x00\x01\x02\x03"]
    request = requests[0]
    assert request.headers["xi-api-key"] == "eleven-secret"
    assert "output_format=pcm_16000" in str(request.url)
    assert json.loads(request.content) == {
        "text": "Hello caller",
        "model_id": "eleven_multilingual_v2",
    }
    await client.aclose()


@pytest.mark.anyio
async def test_elevenlabs_tts_reframes_pcm16_stream_chunks_to_complete_samples() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            stream=ChunkedAsyncByteStream([b"\x00", b"\x01\x02", b"\x03"]),
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tts = ElevenLabsTextToSpeech(
        api_key="eleven-secret",
        voice_id="voice-1",
        model_id="eleven_multilingual_v2",
        client=client,
    )

    chunks = [
        chunk
        async for chunk in tts.synthesize(
            text="Hello caller",
            audio_format=PlivoAudioFormat.L16_16KHZ,
        )
    ]

    assert chunks == [b"\x00\x01", b"\x02\x03"]
    await client.aclose()


@pytest.mark.anyio
async def test_elevenlabs_tts_rejects_incomplete_final_pcm16_sample() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            stream=ChunkedAsyncByteStream([b"\x00\x01", b"\x02"]),
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tts = ElevenLabsTextToSpeech(
        api_key="eleven-secret",
        voice_id="voice-1",
        model_id="eleven_multilingual_v2",
        client=client,
    )

    with pytest.raises(ElevenLabsTextToSpeechError, match="incomplete PCM16 sample"):
        _ = [
            chunk
            async for chunk in tts.synthesize(
                text="Hello caller",
                audio_format=PlivoAudioFormat.L16_16KHZ,
            )
        ]

    await client.aclose()


@pytest.mark.anyio
async def test_elevenlabs_tts_rejects_successful_json_response_before_streaming() -> None:
    stream = ChunkedAsyncByteStream([b'{"error":"not audio"}'])

    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            stream=stream,
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tts = ElevenLabsTextToSpeech(
        api_key="eleven-secret",
        voice_id="voice-1",
        model_id="eleven_multilingual_v2",
        client=client,
    )

    with pytest.raises(ElevenLabsTextToSpeechError, match="non-audio response"):
        _ = [
            chunk
            async for chunk in tts.synthesize(
                text="Hello caller",
                audio_format=PlivoAudioFormat.L16_16KHZ,
            )
        ]

    assert stream.yield_count == 0
    await client.aclose()


@pytest.mark.anyio
async def test_elevenlabs_tts_rejects_oversized_audio_stream_while_reading() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            stream=ChunkedAsyncByteStream([b"\x00\x01", b"\x02\x03", b"\x04\x05"]),
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tts = ElevenLabsTextToSpeech(
        api_key="eleven-secret",
        voice_id="voice-1",
        model_id="eleven_multilingual_v2",
        client=client,
        max_audio_bytes=4,
    )

    with pytest.raises(ElevenLabsTextToSpeechError, match="exceeded configured audio size limit"):
        _ = [
            chunk
            async for chunk in tts.synthesize(
                text="Hello caller",
                audio_format=PlivoAudioFormat.L16_16KHZ,
            )
        ]

    await client.aclose()


@pytest.mark.anyio
async def test_elevenlabs_tts_rejects_oversized_audio_content_length() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            headers={"content-length": "6"},
            stream=ChunkedAsyncByteStream([b"\x00\x01"]),
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tts = ElevenLabsTextToSpeech(
        api_key="eleven-secret",
        voice_id="voice-1",
        model_id="eleven_multilingual_v2",
        client=client,
        max_audio_bytes=4,
    )

    with pytest.raises(ElevenLabsTextToSpeechError, match="exceeded configured audio size limit"):
        _ = [
            chunk
            async for chunk in tts.synthesize(
                text="Hello caller",
                audio_format=PlivoAudioFormat.L16_16KHZ,
            )
        ]

    await client.aclose()


@pytest.mark.anyio
async def test_elevenlabs_tts_rejects_negative_audio_content_length() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            headers={"content-length": "-1"},
            stream=ChunkedAsyncByteStream([b"\x00\x01"]),
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tts = ElevenLabsTextToSpeech(
        api_key="eleven-secret",
        voice_id="voice-1",
        model_id="eleven_multilingual_v2",
        client=client,
    )

    with pytest.raises(ElevenLabsTextToSpeechError, match="Content-Length must be positive"):
        _ = [
            chunk
            async for chunk in tts.synthesize(
                text="Hello caller",
                audio_format=PlivoAudioFormat.L16_16KHZ,
            )
        ]

    await client.aclose()


@pytest.mark.parametrize("field_name", ["api_key", "voice_id", "model_id"])
def test_elevenlabs_tts_rejects_blank_constructor_arguments(field_name: str) -> None:
    arguments: dict[str, Any] = {
        "api_key": "eleven-secret",
        "voice_id": "voice-1",
        "model_id": "eleven_flash_v2_5",
    }
    arguments[field_name] = "   "

    with pytest.raises(ValueError, match=f"{field_name} must not be blank"):
        ElevenLabsTextToSpeech(**arguments)


@pytest.mark.anyio
async def test_elevenlabs_tts_trims_constructor_arguments() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=b"\x00\x01")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tts = ElevenLabsTextToSpeech(
        api_key=" eleven-secret ",
        voice_id=" voice-1 ",
        model_id=" eleven_flash_v2_5 ",
        client=client,
    )

    chunks = [
        chunk
        async for chunk in tts.synthesize(
            text="Hello caller",
            audio_format=PlivoAudioFormat.L16_16KHZ,
        )
    ]

    assert chunks == [b"\x00\x01"]
    assert requests[0].headers["xi-api-key"] == "eleven-secret"
    assert str(requests[0].url).startswith(
        "https://api.elevenlabs.io/v1/text-to-speech/voice-1/stream"
    )
    assert json.loads(requests[0].content)["model_id"] == "eleven_flash_v2_5"
    await client.aclose()


@pytest.mark.anyio
async def test_elevenlabs_tts_trims_text_before_synthesis() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=b"\x00\x01")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tts = ElevenLabsTextToSpeech(
        api_key="eleven-secret",
        voice_id="voice-1",
        model_id="eleven_flash_v2_5",
        client=client,
    )

    chunks = [
        chunk
        async for chunk in tts.synthesize(
            text="  Hello caller  ",
            audio_format=PlivoAudioFormat.L16_16KHZ,
        )
    ]

    assert chunks == [b"\x00\x01"]
    assert json.loads(requests[0].content)["text"] == "Hello caller"
    await client.aclose()


@pytest.mark.anyio
async def test_elevenlabs_tts_rejects_blank_text_without_calling_provider() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=b"\x00\x01")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tts = ElevenLabsTextToSpeech(
        api_key="eleven-secret",
        voice_id="voice-1",
        model_id="eleven_flash_v2_5",
        client=client,
    )

    with pytest.raises(ElevenLabsTextToSpeechError, match="text must not be blank"):
        _ = [
            chunk
            async for chunk in tts.synthesize(
                text="   ",
                audio_format=PlivoAudioFormat.L16_16KHZ,
            )
        ]

    assert requests == []
    await client.aclose()


@pytest.mark.anyio
async def test_elevenlabs_tts_rejects_empty_audio_stream() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, content=b"")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tts = ElevenLabsTextToSpeech(
        api_key="eleven-secret",
        voice_id="voice-1",
        model_id="eleven_flash_v2_5",
        client=client,
    )

    with pytest.raises(ElevenLabsTextToSpeechError, match="ElevenLabs returned no audio"):
        _ = [
            chunk
            async for chunk in tts.synthesize(
                text="Hello caller",
                audio_format=PlivoAudioFormat.L16_16KHZ,
            )
        ]

    await client.aclose()


@pytest.mark.anyio
async def test_elevenlabs_tts_closes_only_owned_http_client() -> None:
    owned = ElevenLabsTextToSpeech(
        api_key="eleven-secret",
        voice_id="voice-1",
        model_id="eleven_flash_v2_5",
    )
    injected_client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200))
    )
    injected = ElevenLabsTextToSpeech(
        api_key="eleven-secret",
        voice_id="voice-1",
        model_id="eleven_flash_v2_5",
        client=injected_client,
    )

    owned_http_client = owned._client
    await owned.close()
    await injected.close()

    assert owned_http_client.is_closed
    assert not injected_client.is_closed
    await injected_client.aclose()


def test_elevenlabs_tts_rejects_non_positive_timeout() -> None:
    with pytest.raises(ValueError, match="timeout_seconds must be positive"):
        ElevenLabsTextToSpeech(
            api_key="eleven-secret",
            voice_id="voice-1",
            model_id="eleven_flash_v2_5",
            timeout_seconds=0,
        )


def test_elevenlabs_tts_rejects_non_positive_max_audio_bytes() -> None:
    with pytest.raises(ValueError, match="max_audio_bytes must be positive"):
        ElevenLabsTextToSpeech(
            api_key="eleven-secret",
            voice_id="voice-1",
            model_id="eleven_flash_v2_5",
            max_audio_bytes=0,
        )


def _sarvam_audio_response(pcm: bytes) -> httpx.Response:
    payload = base64.b64encode(pcm).decode("ascii")
    return httpx.Response(200, json={"request_id": "req-1", "audios": [payload]})


@pytest.mark.anyio
async def test_sarvam_tts_requests_linear16_pcm16_and_yields_audio() -> None:
    requests: list[httpx.Request] = []
    pcm = b"\x00\x01\x02\x03\x04\x05"

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _sarvam_audio_response(pcm)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tts = SarvamTextToSpeech(
        api_key="sarvam-secret",
        speaker="Anand",
        model="bulbul:v3",
        target_language_code="en-IN",
        client=client,
    )

    chunks = [
        chunk
        async for chunk in tts.synthesize(
            text="  Hello caller  ",
            audio_format=PlivoAudioFormat.L16_16KHZ,
        )
    ]

    assert chunks == [pcm]
    request = requests[0]
    assert str(request.url) == "https://api.sarvam.ai/text-to-speech"
    assert request.headers["api-subscription-key"] == "sarvam-secret"
    assert json.loads(request.content) == {
        "text": "Hello caller",
        "target_language_code": "en-IN",
        "model": "bulbul:v3",
        "speech_sample_rate": 16000,
        "output_audio_codec": "linear16",
        "pace": 1.0,
        "enable_preprocessing": True,
        "speaker": "anand",
        "temperature": 0.6,
    }
    await client.aclose()


@pytest.mark.anyio
async def test_sarvam_tts_omits_speaker_when_unset() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _sarvam_audio_response(b"\x10\x11")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tts = SarvamTextToSpeech(api_key="sarvam-secret", client=client)

    _ = [
        chunk
        async for chunk in tts.synthesize(
            text="Hi",
            audio_format=PlivoAudioFormat.L16_16KHZ,
        )
    ]

    assert "speaker" not in json.loads(requests[0].content)
    await client.aclose()


@pytest.mark.anyio
async def test_sarvam_tts_strips_wav_header_to_raw_pcm() -> None:
    pcm = b"\x20\x21\x22\x23"
    wav = (
        b"RIFF"
        + (36 + len(pcm)).to_bytes(4, "little")
        + b"WAVEfmt "
        + (16).to_bytes(4, "little")
        + (1).to_bytes(2, "little")
        + (1).to_bytes(2, "little")
        + (16000).to_bytes(4, "little")
        + (32000).to_bytes(4, "little")
        + (2).to_bytes(2, "little")
        + (16).to_bytes(2, "little")
        + b"data"
        + len(pcm).to_bytes(4, "little")
        + pcm
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return _sarvam_audio_response(wav)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tts = SarvamTextToSpeech(api_key="sarvam-secret", client=client)

    chunks = [
        chunk
        async for chunk in tts.synthesize(
            text="Hello",
            audio_format=PlivoAudioFormat.L16_16KHZ,
        )
    ]

    assert chunks == [pcm]
    await client.aclose()


@pytest.mark.anyio
async def test_sarvam_tts_rejects_incomplete_pcm16_sample() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return _sarvam_audio_response(b"\x00\x01\x02")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tts = SarvamTextToSpeech(api_key="sarvam-secret", client=client)

    with pytest.raises(SarvamTextToSpeechError, match="incomplete PCM16 sample"):
        _ = [
            chunk
            async for chunk in tts.synthesize(
                text="Hello",
                audio_format=PlivoAudioFormat.L16_16KHZ,
            )
        ]
    await client.aclose()


@pytest.mark.anyio
async def test_sarvam_tts_rejects_response_without_audio() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, json={"request_id": "req-1", "audios": []})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tts = SarvamTextToSpeech(api_key="sarvam-secret", client=client)

    with pytest.raises(SarvamTextToSpeechError, match="did not include audio"):
        _ = [
            chunk
            async for chunk in tts.synthesize(
                text="Hello",
                audio_format=PlivoAudioFormat.L16_16KHZ,
            )
        ]
    await client.aclose()


@pytest.mark.anyio
async def test_sarvam_tts_rejects_blank_text_without_calling_provider() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _sarvam_audio_response(b"\x00\x01")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tts = SarvamTextToSpeech(api_key="sarvam-secret", client=client)

    with pytest.raises(SarvamTextToSpeechError, match="must not be blank"):
        _ = [
            chunk
            async for chunk in tts.synthesize(
                text="   ",
                audio_format=PlivoAudioFormat.L16_16KHZ,
            )
        ]
    assert requests == []
    await client.aclose()


def test_sarvam_tts_rejects_non_l16_audio_format_eagerly() -> None:
    tts = SarvamTextToSpeech(api_key="sarvam-secret")

    async def drain() -> None:
        async for _chunk in tts.synthesize(
            text="Hello",
            audio_format=PlivoAudioFormat.MULAW_8KHZ,
        ):
            pass

    with pytest.raises(ValueError, match="PCM16 16kHz"):
        asyncio.run(drain())


def test_sarvam_tts_rejects_non_positive_timeout() -> None:
    with pytest.raises(ValueError, match="timeout_seconds must be positive"):
        SarvamTextToSpeech(api_key="sarvam-secret", timeout_seconds=0)


def test_sarvam_tts_read_timeout_defaults_below_overall_timeout() -> None:
    # The owned client bounds the read phase tighter than the overall timeout so a hung
    # TTS fails fast on a live call. Default caps inter-chunk read wait at <=10s.
    tts = SarvamTextToSpeech(api_key="sarvam-secret", timeout_seconds=30.0)
    assert tts._read_timeout_seconds == 10.0
    assert tts._client.timeout.read == 10.0

    short = SarvamTextToSpeech(api_key="sarvam-secret", timeout_seconds=4.0)
    assert short._read_timeout_seconds == 4.0


def test_sarvam_tts_honours_explicit_read_timeout() -> None:
    tts = SarvamTextToSpeech(
        api_key="sarvam-secret", timeout_seconds=30.0, read_timeout_seconds=6.0
    )
    assert tts._read_timeout_seconds == 6.0
    assert tts._client.timeout.read == 6.0


def test_sarvam_tts_rejects_non_positive_read_timeout() -> None:
    with pytest.raises(ValueError, match="read_timeout_seconds must be positive"):
        SarvamTextToSpeech(api_key="sarvam-secret", read_timeout_seconds=0)


class FakeSarvamSocket:
    def __init__(self, messages: list[str]) -> None:
        self._messages = messages
        self.sent: list[str] = []
        self.closed = False

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def _iter(self) -> AsyncIterator[str]:
        while self._messages:
            yield self._messages.pop(0)

    def __aiter__(self) -> AsyncIterator[str]:
        return self._iter()

    async def close(self) -> None:
        self.closed = True


def _sarvam_audio_event(pcm: bytes) -> str:
    return json.dumps(
        {
            "type": "audio",
            "data": {"content_type": "audio/pcm", "audio": base64.b64encode(pcm).decode("ascii")},
        }
    )


_SARVAM_FINAL_EVENT = json.dumps({"type": "event", "data": {"event_type": "final"}})


@pytest.mark.anyio
async def test_sarvam_streaming_yields_pcm_chunks_until_final(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    socket = FakeSarvamSocket(
        [_sarvam_audio_event(b"\x00\x01"), _sarvam_audio_event(b"\x02\x03"), _SARVAM_FINAL_EVENT]
    )

    async def fake_connect(url: str, **kwargs: Any) -> FakeSarvamSocket:
        captured["url"] = url
        captured["headers"] = kwargs.get("additional_headers")
        return socket

    monkeypatch.setattr("voice_agent.providers.sarvam.websockets.connect", fake_connect)
    tts = SarvamStreamingTextToSpeech(
        api_key="sarvam-secret",
        speaker="Anand",
        model="bulbul:v3",
        target_language_code="en-IN",
    )

    chunks = [
        chunk
        async for chunk in tts.synthesize(
            text="  Hello caller  ",
            audio_format=PlivoAudioFormat.L16_16KHZ,
        )
    ]

    assert chunks == [b"\x00\x01", b"\x02\x03"]
    assert captured["url"] == (
        "wss://api.sarvam.ai/text-to-speech/ws?model=bulbul:v3&send_completion_event=true"
    )
    assert captured["headers"] == {"api-subscription-key": "sarvam-secret"}
    assert socket.closed is False
    config = json.loads(socket.sent[0])
    assert config["type"] == "config"
    assert config["data"] == {
        "target_language_code": "en-IN",
        "output_audio_codec": "linear16",
        "speech_sample_rate": 16000,
        "min_buffer_size": 50,
        "pace": 1.0,
        "speaker": "anand",
        "temperature": 0.6,
    }
    assert json.loads(socket.sent[1]) == {"type": "text", "data": {"text": "Hello caller"}}
    assert json.loads(socket.sent[2]) == {"type": "flush"}
    await tts.close()
    assert socket.closed is True


@pytest.mark.anyio
async def test_sarvam_streaming_reuses_connection_across_syntheses(
    monkeypatch: Any,
) -> None:
    sockets = [
        FakeSarvamSocket(
            [
                _sarvam_audio_event(b"\x00\x01"),
                _SARVAM_FINAL_EVENT,
                _sarvam_audio_event(b"\x02\x03"),
                _SARVAM_FINAL_EVENT,
            ]
        )
    ]
    captured: dict[str, Any] = {"connect_count": 0}

    async def fake_connect(url: str, **kwargs: Any) -> FakeSarvamSocket:
        del url, kwargs
        captured["connect_count"] += 1
        return sockets[0]

    monkeypatch.setattr("voice_agent.providers.sarvam.websockets.connect", fake_connect)
    tts = SarvamStreamingTextToSpeech(api_key="sarvam-secret")

    first = [
        chunk
        async for chunk in tts.synthesize(
            text="First sentence.",
            audio_format=PlivoAudioFormat.L16_16KHZ,
        )
    ]
    second = [
        chunk
        async for chunk in tts.synthesize(
            text="Second sentence.",
            audio_format=PlivoAudioFormat.L16_16KHZ,
        )
    ]
    await tts.close()

    assert first == [b"\x00\x01"]
    assert second == [b"\x02\x03"]
    assert captured["connect_count"] == 1
    sent = [json.loads(message) for message in sockets[0].sent]
    assert [message["type"] for message in sent] == ["config", "text", "flush", "text", "flush"]
    assert sent[1] == {"type": "text", "data": {"text": "First sentence."}}
    assert sent[3] == {"type": "text", "data": {"text": "Second sentence."}}
    assert sockets[0].closed is True


@pytest.mark.anyio
async def test_sarvam_streaming_raises_on_error_event(monkeypatch: Any) -> None:
    socket = FakeSarvamSocket([json.dumps({"type": "error", "data": {"message": "bad speaker"}})])

    async def fake_connect(url: str, **kwargs: Any) -> FakeSarvamSocket:
        return socket

    monkeypatch.setattr("voice_agent.providers.sarvam.websockets.connect", fake_connect)
    tts = SarvamStreamingTextToSpeech(api_key="sarvam-secret")

    with pytest.raises(SarvamTextToSpeechError, match="bad speaker"):
        _ = [
            chunk
            async for chunk in tts.synthesize(
                text="Hello",
                audio_format=PlivoAudioFormat.L16_16KHZ,
            )
        ]
    assert socket.closed is True


@pytest.mark.anyio
async def test_sarvam_streaming_bounds_oversized_audio(monkeypatch: Any) -> None:
    big = _sarvam_audio_event(b"\x00\x01" * 64)
    socket = FakeSarvamSocket([big, big, _SARVAM_FINAL_EVENT])

    async def fake_connect(url: str, **kwargs: Any) -> FakeSarvamSocket:
        return socket

    monkeypatch.setattr("voice_agent.providers.sarvam.websockets.connect", fake_connect)
    tts = SarvamStreamingTextToSpeech(api_key="sarvam-secret", max_audio_bytes=200)

    with pytest.raises(SarvamTextToSpeechError, match="exceeded configured audio size limit"):
        _ = [
            chunk
            async for chunk in tts.synthesize(
                text="Hello",
                audio_format=PlivoAudioFormat.L16_16KHZ,
            )
        ]


@pytest.mark.anyio
async def test_sarvam_streaming_times_out_when_provider_stops_sending(monkeypatch: Any) -> None:
    class HangingSarvamSocket:
        def __init__(self) -> None:
            self.sent: list[str] = []
            self.closed = False

        async def send(self, message: str) -> None:
            self.sent.append(message)

        async def _iter(self) -> AsyncIterator[str]:
            await asyncio.Event().wait()
            yield _SARVAM_FINAL_EVENT

        def __aiter__(self) -> AsyncIterator[str]:
            return self._iter()

        async def close(self) -> None:
            self.closed = True

    socket = HangingSarvamSocket()

    async def fake_connect(url: str, **kwargs: Any) -> HangingSarvamSocket:
        del url, kwargs
        return socket

    monkeypatch.setattr("voice_agent.providers.sarvam.websockets.connect", fake_connect)
    tts = SarvamStreamingTextToSpeech(
        api_key="sarvam-secret",
        read_timeout_seconds=0.01,
    )

    with pytest.raises(SarvamTextToSpeechError, match="timed out"):
        _ = [
            chunk
            async for chunk in tts.synthesize(
                text="Hello",
                audio_format=PlivoAudioFormat.L16_16KHZ,
            )
        ]
    assert socket.closed is True


def test_sarvam_streaming_rejects_blank_text() -> None:
    tts = SarvamStreamingTextToSpeech(api_key="sarvam-secret")

    async def drain() -> None:
        async for _chunk in tts.synthesize(text="  ", audio_format=PlivoAudioFormat.L16_16KHZ):
            pass

    with pytest.raises(SarvamTextToSpeechError, match="must not be blank"):
        asyncio.run(drain())


def test_sarvam_streaming_rejects_buffer_below_provider_minimum() -> None:
    with pytest.raises(ValueError, match="min_buffer_size must be at least 50"):
        SarvamStreamingTextToSpeech(api_key="sarvam-secret", min_buffer_size=20)


def test_sarvam_streaming_rejects_non_l16_format() -> None:
    tts = SarvamStreamingTextToSpeech(api_key="sarvam-secret")

    async def drain() -> None:
        async for _chunk in tts.synthesize(text="Hi", audio_format=PlivoAudioFormat.MULAW_8KHZ):
            pass

    with pytest.raises(ValueError, match="PCM16 16kHz"):
        asyncio.run(drain())
