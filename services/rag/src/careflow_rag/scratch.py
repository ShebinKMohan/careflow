"""A throwaway, migrated Postgres database for the retrieval tests and the retrieval eval (M2-08).

`rag_chunks` is part of the API's schema (migration 0009). The tests and the eval never touch the
dev `careflow` database: they create a database of their own, migrate it with the API's Alembic
scripts (as `careflow_migrator`, in a subprocess, so nothing here imports the API), add clinics,
and drop it afterwards. Connection settings come from the environment (the isolated test stack
sets them) or the repo's `.env`, and are never printed.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final
from urllib.parse import urlsplit, urlunsplit

import psycopg
from psycopg import sql

REPO: Final = Path(__file__).resolve().parents[4]
ALEMBIC_INI: Final = REPO / "services" / "api" / "alembic.ini"


class ScratchDatabaseError(RuntimeError):
    """The throwaway database could not be created or migrated (e.g. no Postgres running)."""


def setting(key: str) -> str | None:
    """`key` from the environment, else from the repo's `.env`."""
    value = os.environ.get(key)
    if value:
        return value
    path = REPO / ".env"
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip() or None
    return None


def libpq(url: str) -> str:
    """A SQLAlchemy URL as psycopg takes it."""
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def with_db(url: str, name: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{name}", parts.query, parts.fragment))


def admin_url() -> str:
    """The superuser URL, as the API's tests take it (`CAREFLOW_TEST_ADMIN_URL` first)."""
    explicit = os.environ.get("CAREFLOW_TEST_ADMIN_URL")
    if explicit:
        return libpq(explicit)
    password = setting("POSTGRES_PASSWORD") or "unset"
    return f"postgresql://postgres:{password}@localhost:5432/postgres"


@dataclass(frozen=True, slots=True)
class ScratchDb:
    name: str
    # The URLs hold passwords: never in a repr (pytest prints fixtures in failure reports).
    app_url: str = field(repr=False)  # careflow_app: row-level security applies
    migrator_url: str = field(repr=False)  # careflow_migrator: owns the schema

    def add_clinic(self, label: str) -> uuid.UUID:
        """A new clinic row (every rag_chunks row references one)."""
        clinic_id = uuid.uuid4()
        with psycopg.connect(self.migrator_url) as conn:
            conn.execute(
                "INSERT INTO clinic (id, name, timezone) VALUES (%s, %s, 'Asia/Dubai')",
                (clinic_id, f"{label}-{clinic_id.hex[:6]}"),
            )
        return clinic_id


def drop(name: str) -> None:
    """Drop the database `name`, disconnecting anyone still in it."""
    with psycopg.connect(admin_url(), autocommit=True) as conn:
        conn.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
        )


def create(name: str) -> ScratchDb:
    """Create `name` (dropping any earlier one), add the extensions, and migrate it to head."""
    admin = admin_url()
    ident = sql.Identifier(name)
    try:
        drop(name)
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(sql.SQL("CREATE DATABASE {} OWNER careflow_migrator").format(ident))
            conn.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO careflow_app").format(ident))
        # As infra/postgres/init/001-init.sql does for careflow: extensions need a superuser.
        with psycopg.connect(with_db(admin, name), autocommit=True) as conn:
            conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            conn.execute("CREATE EXTENSION IF NOT EXISTS pg_textsearch")
    except psycopg.Error as exc:
        raise ScratchDatabaseError(f"cannot create {name}: {type(exc).__name__}") from exc
    migrator_default = "postgresql://careflow_migrator:unset@localhost:5432/careflow"
    app_default = "postgresql://careflow_app:unset@localhost:5432/careflow"
    migrator = with_db(libpq(setting("DATABASE_URL_MIGRATOR") or migrator_default), name)
    app = with_db(libpq(setting("DATABASE_URL") or app_default), name)
    done = subprocess.run(  # noqa: S603 - fixed argv: this interpreter running alembic
        [sys.executable, "-m", "alembic", "-c", str(ALEMBIC_INI), "upgrade", "head"],
        env={**os.environ, "ALEMBIC_DATABASE_URL": migrator},
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode != 0:
        last = (done.stderr.strip().splitlines() or ["no output"])[-1]
        raise ScratchDatabaseError(f"cannot migrate {name}: {last[:200]}")
    return ScratchDb(name=name, app_url=app, migrator_url=migrator)


@contextmanager
def scratch_database(name: str) -> Iterator[ScratchDb]:
    """A migrated database called `name`, dropped on exit."""
    db = create(name)
    try:
        yield db
    finally:
        drop(name)


__all__ = [
    "ScratchDatabaseError",
    "ScratchDb",
    "create",
    "drop",
    "libpq",
    "scratch_database",
    "setting",
]
