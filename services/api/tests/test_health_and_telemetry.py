"""/health components and the OpenTelemetry console exporter."""

from __future__ import annotations

import io
import os
import uuid

from httpx import ASGITransport, AsyncClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from careflow_api.security import create_access_token


async def test_health_reports_every_component(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert set(body) == {"status", "version", "db", "redis", "fhir"}
    assert all(body[k] in {"ok", "down"} for k in ("db", "redis", "fhir"))


async def test_requests_produce_spans(client: AsyncClient, spans: InMemorySpanExporter) -> None:
    token, _ = create_access_token(
        secret=os.environ["JWT_SECRET"], subject="s", role="nurse", clinic_id=uuid.uuid4()
    )
    await client.get("/v1/nurse/worklist", headers={"Authorization": f"Bearer {token}"})
    names = [span.name for span in spans.get_finished_spans()]
    assert any("/v1/nurse/worklist" in name for name in names), names
    resource = spans.get_finished_spans()[0].resource.attributes
    assert resource["service.name"] == "careflow-api"


async def test_console_exporter_prints_a_span() -> None:
    from careflow_api.main import create_app
    from careflow_api.telemetry import build_tracer_provider

    out = io.StringIO()
    app = create_app(tracer_provider=build_tracer_provider(console_out=out))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://api.test") as http:
        await http.get("/v1/auth/me")
    printed = out.getvalue()
    assert '"name": "GET /v1/auth/me' in printed
    assert '"service.name": "careflow-api"' in printed


async def test_unknown_routes_and_methods_are_problems(client: AsyncClient) -> None:
    missing = await client.get("/v1/no-such-route")
    assert missing.status_code == 404
    assert missing.headers["content-type"].startswith("application/problem+json")
    assert missing.json()["title"] == "Not Found"
    wrong_method = await client.put("/v1/auth/login")
    assert wrong_method.status_code == 405
    assert wrong_method.headers["content-type"].startswith("application/problem+json")
