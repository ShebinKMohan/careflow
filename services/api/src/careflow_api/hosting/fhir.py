"""Postgres synthetic FHIR port for the CV demo; not a hospital or a full FHIR server.

Supports precisely the original integration client's read/search/create/update/transaction
operations, including conditional creates and version checks. Resource bodies and provenance
are persisted across function instances. This transport is internal only: no public EHR route.
"""

from __future__ import annotations

import copy
import json
import uuid
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from careflow_api.db import get_sessionmaker

DEMO_FHIR_URL = "https://careflow-demo.internal/fhir"


def factory(url: str) -> httpx.AsyncBaseTransport | None:
    return DemoFhirTransport() if url.rstrip("/") == DEMO_FHIR_URL else None


def matches(resource: dict[str, Any], params: dict[str, str]) -> bool:
    for key, value in params.items():
        if key.startswith("_"):
            continue
        if key == "identifier":
            system, _, number = value.rpartition("|")
            identifiers = resource.get("identifier", [])
            if isinstance(identifiers, dict):  # Bundle has one Identifier; Patient has a list.
                identifiers = [identifiers]
            if not any(
                i.get("value") == (number or value) and (not system or i.get("system") == system)
                for i in identifiers
            ):
                return False
        elif key in {"patient", "subject"}:
            refs = [resource.get(k, {}).get("reference", "") for k in ("patient", "subject")]
            refs += [
                p.get("actor", {}).get("reference", "") for p in resource.get("participant", [])
            ]
            if not any(r == value or r == f"Patient/{value}" for r in refs):
                return False
        elif key == "telecom":
            if not any(i.get("value") == value for i in resource.get("telecom", [])):
                return False
        elif key in {"status", "clinical-status"}:
            status = resource.get("clinicalStatus" if key == "clinical-status" else "status")
            codes = (
                [c.get("code") for c in status.get("coding", [])]
                if isinstance(status, dict)
                else [status]
            )
            if not set(value.split(",")) & set(codes):
                return False
        elif key == "date":
            start = resource.get("start", "")
            if value.startswith("ge") and start[:10] < value[2:]:
                return False
        else:
            # Unsupported queries must not silently expose unrelated resources.
            return False
    return True


async def search(db: AsyncSession, kind: str, params: dict[str, str]) -> list[dict[str, Any]]:
    found = await db.execute(
        text("SELECT body FROM demo_fhir_resource WHERE kind=:kind ORDER BY id"), {"kind": kind}
    )
    return [body for (body,) in found if matches(body, params)]


def outcome(status: int, code: str) -> httpx.Response:
    return httpx.Response(
        status,
        json={
            "resourceType": "OperationOutcome",
            "issue": [
                {
                    "severity": "error",
                    "code": code,
                    "diagnostics": "Synthetic demo FHIR request refused",
                }
            ],
        },
    )


async def store(
    db: AsyncSession,
    resource: dict[str, Any],
    conditional: str | None = None,
    *,
    updating: bool = False,
    version: str | None = None,
) -> tuple[dict[str, Any], bool]:
    kind = resource["resourceType"]
    if conditional:
        params = {k: v[0] for k, v in parse_qs(conditional).items()}
        existing = await search(db, kind, params)
        if existing:
            return existing[0], False
    body = copy.deepcopy(resource)
    rid = str(body.get("id") or uuid.uuid4())
    existing = (
        await db.execute(
            text("SELECT version FROM demo_fhir_resource WHERE kind=:kind AND id=:id FOR UPDATE"),
            {"kind": kind, "id": rid},
        )
    ).scalar_one_or_none()
    if updating and (existing is None or str(existing) != version):
        raise ValueError("version conflict")
    revision = (existing or 0) + 1
    body["id"] = rid
    body["meta"] = {
        **body.get("meta", {}),
        "versionId": str(revision),
        "lastUpdated": datetime.now(UTC).isoformat(),
    }
    await db.execute(
        text(
            "INSERT INTO demo_fhir_resource (kind,id,version,body) "
            "VALUES (:kind,:id,:version,CAST(:body AS jsonb)) ON CONFLICT (kind,id) DO UPDATE "
            "SET version=EXCLUDED.version, body=EXCLUDED.body"
        ),
        {"kind": kind, "id": rid, "version": revision, "body": json.dumps(body)},
    )
    return body, existing is None


def resolve(value: Any, refs: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {
            key: refs.get(item, item)
            if key == "reference" and isinstance(item, str)
            else resolve(item, refs)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [resolve(item, refs) for item in value]
    return value


class DemoFhirTransport(httpx.AsyncBaseTransport):
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/fhir").strip("/")
        if path == "metadata" and request.method == "GET":
            async with get_sessionmaker()() as db:
                await db.execute(text("SELECT 1 FROM demo_fhir_resource LIMIT 1"))
            return httpx.Response(
                200,
                json={
                    "resourceType": "CapabilityStatement",
                    "status": "active",
                    "kind": "instance",
                    "fhirVersion": "4.0.1",
                    "description": "Internal synthetic demo adapter; not a hospital connection",
                },
            )
        async with get_sessionmaker()() as db, db.begin():
            # Serializes conditional creates across instances, so retries cannot duplicate data.
            await db.execute(text("SELECT pg_advisory_xact_lock(748219061)"))
            parts = path.split("/")
            kind = parts[0] if parts else ""
            if request.method == "POST" and path.endswith("/_search"):
                params = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
                resources = await search(db, kind, params)
                return httpx.Response(
                    200,
                    json={
                        "resourceType": "Bundle",
                        "type": "searchset",
                        "total": len(resources),
                        "entry": [{"resource": r} for r in resources],
                    },
                )
            if request.method == "GET" and len(parts) == 2:
                found = (
                    await db.execute(
                        text("SELECT body FROM demo_fhir_resource WHERE kind=:kind AND id=:id"),
                        {"kind": kind, "id": parts[1]},
                    )
                ).scalar_one_or_none()
                return httpx.Response(200, json=found) if found else outcome(404, "not-found")
            if request.method == "POST" and not path:
                bundle = json.loads(request.content)
                if bundle.get("type") != "transaction":
                    return outcome(422, "not-supported")
                entries = copy.deepcopy(bundle.get("entry", []))
                refs = {}
                for entry in entries:
                    resource = entry.get("resource", {})
                    resource.setdefault("id", str(uuid.uuid4()))
                    if entry.get("fullUrl"):
                        refs[entry["fullUrl"]] = f"{resource['resourceType']}/{resource['id']}"
                    conditional = entry.get("request", {}).get("ifNoneExist")
                    if conditional:
                        existing = await search(
                            db,
                            resource["resourceType"],
                            {k: v[0] for k, v in parse_qs(conditional).items()},
                        )
                        if existing and entry.get("fullUrl"):
                            refs[entry["fullUrl"]] = (
                                f"{resource['resourceType']}/{existing[0]['id']}"
                            )
                responses = []
                for entry in entries:
                    if entry.get("request", {}).get("method") not in {"POST", "PUT"}:
                        raise ValueError("unsupported transaction method")
                    body, created = await store(
                        db,
                        resolve(entry["resource"], refs),
                        entry.get("request", {}).get("ifNoneExist"),
                    )
                    responses.append(
                        {
                            "resource": body,
                            "response": {
                                "status": "201 Created" if created else "200 OK",
                                "location": (
                                    f"{body['resourceType']}/{body['id']}/_history/"
                                    f"{body['meta']['versionId']}"
                                ),
                            },
                        }
                    )
                return httpx.Response(
                    200,
                    json={
                        "resourceType": "Bundle",
                        "type": "transaction-response",
                        "entry": responses,
                    },
                )
            if request.method in {"POST", "PUT"} and kind and not path.endswith("$validate"):
                resource = json.loads(request.content)
                if resource.get("resourceType") != kind:
                    return outcome(422, "invalid")
                if request.method == "PUT":
                    if len(parts) != 2 or resource.get("id") != parts[1]:
                        return outcome(422, "invalid")
                    version = (
                        request.headers.get("if-match", "").removeprefix('W/"').removesuffix('"')
                    )
                    try:
                        body, created = await store(db, resource, updating=True, version=version)
                    except ValueError:
                        return outcome(412, "conflict")
                else:
                    body, created = await store(db, resource, request.headers.get("if-none-exist"))
                return httpx.Response(
                    201 if created else 200,
                    json=body,
                    headers={
                        "Location": f"{kind}/{body['id']}/_history/{body['meta']['versionId']}"
                    },
                )
        return outcome(422, "not-supported")
