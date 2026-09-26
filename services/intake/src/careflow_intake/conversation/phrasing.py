"""LLM phrasing of approved questions (M1-07). The provider task `conversation` may only rephrase a
template. An error, a timeout, a reply that is not exactly one question, over 40 words, or blocked
by the output filter falls back to the template itself.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Final, Literal

from careflow_contracts.generated.structured_facts_schema import FactKey
from careflow_intake.conversation.output_filter import MAX_WORDS, check_patient_facing
from careflow_providers.base import ChatProvider, strip_thinking

logger = logging.getLogger("careflow_intake.conversation")

PROMPT: Final = (
    "Rephrase this question kindly in plain English, one question, max 25 words, no advice, "
    "no diagnosis: {template}"
)
DEFAULT_TIMEOUT_SECONDS: Final = 3.0

type Source = Literal["llm", "template"]


def acceptable(text: str) -> bool:
    """Exactly one question, at most 40 words, and allowed by the patient-facing filter."""
    return (
        text.count("?") == 1
        and text.endswith("?")
        and len(text.split()) <= MAX_WORDS
        and check_patient_facing(text).allowed
    )


def _clean(reply: str) -> str:
    lines = [line.strip().strip('"“”').strip() for line in strip_thinking(reply).splitlines()]
    return next((line for line in lines if line), "")


class Phraser:
    """Rephrases approved templates with the `conversation` provider, or returns them unchanged."""

    def __init__(
        self,
        provider: ChatProvider | None,
        *,
        enabled: bool = True,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.provider = provider
        self.enabled = enabled and provider is not None
        self.timeout_seconds = timeout_seconds

    async def phrase(self, key: FactKey, template: str) -> tuple[str, Source]:
        if not self.enabled or self.provider is None:
            return template, "template"
        prompt = PROMPT.replace("{template}", template)
        try:
            reply = await asyncio.wait_for(
                self.provider.chat([{"role": "user", "content": prompt}], max_tokens=80),
                timeout=self.timeout_seconds,
            )
        except TimeoutError:
            logger.info("phrasing.fallback", extra={"key": key.value, "reason": "timeout"})
            return template, "template"
        except Exception as exc:  # the template is always safe to use
            logger.info("phrasing.fallback", extra={"key": key.value, "reason": type(exc).__name__})
            return template, "template"
        text = _clean(reply.text)
        if not acceptable(text):
            logger.info("phrasing.fallback", extra={"key": key.value, "reason": "not_acceptable"})
            return template, "template"
        return text, "llm"


__all__ = ["DEFAULT_TIMEOUT_SECONDS", "PROMPT", "Phraser", "acceptable"]
