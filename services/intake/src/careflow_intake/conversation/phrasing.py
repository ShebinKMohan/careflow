"""LLM phrasing of approved questions (M1-07). The provider task `conversation` may only rephrase a
template. An error, a timeout, a reply that is not exactly one question, over 40 words, or blocked
by the output filter falls back to the template itself. An Arabic session gets an Arabic rephrasing
in short, simple Modern Standard Arabic, ending with the Arabic question mark (M2-03).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Final, Literal

from careflow_contracts.generated.structured_facts_schema import FactKey, Language
from careflow_intake.conversation.output_filter import MAX_WORDS, check_patient_facing
from careflow_providers.base import ChatProvider, strip_thinking

logger = logging.getLogger("careflow_intake.conversation")

PROMPT: Final = (
    "Rephrase this question kindly in plain English, one question, max 25 words, no advice, "
    "no diagnosis: {template}"
)
PROMPTS: Final = {
    Language.en: PROMPT,
    Language.ar: (
        "Rephrase this question kindly in short, simple Modern Standard Arabic: one question "
        "ending with ؟, max 25 words, no advice, no diagnosis, numbers as Western digits. "
        "Reply with the Arabic question only: {template}"
    ),
}
QUESTION_MARKS: Final = {Language.en: "?", Language.ar: "؟"}
DEFAULT_TIMEOUT_SECONDS: Final = 3.0

type Source = Literal["llm", "template"]


def acceptable(text: str, language: Language = Language.en) -> bool:
    """Exactly one question, ending with the language's question mark, at most 40 words, and
    allowed by the patient-facing filter."""
    return (
        text.count("?") + text.count("؟") == 1
        and text.endswith(QUESTION_MARKS[Language(language)])
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

    async def phrase(
        self, key: FactKey, template: str, language: Language = Language.en
    ) -> tuple[str, Source]:
        if not self.enabled or self.provider is None:
            return template, "template"
        language = Language(language)
        prompt = PROMPTS[language].replace("{template}", template)
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
        if not acceptable(text, language):
            logger.info("phrasing.fallback", extra={"key": key.value, "reason": "not_acceptable"})
            return template, "template"
        return text, "llm"


__all__ = ["DEFAULT_TIMEOUT_SECONDS", "PROMPT", "PROMPTS", "Phraser", "acceptable"]
