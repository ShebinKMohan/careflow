"""Fixtures: stub ports, a graph on the in-memory checkpointer, the scripted chest-pain intake,
and (for the restart test) a throwaway `careflow_intake_test` Postgres database.

The Postgres tests need the M0-05 Postgres (`make up`) or the CI container. They use their own
database, because the API suite drops and recreates `careflow_test` in the same pytest run.
Without Postgres they are skipped, unless CAREFLOW_REQUIRE_DB=1 (CI), where they fail.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from psycopg import sql

from careflow_contracts.generated.urgency_suggestion_schema import UrgencySuggestion
from careflow_intake.checkpoint import libpq_url, memory_checkpointer, setup_postgres
from careflow_intake.graph import IntakeGraph, build_graph
from careflow_intake.ports import IntakePorts, Question
from careflow_intake.stubs import ScriptedConversation, stub_ports

REPO = Path(__file__).resolve().parents[3]
FIXTURES = Path(__file__).parent / "fixtures"
INTAKE_TEST_DB = "careflow_intake_test"
DROP_DB = sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(INTAKE_TEST_DB))


type Script = dict[str, Any]
type MakePorts = Callable[..., IntakePorts]
type MakeDecision = Callable[..., dict[str, Any]]


@pytest.fixture
def script() -> Script:
    """The scripted chest-pain intake (tests/fixtures/chest_pain.json)."""
    return json.loads((FIXTURES / "chest_pain.json").read_text(encoding="utf-8"))


@pytest.fixture
def make_ports(script: Script) -> MakePorts:
    """Stub ports with the scripted conversation; keyword arguments replace ports."""
    questions = [Question.model_validate(q) for q in script["questions"]]

    def make(**overrides: Any) -> IntakePorts:
        overrides.setdefault("conversation", ScriptedConversation(questions))
        return stub_ports(**overrides)

    return make


@pytest.fixture
def ports(make_ports: MakePorts) -> IntakePorts:
    return make_ports()


@pytest.fixture
def graph(ports: IntakePorts) -> IntakeGraph:
    return build_graph(ports, memory_checkpointer())


@pytest.fixture
def make_decision() -> MakeDecision:
    """A valid CONFIRM of the suggestion; keyword arguments override any field."""

    def make(
        session_id: str, suggestion: UrgencySuggestion | None, **overrides: Any
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "decision_id": "dec_001",
            "session_id": session_id,
            "suggestion_id": suggestion.suggestion_id if suggestion else None,
            "action": "CONFIRM",
            "final_level": suggestion.level if suggestion else 3,
            "scale": "KTAS",
            "nurse_user_id": "nurse_001",
            "decided_at": "2026-09-27T09:05:00Z",
        }
        body.update(overrides)
        return body

    return make


# --- Postgres (restart test) ---------------------------------------------------------------


def _env(key: str) -> str | None:
    value = os.environ.get(key)
    if value:
        return value
    env_file = REPO / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip() or None
    return None


def _admin_url() -> str:
    explicit = _env("CAREFLOW_TEST_ADMIN_URL")
    if explicit:
        return libpq_url(explicit)
    password = _env("POSTGRES_PASSWORD") or "unset"
    return f"postgresql://postgres:{password}@localhost:5432/postgres"


def _with_db(url: str, db: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{db}", parts.query, parts.fragment))


@pytest.fixture
async def postgres_url() -> AsyncIterator[str]:
    """A fresh `careflow_intake_test` database with the checkpoint tables."""
    admin = _admin_url()
    try:
        async with await psycopg.AsyncConnection.connect(admin, autocommit=True) as conn:
            await conn.execute(DROP_DB)
            await conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(INTAKE_TEST_DB)))
    except psycopg.OperationalError as exc:
        if os.environ.get("CAREFLOW_REQUIRE_DB") == "1":
            raise
        pytest.skip(f"requires_postgres: {type(exc).__name__} (run `make up`)")
    url = _with_db(admin, INTAKE_TEST_DB)
    await setup_postgres(url)
    yield url
    async with await psycopg.AsyncConnection.connect(admin, autocommit=True) as conn:
        await conn.execute(DROP_DB)
