"""Route, role and safety invariants of openapi.yaml."""

from __future__ import annotations

import re
from typing import Any

import yaml

from careflow_contracts.testing import CONTRACTS_DIR

SPEC: dict[str, Any] = yaml.safe_load((CONTRACTS_DIR / "openapi.yaml").read_text(encoding="utf-8"))
ROLES = set(SPEC["components"]["schemas"]["Role"]["enum"])
OPERATIONS = [
    (method, route, op)
    for route, item in SPEC["paths"].items()
    for method, op in item.items()
    if method in ("get", "post", "put", "patch", "delete")
]
# The route list in the M0-03 issue (the single source for names used by later issues), plus the
# routes later issues add: M1-03 the dev-only vitals simulator, M1-10 closing a queue token.
EXPECTED_ROUTES = """
post /v1/auth/login
post /v1/auth/device-token
get /v1/auth/me
post /v1/ws-ticket
post /v1/checkin/keypad
post /v1/checkin/id-tap
post /v1/sessions/{session_id}/qr
post /v1/checkin/qr/claim
post /v1/sessions/{session_id}/consent
post /v1/sessions/{session_id}/turns
get /v1/sessions/{session_id}/facts
get /v1/sessions/{session_id}/status
post /v1/sessions/{session_id}/vitals
post /v1/nurse/sessions/{session_id}/vitals
get /v1/sessions/{session_id}/triage
post /v1/nurse/sessions/{session_id}/decision
post /v1/nurse/sessions/{session_id}/tasks/{task_id}/complete
get /v1/nurse/worklist
get /v1/sessions/{session_id}/token
get /v1/board/{department}
post /v1/queue/{department}/call-next
post /v1/queue/tokens/{token_id}/arrived
post /v1/queue/tokens/{token_id}/no-show
post /v1/queue/tokens/{token_id}/close
get /v1/doctor/sessions
get /v1/doctor/sessions/{session_id}/briefing
get /v1/companion/status
post /v1/companion/feels-worse
post /v1/companion/ask
get /v1/admin/providers
get /v1/admin/status
post /v1/dev/sessions/{session_id}/vitals-sim
get /health
"""
EXPECTED = {(m, r) for m, r in (line.split() for line in EXPECTED_ROUTES.strip().splitlines())}
UNAUTHENTICATED = {
    ("get", "/health"), ("post", "/v1/auth/login"), ("post", "/v1/auth/device-token"),
    ("post", "/v1/checkin/qr/claim"),
}  # fmt: skip


def schema(name: str) -> dict[str, Any]:
    return SPEC["components"]["schemas"][name]


def test_every_route_in_the_issue_is_present_and_nothing_else() -> None:
    assert {(m, r) for m, r, _ in OPERATIONS} == EXPECTED


def test_rest_routes_live_under_v1() -> None:
    assert all(route.startswith("/v1/") for _, route, _ in OPERATIONS if route != "/health")


def test_operations_have_ids_and_valid_roles() -> None:
    ids = [op["operationId"] for _, _, op in OPERATIONS]
    assert len(ids) == len(set(ids))
    for method, route, op in OPERATIONS:
        roles = op["x-careflow-roles"]
        assert set(roles) <= ROLES, (method, route)
        assert bool(roles) != ((method, route) in UNAUTHENTICATED), (method, route)


def test_only_token_endpoints_and_health_skip_the_bearer() -> None:
    assert SPEC["security"] == [{"bearerAuth": []}]
    open_ops = {(m, r) for m, r, op in OPERATIONS if op.get("security") == []}
    assert open_ops == UNAUTHENTICATED


def test_no_patient_identity_in_urls() -> None:
    params = {p for route, _ in SPEC["paths"].items() for p in re.findall(r"{(\w+)}", route)}
    assert params == {"session_id", "task_id", "token_id", "department"}
    assert "digits" in schema("KeypadCheckinRequest")["properties"]


def test_session_status_never_carries_a_level_or_alert() -> None:
    props = set(schema("SessionStatus")["properties"])
    assert props == {"state", "token_code", "room", "wait_estimate_min", "vitals"}
    # The kiosk vitals panel (M1-03) shows measured numbers only: no score, level or alert.
    vitals = set(schema("LatestVitals")["properties"])
    assert vitals == {
        "heart_rate", "spo2", "temperature_c", "systolic_bp", "diastolic_bp",
        "measured_at", "device_id", "simulated",
    }  # fmt: skip


def test_only_the_decision_route_makes_a_level_final() -> None:
    def body_ref(op: dict[str, Any]) -> str | None:
        body = op.get("requestBody", {}).get("content", {}).get("application/json", {})
        return body.get("schema", {}).get("$ref")

    takers = [
        (m, r) for m, r, op in OPERATIONS if body_ref(op) == "#/components/schemas/NurseDecision"
    ]
    assert takers == [("post", "/v1/nurse/sessions/{session_id}/decision")]


def test_briefing_items_carry_provenance_and_ask_can_refuse() -> None:
    assert {"provenance", "source"} <= set(schema("BriefingItem")["required"])
    assert {"refused_medical", "dont_know", "citations"} <= set(schema("AskResponse")["required"])


def test_errors_are_problem_details() -> None:
    for name, response in SPEC["components"]["responses"].items():
        assert list(response["content"]) == ["application/problem+json"], name


def test_websocket_ticket_route_is_documented() -> None:
    assert "?ticket=" in SPEC["paths"]["/v1/ws-ticket"]["post"]["description"]
