"""Prepare a NEW Neon demo database; never pointed at the original local database.

Read Vercel's gitignored .env.vercel-demo, create least-privilege app/migrator roles, migrate
and seed synthetic personas. Save deployment variables to .env.vercel-runtime, not .env.
No credentials are printed. Idempotent; refuses any URL that is not a Neon host.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
from pathlib import Path

import psycopg
import yaml
from alembic import command
from alembic.config import Config
from dotenv import dotenv_values
from psycopg import sql
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    values = dotenv_values(ROOT / ".env.vercel-demo")
    owner_url = values.get("DATABASE_URL_UNPOOLED")
    if not owner_url or not (make_url(owner_url).host or "").endswith(".neon.tech"):
        raise SystemExit("Expected a Neon DATABASE_URL_UNPOOLED in .env.vercel-demo")
    runtime_path = ROOT / ".env.vercel-runtime"
    saved = dict(dotenv_values(runtime_path)) if runtime_path.exists() else {}

    def password(role: str) -> str:
        configured = saved.get(
            "DATABASE_URL" if role == "careflow_app" else "DATABASE_URL_MIGRATOR"
        )
        return str(make_url(configured).password) if configured else secrets.token_urlsafe(32)

    passwords = {role: password(role) for role in ("careflow_app", "careflow_migrator")}
    with psycopg.connect(owner_url, autocommit=True, connect_timeout=15) as db:
        row = db.execute("SELECT current_user").fetchone()
        if row is None:
            raise RuntimeError("Database did not return the current role")
        current = row[0]
        for role, secret in passwords.items():
            found = db.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)).fetchone()
            if not found:
                db.execute(
                    sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS").format(
                        sql.Identifier(role), sql.Literal(secret)
                    )
                )
            db.execute(
                sql.SQL("GRANT {} TO {}").format(sql.Identifier(role), sql.Identifier(current))
            )
        db.execute("GRANT USAGE, CREATE ON SCHEMA public TO careflow_migrator")
        db.execute("CREATE EXTENSION IF NOT EXISTS vector")
    # Keep a direct endpoint: LangGraph holds transaction/advisory locks across graph steps.
    base = make_url(owner_url)
    for role in passwords:
        name = "DATABASE_URL" if role == "careflow_app" else "DATABASE_URL_MIGRATOR"
        saved[name] = base.set(username=role, password=passwords[role]).render_as_string(
            hide_password=False
        )
    saved.update(
        {
            "JWT_SECRET": saved.get("JWT_SECRET") or secrets.token_urlsafe(48),
            "CAREFLOW_HANDOVER_SIGNING_KEY": saved.get("CAREFLOW_HANDOVER_SIGNING_KEY")
            or secrets.token_urlsafe(48),
            "CAREFLOW_DEV_PASSWORD": saved.get("CAREFLOW_DEV_PASSWORD")
            or secrets.token_urlsafe(24),
            "CAREFLOW_DEV_DEVICE_SECRET": saved.get("CAREFLOW_DEV_DEVICE_SECRET")
            or secrets.token_urlsafe(32),
            "CAREFLOW_ENV": "dev",
            "CAREFLOW_SERVERLESS": "1",
            "CAREFLOW_DEMO_FHIR": "1",
            "CAREFLOW_DEMO_MODE": "0",
            "CAREFLOW_PROVIDERS_CONFIG": "config/providers.gemini.yaml",
            "CAREFLOW_RAG_CONFIG": "config/rag.gemini.yaml",
            "CAREFLOW_GEMINI_VOICE": "1",
            "CAREFLOW_DEMO_ACCESS": "1",
            "FHIR_BASE_URL": "https://careflow-demo.internal/fhir",
            "VITALS_SIM_SPEED": "0",
            "TRIAGE_MODEL_KIND": "laya",
            "TRIAGE_MODEL_PATH": "/missing-trained-laya-checkpoint",
        }
    )
    gemini = dotenv_values(ROOT / ".env").get("GEMINI_API_KEY")
    if gemini:
        saved["GEMINI_API_KEY"] = gemini
    redis_url = values.get("REDIS_URL") or values.get("KV_URL")
    if redis_url:
        saved["REDIS_URL"] = redis_url
    runtime_path.write_text("".join(f"{k}={json.dumps(v)}\n" for k, v in saved.items() if v))
    runtime_path.chmod(0o600)
    os.environ.update({k: str(v) for k, v in saved.items() if v})
    os.environ["ALEMBIC_DATABASE_URL"] = str(saved["DATABASE_URL_MIGRATOR"])
    command.upgrade(Config(str(ROOT / "services/api/alembic.ini")), "head")
    from careflow_api.cli import seed_dev, setup_checkpoints

    seed_dev()
    setup_checkpoints()
    asyncio.run(seed_fhir())
    seed_handbook()
    print("Hosted demo database ready. Secrets saved to .env.vercel-runtime; values omitted.")


async def seed_fhir() -> None:
    from careflow_api.hosting.fhir import factory
    from careflow_integration.fhir.client import FhirClient, configure_transport

    configure_transport(factory)
    personas = yaml.safe_load((ROOT / "infra/seed/personas.yaml").read_text())["personas"]
    async with FhirClient("https://careflow-demo.internal/fhir") as client:
        for path in sorted((ROOT / "infra/seed/personas").glob("*.json")):
            bundle = json.loads(path.read_text())
            for entry in bundle["entry"]:
                r = entry["resource"]
                if r["resourceType"] == "Patient":
                    persona = next((p for p in personas if p["fhir_patient_id"] == r["id"]), {})
                    if persona.get("mock_national_id"):
                        r.setdefault("identifier", []).append(
                            {
                                "system": "https://careflow.example/fhir/sid/mock-national-id",
                                "value": persona["mock_national_id"],
                            }
                        )
            await client.transaction(bundle)
    print("Synthetic FHIR personas loaded.")


def seed_handbook() -> None:
    from careflow_api.cli import CLINICS, dev_id
    from careflow_rag.gemini_embed import GeminiEmbedder
    from careflow_rag.handbook import load
    from careflow_rag.ingest import ingest

    chunks = load(ROOT / "services/rag/handbook")
    embedder = GeminiEmbedder()
    with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=15) as conn:
        for clinic in CLINICS:
            counts = ingest(conn, chunks, clinic_id=dev_id(clinic), embedder=embedder)
            print(f"Handbook {clinic}: {counts}")


if __name__ == "__main__":
    main()
