"""`WS /kiosk/audio`: tokens, the turn round trip, barge-in, errors, flags and latency.

The app is the real CareFlow voice app with the real turn loop and endpointing; only the models
are fakes (conftest.FakeSpeech) and the CareFlow API is mocked with respx.
"""

from __future__ import annotations

import json
import secrets
from collections.abc import Callable, Iterator
from typing import Any, Final

import anyio
import httpx
import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession
from starlette.websockets import WebSocketDisconnect

API = "http://api.test"
SESSION = "ses_voice"
FRAME = 640
NEXT_QUESTION = {
    "question_id": "q_symptom_onset_at_1",
    "text": "When did this start?",
    "key": "symptom_onset_at",
    "notice": "Thank you. A nurse will be with you shortly. Please stay here.",
}


def _reply(next_question: dict[str, Any] | None = NEXT_QUESTION, **extra: Any) -> dict[str, Any]:
    return {
        "turn_id": "turn_01",
        "assistant_text": "When did this start?" if next_question else "Thank you.",
        "done": next_question is None,
        "next_question": next_question,
        **extra,
    }


CONSENTED: Final = {
    "session_id": SESSION,
    "camera": False,
    "voice": True,
    "data_use": True,
    "consent_version": "2026-09-27.1",
    "recorded_at": "2026-09-27T09:00:00Z",
}


@pytest.fixture
def api() -> Iterator[respx.MockRouter]:
    """The CareFlow API; the session has consented to voice (M2-05)."""
    with respx.mock(base_url=API, assert_all_called=False) as router:
        router.get(f"/v1/sessions/{SESSION}/consent").respond(200, json=CONSENTED)
        yield router


def _start(token: str, **overrides: Any) -> str:
    message = {"type": "start", "session_id": SESSION, "token": token, "sample_rate": 16000}
    message.update(overrides)
    return json.dumps(message)


def _send_audio(ws: WebSocketTestSession, pcm: bytes) -> None:
    for offset in range(0, len(pcm), FRAME):
        ws.send_bytes(pcm[offset : offset + FRAME])


def _receive(ws: WebSocketTestSession, timeout: float = 5.0) -> dict[str, Any]:
    """`ws.receive()` with a deadline, so a regression fails instead of hanging."""
    stream = getattr(ws, "_send_rx", None)
    if stream is None:
        return dict(ws.receive())

    async def receive() -> dict[str, Any]:
        with anyio.fail_after(timeout):
            return dict(await stream.receive())

    return ws.portal.call(receive)


def _messages_until(
    ws: WebSocketTestSession, done: Callable[[dict[str, Any]], bool]
) -> tuple[list[dict[str, Any]], int]:
    """JSON messages up to and including the first `done` one, and the TTS bytes in between."""
    messages: list[dict[str, Any]] = []
    audio = 0
    while True:
        message = _receive(ws)
        if message.get("bytes") is not None:
            audio += len(message["bytes"])
            continue
        if message["type"] == "websocket.close":
            raise AssertionError(f"closed early with {message.get('code')}: {messages}")
        parsed = json.loads(message["text"])
        messages.append(parsed)
        if done(parsed):
            return messages, audio


def _close_code(ws: WebSocketTestSession) -> int:
    try:
        message = _receive(ws)
    except WebSocketDisconnect as closed:
        return closed.code
    assert message["type"] == "websocket.close", message
    return int(message.get("code", 1000))


# --- the start message ---


@pytest.mark.parametrize(
    "variant",
    ["garbage", "expired", "wrong_secret", "nurse", "other_session", "bad_rate", "not_json"],
)
def test_a_bad_start_closes_with_1008(
    variant: str,
    make_kiosk_app: Callable[..., FastAPI],
    patient_token: Callable[..., str],
    api: respx.MockRouter,
) -> None:
    starts = {
        "garbage": _start("not-a-jwt"),
        "expired": _start(patient_token(expires_in=-60)),
        "wrong_secret": _start(patient_token(secret=secrets.token_urlsafe(32))),
        "nurse": _start(patient_token(role="nurse")),
        "other_session": _start(patient_token("ses_other")),
        "bad_rate": _start(patient_token(), sample_rate=8000),
        "not_json": "hello",
    }
    with TestClient(make_kiosk_app()) as client, client.websocket_connect("/kiosk/audio") as ws:
        ws.send_text(starts[variant])
        assert _close_code(ws) == 1008


def test_no_start_message_within_the_timeout_closes_with_1008(
    make_kiosk_app: Callable[..., FastAPI],
) -> None:
    app = make_kiosk_app(kiosk_start_timeout_seconds=0.2)
    with TestClient(app) as client, client.websocket_connect("/kiosk/audio") as ws:
        assert _close_code(ws) == 1008


def test_audio_before_the_start_message_closes_with_1008(
    make_kiosk_app: Callable[..., FastAPI],
) -> None:
    with TestClient(make_kiosk_app()) as client, client.websocket_connect("/kiosk/audio") as ws:
        ws.send_bytes(bytes(FRAME))
        assert _close_code(ws) == 1008


# --- a turn ---


def test_fixture_frames_become_a_final_transcript_a_question_and_tts_frames(
    make_kiosk_app: Callable[..., FastAPI],
    fake_speech: Any,
    patient_token: Callable[..., str],
    chest_pain_pcm: bytes,
    api: respx.MockRouter,
) -> None:
    route = api.post(f"/v1/sessions/{SESSION}/turns").respond(200, json=_reply())
    speech = fake_speech()
    token = patient_token()
    with (
        TestClient(make_kiosk_app(speech=speech)) as client,
        client.websocket_connect("/kiosk/audio") as ws,
    ):
        ws.send_text(_start(token))
        _send_audio(ws, chest_pain_pcm)
        messages, audio = _messages_until(ws, lambda m: m["type"] == "tts_end")
        ws.send_text(json.dumps({"type": "stop"}))

    assert messages == [
        {"type": "final", "text": "I have chest pain since this morning."},
        {
            "type": "question",
            "text": f"{NEXT_QUESTION['notice']} {NEXT_QUESTION['text']}",
            "question": NEXT_QUESTION,
            "done": False,
        },
        {"type": "tts_start", "sample_rate": 24000},
        {"type": "tts_end", "interrupted": False},
    ]
    assert audio == int(0.6 * 24000) * 2  # the fake question audio, PCM16 at 24 kHz
    [call] = route.calls
    assert json.loads(call.request.content) == {
        "input_mode": "voice",
        "text": "I have chest pain since this morning.",
        "language": "en",
    }
    assert call.request.headers["authorization"] == f"Bearer {token}"
    [engine] = speech.tts_engines
    assert engine.spoken == [f"{NEXT_QUESTION['notice']} {NEXT_QUESTION['text']}"]
    assert [fmt.sample_rate for fmt in engine.formats] == [24000]


def test_speech_during_the_question_barges_in_and_becomes_the_next_turn(
    make_kiosk_app: Callable[..., FastAPI],
    fake_speech: Any,
    patient_token: Callable[..., str],
    chest_pain_pcm: bytes,
    api: respx.MockRouter,
) -> None:
    route = api.post(f"/v1/sessions/{SESSION}/turns").respond(200, json=_reply())
    speech = fake_speech(tts_seconds=5.0)  # a long question, still playing when the patient talks
    with (
        TestClient(make_kiosk_app(speech=speech)) as client,
        client.websocket_connect("/kiosk/audio") as ws,
    ):
        ws.send_text(_start(patient_token()))
        _send_audio(ws, chest_pain_pcm)
        _messages_until(ws, lambda m: m["type"] == "tts_start")
        _send_audio(ws, chest_pain_pcm)  # the patient answers over the question
        messages, _ = _messages_until(ws, lambda m: m["type"] == "question")
        ws.send_text(json.dumps({"type": "stop"}))

    assert {"type": "tts_end", "interrupted": True} in messages
    assert {"type": "final", "text": "I have chest pain since this morning."} in messages
    assert len(route.calls) == 2  # the interrupting speech was transcribed as the next turn


def test_a_done_reply_ends_the_questions(
    make_kiosk_app: Callable[..., FastAPI],
    patient_token: Callable[..., str],
    chest_pain_pcm: bytes,
    api: respx.MockRouter,
) -> None:
    api.post(f"/v1/sessions/{SESSION}/turns").respond(200, json=_reply(None))
    with TestClient(make_kiosk_app()) as client, client.websocket_connect("/kiosk/audio") as ws:
        ws.send_text(_start(patient_token()))
        _send_audio(ws, chest_pain_pcm)
        messages, _ = _messages_until(ws, lambda m: m["type"] == "question")
    assert messages[-1] == {
        "type": "question",
        "text": "Thank you.",
        "question": None,
        "done": True,
    }


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (401, "unauthorized"),
        (404, "session_not_found"),
        (409, "conflict"),
        (503, "api_unavailable"),
    ],
)
def test_an_api_failure_is_an_error_message_and_the_session_stays_open(
    status: int,
    code: str,
    make_kiosk_app: Callable[..., FastAPI],
    patient_token: Callable[..., str],
    chest_pain_pcm: bytes,
    api: respx.MockRouter,
) -> None:
    api.post(f"/v1/sessions/{SESSION}/turns").respond(status, json={"title": "x", "status": status})
    with TestClient(make_kiosk_app()) as client, client.websocket_connect("/kiosk/audio") as ws:
        ws.send_text(_start(patient_token()))
        _send_audio(ws, chest_pain_pcm)
        messages, _ = _messages_until(ws, lambda m: m["type"] == "error")
        ws.send_text(json.dumps({"type": "unknown"}))
        assert ws.receive_json() == {"type": "error", "code": "bad_message"}
    assert messages[-1] == {"type": "error", "code": code}


def test_an_unreachable_api_is_api_unavailable(
    make_kiosk_app: Callable[..., FastAPI],
    patient_token: Callable[..., str],
    chest_pain_pcm: bytes,
    api: respx.MockRouter,
) -> None:
    api.post(f"/v1/sessions/{SESSION}/turns").mock(side_effect=httpx.ConnectError("refused"))
    with TestClient(make_kiosk_app()) as client, client.websocket_connect("/kiosk/audio") as ws:
        ws.send_text(_start(patient_token()))
        _send_audio(ws, chest_pain_pcm)
        messages, _ = _messages_until(ws, lambda m: m["type"] == "error")
    assert messages[-1] == {"type": "error", "code": "api_unavailable"}


def test_oversized_or_odd_frames_are_refused(
    make_kiosk_app: Callable[..., FastAPI],
    patient_token: Callable[..., str],
    api: respx.MockRouter,
) -> None:
    with TestClient(make_kiosk_app()) as client, client.websocket_connect("/kiosk/audio") as ws:
        ws.send_text(_start(patient_token()))
        ws.send_bytes(bytes(16002))
        assert ws.receive_json() == {"type": "error", "code": "bad_frame"}
        ws.send_bytes(bytes(641))
        assert ws.receive_json() == {"type": "error", "code": "bad_frame"}


# --- flags, latency, disk ---


def test_the_plivo_routes_are_off_unless_the_flag_is_set(
    make_kiosk_app: Callable[..., FastAPI],
) -> None:
    with TestClient(make_kiosk_app()) as client:
        assert client.post("/plivo/answer").status_code == 404
        assert client.post("/calls/outbound", json={}).status_code == 404
        assert client.post("/preview/turn", json={}).status_code == 404
        assert client.get("/healthz").status_code == 200
    enabled = make_kiosk_app(plivo_enabled=True, public_base_url="https://voice.example.com")
    with TestClient(enabled) as client:
        assert client.post("/plivo/answer").status_code != 404


def test_the_flag_needs_a_public_base_url(make_kiosk_app: Callable[..., FastAPI]) -> None:
    with pytest.raises(ValueError, match="public_base_url is required"):
        make_kiosk_app(plivo_enabled=True)


def test_latency_summary_reports_kiosk_turn_p50_and_p95(
    make_kiosk_app: Callable[..., FastAPI],
    patient_token: Callable[..., str],
    chest_pain_pcm: bytes,
    api: respx.MockRouter,
) -> None:
    api.post(f"/v1/sessions/{SESSION}/turns").respond(200, json=_reply())
    with TestClient(make_kiosk_app()) as client:
        with client.websocket_connect("/kiosk/audio") as ws:
            ws.send_text(_start(patient_token()))
            _send_audio(ws, chest_pain_pcm)
            _messages_until(ws, lambda m: m["type"] == "tts_end")
        summary = client.get("/latency/summary").json()["kiosk"]
    assert summary["turn_count"] == 1
    for stage in (
        "speech_end_to_transcript",
        "transcript_to_api_response",
        "api_response_to_first_tts_byte",
        "total",
    ):
        assert summary["stages"][stage]["p50_ms"] is not None, stage
        assert summary["stages"][stage]["p95_ms"] is not None, stage


def test_a_full_kiosk_session_opens_no_file_for_writing(
    make_kiosk_app: Callable[..., FastAPI],
    patient_token: Callable[..., str],
    chest_pain_pcm: bytes,
    api: respx.MockRouter,
    disk_writes: Any,
) -> None:
    api.post(f"/v1/sessions/{SESSION}/turns").respond(200, json=_reply())
    log_dir = None  # the service logs to stderr: no file may be written at all
    with TestClient(make_kiosk_app()) as client:
        with disk_writes, client.websocket_connect("/kiosk/audio") as ws:
            ws.send_text(_start(patient_token()))
            _send_audio(ws, chest_pain_pcm)
            _messages_until(ws, lambda m: m["type"] == "tts_end")
            ws.send_text(json.dumps({"type": "stop"}))
    outside_logs = [
        path for path in disk_writes.paths if log_dir is None or not path.startswith(log_dir)
    ]
    assert outside_logs == []
