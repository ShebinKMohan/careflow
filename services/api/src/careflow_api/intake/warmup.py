"""Warm and verify the real local extraction prompts before the API accepts patients.

Only fixed synthetic statements are used. No session, FHIR record, event or patient fact is
created. This startup budget is independent from the unchanged per-patient deadline.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime
from typing import Any

import httpx

from careflow_contracts.generated.structured_facts_schema import FactKey, Language, Turn, TurnRole
from careflow_intake.extraction.extractor import LlmExtractor

logger = logging.getLogger("uvicorn.error")
SAMPLES = {Language.en: "My wrist hurts.", Language.ar: "أشعر بألم في معصمي."}


async def warm_local_extraction(
    extractor: Any, *, timeout_seconds: float = 60.0
) -> list[dict[str, Any]]:
    """Validate quoted facts for EN/AR using the configured provider and its exact prompt."""
    if not isinstance(extractor, LlmExtractor) or extractor.provider_kind != "local":
        return []
    rows: list[dict[str, Any]] = []
    # A separate wrapper shares the provider/prompt/schema; never mutate patient timeouts.
    warmer = LlmExtractor(
        extractor.provider, timeout_seconds=timeout_seconds, provider_kind="local"
    )
    try:
        async with asyncio.timeout(timeout_seconds):
            base_url = getattr(extractor.provider, "base_url", None)
            if isinstance(base_url, str):
                async with httpx.AsyncClient(timeout=2) as client:
                    while True:
                        try:
                            response = await client.get(f"{base_url.rstrip('/')}/models")
                            if response.is_success:
                                break
                        except httpx.HTTPError:
                            pass
                        await asyncio.sleep(0.5)
            for language, text in SAMPLES.items():
                began = time.monotonic()
                turn = Turn(
                    turn_id=f"startup-synthetic-{language.value}",
                    role=TurnRole.patient,
                    text=text,
                    question_key=FactKey.chief_complaint,
                    created_at=datetime.now(UTC),
                )
                result = await warmer.extract(
                    [turn], [], {turn.turn_id: FactKey.chief_complaint}, language
                )
                chief = next(
                    (item for item in result.facts if item.key == FactKey.chief_complaint), None
                )
                if result.extraction_incomplete or chief is None:
                    raise RuntimeError(
                        f"Local extraction warmup failed for {language.value}: "
                        "no verified chief complaint"
                    )
                fact = chief.fact.model_dump(mode="json")
                source = fact.get("source", {})
                quote = source.get("quote")
                if (
                    source.get("kind") != "transcript_span"
                    or not isinstance(quote, str)
                    or quote not in text
                ):
                    raise RuntimeError("Local extraction warmup produced an unverified source")
                row = {
                    "language": language.value,
                    "elapsed_ms": round((time.monotonic() - began) * 1000),
                    "verified_facts": len(result.facts),
                    "model": result.model_id,
                }
                rows.append(row)
                logger.info("Local extraction startup warmup verified: %s", row)
    except TimeoutError as exc:
        raise RuntimeError(
            "Local extraction startup warmup did not finish within 60 seconds"
        ) from exc
    return rows
