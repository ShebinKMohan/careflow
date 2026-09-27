"""`careflow-api` command line: `careflow-api seed-dev` (manual development runs only) and
`careflow-api relay` (the outbox relay as its own process, with CAREFLOW_OUTBOX_RELAY=0 on the API).

Creates clinics `clinic-a` and `clinic-b`, each with a nurse, a doctor, an admin, a kiosk, a
board device and an integration service account. Passwords and device secrets come from
CAREFLOW_DEV_PASSWORD and CAREFLOW_DEV_DEVICE_SECRET (scripts/gen_dev_env.py); nothing secret is
printed or committed. Ids are deterministic (uuid5), so reruns change nothing.
"""

from __future__ import annotations

import os
import uuid
from typing import Final

import typer
from sqlalchemy import create_engine, text

from careflow_api.config import get_settings, sqlalchemy_url
from careflow_api.security import hash_password

NAMESPACE: Final = uuid.UUID("7b0f1f7e-5c1a-4c4e-9f3e-2f8a8f0c0a55")
CLINICS: Final = ("clinic-a", "clinic-b")
STAFF_ROLES: Final = ("nurse", "doctor", "admin")
DEVICE_KINDS: Final = ("kiosk", "board", "integration")

app = typer.Typer(help="CareFlow API tools. Research prototype; synthetic data only.")


def dev_id(*parts: str) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, "/".join(parts))


@app.callback()
def main_callback() -> None:
    """CareFlow API tools."""


@app.command("seed-dev")
def seed_dev() -> None:
    """Create the two development clinics with staff, devices and service accounts."""
    password = os.environ.get("CAREFLOW_DEV_PASSWORD") or get_env_file_value(
        "CAREFLOW_DEV_PASSWORD"
    )
    device_secret = os.environ.get("CAREFLOW_DEV_DEVICE_SECRET") or get_env_file_value(
        "CAREFLOW_DEV_DEVICE_SECRET"
    )
    if not password or not device_secret:
        typer.echo(
            "seed-dev: run scripts/gen_dev_env.py first (dev password and device secret)", err=True
        )
        raise typer.Exit(1)
    engine = create_engine(sqlalchemy_url(get_settings().database_url_migrator))
    password_hash, secret_hash = hash_password(password), hash_password(device_secret)
    created = 0
    with engine.begin() as connection:
        for clinic in CLINICS:
            clinic_id = dev_id(clinic)
            connection.execute(
                text(
                    "INSERT INTO clinic (id, name, timezone) VALUES (:id, :name, 'Asia/Dubai') "
                    "ON CONFLICT (id) DO NOTHING"
                ),
                {"id": clinic_id, "name": clinic},
            )
            connection.execute(
                text("SELECT set_config('app.clinic_id', :cid, true)"), {"cid": str(clinic_id)}
            )
            letter = clinic.rsplit("-", 1)[-1]
            for role in STAFF_ROLES:
                result = connection.execute(
                    text(
                        "INSERT INTO staff_user (id, clinic_id, email, password_hash, role) "
                        "VALUES (:id, :cid, :email, :hash, :role) ON CONFLICT (id) DO NOTHING"
                    ),
                    {
                        "id": dev_id(clinic, role),
                        "cid": clinic_id,
                        "email": f"{role}.{letter}@careflow.example",
                        "hash": password_hash,
                        "role": role,
                    },
                )
                created += result.rowcount
            for kind in DEVICE_KINDS:
                result = connection.execute(
                    text(
                        "INSERT INTO device (id, clinic_id, kind, label, secret_hash) "
                        "VALUES (:id, :cid, :kind, :label, :hash) ON CONFLICT (id) DO NOTHING"
                    ),
                    {
                        "id": dev_id(clinic, kind),
                        "cid": clinic_id,
                        "kind": kind,
                        "label": f"{clinic} {kind}",
                        "hash": secret_hash,
                    },
                )
                created += result.rowcount
    typer.echo(f"seed-dev: {created} new staff/device rows across {', '.join(CLINICS)}")
    for clinic in CLINICS:
        letter = clinic.rsplit("-", 1)[-1]
        typer.echo(
            f"  {clinic}: staff {', '.join(f'{r}.{letter}@careflow.example' for r in STAFF_ROLES)}"
        )
        typer.echo(
            f"  {clinic}: devices " + ", ".join(f"{k}={dev_id(clinic, k)}" for k in DEVICE_KINDS)
        )


CHECKPOINT_TABLES: Final = (
    "checkpoint_migrations",
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
)


@app.command("setup-checkpoints")
def setup_checkpoints() -> None:
    """Create or migrate the intake graph's checkpoint tables (as the migrator) and let the app
    role use them (M1-12). Idempotent; `make migrate` runs it after Alembic."""
    import asyncio

    from careflow_intake.checkpoint import setup_postgres

    settings = get_settings()
    asyncio.run(setup_postgres(settings.database_url_migrator))
    engine = create_engine(sqlalchemy_url(settings.database_url_migrator))
    with engine.begin() as connection:
        for table in CHECKPOINT_TABLES:
            connection.execute(
                text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO careflow_app")
            )
    typer.echo(f"setup-checkpoints: {len(CHECKPOINT_TABLES)} tables ready for careflow_app")


def get_env_file_value(key: str) -> str | None:
    """Read one key from the repo .env without printing it (pydantic-settings reads .env too)."""
    from careflow_api.config import REPO_ROOT

    env = REPO_ROOT / ".env"
    if not env.exists():
        return None
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip() or None
    return None


@app.command("relay")
def relay() -> None:
    """Publish the transactional outbox to the event stream until stopped (M1-11)."""
    import asyncio

    from redis.asyncio import Redis

    from careflow_api.events.relay import OutboxRelay

    settings = get_settings()

    async def run() -> None:
        redis = Redis.from_url(settings.redis_url, decode_responses=True)
        try:
            await OutboxRelay(redis, stream=settings.event_stream).run()
        finally:
            await redis.aclose()

    asyncio.run(run())


def main() -> None:
    app()
