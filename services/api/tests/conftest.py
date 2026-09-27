"""Fixtures: a throwaway `careflow_test` database migrated by Alembic, two clinics, an ASGI client.

Needs the M0-05 Postgres (`make up`) or the CI container. The superuser URL is used only to
(re)create the test database; the schema is built as careflow_migrator and the app runs as
careflow_app, exactly as in production. Without Postgres the DB tests are skipped, unless
CAREFLOW_REQUIRE_DB=1 (CI), where they fail instead.
"""

from __future__ import annotations

import contextlib
import os
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url

API_DIR = Path(__file__).resolve().parents[1]
TEST_DB = "careflow_test"
TEST_JWT_SECRET = "test-only-jwt-secret-not-used-anywhere-else-0000"
TEST_PASSWORD = "Test-Password-123"
TEST_DEVICE_SECRET = "test-device-secret-000000"


def _env(key: str) -> str | None:
    value = os.environ.get(key)
    if value:
        return value
    env_file = API_DIR.parents[1] / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip() or None
    return None


def _with_db(url: str, db: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{db}", parts.query, parts.fragment))


# Settings for every test; the JWT secret is fixed so tests can mint tokens.
os.environ["JWT_SECRET"] = TEST_JWT_SECRET
_APP_URL = _env("DATABASE_URL") or "postgresql://careflow_app:unset@localhost:5432/careflow"
_MIGRATOR_URL = (
    _env("DATABASE_URL_MIGRATOR") or "postgresql://careflow_migrator:unset@localhost:5432/careflow"
)
os.environ["DATABASE_URL"] = _with_db(_APP_URL, TEST_DB)
os.environ["DATABASE_URL_MIGRATOR"] = _with_db(_MIGRATOR_URL, TEST_DB)
os.environ.setdefault("REDIS_URL", _env("REDIS_URL") or "redis://localhost:6379/0")
os.environ.setdefault("FHIR_BASE_URL", _env("FHIR_BASE_URL") or "http://localhost:8080/fhir")
# Never the dev stream `careflow:events`: its consumer groups would get entries for clinics that
# exist only in careflow_test (ADR-043). The `app` fixture gives each test a stream of its own.
os.environ["CAREFLOW_EVENT_STREAM"] = "careflow:test:unscoped"


def _admin_url() -> str:
    explicit = os.environ.get("CAREFLOW_TEST_ADMIN_URL")
    if explicit:
        return explicit
    password = _env("POSTGRES_PASSWORD") or "unset"
    return f"postgresql+psycopg://postgres:{password}@localhost:5432/postgres"


def _sync(url: str) -> str:
    return url.replace("postgresql://", "postgresql+psycopg://", 1)


@dataclass(frozen=True)
class Clinics:
    a: uuid.UUID
    b: uuid.UUID
    staff: dict[tuple[str, str], str]  # (clinic letter, role) -> email
    devices: dict[tuple[str, str], uuid.UUID]  # (clinic letter, kind) -> device id


@pytest.fixture(scope="session")
def database() -> Iterator[Engine]:
    """Create and migrate careflow_test; yield a migrator engine (schema owner)."""
    try:
        admin = create_engine(_admin_url(), isolation_level="AUTOCOMMIT")
        with admin.connect() as connection:
            connection.execute(text(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)"))
            connection.execute(text(f"CREATE DATABASE {TEST_DB} OWNER careflow_migrator"))
            connection.execute(text(f"GRANT CONNECT ON DATABASE {TEST_DB} TO careflow_app"))
        admin.dispose()
        # As infra/postgres/init/001-init.sql does for careflow: extensions need a superuser.
        test_admin = create_engine(
            make_url(_admin_url()).set(database=TEST_DB), isolation_level="AUTOCOMMIT"
        )
        with test_admin.connect() as connection:
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS pg_textsearch"))
        test_admin.dispose()
    except Exception as exc:  # noqa: BLE001
        if os.environ.get("CAREFLOW_REQUIRE_DB") == "1":
            raise
        pytest.skip(f"requires_postgres: {type(exc).__name__} (run `make up`)")

    from alembic import command
    from alembic.config import Config

    from careflow_api.config import get_settings

    get_settings.cache_clear()
    config = Config(str(API_DIR / "alembic.ini"))
    os.environ["ALEMBIC_DATABASE_URL"] = os.environ["DATABASE_URL_MIGRATOR"]
    command.upgrade(config, "head")
    from careflow_api.cli import setup_checkpoints

    setup_checkpoints()  # as `make migrate` does: the intake graph's checkpoint tables
    engine = create_engine(_sync(os.environ["DATABASE_URL_MIGRATOR"]))
    yield engine
    engine.dispose()


def make_clinics(database: Engine) -> Clinics:
    """Two new clinics, each with a nurse, doctor and admin, a kiosk, board and integration
    device, and one patient session."""
    from careflow_api.security import hash_password

    password_hash, secret_hash = hash_password(TEST_PASSWORD), hash_password(TEST_DEVICE_SECRET)
    ids = {"a": uuid.uuid4(), "b": uuid.uuid4()}
    staff: dict[tuple[str, str], str] = {}
    devices: dict[tuple[str, str], uuid.UUID] = {}
    with database.begin() as connection:
        for letter, clinic_id in ids.items():
            connection.execute(
                text("INSERT INTO clinic (id, name, timezone) VALUES (:id, :name, 'Asia/Dubai')"),
                {"id": clinic_id, "name": f"test-clinic-{letter}-{clinic_id.hex[:6]}"},
            )
            connection.execute(
                text("SELECT set_config('app.clinic_id', :c, true)"), {"c": str(clinic_id)}
            )
            for role in ("nurse", "doctor", "admin"):
                email = f"{role}.{letter}.{clinic_id.hex[:6]}@test.example"
                connection.execute(
                    text(
                        "INSERT INTO staff_user (clinic_id, email, password_hash, role) "
                        "VALUES (:c, :e, :h, :r)"
                    ),
                    {"c": clinic_id, "e": email, "h": password_hash, "r": role},
                )
                staff[(letter, role)] = email
            for kind in ("kiosk", "board", "integration"):
                device_id = uuid.uuid4()
                connection.execute(
                    text(
                        "INSERT INTO device (id, clinic_id, kind, label, secret_hash) "
                        "VALUES (:id, :c, :k, :l, :h)"
                    ),
                    {
                        "id": device_id,
                        "c": clinic_id,
                        "k": kind,
                        "l": f"{kind} {letter}",
                        "h": secret_hash,
                    },
                )
                devices[(letter, kind)] = device_id
            connection.execute(
                text(
                    "INSERT INTO patient_session (clinic_id, language, expires_at) "
                    "VALUES (:c, 'en', now() + interval '2 hours')"
                ),
                {"c": clinic_id},
            )
    return Clinics(a=ids["a"], b=ids["b"], staff=staff, devices=devices)


@pytest.fixture(scope="session")
def clinics(database: Engine) -> Clinics:
    return make_clinics(database)


@pytest.fixture(scope="session")
def clinics_factory(database: Engine) -> Callable[[], Clinics]:
    """Clinics of a test module's own, for tests that add sessions (the shared ones are counted)."""
    return lambda: make_clinics(database)


@pytest.fixture
async def redis_client() -> AsyncIterator[Redis]:
    """Valkey from compose (M0-05). Skipped when it does not answer, unless
    CAREFLOW_REQUIRE_REDIS=1 (CI), where the tests fail instead."""
    client = Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    try:
        await client.ping()
    except Exception as exc:  # noqa: BLE001
        await client.aclose()
        if os.environ.get("CAREFLOW_REQUIRE_REDIS") == "1":
            raise
        pytest.skip(f"requires_redis: {type(exc).__name__} (run `make up`)")
    yield client
    await client.aclose()


@pytest.fixture
async def stream() -> AsyncIterator[str]:
    """A stream of the test's own, deleted afterwards with its dead letters. Needs no Redis: a
    test without it wrote nothing, and the delete fails fast."""
    from careflow_api.events.bus import DEAD_SUFFIX

    name = f"careflow:test:{uuid.uuid4().hex}"
    yield name
    client = Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    with contextlib.suppress(RedisError):
        await client.delete(name, f"{name}{DEAD_SUFFIX}")
    await client.aclose()


@pytest.fixture
def new_clinic(database: Engine) -> Callable[[], uuid.UUID]:
    """Make a fresh clinic, so a test's rows never mix with another's."""

    def make() -> uuid.UUID:
        clinic_id = uuid.uuid4()
        with database.begin() as connection:
            connection.execute(
                text("INSERT INTO clinic (id, name, timezone) VALUES (:id, :name, 'Asia/Dubai')"),
                {"id": clinic_id, "name": f"events-{clinic_id.hex[:10]}"},
            )
        return clinic_id

    return make


@pytest.fixture
def spans() -> InMemorySpanExporter:
    return InMemorySpanExporter()


@pytest.fixture
async def app(
    spans: InMemorySpanExporter, stream: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[FastAPI]:
    """A fresh app (settings, engine and rate-limit buckets reset) on the test's own event stream;
    tests may add probe routes."""
    from careflow_api import aftercommit, db, ratelimit
    from careflow_api.config import get_settings
    from careflow_api.main import create_app
    from careflow_api.telemetry import build_tracer_provider

    monkeypatch.setenv("CAREFLOW_EVENT_STREAM", stream)
    get_settings.cache_clear()
    db.get_engine.cache_clear()
    db.get_sessionmaker.cache_clear()
    ratelimit.reset_memory()
    ratelimit.key_prefix = f"cf:test:{uuid.uuid4().hex}"
    yield create_app(tracer_provider=build_tracer_provider(exporter=spans))
    await aftercommit.drain()  # queue broadcasts still in flight write to the stream before it goes
    await db.get_engine().dispose()
    get_settings.cache_clear()  # the next test never sees this test's stream


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """An ASGI client from a unique IP, so rate-limit buckets never leak between tests."""
    ip = f"198.51.100.{uuid.uuid4().int % 250 + 1}"
    transport = ASGITransport(app=app, client=(ip, 40000))
    async with AsyncClient(transport=transport, base_url="http://api.test") as http:
        yield http
