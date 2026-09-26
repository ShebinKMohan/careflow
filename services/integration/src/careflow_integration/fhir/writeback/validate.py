"""Check resources with the server's `$validate` (M1-09 tests and evidence)."""

from __future__ import annotations

from typing import Any, Final

from careflow_integration.fhir.client import FhirClient

type Json = dict[str, Any]

BLOCKING: Final = frozenset({"error", "fatal"})


async def blocking_issues(client: FhirClient, resource: Json) -> list[Json]:
    """The `error` and `fatal` issues `$validate` reports for `resource` (nothing is stored)."""
    outcome = await client.validate(resource)
    return [issue for issue in outcome.get("issue", []) if issue.get("severity") in BLOCKING]


__all__ = ["BLOCKING", "blocking_issues"]
