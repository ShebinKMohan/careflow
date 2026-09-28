"""Patient receipt content and authorization with isolated ASGI fakes, no database or models."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from careflow_api.deps import Principal, get_principal
from careflow_api.routers import intake_summary
from careflow_api.routers.sessions import runtime
from careflow_api.security import Role
from careflow_contracts.generated.alert_schema import Alert
from careflow_contracts.generated.nurse_decision_schema import NurseDecision
from careflow_contracts.generated.structured_facts_schema import Language
from careflow_contracts.generated.urgency_suggestion_schema import UrgencySuggestion
from careflow_contracts.ids import stable_fact_id
from careflow_intake.facts import FactEntry
from careflow_intake.state import IntakeState

SESSION = "11111111-1111-4111-8111-111111111111"
CLINIC = uuid.UUID("22222222-2222-4222-8222-222222222222")
WHEN = datetime(2026, 9, 29, 9, tzinfo=UTC)
ROUTE = f"/v1/sessions/{SESSION}/intake-summary"


def fact(
    key: str,
    value: Any,
    *,
    provenance: str = "PATIENT_STATED",
    source: dict[str, Any] | None = None,
    unit: str | None = None,
    minutes: int = 0,
) -> FactEntry:
    body = {
        "fact_id": stable_fact_id(key, provenance, str(minutes)),
        "recorded_at": WHEN + timedelta(minutes=minutes),
        "provenance": provenance,
        "source": source
        or {
            "kind": "transcript_span",
            "turn_id": "private_turn_id",
            "start": 0,
            "end": 25,
            "quote": "PRIVATE_UNREDACTED_QUOTE",
        },
        "value": value,
    }
    if unit is not None:
        body["unit"] = unit
    return FactEntry.model_validate({"key": key, "fact": body})


def pulse(value: float, *, minutes: int = 0) -> FactEntry:
    return fact(
        "vitals.heart_rate",
        value,
        unit="/min",
        provenance="DEVICE_MEASURED",
        source={
            "kind": "device",
            "device_id": "private_device_id",
            "reading_id": "private_reading",
        },
        minutes=minutes,
    )


def state(*entries: FactEntry) -> IntakeState:
    return {
        "session_id": SESSION,
        "clinic_id": str(CLINIC),
        "language": Language.en,
        "arrived_at": WHEN,
        "facts": list(entries),
    }


def suggestion() -> UrgencySuggestion:
    return UrgencySuggestion.model_validate(
        {
            "suggestion_id": "sug_real_current",
            "session_id": SESSION,
            "level": 3,
            "scale": "KTAS",
            "confidence": 0.71,
            "reasons": [{"text": "Measured pulse", "fact_ids": [pulse(112).fact_id]}],
            "model_id": "actual_test_urgency_model",
            "model_version": "7.4.1",
            "created_at": WHEN,
        }
    )


def test_receipt_preserves_provenance_and_marks_only_simulated_device_values() -> None:
    entries = [
        fact("chief_complaint", "Headache"),
        pulse(112),
        fact(
            "vitals.respiratory_rate",
            18,
            unit="/min",
            provenance="NURSE_CONFIRMED",
            source={"kind": "nurse", "staff_user_id": "private_nurse_id"},
        ),
        fact(
            "ehr_context.conditions",
            {"system": "https://example.test/codes", "code": "htn", "display": "Hypertension"},
            provenance="EHR",
            source={"kind": "fhir", "resource_type": "Condition", "id": "private_condition_id"},
        ),
        fact(
            "complaint_category",
            {"system": "https://example.test/codes", "code": "headache"},
            provenance="MODEL_INFERRED",
            source={"kind": "model", "model_id": "category_model", "model_version": "1.2"},
        ),
    ]
    view = intake_summary.receipt(state(*entries), simulated=True)
    rows = {row.key: row.model_dump() for row in view.facts}

    assert len(rows) == len(entries)
    for entry in entries:
        row = rows[entry.key.value]
        assert row["fact_id"] == entry.fact_id
        assert row["provenance"] == entry.provenance.value
        assert row["simulated"] is (entry.provenance.value == "DEVICE_MEASURED")
        assert set(row) == {"key", "fact_id", "value", "provenance", "simulated"}
    assert rows["vitals.heart_rate"]["value"] == "112.0 /min"
    assert rows["ehr_context.conditions"]["value"] == "Hypertension"
    assert rows["complaint_category"]["value"] == "headache"
    assert not intake_summary.receipt(state(pulse(112))).facts[0].simulated


def test_receipt_uses_current_nurse_measurement_over_later_device_values() -> None:
    measured = fact(
        "vitals.heart_rate",
        88,
        unit="/min",
        provenance="NURSE_CONFIRMED",
        source={"kind": "nurse", "staff_user_id": "nurse_1"},
        minutes=1,
    )
    view = intake_summary.receipt(
        state(pulse(112), measured, pulse(125, minutes=2)), simulated=True
    )

    assert len(view.facts) == 1
    assert view.facts[0].fact_id == measured.fact_id
    assert view.facts[0].value == "88.0 /min"
    assert view.facts[0].provenance == "NURSE_CONFIRMED"
    assert view.facts[0].simulated is False


def test_absent_values_and_camera_consent_never_invent_normal_measurements() -> None:
    current = state(fact("pain_score", 0), fact("chest_pain.present", "unknown"))
    current["reception_context"] = {
        "consent": {"camera": True},
        "caregiver": {"name": "PRIVATE_CAREGIVER", "phone": "PRIVATE_PHONE"},
    }
    current["patient_ref"] = "Patient/private_record"
    view = intake_summary.receipt(current, simulated=True)
    rows = {row.key: row.value for row in view.facts}

    assert rows == {"pain_score": "0", "chest_pain.present": "unknown"}
    assert not any(key.startswith("vitals.") for key in rows)
    assert intake_summary.receipt(state()).facts == []
    body = view.model_dump_json()
    for private in [
        "PRIVATE_CAREGIVER",
        "PRIVATE_PHONE",
        "PRIVATE_UNREDACTED_QUOTE",
        "private_turn_id",
        "Patient/private_record",
        "camera",
    ]:
        assert private not in body


def test_actual_suggestion_identity_is_provisional_until_a_nurse_decides() -> None:
    current = state(pulse(112))
    current["suggestion"] = suggestion()
    view = intake_summary.receipt(current)

    assert view.suggestion is not None
    assert view.suggestion.model_dump(mode="json") == suggestion().model_dump(mode="json")
    assert view.nurse_confirmed is False
    assert "confirmed_level" not in view.model_dump(exclude_unset=True)
    assert "confirmed_scale" not in view.model_dump(exclude_unset=True)
    assert view.visit_reference.startswith("CF-")
    assert not {"token_code", "queue_position", "room"} & view.model_dump().keys()

    current["nurse_decision"] = NurseDecision.model_validate(
        {
            "decision_id": "dec_receipt",
            "session_id": SESSION,
            "suggestion_id": "sug_real_current",
            "action": "OVERRIDE",
            "final_level": 2,
            "scale": "KTAS",
            "override_reason_code": "clinical_judgement",
            "nurse_user_id": "private_nurse_id",
            "decided_at": WHEN,
        }
    )
    confirmed = intake_summary.receipt(current)
    assert confirmed.nurse_confirmed is True
    assert confirmed.confirmed_level == 2
    assert confirmed.confirmed_scale is not None and confirmed.confirmed_scale.value == "KTAS"
    assert confirmed.suggestion is not None and confirmed.suggestion.level == 3


def test_model_failure_does_not_fabricate_a_suggestion_or_drop_warnings_and_rule_alerts() -> None:
    current = state(pulse(112))
    current["suggestion"] = None
    current["suggestion_unavailable"] = "PRIVATE_MODEL_ERROR"
    current["flags"] = {
        "extraction_incomplete": True,
        "ehr_unavailable": True,
        "intake_truncated": True,
        "assistance_requested": True,
    }
    current["alerts"] = [
        Alert.model_validate(
            {
                "alert_id": "alert_receipt",
                "session_id": SESSION,
                "rule_id": "SEPSIS_NEWS2",
                "ruleset_version": "1.0.0",
                "severity": "high",
                "evidence_fact_ids": [pulse(112).fact_id],
                "required_action": "nurse_review",
                "partial": True,
                "missing_inputs": ["vitals.respiratory_rate"],
                "raised_at": WHEN,
            }
        )
    ]
    view = intake_summary.receipt(current)

    assert view.suggestion is None and view.nurse_confirmed is False
    assert view.assistance_requested is True
    assert set(view.warnings) == {
        "extraction_incomplete",
        "ehr_unavailable",
        "intake_stopped_early",
    }
    assert [alert.model_dump(mode="json") for alert in view.alerts] == [
        {"code": "SEPSIS_NEWS2", "severity": "high"}
    ]
    assert "PRIVATE_MODEL_ERROR" not in view.model_dump_json()


@pytest.fixture
async def isolated(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[SimpleNamespace]:
    principal = Principal(
        subject=f"session:{SESSION}",
        role="patient_session",
        clinic_id=CLINIC,
        token_id="test-patient-token",
        session_id=SESSION,
        channel="phone",
    )
    loaded = AsyncMock(return_value=state(pulse(112)))
    lookup = AsyncMock(return_value=SimpleNamespace())
    latest = Mock(return_value=SimpleNamespace(simulated=True))
    harness = SimpleNamespace(principal=principal, loaded=loaded, lookup=lookup, latest=latest)
    app = FastAPI()
    app.include_router(intake_summary.router)
    app.dependency_overrides[get_principal] = lambda: harness.principal
    app.dependency_overrides[runtime] = lambda: SimpleNamespace(state=loaded)
    monkeypatch.setattr(intake_summary, "session_row", lookup)
    monkeypatch.setattr(intake_summary, "latest_vitals", SimpleNamespace(get=latest))
    harness.app = app
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://api.test"
    ) as http:
        harness.http = http
        yield harness


@pytest.mark.parametrize("channel", ["phone", "kiosk"])
async def test_own_session_receipt_checks_the_session_then_reads_its_state(
    isolated: SimpleNamespace, channel: str
) -> None:
    isolated.principal = replace(isolated.principal, channel=channel)
    response = await isolated.http.get(ROUTE)

    assert response.status_code == 200
    assert response.json()["session_id"] == SESSION
    assert response.json()["facts"][0]["simulated"] is True
    isolated.lookup.assert_awaited_once_with(isolated.principal, SESSION)
    isolated.loaded.assert_awaited_once_with(SESSION)
    isolated.latest.assert_called_once_with(SESSION)


async def test_other_patient_session_is_denied_before_lookup_or_state_access(
    isolated: SimpleNamespace,
) -> None:
    response = await isolated.http.get(f"/v1/sessions/{uuid.uuid4()}/intake-summary")

    assert response.status_code == 403
    isolated.lookup.assert_not_awaited()
    isolated.loaded.assert_not_awaited()
    isolated.latest.assert_not_called()


@pytest.mark.parametrize(
    "role", ["nurse", "doctor", "admin", "kiosk_device", "board", "integration"]
)
async def test_non_patient_roles_cannot_use_the_patient_receipt(
    isolated: SimpleNamespace, role: Role
) -> None:
    isolated.principal = replace(isolated.principal, role=role)
    response = await isolated.http.get(ROUTE)

    assert response.status_code == 403
    isolated.lookup.assert_not_awaited()
    isolated.loaded.assert_not_awaited()


async def test_missing_bearer_authentication_cannot_read_the_receipt(
    isolated: SimpleNamespace,
) -> None:
    isolated.app.dependency_overrides.pop(get_principal)
    response = await isolated.http.get(ROUTE)

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    isolated.lookup.assert_not_awaited()
    isolated.loaded.assert_not_awaited()


async def test_session_lookup_failure_never_reads_intake_state(isolated: SimpleNamespace) -> None:
    isolated.lookup.side_effect = HTTPException(404, "No such session.")
    response = await isolated.http.get(ROUTE)

    assert response.status_code == 404
    isolated.loaded.assert_not_awaited()
    isolated.latest.assert_not_called()


async def test_absent_intake_returns_not_found_instead_of_an_empty_normal_receipt(
    isolated: SimpleNamespace,
) -> None:
    isolated.loaded.return_value = None
    response = await isolated.http.get(ROUTE)

    assert response.status_code == 404
    assert response.json() == {"detail": "No intake for this session."}
    isolated.latest.assert_not_called()


async def test_runtime_failure_does_not_return_a_successful_receipt(
    isolated: SimpleNamespace,
) -> None:
    isolated.loaded.side_effect = RuntimeError("PRIVATE_CHECKPOINT_ERROR")
    response = await isolated.http.get(ROUTE)

    assert response.status_code == 500
    assert "PRIVATE_CHECKPOINT_ERROR" not in response.text
    isolated.latest.assert_not_called()
