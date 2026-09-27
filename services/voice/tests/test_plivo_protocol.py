import base64

import pytest

from voice_agent.telephony.plivo import (
    PlivoAudioFormat,
    PlivoProtocolError,
    build_answer_xml,
    checkpoint_message,
    clear_audio_message,
    extract_start_identifiers,
    parse_plivo_message,
    play_audio_message,
)


def test_build_answer_xml_starts_bidirectional_l16_stream() -> None:
    xml = build_answer_xml(
        websocket_url="wss://voice.example.com/plivo/media",
        status_callback_url="https://voice.example.com/plivo/status",
        status_callback_method="POST",
        stream_timeout_seconds=1800,
        audio_format=PlivoAudioFormat.L16_16KHZ,
        noise_cancellation=False,
        noise_cancellation_level=85,
    )

    assert '<Response>' in xml
    assert 'bidirectional="true"' in xml
    assert 'keepCallAlive="true"' in xml
    assert 'contentType="audio/x-l16;rate=16000"' in xml
    assert 'streamTimeout="1800"' in xml
    assert 'statusCallbackUrl="https://voice.example.com/plivo/status"' in xml
    assert 'statusCallbackMethod="POST"' in xml
    assert "noiseCancellation" not in xml
    assert '>wss://voice.example.com/plivo/media</Stream>' in xml


def test_build_answer_xml_can_enable_plivo_noise_cancellation() -> None:
    xml = build_answer_xml(
        websocket_url="wss://voice.example.com/plivo/media",
        status_callback_url="https://voice.example.com/plivo/status",
        status_callback_method="POST",
        stream_timeout_seconds=1800,
        audio_format=PlivoAudioFormat.L16_16KHZ,
        noise_cancellation=True,
        noise_cancellation_level=90,
    )

    assert 'noiseCancellation="true"' in xml
    assert 'noiseCancellationLevel="90"' in xml


def test_build_answer_xml_rejects_invalid_noise_cancellation_level() -> None:
    with pytest.raises(PlivoProtocolError):
        build_answer_xml(
            websocket_url="wss://voice.example.com/plivo/media",
            status_callback_url="https://voice.example.com/plivo/status",
            status_callback_method="POST",
            stream_timeout_seconds=1800,
            audio_format=PlivoAudioFormat.L16_16KHZ,
            noise_cancellation=True,
            noise_cancellation_level=101,
        )


def test_play_audio_message_base64_encodes_pcm16_audio() -> None:
    payload = b"\x00\x01\x02\x03"

    message = play_audio_message(payload, PlivoAudioFormat.L16_16KHZ)

    assert message == {
        "event": "playAudio",
        "media": {
            "contentType": "audio/x-l16",
            "sampleRate": 16000,
            "payload": base64.b64encode(payload).decode("ascii"),
        },
    }


def test_play_audio_message_rejects_misaligned_l16_audio_frame() -> None:
    with pytest.raises(PlivoProtocolError, match="16-bit PCM"):
        play_audio_message(b"\x00", PlivoAudioFormat.L16_16KHZ)


def test_clear_audio_message_matches_plivo_interrupt_protocol() -> None:
    assert clear_audio_message() == {"event": "clearAudio"}


def test_parse_plivo_media_event_decodes_audio_payload() -> None:
    message = {
        "event": "media",
        "media": {
            "payload": base64.b64encode(b"caller audio").decode("ascii"),
        },
    }

    parsed = parse_plivo_message(message)

    assert parsed.kind == "media"
    assert parsed.audio == b"caller audio"


def test_parse_plivo_media_event_rejects_invalid_payload() -> None:
    with pytest.raises(PlivoProtocolError):
        parse_plivo_message({"event": "media", "media": {"payload": "not base64 @@"}})


def test_parse_plivo_media_event_rejects_empty_decoded_audio_payload() -> None:
    with pytest.raises(PlivoProtocolError, match="empty audio"):
        parse_plivo_message({"event": "media", "media": {"payload": ""}})


def test_parse_plivo_media_event_rejects_misaligned_l16_audio_frame() -> None:
    with pytest.raises(PlivoProtocolError, match="16-bit PCM"):
        parse_plivo_message(
            {
                "event": "media",
                "media": {
                    "payload": base64.b64encode(b"\x00").decode("ascii"),
                },
            },
            audio_format=PlivoAudioFormat.L16_16KHZ,
        )


@pytest.mark.parametrize(
    "message",
    [
        {"event": "dtmf"},
        {"event": "dtmf", "dtmf": {}},
        {"event": "dtmf", "dtmf": {"digit": ""}},
        {"event": "dtmf", "dtmf": {"digit": "12"}},
        {"event": "dtmf", "dtmf": {"digit": "x"}},
    ],
)
def test_parse_plivo_dtmf_event_rejects_invalid_digit(message: dict[str, object]) -> None:
    with pytest.raises(PlivoProtocolError, match="DTMF"):
        parse_plivo_message(message)


def test_extract_start_identifiers_trims_call_and_stream_ids() -> None:
    call_id, stream_id = extract_start_identifiers(
        {
            "start": {
                "call_uuid": " call-1 ",
                "stream_id": " stream-1 ",
            }
        }
    )

    assert call_id == "call-1"
    assert stream_id == "stream-1"


def test_extract_start_identifiers_rejects_blank_call_or_stream_ids() -> None:
    with pytest.raises(PlivoProtocolError):
        extract_start_identifiers(
            {
                "start": {
                    "call_uuid": "   ",
                    "stream_id": "stream-1",
                }
            }
        )
    with pytest.raises(PlivoProtocolError):
        extract_start_identifiers(
            {
                "start": {
                    "call_uuid": "call-1",
                    "stream_id": "   ",
                }
            }
        )


@pytest.mark.parametrize(
    "start_metadata",
    [
        {"call_uuid": "call/1", "stream_id": "stream-1"},
        {"call_uuid": "call 1", "stream_id": "stream-1"},
        {"call_uuid": "call\n1", "stream_id": "stream-1"},
        {"call_uuid": "a" * 129, "stream_id": "stream-1"},
        {"call_uuid": "call-1", "stream_id": "stream/1"},
        {"call_uuid": "call-1", "stream_id": "stream 1"},
        {"call_uuid": "call-1", "stream_id": "stream\n1"},
        {"call_uuid": "call-1", "stream_id": "a" * 129},
    ],
)
def test_extract_start_identifiers_rejects_unsafe_call_or_stream_ids(
    start_metadata: dict[str, str],
) -> None:
    with pytest.raises(PlivoProtocolError, match="identifier must be 1-128"):
        extract_start_identifiers({"start": start_metadata})


def test_checkpoint_message_format() -> None:
    assert checkpoint_message("turn-1:1") == {"event": "checkpoint", "name": "turn-1:1"}


def test_parse_played_stream_event_extracts_name() -> None:
    parsed = parse_plivo_message(
        {"event": "playedStream", "name": "turn-1:1", "streamId": "stream-1"}
    )
    assert parsed.kind == "playedStream"
    assert parsed.name == "turn-1:1"
    assert parsed.stream_id == "stream-1"


def test_parse_cleared_audio_event_is_passthrough() -> None:
    parsed = parse_plivo_message({"event": "clearedAudio", "streamId": "stream-1"})
    assert parsed.kind == "clearedAudio"
    assert parsed.stream_id == "stream-1"
