"""Fixtures: a throwaway `careflow_rag_test` database migrated by the API's Alembic scripts (M2-08).

The tests that need it are skipped without Postgres, unless CAREFLOW_REQUIRE_DB=1 (CI), where they
fail. They run on the isolated test stack: `python3 scripts/stack.py run --stack careflow-test --
uv run pytest services/rag`.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

from careflow_rag.scratch import ScratchDatabaseError, ScratchDb, create, drop

TEST_DB = "careflow_rag_test"


@pytest.fixture(scope="session")
def scratch() -> Iterator[ScratchDb]:
    try:
        db = create(TEST_DB)
    except ScratchDatabaseError as exc:
        if os.environ.get("CAREFLOW_REQUIRE_DB") == "1":
            raise
        pytest.skip(f"requires_postgres: {exc}")
    try:
        yield db
    finally:
        drop(TEST_DB)
