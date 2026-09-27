import base64
from collections.abc import Callable

import httpx
import pytest

from voice_agent.providers._retry import (
    compute_backoff,
    retry_after_seconds,
    stream_with_retry,
)
from voice_agent.providers.elevenlabs import ElevenLabsTextToSpeech
from voice_agent.providers.orchestrator import OrchestratorLLMClient
from voice_agent.providers.sarvam import SarvamTextToSpeech
from voice_agent.telephony.plivo import PlivoAudioFormat

Step = Callable[[httpx.Request], httpx.Response]


def _client(steps: list[Step]) -> tuple[httpx.AsyncClient, dict[str, int]]:
    """Build an AsyncClient whose MockTransport returns/raises the given steps in order
    (the last step repeats for any further calls). Returns the client and a call counter."""
    state = {"calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        index = min(state["calls"], len(steps) - 1)
        state["calls"] += 1
        return steps[index](request)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), state


def _ok(status: int = 200, **kwargs: object) -> Step:
    return lambda request: httpx.Response(status, **kwargs)  # type: ignore[arg-type]


def _connect_error() -> Step:
    def step(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    return step


class _RecordingSleeper:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


def test_compute_backoff_is_capped_exponential() -> None:
    assert compute_backoff(1, base_seconds=0.5, max_seconds=2.0) == 0.5
    assert compute_backoff(2, base_seconds=0.5, max_seconds=2.0) == 1.0
    assert compute_backoff(3, base_seconds=0.5, max_seconds=2.0) == 2.0
    assert compute_backoff(9, base_seconds=0.5, max_seconds=2.0) == 2.0


def test_retry_after_parses_delta_seconds_and_ignores_dates() -> None:
    delta = httpx.Response(503, headers={"retry-after": "1"})
    assert retry_after_seconds(delta, max_seconds=5.0) == 1.0
    capped = httpx.Response(503, headers={"retry-after": "99"})
    assert retry_after_seconds(capped, max_seconds=5.0) == 5.0
    http_date = httpx.Response(503, headers={"retry-after": "Wed, 21 Oct 2099 07:28:00 GMT"})
    assert retry_after_seconds(http_date, max_seconds=5.0) is None
    assert retry_after_seconds(httpx.Response(503), max_seconds=5.0) is None


async def test_stream_with_retry_retries_on_503_then_succeeds() -> None:
    client, state = _client([_ok(503), _ok(200, content=b"hello")])
    sleeper = _RecordingSleeper()
    async with client, stream_with_retry(
        client,
        "POST",
        "https://example.test/respond",
        max_attempts=2,
        backoff_seconds=0.5,
        max_backoff_seconds=2.0,
        sleep=sleeper,
    ) as response:
        body = await response.aread()
    assert body == b"hello"
    assert state["calls"] == 2
    assert sleeper.delays == [0.5]


async def test_stream_with_retry_retries_on_connect_error() -> None:
    client, state = _client([_connect_error(), _ok(200, content=b"ok")])
    sleeper = _RecordingSleeper()
    async with client, stream_with_retry(
        client,
        "POST",
        "https://example.test/respond",
        max_attempts=2,
        backoff_seconds=0.1,
        max_backoff_seconds=2.0,
        sleep=sleeper,
    ) as response:
        body = await response.aread()
    assert body == b"ok"
    assert state["calls"] == 2
    assert sleeper.delays == [0.1]


async def test_stream_with_retry_honors_retry_after_header() -> None:
    client, state = _client(
        [_ok(503, headers={"retry-after": "1"}), _ok(200, content=b"x")]
    )
    sleeper = _RecordingSleeper()
    async with client, stream_with_retry(
        client,
        "POST",
        "https://example.test/respond",
        max_attempts=2,
        backoff_seconds=0.5,
        max_backoff_seconds=10.0,
        sleep=sleeper,
    ) as response:
        await response.aread()
    assert sleeper.delays == [1.0]
    assert state["calls"] == 2


async def test_stream_with_retry_does_not_retry_non_retryable_status() -> None:
    client, state = _client([_ok(400, content=b"bad")])
    sleeper = _RecordingSleeper()
    async with client:
        with pytest.raises(httpx.HTTPStatusError):
            async with stream_with_retry(
                client,
                "POST",
                "https://example.test/respond",
                max_attempts=3,
                backoff_seconds=0.1,
                max_backoff_seconds=2.0,
                sleep=sleeper,
            ):
                pass
    assert state["calls"] == 1
    assert sleeper.delays == []


async def test_stream_with_retry_raises_after_exhausting_attempts() -> None:
    client, state = _client([_ok(503), _ok(503)])
    sleeper = _RecordingSleeper()
    async with client:
        with pytest.raises(httpx.HTTPStatusError):
            async with stream_with_retry(
                client,
                "POST",
                "https://example.test/respond",
                max_attempts=2,
                backoff_seconds=0.1,
                max_backoff_seconds=2.0,
                sleep=sleeper,
            ):
                pass
    assert state["calls"] == 2
    assert sleeper.delays == [0.1]


async def test_orchestrator_retries_transient_5xx() -> None:
    client, state = _client(
        [_ok(503), _ok(200, json={"reply": "Hello from orchestration."})]
    )
    llm = OrchestratorLLMClient(
        url="https://orchestrator.test/respond",
        client=client,
        retry_max_attempts=2,
        retry_backoff_seconds=0.001,
    )
    chunks = [chunk async for chunk in llm.generate_reply(call_id="call-1", user_text="hi")]
    assert "".join(chunks) == "Hello from orchestration."
    assert state["calls"] == 2


async def test_orchestrator_does_not_retry_by_default() -> None:
    client, state = _client([_ok(503), _ok(200, json={"reply": "unused"})])
    llm = OrchestratorLLMClient(url="https://orchestrator.test/respond", client=client)
    with pytest.raises(httpx.HTTPStatusError):
        async for _ in llm.generate_reply(call_id="call-1", user_text="hi"):
            pass
    assert state["calls"] == 1


async def test_sarvam_rest_retries_transient_5xx() -> None:
    pcm_b64 = base64.b64encode(b"\x01\x02\x03\x04").decode("ascii")
    client, state = _client([_ok(503), _ok(200, json={"audios": [pcm_b64]})])
    tts = SarvamTextToSpeech(
        api_key="key",
        client=client,
        retry_max_attempts=2,
        retry_backoff_seconds=0.001,
    )
    audio = b"".join(
        [
            chunk
            async for chunk in tts.synthesize(
                text="hello", audio_format=PlivoAudioFormat.L16_16KHZ
            )
        ]
    )
    assert audio == b"\x01\x02\x03\x04"
    assert state["calls"] == 2


async def test_elevenlabs_retries_transient_5xx() -> None:
    client, state = _client(
        [
            _ok(503),
            _ok(200, headers={"content-type": "audio/pcm"}, content=b"\x01\x02\x03\x04"),
        ]
    )
    tts = ElevenLabsTextToSpeech(
        api_key="key",
        voice_id="voice-1",
        model_id="eleven_flash_v2_5",
        client=client,
        retry_max_attempts=2,
        retry_backoff_seconds=0.001,
    )
    audio = b"".join(
        [
            chunk
            async for chunk in tts.synthesize(
                text="hello", audio_format=PlivoAudioFormat.L16_16KHZ
            )
        ]
    )
    assert audio == b"\x01\x02\x03\x04"
    assert state["calls"] == 2
