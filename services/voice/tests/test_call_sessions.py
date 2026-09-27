import base64
import hashlib
import hmac
import json
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from voice_agent.app import create_app
from voice_agent.config.settings import Settings
from voice_agent.security.plivo_signature import build_v3_signature
from voice_agent.security.stream_token import build_stream_token
from voice_agent.storage.call_events import InMemoryCallEventStore
from voice_agent.storage.call_sessions import (
    CallSessionStoreError,
    CallSessionUpdate,
    InMemoryCallSessionStore,
    RedisCallSessionStore,
    build_call_session_store,
)
from voice_agent.telephony.plivo_client import PlivoCallClientError, PlivoCallCreateResult

CONTROL_API_KEY = "control-secret-0123456789abcdef012345"
AUTHORIZATION = f"Bearer {CONTROL_API_KEY}"


def test_outbound_call_route_records_request_session_and_exposes_control_lookup() -> None:
    session_store = InMemoryCallSessionStore()
    app = create_app(
        _local_settings(control_api_key=CONTROL_API_KEY, plivo_from_number="+14155550100"),
        call_event_store=InMemoryCallEventStore(),
        call_session_store=session_store,
        outbound_call_client=FakeOutboundCallClient(),
    )
    client = TestClient(app)

    create_response = client.post(
        "/calls/outbound",
        headers={
            "Authorization": AUTHORIZATION,
            "Idempotency-Key": "workflow-call-1",
        },
        json={"to": "+919876543210"},
    )
    lookup_response = client.get(
        "/calls/request-1",
        headers={"Authorization": AUTHORIZATION},
    )

    assert create_response.status_code == 202
    assert lookup_response.status_code == 200
    session = lookup_response.json()
    assert session["request_uuid"] == "request-1"
    assert session["call_id"] is None
    assert session["direction"] == "outbound"
    assert session["from_number"] == "+14155550100"
    assert session["to_number"] == "+919876543210"
    assert session["idempotency_key"] == "workflow-call-1"
    assert session["api_id"] == "api-1"
    assert session["message"] == "call fired"
    assert session["status"] == "requested"
    assert session["event"] == "outbound_requested"
    assert isinstance(session["created_at"], str)
    assert isinstance(session["updated_at"], str)


def test_outbound_call_route_trims_destination_before_plivo_and_session_storage() -> None:
    call_client = FakeOutboundCallClient()
    session_store = InMemoryCallSessionStore()
    app = create_app(
        _local_settings(control_api_key=CONTROL_API_KEY, plivo_from_number="+14155550100"),
        call_event_store=InMemoryCallEventStore(),
        call_session_store=session_store,
        outbound_call_client=call_client,
    )
    client = TestClient(app)

    create_response = client.post(
        "/calls/outbound",
        headers={
            "Authorization": AUTHORIZATION,
            "Idempotency-Key": "workflow-call-1",
        },
        json={"to": " +919876543210 "},
    )
    lookup_response = client.get(
        "/calls/request-1",
        headers={"Authorization": AUTHORIZATION},
    )

    assert create_response.status_code == 202
    assert call_client.calls == ["+919876543210"]
    assert lookup_response.status_code == 200
    assert lookup_response.json()["to_number"] == "+919876543210"


def test_outbound_call_route_requires_idempotency_key_when_outbound_is_configured() -> None:
    call_client = FakeOutboundCallClient()
    app = create_app(
        _local_settings(control_api_key=CONTROL_API_KEY, plivo_from_number="+14155550100"),
        call_event_store=InMemoryCallEventStore(),
        call_session_store=InMemoryCallSessionStore(),
        outbound_call_client=call_client,
    )
    client = TestClient(app)

    response = client.post(
        "/calls/outbound",
        headers={"Authorization": AUTHORIZATION},
        json={"to": "+919876543210"},
    )

    assert response.status_code == 400
    assert call_client.calls == []


def test_outbound_call_route_trims_idempotency_key_before_reservation() -> None:
    call_client = FakeOutboundCallClient()
    app = create_app(
        _local_settings(control_api_key=CONTROL_API_KEY, plivo_from_number="+14155550100"),
        call_event_store=InMemoryCallEventStore(),
        call_session_store=InMemoryCallSessionStore(),
        outbound_call_client=call_client,
    )
    client = TestClient(app)

    first_response = client.post(
        "/calls/outbound",
        headers={
            "Authorization": AUTHORIZATION,
            "Idempotency-Key": " workflow-call-1 ",
        },
        json={"to": "+919876543210"},
    )
    second_response = client.post(
        "/calls/outbound",
        headers={
            "Authorization": AUTHORIZATION,
            "Idempotency-Key": "workflow-call-1",
        },
        json={"to": "+919876543210"},
    )
    lookup_response = client.get(
        "/calls/workflow-call-1",
        headers={"Authorization": AUTHORIZATION},
    )

    assert first_response.status_code == 202
    assert second_response.status_code == 200
    assert second_response.json() == first_response.json()
    assert call_client.calls == ["+919876543210"]
    assert lookup_response.status_code == 200
    assert lookup_response.json()["idempotency_key"] == "workflow-call-1"


def test_outbound_call_route_reuses_matching_idempotency_key_without_second_plivo_call() -> None:
    call_client = FakeOutboundCallClient()
    app = create_app(
        _local_settings(control_api_key=CONTROL_API_KEY, plivo_from_number="+14155550100"),
        call_event_store=InMemoryCallEventStore(),
        call_session_store=InMemoryCallSessionStore(),
        outbound_call_client=call_client,
    )
    client = TestClient(app)
    headers = {
        "Authorization": AUTHORIZATION,
        "Idempotency-Key": "workflow-call-1",
    }

    first_response = client.post(
        "/calls/outbound",
        headers=headers,
        json={"to": "+919876543210"},
    )
    second_response = client.post(
        "/calls/outbound",
        headers=headers,
        json={"to": "+919876543210"},
    )

    assert first_response.status_code == 202
    assert second_response.status_code == 200
    assert second_response.json() == first_response.json()
    assert len(call_client.calls) == 1


def test_outbound_call_route_rejects_idempotency_key_reuse_for_different_destination() -> None:
    call_client = FakeOutboundCallClient()
    app = create_app(
        _local_settings(control_api_key=CONTROL_API_KEY, plivo_from_number="+14155550100"),
        call_event_store=InMemoryCallEventStore(),
        call_session_store=InMemoryCallSessionStore(),
        outbound_call_client=call_client,
    )
    client = TestClient(app)
    headers = {
        "Authorization": AUTHORIZATION,
        "Idempotency-Key": "workflow-call-1",
    }

    first_response = client.post(
        "/calls/outbound",
        headers=headers,
        json={"to": "+919876543210"},
    )
    second_response = client.post(
        "/calls/outbound",
        headers=headers,
        json={"to": "+919999999999"},
    )

    assert first_response.status_code == 202
    assert second_response.status_code == 409
    assert len(call_client.calls) == 1


def test_outbound_call_route_marks_idempotency_key_failed_when_plivo_request_fails() -> None:
    app = create_app(
        _local_settings(control_api_key=CONTROL_API_KEY, plivo_from_number="+14155550100"),
        call_event_store=InMemoryCallEventStore(),
        call_session_store=InMemoryCallSessionStore(),
        outbound_call_client=FailingOutboundCallClient(),
    )
    client = TestClient(app)

    response = client.post(
        "/calls/outbound",
        headers={
            "Authorization": AUTHORIZATION,
            "Idempotency-Key": "workflow-call-failed",
        },
        json={"to": "+919876543210"},
    )
    session_response = client.get(
        "/calls/workflow-call-failed",
        headers={"Authorization": AUTHORIZATION},
    )

    assert response.status_code == 502
    assert session_response.status_code == 200
    session = session_response.json()
    assert session["status"] == "failed"
    assert session["event"] == "outbound_create_failed"


def test_outbound_call_route_replays_failed_idempotency_key_without_second_plivo_call() -> None:
    call_client = RecoveringOutboundCallClient()
    app = create_app(
        _local_settings(control_api_key=CONTROL_API_KEY, plivo_from_number="+14155550100"),
        call_event_store=InMemoryCallEventStore(),
        call_session_store=InMemoryCallSessionStore(),
        outbound_call_client=call_client,
    )
    client = TestClient(app)
    headers = {
        "Authorization": AUTHORIZATION,
        "Idempotency-Key": "workflow-call-failed",
    }

    first_response = client.post(
        "/calls/outbound",
        headers=headers,
        json={"to": "+919876543210"},
    )
    second_response = client.post(
        "/calls/outbound",
        headers=headers,
        json={"to": "+919876543210"},
    )

    assert first_response.status_code == 502
    assert second_response.status_code == 409
    assert second_response.json()["detail"] == (
        "Outbound call creation previously failed for this Idempotency-Key. "
        "Use a new Idempotency-Key to retry."
    )
    assert call_client.calls == ["+919876543210"]


def test_outbound_call_route_marks_idempotency_key_failed_when_plivo_response_is_invalid() -> None:
    app = create_app(
        _local_settings(control_api_key=CONTROL_API_KEY, plivo_from_number="+14155550100"),
        call_event_store=InMemoryCallEventStore(),
        call_session_store=InMemoryCallSessionStore(),
        outbound_call_client=MalformedOutboundCallClient(),
    )
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/calls/outbound",
        headers={
            "Authorization": AUTHORIZATION,
            "Idempotency-Key": "workflow-call-malformed",
        },
        json={"to": "+919876543210"},
    )
    session_response = client.get(
        "/calls/workflow-call-malformed",
        headers={"Authorization": AUTHORIZATION},
    )

    assert response.status_code == 502
    assert session_response.status_code == 200
    session = session_response.json()
    assert session["status"] == "failed"
    assert session["event"] == "outbound_create_failed"


def test_call_session_lookup_requires_control_auth_when_configured() -> None:
    app = create_app(
        _local_settings(control_api_key=CONTROL_API_KEY),
        call_event_store=InMemoryCallEventStore(),
        call_session_store=InMemoryCallSessionStore(),
    )
    client = TestClient(app)

    response = client.get("/calls/request-1")

    assert response.status_code == 401


def test_call_session_lookup_rejects_oversized_identifier_before_storage_access() -> None:
    app = create_app(
        _local_settings(control_api_key=CONTROL_API_KEY),
        call_event_store=InMemoryCallEventStore(),
        call_session_store=RejectingGetCallSessionStore(),
    )
    client = TestClient(app)

    response = client.get(
        f"/calls/{'x' * 129}",
        headers={"Authorization": AUTHORIZATION},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == (
        "Call identifier must be 1-128 characters using letters, numbers, "
        "dot, underscore, colon, or hyphen."
    )


def test_hangup_call_route_requires_control_auth_when_configured() -> None:
    app = create_app(
        _local_settings(control_api_key=CONTROL_API_KEY),
        call_event_store=InMemoryCallEventStore(),
        call_session_store=InMemoryCallSessionStore(),
        outbound_call_client=FakeOutboundCallClient(),
    )
    client = TestClient(app)

    response = client.delete("/calls/call-1")

    assert response.status_code == 401


def test_hangup_call_route_rejects_oversized_identifier_before_storage_or_plivo() -> None:
    call_client = FakeOutboundCallClient()
    app = create_app(
        _local_settings(control_api_key=CONTROL_API_KEY),
        call_event_store=InMemoryCallEventStore(),
        call_session_store=RejectingGetCallSessionStore(),
        outbound_call_client=call_client,
    )
    client = TestClient(app)

    response = client.delete(
        f"/calls/{'x' * 129}",
        headers={"Authorization": AUTHORIZATION},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == (
        "Call identifier must be 1-128 characters using letters, numbers, "
        "dot, underscore, colon, or hyphen."
    )
    assert call_client.hangups == []


def test_hangup_call_route_rejects_unknown_session_without_calling_plivo() -> None:
    call_client = FakeOutboundCallClient()
    app = create_app(
        _local_settings(control_api_key=CONTROL_API_KEY),
        call_event_store=InMemoryCallEventStore(),
        call_session_store=InMemoryCallSessionStore(),
        outbound_call_client=call_client,
    )
    client = TestClient(app)

    response = client.delete(
        "/calls/call-1",
        headers={"Authorization": AUTHORIZATION},
    )

    assert response.status_code == 404
    assert call_client.hangups == []


def test_hangup_call_route_falls_back_to_plivo_identifier_when_session_is_missing() -> None:
    call_client = FakeOutboundCallClient()
    app = create_app(
        _local_settings(control_api_key=CONTROL_API_KEY),
        call_event_store=InMemoryCallEventStore(),
        call_session_store=InMemoryCallSessionStore(),
        outbound_call_client=call_client,
    )
    client = TestClient(app)
    identifier = str(uuid.uuid4())

    response = client.delete(
        f"/calls/{identifier}",
        headers={"Authorization": AUTHORIZATION},
    )

    assert response.status_code == 202
    assert response.json() == {
        "status": "hangup_requested",
        "identifier": identifier,
        "plivo_identifier": identifier,
    }
    assert call_client.hangups == [identifier]


def test_hangup_call_route_deletes_plivo_call_and_updates_session_by_call_id() -> None:
    call_client = FakeOutboundCallClient()
    session_store = InMemoryCallSessionStore()
    app = create_app(
        _local_settings(control_api_key=CONTROL_API_KEY),
        call_event_store=InMemoryCallEventStore(),
        call_session_store=session_store,
        outbound_call_client=call_client,
    )
    client = TestClient(app)
    client.post(
        "/plivo/answer",
        json={
            "CallUUID": "call-1",
            "RequestUUID": "request-1",
            "CallStatus": "in-progress",
            "Event": "StartApp",
        },
    )

    response = client.delete(
        "/calls/call-1",
        headers={"Authorization": AUTHORIZATION},
    )
    session_response = client.get(
        "/calls/call-1",
        headers={"Authorization": AUTHORIZATION},
    )

    assert response.status_code == 202
    assert response.json() == {
        "status": "hangup_requested",
        "identifier": "call-1",
        "plivo_identifier": "call-1",
    }
    assert call_client.hangups == ["call-1"]
    session = session_response.json()
    assert session["status"] == "hangup_requested"
    assert session["event"] == "control_hangup_requested"


def test_hangup_call_route_rejects_terminal_session_without_calling_plivo() -> None:
    call_client = FakeOutboundCallClient()
    app = create_app(
        _local_settings(control_api_key=CONTROL_API_KEY),
        call_event_store=InMemoryCallEventStore(),
        call_session_store=InMemoryCallSessionStore(),
        outbound_call_client=call_client,
    )
    client = TestClient(app)
    client.post(
        "/plivo/status",
        json={
            "CallUUID": "call-1",
            "RequestUUID": "request-1",
            "CallStatus": "completed",
            "Event": "Hangup",
        },
    )

    response = client.delete(
        "/calls/call-1",
        headers={"Authorization": AUTHORIZATION},
    )
    session_response = client.get(
        "/calls/call-1",
        headers={"Authorization": AUTHORIZATION},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "Tracked call session is already terminal."
    assert call_client.hangups == []
    session = session_response.json()
    assert session["status"] == "completed"
    assert session["event"] == "Hangup"


def test_hangup_call_route_rejects_terminal_session_without_outbound_client() -> None:
    app = create_app(
        _local_settings(control_api_key=CONTROL_API_KEY),
        call_event_store=InMemoryCallEventStore(),
        call_session_store=InMemoryCallSessionStore(),
    )
    client = TestClient(app)
    client.post(
        "/plivo/status",
        json={
            "CallUUID": "call-1",
            "CallStatus": "completed",
            "Event": "Hangup",
        },
    )

    response = client.delete(
        "/calls/call-1",
        headers={"Authorization": AUTHORIZATION},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "Tracked call session is already terminal."


def test_hangup_call_route_uses_request_uuid_when_call_uuid_is_not_available() -> None:
    call_client = FakeOutboundCallClient()
    app = create_app(
        _local_settings(control_api_key=CONTROL_API_KEY, plivo_from_number="+14155550100"),
        call_event_store=InMemoryCallEventStore(),
        call_session_store=InMemoryCallSessionStore(),
        outbound_call_client=call_client,
    )
    client = TestClient(app)
    client.post(
        "/calls/outbound",
        headers={
            "Authorization": AUTHORIZATION,
            "Idempotency-Key": "workflow-call-1",
        },
        json={"to": "+919876543210"},
    )

    response = client.delete(
        "/calls/request-1",
        headers={"Authorization": AUTHORIZATION},
    )

    assert response.status_code == 202
    assert response.json()["plivo_identifier"] == "request-1"
    assert call_client.hangups == ["request-1"]


def test_hangup_call_route_marks_session_failed_when_plivo_hangup_request_fails() -> None:
    app = create_app(
        _local_settings(control_api_key=CONTROL_API_KEY),
        call_event_store=InMemoryCallEventStore(),
        call_session_store=InMemoryCallSessionStore(),
        outbound_call_client=FailingHangupCallClient(),
    )
    client = TestClient(app)
    client.post(
        "/plivo/answer",
        json={
            "CallUUID": "call-1",
            "RequestUUID": "request-1",
            "CallStatus": "in-progress",
            "Event": "StartApp",
        },
    )

    response = client.delete(
        "/calls/call-1",
        headers={"Authorization": AUTHORIZATION},
    )
    session_response = client.get(
        "/calls/call-1",
        headers={"Authorization": AUTHORIZATION},
    )

    assert response.status_code == 502
    session = session_response.json()
    assert session["status"] == "hangup_failed"
    assert session["event"] == "control_hangup_failed"


def test_hangup_call_route_marks_session_failed_when_plivo_hangup_response_is_invalid() -> None:
    app = create_app(
        _local_settings(control_api_key=CONTROL_API_KEY),
        call_event_store=InMemoryCallEventStore(),
        call_session_store=InMemoryCallSessionStore(),
        outbound_call_client=InvalidHangupCallClient(),
    )
    client = TestClient(app, raise_server_exceptions=False)
    client.post(
        "/plivo/answer",
        json={
            "CallUUID": "call-1",
            "RequestUUID": "request-1",
            "CallStatus": "in-progress",
            "Event": "StartApp",
        },
    )

    response = client.delete(
        "/calls/call-1",
        headers={"Authorization": AUTHORIZATION},
    )
    session_response = client.get(
        "/calls/call-1",
        headers={"Authorization": AUTHORIZATION},
    )

    assert response.status_code == 502
    assert response.json()["detail"] == "Plivo returned an invalid hangup response."
    session = session_response.json()
    assert session["status"] == "hangup_failed"
    assert session["event"] == "control_hangup_failed"


def test_answer_and_status_callbacks_merge_session_by_request_and_call_ids() -> None:
    session_store = InMemoryCallSessionStore()
    app = create_app(
        _local_settings(),
        call_event_store=InMemoryCallEventStore(),
        call_session_store=session_store,
    )
    client = TestClient(app)

    answer_response = client.post(
        "/plivo/answer",
        data={
            "CallUUID": "call-1",
            "RequestUUID": "request-1",
            "From": "+14155550100",
            "To": "+919876543210",
            "Direction": "outbound",
            "CallStatus": "in-progress",
            "Event": "StartApp",
        },
    )
    status_response = client.post(
        "/plivo/status",
        json={
            "CallUUID": "call-1",
            "RequestUUID": "request-1",
            "CallStatus": "completed",
            "Event": "Hangup",
        },
    )

    assert answer_response.status_code == 200
    assert status_response.status_code == 200
    by_call = client.get("/calls/call-1")
    by_request = client.get("/calls/request-1")

    assert by_call.status_code == 200
    assert by_request.status_code == 200
    assert by_call.json() == by_request.json()
    session = by_call.json()
    assert session["call_id"] == "call-1"
    assert session["request_uuid"] == "request-1"
    assert session["direction"] == "outbound"
    assert session["from_number"] == "+14155550100"
    assert session["to_number"] == "+919876543210"
    assert session["status"] == "completed"
    assert session["event"] == "Hangup"
    assert session["last_event"] == {
        "CallUUID": "call-1",
        "RequestUUID": "request-1",
        "CallStatus": "completed",
        "Event": "Hangup",
    }


def test_media_start_records_stream_id_for_call_session() -> None:
    session_store = InMemoryCallSessionStore()
    app = create_app(
        _local_settings(),
        call_event_store=InMemoryCallEventStore(),
        call_session_store=session_store,
    )
    client = TestClient(app)

    with client.websocket_connect("/plivo/media") as websocket:
        websocket.send_json(
            {
                "event": "start",
                "start": {
                    "call_uuid": "call-1",
                    "stream_id": "stream-1",
                },
            }
        )
        response = client.get("/calls/call-1")
        websocket.send_json({"event": "stop"})

    assert response.status_code == 200
    session = response.json()
    assert session["call_id"] == "call-1"
    assert session["stream_id"] == "stream-1"
    assert session["status"] == "media_started"
    assert session["event"] == "media_start"


def test_media_stop_updates_call_session_lifecycle() -> None:
    session_store = InMemoryCallSessionStore()
    app = create_app(
        _local_settings(),
        call_event_store=InMemoryCallEventStore(),
        call_session_store=session_store,
    )
    client = TestClient(app)

    with client.websocket_connect("/plivo/media") as websocket:
        websocket.send_json(
            {
                "event": "start",
                "start": {
                    "call_uuid": "call-1",
                    "stream_id": "stream-1",
                },
            }
        )
        websocket.send_json({"event": "stop"})

    response = client.get("/calls/call-1")

    assert response.status_code == 200
    session = response.json()
    assert session["call_id"] == "call-1"
    assert session["stream_id"] == "stream-1"
    assert session["status"] == "media_stopped"
    assert session["event"] == "media_stop"


def test_media_stop_storage_failure_does_not_close_websocket_as_server_error() -> None:
    session_store = FailingMediaStopCallSessionStore()
    app = create_app(
        _local_settings(),
        call_event_store=InMemoryCallEventStore(),
        call_session_store=session_store,
    )
    client = TestClient(app)

    with client.websocket_connect("/plivo/media") as websocket:
        websocket.send_json(
            {
                "event": "start",
                "start": {
                    "call_uuid": "call-1",
                    "stream_id": "stream-1",
                },
            }
        )
        websocket.send_json({"event": "stop"})

    assert session_store.events == ["media_start", "media_stop"]


def test_media_disconnect_updates_call_session_lifecycle() -> None:
    session_store = InMemoryCallSessionStore()
    app = create_app(
        _local_settings(),
        call_event_store=InMemoryCallEventStore(),
        call_session_store=session_store,
    )
    client = TestClient(app)

    with client.websocket_connect("/plivo/media") as websocket:
        websocket.send_json(
            {
                "event": "start",
                "start": {
                    "call_uuid": "call-1",
                    "stream_id": "stream-1",
                },
            }
        )

    response = client.get("/calls/call-1")

    assert response.status_code == 200
    session = response.json()
    assert session["call_id"] == "call-1"
    assert session["stream_id"] == "stream-1"
    assert session["status"] == "media_disconnected"
    assert session["event"] == "media_disconnect"


def test_media_disconnect_storage_failure_does_not_escape_websocket_handler() -> None:
    session_store = FailingMediaDisconnectCallSessionStore()
    app = create_app(
        _local_settings(),
        call_event_store=InMemoryCallEventStore(),
        call_session_store=session_store,
    )
    client = TestClient(app)

    with client.websocket_connect("/plivo/media") as websocket:
        websocket.send_json(
            {
                "event": "start",
                "start": {
                    "call_uuid": "call-1",
                    "stream_id": "stream-1",
                },
            }
        )

    assert session_store.events == ["media_start", "media_disconnect"]


def test_media_protocol_error_updates_call_session_lifecycle() -> None:
    session_store = InMemoryCallSessionStore()
    app = create_app(
        _local_settings(),
        call_event_store=InMemoryCallEventStore(),
        call_session_store=session_store,
    )
    client = TestClient(app)

    with client.websocket_connect("/plivo/media") as websocket:
        websocket.send_json(
            {
                "event": "start",
                "start": {
                    "call_uuid": "call-1",
                    "stream_id": "stream-1",
                },
            }
        )
        websocket.send_json(
            {
                "event": "media",
                "streamId": "stream-1",
                "media": {"payload": "not base64 @@"},
            }
        )
        with pytest.raises(WebSocketDisconnect) as exc_info:
            websocket.receive_json()

    response = client.get("/calls/call-1")

    assert exc_info.value.code == 1008
    assert response.status_code == 200
    session = response.json()
    assert session["call_id"] == "call-1"
    assert session["stream_id"] == "stream-1"
    assert session["status"] == "media_failed"
    assert session["event"] == "media_protocol_error"


def test_duplicate_media_start_updates_call_session_lifecycle() -> None:
    session_store = InMemoryCallSessionStore()
    app = create_app(
        _local_settings(),
        call_event_store=InMemoryCallEventStore(),
        call_session_store=session_store,
    )
    client = TestClient(app)

    with client.websocket_connect("/plivo/media") as websocket:
        websocket.send_json(
            {
                "event": "start",
                "start": {
                    "call_uuid": "call-1",
                    "stream_id": "stream-1",
                },
            }
        )
        websocket.send_json(
            {
                "event": "start",
                "start": {
                    "call_uuid": "call-1",
                    "stream_id": "stream-1",
                },
            }
        )
        with pytest.raises(WebSocketDisconnect) as exc_info:
            websocket.receive_json()

    response = client.get("/calls/call-1")

    assert exc_info.value.code == 1008
    assert response.status_code == 200
    session = response.json()
    assert session["call_id"] == "call-1"
    assert session["stream_id"] == "stream-1"
    assert session["status"] == "media_failed"
    assert session["event"] == "media_protocol_error"


def test_media_stream_mismatch_updates_call_session_lifecycle() -> None:
    session_store = InMemoryCallSessionStore()
    app = create_app(
        _local_settings(),
        call_event_store=InMemoryCallEventStore(),
        call_session_store=session_store,
    )
    client = TestClient(app)

    with client.websocket_connect("/plivo/media") as websocket:
        websocket.send_json(
            {
                "event": "start",
                "start": {
                    "call_uuid": "call-1",
                    "stream_id": "stream-1",
                },
            }
        )
        websocket.send_json(
            {
                "event": "media",
                "streamId": "stream-2",
                "media": {
                    "payload": base64.b64encode(b"hello!").decode("ascii"),
                },
            }
        )
        with pytest.raises(WebSocketDisconnect) as exc_info:
            websocket.receive_json()

    response = client.get("/calls/call-1")

    assert exc_info.value.code == 1008
    assert response.status_code == 200
    session = response.json()
    assert session["call_id"] == "call-1"
    assert session["stream_id"] == "stream-1"
    assert session["status"] == "media_failed"
    assert session["event"] == "media_protocol_error"


def test_media_stop_stream_mismatch_updates_call_session_lifecycle() -> None:
    session_store = InMemoryCallSessionStore()
    app = create_app(
        _local_settings(),
        call_event_store=InMemoryCallEventStore(),
        call_session_store=session_store,
    )
    client = TestClient(app)

    with client.websocket_connect("/plivo/media") as websocket:
        websocket.send_json(
            {
                "event": "start",
                "start": {
                    "call_uuid": "call-1",
                    "stream_id": "stream-1",
                },
            }
        )
        websocket.send_json(
            {
                "event": "stop",
                "streamId": "stream-2",
            }
        )

    response = client.get("/calls/call-1")

    assert response.status_code == 200
    session = response.json()
    assert session["call_id"] == "call-1"
    assert session["stream_id"] == "stream-1"
    assert session["status"] == "media_failed"
    assert session["event"] == "media_protocol_error"


def test_media_dtmf_stream_mismatch_updates_call_session_lifecycle() -> None:
    session_store = InMemoryCallSessionStore()
    app = create_app(
        _local_settings(),
        call_event_store=InMemoryCallEventStore(),
        call_session_store=session_store,
    )
    client = TestClient(app)

    with client.websocket_connect("/plivo/media") as websocket:
        websocket.send_json(
            {
                "event": "start",
                "start": {
                    "call_uuid": "call-1",
                    "stream_id": "stream-1",
                },
            }
        )
        websocket.send_json(
            {
                "event": "dtmf",
                "streamId": "stream-2",
                "dtmf": {"digit": "1"},
            }
        )

    response = client.get("/calls/call-1")

    assert response.status_code == 200
    session = response.json()
    assert session["call_id"] == "call-1"
    assert session["stream_id"] == "stream-1"
    assert session["status"] == "media_failed"
    assert session["event"] == "media_protocol_error"


def test_malformed_media_dtmf_updates_call_session_lifecycle() -> None:
    session_store = InMemoryCallSessionStore()
    app = create_app(
        _local_settings(),
        call_event_store=InMemoryCallEventStore(),
        call_session_store=session_store,
    )
    client = TestClient(app)

    with client.websocket_connect("/plivo/media") as websocket:
        websocket.send_json(
            {
                "event": "start",
                "start": {
                    "call_uuid": "call-1",
                    "stream_id": "stream-1",
                },
            }
        )
        websocket.send_json(
            {
                "event": "dtmf",
                "streamId": "stream-1",
                "dtmf": {"digit": "12"},
            }
        )

    response = client.get("/calls/call-1")

    assert response.status_code == 200
    session = response.json()
    assert session["call_id"] == "call-1"
    assert session["stream_id"] == "stream-1"
    assert session["status"] == "media_failed"
    assert session["event"] == "media_protocol_error"


def test_unknown_media_event_stream_mismatch_updates_call_session_lifecycle() -> None:
    session_store = InMemoryCallSessionStore()
    app = create_app(
        _local_settings(),
        call_event_store=InMemoryCallEventStore(),
        call_session_store=session_store,
    )
    client = TestClient(app)

    with client.websocket_connect("/plivo/media") as websocket:
        websocket.send_json(
            {
                "event": "start",
                "start": {
                    "call_uuid": "call-1",
                    "stream_id": "stream-1",
                },
            }
        )
        websocket.send_json(
            {
                "event": "checkpoint",
                "streamId": "stream-2",
            }
        )

    response = client.get("/calls/call-1")

    assert response.status_code == 200
    session = response.json()
    assert session["call_id"] == "call-1"
    assert session["stream_id"] == "stream-1"
    assert session["status"] == "media_failed"
    assert session["event"] == "media_protocol_error"


def test_media_processing_error_updates_call_session_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_store = InMemoryCallSessionStore()
    failing_runner = FailingMediaRunner()

    async def build_failing_runner(**kwargs: object) -> FailingMediaRunner:
        del kwargs
        return failing_runner

    monkeypatch.setattr("voice_agent.app.build_media_session_runner", build_failing_runner)
    app = create_app(
        _local_settings(),
        call_event_store=InMemoryCallEventStore(),
        call_session_store=session_store,
    )
    client = TestClient(app)

    with client.websocket_connect("/plivo/media") as websocket:
        websocket.send_json(
            {
                "event": "start",
                "start": {
                    "call_uuid": "call-1",
                    "stream_id": "stream-1",
                },
            }
        )
        websocket.send_json(
            {
                "event": "media",
                "streamId": "stream-1",
                "media": {
                    "payload": base64.b64encode(b"hello!").decode("ascii"),
                },
            }
        )
        with pytest.raises(WebSocketDisconnect) as exc_info:
            websocket.receive_json()

    response = client.get("/calls/call-1")

    assert exc_info.value.code == 1011
    assert failing_runner.closed
    assert response.status_code == 200
    session = response.json()
    assert session["call_id"] == "call-1"
    assert session["stream_id"] == "stream-1"
    assert session["status"] == "media_failed"
    assert session["event"] == "media_processing_failed"


def test_media_runner_start_failure_updates_call_session_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_store = InMemoryCallSessionStore()

    async def fail_runner_start(**kwargs: object) -> None:
        del kwargs
        raise RuntimeError("media provider startup failed")

    monkeypatch.setattr("voice_agent.app.build_media_session_runner", fail_runner_start)
    app = create_app(
        _local_settings(),
        call_event_store=InMemoryCallEventStore(),
        call_session_store=session_store,
    )
    client = TestClient(app)

    with client.websocket_connect("/plivo/media") as websocket:
        websocket.send_json(
            {
                "event": "start",
                "start": {
                    "call_uuid": "call-1",
                    "stream_id": "stream-1",
                },
            }
        )
        with pytest.raises(WebSocketDisconnect) as exc_info:
            websocket.receive_json()

    response = client.get("/calls/call-1")

    assert exc_info.value.code == 1011
    assert response.status_code == 200
    session = response.json()
    assert session["call_id"] == "call-1"
    assert session["stream_id"] == "stream-1"
    assert session["status"] == "media_failed"
    assert session["event"] == "media_start_failed"


def test_production_media_token_mismatch_updates_claimed_call_session_lifecycle() -> None:
    session_store = InMemoryCallSessionStore()
    settings = _production_settings()
    app = create_app(
        settings,
        call_event_store=InMemoryCallEventStore(),
        call_session_store=session_store,
    )
    client = TestClient(app, base_url="https://voice.example.com")
    answer_form = {"CallUUID": "call-1"}
    answer_nonce = "answer-nonce-1"
    answer_signature = build_v3_signature(
        method="POST",
        url="https://voice.example.com/plivo/answer",
        nonce=answer_nonce,
        auth_token="plivo-auth-token",
        params=answer_form,
    )
    answer_response = client.post(
        "/plivo/answer",
        data=answer_form,
        headers={
            "X-Plivo-Signature-V3": answer_signature,
            "X-Plivo-Signature-V3-Nonce": answer_nonce,
        },
    )
    token = build_stream_token(
        signing_secret="stream-secret-0123456789abcdef012345",
        ttl_seconds=300,
        now=4_102_444_800,
        nonce="nonce-1",
        call_id="call-1",
    )
    path = f"/plivo/media?token={token}"

    with client.websocket_connect(path, headers=_plivo_websocket_headers(path)) as websocket:
        websocket.send_json(
            {
                "event": "start",
                "start": {
                    "call_uuid": "call-2",
                    "stream_id": "stream-1",
                },
            }
        )
        with pytest.raises(WebSocketDisconnect) as exc_info:
            websocket.receive_json()

    response = client.get("/calls/call-1", headers={"Authorization": AUTHORIZATION})

    assert answer_response.status_code == 200
    assert exc_info.value.code == 1008
    assert response.status_code == 200
    session = response.json()
    assert session["call_id"] == "call-1"
    assert session["stream_id"] == "stream-1"
    assert session["status"] == "media_failed"
    assert session["event"] == "media_token_mismatch"


def test_production_pre_start_media_updates_claimed_call_session_lifecycle() -> None:
    session_store = InMemoryCallSessionStore()
    settings = _production_settings()
    app = create_app(
        settings,
        call_event_store=InMemoryCallEventStore(),
        call_session_store=session_store,
    )
    client = TestClient(app, base_url="https://voice.example.com")
    answer_form = {"CallUUID": "call-1"}
    answer_nonce = "answer-nonce-1"
    answer_signature = build_v3_signature(
        method="POST",
        url="https://voice.example.com/plivo/answer",
        nonce=answer_nonce,
        auth_token="plivo-auth-token",
        params=answer_form,
    )
    answer_response = client.post(
        "/plivo/answer",
        data=answer_form,
        headers={
            "X-Plivo-Signature-V3": answer_signature,
            "X-Plivo-Signature-V3-Nonce": answer_nonce,
        },
    )
    token = build_stream_token(
        signing_secret="stream-secret-0123456789abcdef012345",
        ttl_seconds=300,
        now=4_102_444_800,
        nonce="nonce-1",
        call_id="call-1",
    )
    path = f"/plivo/media?token={token}"

    with client.websocket_connect(path, headers=_plivo_websocket_headers(path)) as websocket:
        websocket.send_json(
            {
                "event": "media",
                "streamId": "stream-early",
                "media": {
                    "payload": base64.b64encode(b"hello!").decode("ascii"),
                },
            }
        )
        with pytest.raises(WebSocketDisconnect) as exc_info:
            websocket.receive_json()

    response = client.get("/calls/call-1", headers={"Authorization": AUTHORIZATION})

    assert answer_response.status_code == 200
    assert exc_info.value.code == 1008
    assert response.status_code == 200
    session = response.json()
    assert session["call_id"] == "call-1"
    assert session["stream_id"] == "stream-early"
    assert session["status"] == "media_failed"
    assert session["event"] == "media_protocol_error"


def test_production_pre_start_stop_updates_claimed_call_session_lifecycle() -> None:
    session_store = InMemoryCallSessionStore()
    settings = _production_settings()
    app = create_app(
        settings,
        call_event_store=InMemoryCallEventStore(),
        call_session_store=session_store,
    )
    client = TestClient(app, base_url="https://voice.example.com")
    answer_form = {"CallUUID": "call-1"}
    answer_nonce = "answer-nonce-1"
    answer_signature = build_v3_signature(
        method="POST",
        url="https://voice.example.com/plivo/answer",
        nonce=answer_nonce,
        auth_token="plivo-auth-token",
        params=answer_form,
    )
    answer_response = client.post(
        "/plivo/answer",
        data=answer_form,
        headers={
            "X-Plivo-Signature-V3": answer_signature,
            "X-Plivo-Signature-V3-Nonce": answer_nonce,
        },
    )
    token = build_stream_token(
        signing_secret="stream-secret-0123456789abcdef012345",
        ttl_seconds=300,
        now=4_102_444_800,
        nonce="nonce-1",
        call_id="call-1",
    )
    path = f"/plivo/media?token={token}"

    with client.websocket_connect(path, headers=_plivo_websocket_headers(path)) as websocket:
        websocket.send_json({"event": "stop", "streamId": "stream-early"})

    response = client.get("/calls/call-1", headers={"Authorization": AUTHORIZATION})

    assert answer_response.status_code == 200
    assert response.status_code == 200
    session = response.json()
    assert session["call_id"] == "call-1"
    assert session["stream_id"] == "stream-early"
    assert session["status"] == "media_failed"
    assert session["event"] == "media_protocol_error"


def test_production_pre_start_dtmf_updates_claimed_call_session_lifecycle() -> None:
    session_store = InMemoryCallSessionStore()
    settings = _production_settings()
    app = create_app(
        settings,
        call_event_store=InMemoryCallEventStore(),
        call_session_store=session_store,
    )
    client = TestClient(app, base_url="https://voice.example.com")
    answer_form = {"CallUUID": "call-1"}
    answer_nonce = "answer-nonce-1"
    answer_signature = build_v3_signature(
        method="POST",
        url="https://voice.example.com/plivo/answer",
        nonce=answer_nonce,
        auth_token="plivo-auth-token",
        params=answer_form,
    )
    answer_response = client.post(
        "/plivo/answer",
        data=answer_form,
        headers={
            "X-Plivo-Signature-V3": answer_signature,
            "X-Plivo-Signature-V3-Nonce": answer_nonce,
        },
    )
    token = build_stream_token(
        signing_secret="stream-secret-0123456789abcdef012345",
        ttl_seconds=300,
        now=4_102_444_800,
        nonce="nonce-1",
        call_id="call-1",
    )
    path = f"/plivo/media?token={token}"

    with client.websocket_connect(path, headers=_plivo_websocket_headers(path)) as websocket:
        websocket.send_json(
            {
                "event": "dtmf",
                "streamId": "stream-early",
                "dtmf": {"digit": "1"},
            }
        )

    response = client.get("/calls/call-1", headers={"Authorization": AUTHORIZATION})

    assert answer_response.status_code == 200
    assert response.status_code == 200
    session = response.json()
    assert session["call_id"] == "call-1"
    assert session["stream_id"] == "stream-early"
    assert session["status"] == "media_failed"
    assert session["event"] == "media_protocol_error"


def test_production_pre_start_dtmf_discards_unsafe_stream_id() -> None:
    session_store = InMemoryCallSessionStore()
    settings = _production_settings()
    app = create_app(
        settings,
        call_event_store=InMemoryCallEventStore(),
        call_session_store=session_store,
    )
    client = TestClient(app, base_url="https://voice.example.com")
    answer_form = {"CallUUID": "call-1"}
    answer_nonce = "answer-nonce-1"
    answer_signature = build_v3_signature(
        method="POST",
        url="https://voice.example.com/plivo/answer",
        nonce=answer_nonce,
        auth_token="plivo-auth-token",
        params=answer_form,
    )
    answer_response = client.post(
        "/plivo/answer",
        data=answer_form,
        headers={
            "X-Plivo-Signature-V3": answer_signature,
            "X-Plivo-Signature-V3-Nonce": answer_nonce,
        },
    )
    token = build_stream_token(
        signing_secret="stream-secret-0123456789abcdef012345",
        ttl_seconds=300,
        now=4_102_444_800,
        nonce="nonce-1",
        call_id="call-1",
    )
    path = f"/plivo/media?token={token}"

    with client.websocket_connect(path, headers=_plivo_websocket_headers(path)) as websocket:
        websocket.send_json(
            {
                "event": "dtmf",
                "streamId": "stream\nunsafe",
                "dtmf": {"digit": "1"},
            }
        )

    response = client.get("/calls/call-1", headers={"Authorization": AUTHORIZATION})

    assert answer_response.status_code == 200
    assert response.status_code == 200
    session = response.json()
    assert session["call_id"] == "call-1"
    assert session["stream_id"] is None
    assert session["status"] == "media_failed"
    assert session["event"] == "media_protocol_error"


def test_production_malformed_start_updates_claimed_call_session_lifecycle() -> None:
    session_store = InMemoryCallSessionStore()
    settings = _production_settings()
    app = create_app(
        settings,
        call_event_store=InMemoryCallEventStore(),
        call_session_store=session_store,
    )
    client = TestClient(app, base_url="https://voice.example.com")
    answer_form = {"CallUUID": "call-1"}
    answer_nonce = "answer-nonce-1"
    answer_signature = build_v3_signature(
        method="POST",
        url="https://voice.example.com/plivo/answer",
        nonce=answer_nonce,
        auth_token="plivo-auth-token",
        params=answer_form,
    )
    answer_response = client.post(
        "/plivo/answer",
        data=answer_form,
        headers={
            "X-Plivo-Signature-V3": answer_signature,
            "X-Plivo-Signature-V3-Nonce": answer_nonce,
        },
    )
    token = build_stream_token(
        signing_secret="stream-secret-0123456789abcdef012345",
        ttl_seconds=300,
        now=4_102_444_800,
        nonce="nonce-1",
        call_id="call-1",
    )
    path = f"/plivo/media?token={token}"

    with client.websocket_connect(path, headers=_plivo_websocket_headers(path)) as websocket:
        websocket.send_json(
            {
                "event": "start",
                "start": {
                    "call_uuid": "   ",
                    "stream_id": "stream-1",
                },
            }
        )
        with pytest.raises(WebSocketDisconnect) as exc_info:
            websocket.receive_json()

    response = client.get("/calls/call-1", headers={"Authorization": AUTHORIZATION})

    assert answer_response.status_code == 200
    assert exc_info.value.code == 1008
    assert response.status_code == 200
    session = response.json()
    assert session["call_id"] == "call-1"
    assert session["stream_id"] == "stream-1"
    assert session["status"] == "media_failed"
    assert session["event"] == "media_protocol_error"


def test_production_start_timeout_updates_claimed_call_session_lifecycle() -> None:
    session_store = InMemoryCallSessionStore()
    settings = _production_settings().model_copy(update={"media_start_timeout_seconds": 0.01})
    app = create_app(
        settings,
        call_event_store=InMemoryCallEventStore(),
        call_session_store=session_store,
    )
    client = TestClient(app, base_url="https://voice.example.com")
    answer_form = {"CallUUID": "call-1"}
    answer_nonce = "answer-nonce-1"
    answer_signature = build_v3_signature(
        method="POST",
        url="https://voice.example.com/plivo/answer",
        nonce=answer_nonce,
        auth_token="plivo-auth-token",
        params=answer_form,
    )
    answer_response = client.post(
        "/plivo/answer",
        data=answer_form,
        headers={
            "X-Plivo-Signature-V3": answer_signature,
            "X-Plivo-Signature-V3-Nonce": answer_nonce,
        },
    )
    token = build_stream_token(
        signing_secret="stream-secret-0123456789abcdef012345",
        ttl_seconds=300,
        now=4_102_444_800,
        nonce="nonce-1",
        call_id="call-1",
    )
    path = f"/plivo/media?token={token}"

    with (
        client.websocket_connect(path, headers=_plivo_websocket_headers(path)) as websocket,
        pytest.raises(WebSocketDisconnect) as exc_info,
    ):
        websocket.receive_json()

    response = client.get("/calls/call-1", headers={"Authorization": AUTHORIZATION})

    assert answer_response.status_code == 200
    assert exc_info.value.code == 1008
    assert response.status_code == 200
    session = response.json()
    assert session["call_id"] == "call-1"
    assert session["stream_id"] is None
    assert session["status"] == "media_failed"
    assert session["event"] == "media_start_timeout"


@pytest.mark.anyio
async def test_call_session_upsert_merges_aliases_when_callback_races_outbound_response() -> None:
    store = InMemoryCallSessionStore()

    await store.reserve_idempotency_key(
        CallSessionUpdate(
            idempotency_key="workflow-call-1",
            direction="outbound",
            from_number="+14155550100",
            to_number="+919876543210",
            status="creating",
            event="outbound_create_requested",
        )
    )
    await store.upsert(
        CallSessionUpdate(
            call_id="call-1",
            request_uuid="request-1",
            direction="outbound",
            from_number="+14155550100",
            to_number="+919876543210",
            status="in-progress",
            event="answer",
        )
    )
    await store.upsert(
        CallSessionUpdate(
            idempotency_key="workflow-call-1",
            request_uuid="request-1",
            api_id="api-1",
            message="call fired",
            direction="outbound",
            from_number="+14155550100",
            to_number="+919876543210",
            status="requested",
            event="outbound_requested",
        )
    )

    by_idempotency = await store.get("workflow-call-1")
    by_request = await store.get("request-1")
    by_call = await store.get("call-1")

    assert by_idempotency == by_request == by_call
    assert by_idempotency is not None
    assert by_idempotency["idempotency_key"] == "workflow-call-1"
    assert by_idempotency["request_uuid"] == "request-1"
    assert by_idempotency["call_id"] == "call-1"
    assert by_idempotency["api_id"] == "api-1"
    assert by_idempotency["message"] == "call fired"


@pytest.mark.anyio
async def test_call_session_alias_merge_preserves_terminal_status_from_racing_callback() -> None:
    store = InMemoryCallSessionStore()

    await store.reserve_idempotency_key(
        CallSessionUpdate(
            idempotency_key="workflow-call-1",
            direction="outbound",
            from_number="+14155550100",
            to_number="+919876543210",
            status="creating",
            event="outbound_create_requested",
        )
    )
    await store.upsert(
        CallSessionUpdate(
            call_id="call-1",
            request_uuid="request-1",
            status="completed",
            event="Hangup",
        )
    )
    await store.upsert(
        CallSessionUpdate(
            idempotency_key="workflow-call-1",
            request_uuid="request-1",
            api_id="api-1",
            message="call fired",
            direction="outbound",
            from_number="+14155550100",
            to_number="+919876543210",
            status="requested",
            event="outbound_requested",
        )
    )

    by_idempotency = await store.get("workflow-call-1")
    by_request = await store.get("request-1")
    by_call = await store.get("call-1")

    assert by_idempotency == by_request == by_call
    assert by_idempotency is not None
    assert by_idempotency["status"] == "completed"
    assert by_idempotency["event"] == "Hangup"
    assert by_idempotency["api_id"] == "api-1"
    assert by_idempotency["message"] == "call fired"


@pytest.mark.anyio
async def test_redis_call_session_alias_merge_preserves_terminal_racing_callback() -> None:
    store = RedisCallSessionStore(redis_url="redis://redis:6379/0")
    store._redis = FakeRedis()

    await store.reserve_idempotency_key(
        CallSessionUpdate(
            idempotency_key="workflow-call-1",
            direction="outbound",
            from_number="+14155550100",
            to_number="+919876543210",
            status="creating",
            event="outbound_create_requested",
        )
    )
    await store.upsert(
        CallSessionUpdate(
            call_id="call-1",
            request_uuid="request-1",
            status="completed",
            event="Hangup",
        )
    )
    await store.upsert(
        CallSessionUpdate(
            idempotency_key="workflow-call-1",
            request_uuid="request-1",
            api_id="api-1",
            message="call fired",
            direction="outbound",
            from_number="+14155550100",
            to_number="+919876543210",
            status="requested",
            event="outbound_requested",
        )
    )

    by_idempotency = await store.get("workflow-call-1")
    by_request = await store.get("request-1")
    by_call = await store.get("call-1")

    assert by_idempotency == by_request == by_call
    assert by_idempotency is not None
    assert by_idempotency["status"] == "completed"
    assert by_idempotency["event"] == "Hangup"
    assert by_idempotency["api_id"] == "api-1"
    assert by_idempotency["message"] == "call fired"


@pytest.mark.anyio
async def test_redis_call_session_upsert_sets_ttl_on_all_session_aliases() -> None:
    store = RedisCallSessionStore(redis_url="redis://redis:6379/0", ttl_seconds=42)
    fake_redis = FakeRedis()
    store._redis = fake_redis

    await store.upsert(
        CallSessionUpdate(
            idempotency_key="workflow-call-1",
            call_id="call-1",
            request_uuid="request-1",
            api_id="api-1",
            status="requested",
        )
    )

    session_ttls = {
        key: value for key, value in fake_redis.ttls.items() if ":locks:" not in key
    }
    assert session_ttls == {
        "voice_agent:call_sessions:workflow-call-1": 42,
        "voice_agent:call_sessions:call-1": 42,
        "voice_agent:call_sessions:request-1": 42,
    }


@pytest.mark.anyio
async def test_redis_call_session_upsert_locks_update_identifiers_before_loading() -> None:
    store = RedisCallSessionStore(redis_url="redis://redis:6379/0")
    fake_redis = FakeRedis()
    store._redis = fake_redis

    await store.upsert(
        CallSessionUpdate(
            call_id="call-1",
            request_uuid="request-1",
            status="answered",
        )
    )

    lock_set_indexes = [
        index
        for index, operation in enumerate(fake_redis.operations)
        if operation == ("set", "voice_agent:call_sessions:locks:call-1", True, 10)
        or operation == ("set", "voice_agent:call_sessions:locks:request-1", True, 10)
    ]
    first_session_load_index = fake_redis.operations.index(
        ("get", "voice_agent:call_sessions:call-1")
    )

    assert len(lock_set_indexes) == 2
    assert max(lock_set_indexes) < first_session_load_index
    assert set(fake_redis.deleted_keys) == {
        "voice_agent:call_sessions:locks:call-1",
        "voice_agent:call_sessions:locks:request-1",
    }


@pytest.mark.anyio
async def test_redis_call_session_upsert_locks_discovered_aliases_before_writing() -> None:
    store = RedisCallSessionStore(redis_url="redis://redis:6379/0")
    fake_redis = FakeRedis()
    store._redis = fake_redis
    await store.upsert(
        CallSessionUpdate(
            idempotency_key="workflow-call-1",
            call_id="call-1",
            request_uuid="request-1",
            status="requested",
        )
    )
    fake_redis.operations.clear()
    fake_redis.deleted_keys.clear()

    await store.upsert(
        CallSessionUpdate(
            call_id="call-1",
            stream_id="stream-1",
            status="media_started",
            event="media_start",
        )
    )

    first_session_write_index = min(
        index
        for index, operation in enumerate(fake_redis.operations)
        if operation[0] == "set" and ":locks:" not in operation[1]
    )
    lock_keys_before_write = {
        operation[1]
        for operation in fake_redis.operations[:first_session_write_index]
        if operation[0] == "set" and ":locks:" in operation[1]
    }

    assert lock_keys_before_write == {
        "voice_agent:call_sessions:locks:workflow-call-1",
        "voice_agent:call_sessions:locks:call-1",
        "voice_agent:call_sessions:locks:request-1",
    }
    assert set(fake_redis.deleted_keys) == lock_keys_before_write


@pytest.mark.anyio
async def test_redis_call_session_upsert_reloads_all_locked_aliases_before_writing() -> None:
    store = RedisCallSessionStore(redis_url="redis://redis:6379/0")
    fake_redis = FakeRedis()
    store._redis = fake_redis
    call_session_without_api_metadata = {
        "idempotency_key": "workflow-call-1",
        "call_id": "call-1",
        "request_uuid": "request-1",
        "status": "answered",
        "event": "answer",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:01Z",
    }
    session_with_api_metadata = {
        "idempotency_key": "workflow-call-1",
        "call_id": "call-1",
        "request_uuid": "request-1",
        "api_id": "api-1",
        "message": "call fired",
        "direction": "outbound",
        "from_number": "+14155550100",
        "to_number": "+919876543210",
        "status": "requested",
        "event": "outbound_requested",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:02Z",
    }
    fake_redis.values = {
        "voice_agent:call_sessions:call-1": json.dumps(call_session_without_api_metadata),
        "voice_agent:call_sessions:request-1": json.dumps(session_with_api_metadata),
        "voice_agent:call_sessions:workflow-call-1": json.dumps(session_with_api_metadata),
    }

    await store.upsert(
        CallSessionUpdate(
            call_id="call-1",
            stream_id="stream-1",
            status="media_started",
            event="media_start",
        )
    )

    by_call = await store.get("call-1")
    by_request = await store.get("request-1")
    by_idempotency = await store.get("workflow-call-1")

    assert by_call == by_request == by_idempotency
    assert by_call is not None
    assert by_call["idempotency_key"] == "workflow-call-1"
    assert by_call["request_uuid"] == "request-1"
    assert by_call["call_id"] == "call-1"
    assert by_call["api_id"] == "api-1"
    assert by_call["message"] == "call fired"
    assert by_call["direction"] == "outbound"
    assert by_call["from_number"] == "+14155550100"
    assert by_call["to_number"] == "+919876543210"
    assert by_call["stream_id"] == "stream-1"
    assert by_call["status"] == "media_started"
    assert by_call["event"] == "media_start"


@pytest.mark.anyio
async def test_redis_call_session_idempotency_reservation_sets_ttl() -> None:
    store = RedisCallSessionStore(redis_url="redis://redis:6379/0", ttl_seconds=42)
    fake_redis = FakeRedis()
    store._redis = fake_redis

    await store.reserve_idempotency_key(
        CallSessionUpdate(
            idempotency_key="workflow-call-1",
            direction="outbound",
            status="creating",
        )
    )

    assert fake_redis.ttls == {"voice_agent:call_sessions:workflow-call-1": 42}


@pytest.mark.anyio
async def test_redis_call_session_existing_idempotency_replay_merges_linked_aliases() -> None:
    store = RedisCallSessionStore(redis_url="redis://redis:6379/0")
    fake_redis = FakeRedis()
    store._redis = fake_redis
    stale_idempotency_session = {
        "idempotency_key": "workflow-call-1",
        "call_id": "call-1",
        "request_uuid": "request-1",
        "direction": "outbound",
        "from_number": "+14155550100",
        "to_number": "+919876543210",
        "status": "creating",
        "event": "outbound_create_requested",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:01Z",
    }
    session_with_api_metadata = {
        "idempotency_key": "workflow-call-1",
        "call_id": "call-1",
        "request_uuid": "request-1",
        "api_id": "api-1",
        "message": "call fired",
        "direction": "outbound",
        "from_number": "+14155550100",
        "to_number": "+919876543210",
        "status": "requested",
        "event": "outbound_requested",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:02Z",
    }
    fake_redis.values = {
        "voice_agent:call_sessions:workflow-call-1": json.dumps(stale_idempotency_session),
        "voice_agent:call_sessions:request-1": json.dumps(session_with_api_metadata),
        "voice_agent:call_sessions:call-1": json.dumps(session_with_api_metadata),
    }

    existing = await store.reserve_idempotency_key(
        CallSessionUpdate(
            idempotency_key="workflow-call-1",
            direction="outbound",
            from_number="+14155550100",
            to_number="+919876543210",
            status="creating",
            event="outbound_create_requested",
        )
    )
    by_idempotency = await store.get("workflow-call-1")
    by_request = await store.get("request-1")
    by_call = await store.get("call-1")

    assert existing == by_idempotency == by_request == by_call
    assert existing is not None
    assert existing["api_id"] == "api-1"
    assert existing["message"] == "call fired"
    assert existing["request_uuid"] == "request-1"
    assert existing["call_id"] == "call-1"
    assert existing["status"] == "requested"
    assert existing["event"] == "outbound_requested"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("raw_session", "match"),
    [
        ("not-json", "Stored Redis call session for call-1 was not valid JSON"),
        (
            json.dumps(["not", "an", "object"]),
            "Stored Redis call session for call-1 was not a JSON object",
        ),
    ],
)
async def test_redis_call_session_get_rejects_malformed_stored_payload(
    raw_session: str,
    match: str,
) -> None:
    store = RedisCallSessionStore(redis_url="redis://redis:6379/0")
    fake_redis = FakeRedis()
    store._redis = fake_redis
    fake_redis.values = {"voice_agent:call_sessions:call-1": raw_session}

    with pytest.raises(CallSessionStoreError, match=match):
        await store.get("call-1")


def test_build_call_session_store_passes_configured_redis_ttl() -> None:
    store = build_call_session_store(
        Settings(
            environment="test",
            public_base_url="https://voice.example.com",
            session_backend="redis",
            redis_url="redis://redis:6379/0",
            stt_provider="local",
            llm_provider="local",
            tts_provider="local",
            call_session_ttl_seconds=42,
        )
    )

    assert isinstance(store, RedisCallSessionStore)
    assert store._ttl_seconds == 42


@pytest.mark.anyio
async def test_call_session_preserves_terminal_status_when_late_nonterminal_event_arrives() -> None:
    store = InMemoryCallSessionStore()

    await store.upsert(
        CallSessionUpdate(
            call_id="call-1",
            request_uuid="request-1",
            status="completed",
            event="Hangup",
        )
    )
    await store.upsert(
        CallSessionUpdate(
            call_id="call-1",
            stream_id="stream-1",
            status="media_started",
            event="media_start",
        )
    )

    session = await store.get("call-1")

    assert session is not None
    assert session["status"] == "completed"
    assert session["event"] == "Hangup"
    assert session["stream_id"] == "stream-1"


@pytest.mark.anyio
async def test_call_session_upsert_rejects_blank_identifiers() -> None:
    store = InMemoryCallSessionStore()

    with pytest.raises(CallSessionStoreError, match="require call_id or request_uuid"):
        await store.upsert(CallSessionUpdate(call_id="   "))


@pytest.mark.anyio
@pytest.mark.parametrize(
    "call_id",
    [
        "call/1",
        "call 1",
        "call\n1",
        "a" * 129,
    ],
)
async def test_call_session_upsert_rejects_unsafe_identifier_aliases(call_id: str) -> None:
    store = InMemoryCallSessionStore()

    with pytest.raises(CallSessionStoreError, match="call_id must be 1-128"):
        await store.upsert(CallSessionUpdate(call_id=call_id, status="answered"))

    assert await store.get(call_id) is None


@pytest.mark.anyio
async def test_call_session_store_trims_session_identifiers_before_indexing() -> None:
    store = InMemoryCallSessionStore()

    await store.upsert(
        CallSessionUpdate(
            idempotency_key=" workflow-call-1 ",
            call_id=" call-1 ",
            request_uuid=" request-1 ",
            api_id=" api-1 ",
            message=" call fired ",
            status=" requested ",
        )
    )

    by_call = await store.get("call-1")
    by_request = await store.get(" request-1 ")
    by_idempotency = await store.get("workflow-call-1")

    assert by_call == by_request == by_idempotency
    assert by_call is not None
    assert by_call["idempotency_key"] == "workflow-call-1"
    assert by_call["call_id"] == "call-1"
    assert by_call["request_uuid"] == "request-1"
    assert by_call["api_id"] == "api-1"
    assert by_call["message"] == "call fired"
    assert by_call["status"] == "requested"


def _local_settings(
    *,
    control_api_key: str | None = None,
    plivo_from_number: str | None = None,
) -> Settings:
    return Settings(
        environment="test",
        public_base_url="https://voice.example.com",
        session_backend="memory",
        stt_provider="local",
        llm_provider="local",
        tts_provider="local",
        control_api_key=control_api_key,
        plivo_from_number=plivo_from_number,
    )


def _production_settings() -> Settings:
    return Settings(
        environment="production",
        public_base_url="https://voice.example.com",
        session_backend="redis",
        redis_url="redis://redis:6379/0",
        stt_provider="deepgram",
        deepgram_api_key="deepgram-secret",
        llm_provider="orchestrator",
        orchestrator_url="https://orchestrator.example.com/respond",
        orchestrator_api_key="orchestrator-secret",
        tts_provider="elevenlabs",
        elevenlabs_api_key="eleven-secret",
        elevenlabs_voice_id="voice-1",
        control_api_key=CONTROL_API_KEY,
        plivo_auth_id="plivo-auth-id",
        plivo_auth_token="plivo-auth-token",
        plivo_from_number="+14155550100",
        stream_auth_token="stream-secret-0123456789abcdef012345",
    )


def _plivo_websocket_headers(path: str) -> dict[str, str]:
    nonce = "plivo-nonce-1"
    signed_payload = f"GEThttps://voice.example.com{path}{nonce}"
    signature = base64.b64encode(
        hmac.new(
            b"plivo-auth-token",
            signed_payload.encode("utf-8"),
            hashlib.sha256,
        ).digest()
    ).decode("ascii")
    return {
        "X-Plivo-Signature-V3": signature,
        "X-Plivo-Signature-V3-Nonce": nonce,
    }


class FakeOutboundCallClient:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.hangups: list[str] = []

    async def create_call(
        self,
        *,
        from_number: str,
        to_number: str,
        answer_url: str,
        answer_method: str,
        hangup_url: str,
        hangup_method: str,
        hangup_on_ring_seconds: int,
        time_limit_seconds: int,
    ) -> PlivoCallCreateResult:
        self.calls.append(to_number)
        del (
            from_number,
            to_number,
            answer_url,
            answer_method,
            hangup_url,
            hangup_method,
            hangup_on_ring_seconds,
            time_limit_seconds,
        )
        return PlivoCallCreateResult(
            api_id="api-1",
            message="call fired",
            request_uuid="request-1",
        )

    async def hangup_call(self, identifier: str) -> None:
        self.hangups.append(identifier)


class RejectingGetCallSessionStore(InMemoryCallSessionStore):
    async def get(self, identifier: str) -> dict[str, object] | None:
        del identifier
        raise AssertionError("get should not run for invalid control identifiers")


class FailingMediaRunner:
    call_id = "call-1"
    stream_id = "stream-1"

    def __init__(self) -> None:
        self.closed = False

    async def start(self) -> None:
        return None

    async def greet(self) -> None:
        return None

    async def accept_audio(self, audio: bytes) -> None:
        del audio
        raise RuntimeError("media provider unavailable")

    async def close(self) -> None:
        self.closed = True


class FailingMediaStopCallSessionStore(InMemoryCallSessionStore):
    def __init__(self) -> None:
        super().__init__()
        self.events: list[str] = []

    async def upsert(self, update: CallSessionUpdate) -> dict[str, object]:
        if update.event is not None:
            self.events.append(update.event)
        if update.event == "media_stop":
            raise RuntimeError("call session storage is unavailable")
        return await super().upsert(update)


class FailingMediaDisconnectCallSessionStore(InMemoryCallSessionStore):
    def __init__(self) -> None:
        super().__init__()
        self.events: list[str] = []

    async def upsert(self, update: CallSessionUpdate) -> dict[str, object]:
        if update.event is not None:
            self.events.append(update.event)
        if update.event == "media_disconnect":
            raise RuntimeError("call session storage is unavailable")
        return await super().upsert(update)


class FailingOutboundCallClient:
    async def create_call(
        self,
        *,
        from_number: str,
        to_number: str,
        answer_url: str,
        answer_method: str,
        hangup_url: str,
        hangup_method: str,
        hangup_on_ring_seconds: int,
        time_limit_seconds: int,
    ) -> PlivoCallCreateResult:
        del (
            from_number,
            to_number,
            answer_url,
            answer_method,
            hangup_url,
            hangup_method,
            hangup_on_ring_seconds,
            time_limit_seconds,
        )
        raise httpx.RequestError("Plivo API is unreachable.")

    async def hangup_call(self, identifier: str) -> None:
        del identifier
        return None


class RecoveringOutboundCallClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def create_call(
        self,
        *,
        from_number: str,
        to_number: str,
        answer_url: str,
        answer_method: str,
        hangup_url: str,
        hangup_method: str,
        hangup_on_ring_seconds: int,
        time_limit_seconds: int,
    ) -> PlivoCallCreateResult:
        del (
            from_number,
            answer_url,
            answer_method,
            hangup_url,
            hangup_method,
            hangup_on_ring_seconds,
            time_limit_seconds,
        )
        self.calls.append(to_number)
        if len(self.calls) == 1:
            raise httpx.RequestError("Plivo API is unreachable.")
        return PlivoCallCreateResult(
            api_id="api-1",
            message="call fired",
            request_uuid="request-1",
        )

    async def hangup_call(self, identifier: str) -> None:
        del identifier
        return None


class MalformedOutboundCallClient:
    async def create_call(
        self,
        *,
        from_number: str,
        to_number: str,
        answer_url: str,
        answer_method: str,
        hangup_url: str,
        hangup_method: str,
        hangup_on_ring_seconds: int,
        time_limit_seconds: int,
    ) -> PlivoCallCreateResult:
        del (
            from_number,
            to_number,
            answer_url,
            answer_method,
            hangup_url,
            hangup_method,
            hangup_on_ring_seconds,
            time_limit_seconds,
        )
        raise PlivoCallClientError("Plivo create-call response was invalid.")

    async def hangup_call(self, identifier: str) -> None:
        del identifier
        return None


class FailingHangupCallClient:
    async def create_call(
        self,
        *,
        from_number: str,
        to_number: str,
        answer_url: str,
        answer_method: str,
        hangup_url: str,
        hangup_method: str,
        hangup_on_ring_seconds: int,
        time_limit_seconds: int,
    ) -> PlivoCallCreateResult:
        del (
            from_number,
            to_number,
            answer_url,
            answer_method,
            hangup_url,
            hangup_method,
            hangup_on_ring_seconds,
            time_limit_seconds,
        )
        return PlivoCallCreateResult(
            api_id="api-1",
            message="call fired",
            request_uuid="request-1",
        )

    async def hangup_call(self, identifier: str) -> None:
        del identifier
        raise httpx.RequestError("Plivo API is unreachable.")


class InvalidHangupCallClient:
    async def create_call(
        self,
        *,
        from_number: str,
        to_number: str,
        answer_url: str,
        answer_method: str,
        hangup_url: str,
        hangup_method: str,
        hangup_on_ring_seconds: int,
        time_limit_seconds: int,
    ) -> PlivoCallCreateResult:
        del (
            from_number,
            to_number,
            answer_url,
            answer_method,
            hangup_url,
            hangup_method,
            hangup_on_ring_seconds,
            time_limit_seconds,
        )
        return PlivoCallCreateResult(
            api_id="api-1",
            message="call fired",
            request_uuid="request-1",
        )

    async def hangup_call(self, identifier: str) -> None:
        del identifier
        raise PlivoCallClientError("Plivo hangup response was invalid.")


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int] = {}
        self.operations: list[tuple[str, str, bool | None, int | None] | tuple[str, str]] = []
        self.deleted_keys: list[str] = []

    async def set(
        self,
        key: str,
        value: str,
        *,
        nx: bool = False,
        ex: int | None = None,
    ) -> bool | None:
        self.operations.append(("set", key, nx, ex))
        if nx and key in self.values:
            return None
        self.values[key] = value
        if ex is not None:
            self.ttls[key] = ex
        return True

    async def get(self, key: str) -> str | None:
        self.operations.append(("get", key))
        return self.values.get(key)

    async def delete(self, key: str) -> int:
        self.operations.append(("delete", key))
        self.deleted_keys.append(key)
        return 1 if self.values.pop(key, None) is not None else 0

    async def ping(self) -> bool:
        return True

    async def aclose(self) -> None:
        return None
