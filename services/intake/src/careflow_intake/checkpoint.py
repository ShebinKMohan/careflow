"""Checkpointers for the intake graph.

State is stored with LangGraph's msgpack serializer and an explicit allowlist: only the contract
and intake models (and LangGraph's own safe types) are rebuilt from a checkpoint; any other type
is blocked. Postgres: `AsyncPostgresSaver`, whose `setup()` creates its tables once at startup
(it needs DDL rights, so run it with the migrator role).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from enum import Enum
from functools import cache
from inspect import isclass
from types import ModuleType

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from pydantic import BaseModel

import careflow_intake.facts
import careflow_intake.ports
from careflow_contracts.generated import (
    alert_schema,
    fact_schema,
    nurse_decision_schema,
    structured_facts_schema,
    urgency_suggestion_schema,
)

STATE_MODULES: tuple[ModuleType, ...] = (
    fact_schema,
    structured_facts_schema,
    alert_schema,
    urgency_suggestion_schema,
    nurse_decision_schema,
    careflow_intake.facts,
    careflow_intake.ports,
)


@cache
def state_types() -> tuple[type, ...]:
    """Every model and enum class defined in the modules the state is built from."""
    found = {
        obj
        for module in STATE_MODULES
        for obj in vars(module).values()
        if isclass(obj) and obj.__module__ == module.__name__ and issubclass(obj, BaseModel | Enum)
    }
    return tuple(sorted(found, key=lambda cls: (cls.__module__, cls.__name__)))


def serializer() -> JsonPlusSerializer:
    return JsonPlusSerializer(allowed_msgpack_modules=state_types())


def memory_checkpointer() -> InMemorySaver:
    """For tests and single-process demos."""
    return InMemorySaver(serde=serializer())


def libpq_url(url: str) -> str:
    """`postgresql+psycopg://...` (SQLAlchemy) -> `postgresql://...` (libpq/psycopg)."""
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


@asynccontextmanager
async def postgres_checkpointer(url: str) -> AsyncIterator[AsyncPostgresSaver]:
    """An `AsyncPostgresSaver` on its own connection, closed on exit."""
    async with AsyncPostgresSaver.from_conn_string(libpq_url(url), serde=serializer()) as saver:
        yield saver


async def setup_postgres(url: str) -> None:
    """Create or migrate the checkpoint tables (idempotent)."""
    async with postgres_checkpointer(url) as saver:
        await saver.setup()


__all__ = [
    "libpq_url",
    "memory_checkpointer",
    "postgres_checkpointer",
    "serializer",
    "setup_postgres",
    "state_types",
]
