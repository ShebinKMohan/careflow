"""Explicit, bounded synthetic demo adapters; no model or vendor API is called.

The original graph, fact verification, clinical rules, consent, authentication,
database, queue and FHIR ports still run. This is a workflow demo, not live AI.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any, Final

from careflow_contracts.generated.fact_schema import TextFact, TriStateFact
from careflow_contracts.generated.structured_facts_schema import FactKey, Language, Turn, TurnRole
from careflow_intake.extraction.extractor import LlmExtractor
from careflow_intake.extraction.record import ScriptedChat
from careflow_intake.facts import FactEntry, fact_class
from careflow_intake.ports import ExtractionResult
from careflow_intake.tap import explicit_answer, tap_value
from careflow_providers.base import ChatResult, Message
from careflow_providers.errors import ProviderUnavailable
from careflow_rag.agent import ANSWER_PROMPT, CLASSIFY_PROMPT, PLAN_PROMPT

DEMO_MODEL: Final = "demo-scripted-v1"
DEMO_TASKS: Final = frozenset({"conversation", "extraction", "summary"})


def normalise(text: str) -> str:
    return " ".join(text.casefold().replace("’", "'").split()).strip(" .!?؟")


# Whole statements only: substrings must never turn a denial or hypothetical
# into a positive danger fact. These are hand-written synthetic examples.
SAMPLES: Final[dict[str, tuple[tuple[str, str | bool], ...]]] = {
    "i have had a cough for two days": (("symptoms", "cough"),),
    "i have a cough": (("symptoms", "cough"),),
    "i have a headache": (("symptoms", "headache"),),
    "i have chest pain": (("chest_pain.present", True),),
    "i've had chest pain since this morning": (("chest_pain.present", True),),
    "i have a fever": (("infection.fever_reported", True),),
    "i hurt my ankle yesterday": (("injury", True),),
    "عندي سعال منذ يومين": (("symptoms", "cough"),),
    "عندي سعال": (("symptoms", "cough"),),
    "عندي صداع": (("symptoms", "headache"),),
    "عندي ألم في الصدر": (("chest_pain.present", True),),
    "عندي حمى": (("infection.fever_reported", True),),
}


class DemoExtractor:
    """Verbatim answers and exact sample statements, through original verification.

    Unsupported descriptions remain visible and are marked incomplete for the
    nurse. No arbitrary symptom, diagnosis, negative answer or urgency is guessed.
    """

    async def extract(
        self,
        turns: Sequence[Turn],
        existing_facts: Sequence[FactEntry],
        question_keys: Mapping[str, FactKey],
        language: Language = Language.en,
    ) -> ExtractionResult:
        facts: list[dict[str, Any]] = []
        incomplete = True
        if turns and turns[-1].role is TurnRole.patient:
            turn = turns[-1]
            key = question_keys.get(turn.turn_id)
            label = f"t{len(turns)}"
            quote = turn.text[:1000]

            def add(name: str, value: str | int | bool) -> None:
                facts.append({"key": name, "value": value, "turn_id": label, "quote": quote})

            if key is not None and quote.strip():
                if issubclass(fact_class(key), TextFact):
                    add(key.value, quote)
                    incomplete = key is FactKey.chief_complaint
                else:
                    value = tap_value(key, turn.text)
                    explicit = (
                        explicit_answer(turn.text)
                        if issubclass(fact_class(key), TriStateFact)
                        else None
                    )
                    if value is not None:
                        add(key.value, value)
                        incomplete = False
                    elif explicit is not None:
                        add(key.value, explicit[0].value)
                        incomplete = not explicit[1]
                # Extra facts only for a complete, approved sample description.
                sample = None if {"?", "؟"} & set(turn.text) else SAMPLES.get(normalise(turn.text))
                if sample:
                    for name, value in sample:
                        add(name, value)
                    incomplete = False
                incomplete = incomplete or len(turn.text) > 1000
        provider = ScriptedChat([json.dumps({"facts": facts}, ensure_ascii=False)])
        provider.model_name = DEMO_MODEL
        result = await LlmExtractor(provider, provider_kind="demo").extract(
            turns, existing_facts, question_keys, language
        )
        return result.model_copy(
            update={
                "extraction_incomplete": incomplete or result.extraction_incomplete,
                "error_code": "demo_limited_extraction" if incomplete else result.error_code,
            }
        )


# Exact practical questions, rather than a permissive medical classifier. Anything
# else follows the existing medical refusal and nurse-flag path.
FAQ: Final[dict[str, str]] = {
    "where are the toilets": "facilities.toilets",
    "where is the toilet": "facilities.toilets",
    "أين دورات المياه": "facilities.toilets",
    "where can i park": "parking.",
    "where is the parking": "parking.",
    "أين موقف السيارات": "parking.",
    "what are the opening hours": "hours.opening",
    "when does the clinic open": "hours.opening",
    "ما هي ساعات العمل": "hours.opening",
    "where is the pharmacy": "pharmacy.",
    "أين الصيدلية": "pharmacy.",
    "is there wifi": "facilities.wifi",
    "is there wi-fi": "facilities.wifi",
    "what is my queue position": "live:queue",
    "how long is the wait": "live:queue",
    "how long will i wait": "live:queue",
    "كم مدة الانتظار": "live:queue",
}


class DemoChat:
    """Copied fact summaries and cited handbook excerpts; not generated AI text."""

    model_name = DEMO_MODEL

    def __init__(self, task: str) -> None:
        self.task = task

    async def chat(
        self,
        messages: Sequence[Message],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> ChatResult:
        system = messages[0]["content"] if messages and messages[0]["role"] == "system" else ""
        user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        if self.task == "summary":
            sentences = []
            for line in user.removeprefix("Items:\n").splitlines():
                match = re.fullmatch(r"([A-Za-z0-9_.:-]+) \[[^\]]+\] (.+)", line)
                if match and len(match[2]) <= 400:
                    sentences.append({"text": match[2], "fact_ids": [match[1]]})
            reply = {"sentences": sentences[:5]}
        elif system.startswith(CLASSIFY_PROMPT):
            match = re.fullmatch(r"<question>(.*)</question>", user, re.S)
            reply = {"intent": "clinic" if match and normalise(match[1]) in FAQ else "medical"}
        elif system.startswith(PLAN_PROMPT):
            match = re.fullmatch(r"<question>(.*)</question>", user, re.S)
            topic = FAQ.get(normalise(match[1])) if match else None
            reply = {
                "tools": ["queue_position", "wait_time"] if topic == "live:queue" else ["retrieve"]
            }
        elif system.startswith(ANSWER_PROMPT.split("{language}", 1)[0]):
            reply = self._answer(user)
        elif "set three flags" in system:
            match = re.fullmatch(r"<asked>.*</asked>\n<message>(.*)</message>", user, re.S)
            clinic = bool(match and normalise(match[1]) in FAQ)
            reply = {
                "intake_answer": False,
                "clinic_question": clinic,
                "medical_question": not clinic,
            }
        else:
            raise ProviderUnavailable(self.task, "unsupported scripted demo request")
        return ChatResult(text=json.dumps(reply, ensure_ascii=False), model=self.model_name)

    def _answer(self, user: str) -> dict[str, Any]:
        # Answer only from the current sources; ignore earlier conversation sources.
        current, _, tail = user.partition("</sources>")
        question = re.search(r"<question>([^<>]*)</question>\s*$", tail)
        topic = FAQ.get(normalise(question[1])) if question else None
        for match in re.finditer(
            r'<source id="([^"<>]+)" title="[^"<>]*">\n(.*?)\n</source>', current, re.S
        ):
            section, body = match[1], match[2]
            if topic is None or not section.startswith(topic):
                continue
            if section == "live:queue":
                data = json.loads(body)
                # The original output gate validates these against the live queue source.
                position, wait = data["position_in_queue"], data["estimated_wait_minutes"]
                arabic = bool(re.search(r"[\u0600-\u06ff]", question[1] if question else ""))
                parts = []
                if position is not None:
                    parts.append(
                        f"دورك في قائمة الانتظار: {position}."
                        if arabic
                        else f"Queue position: {position}."
                    )
                if wait is not None:
                    parts.append(
                        f"وقت الانتظار التقديري: {wait} دقيقة."
                        if arabic
                        else f"Estimated wait: {wait} minutes."
                    )
                if not parts:
                    continue
                text = " ".join(parts)
            else:
                text = body.strip()[:1000]
            return {"answer": text, "cited_section_ids": [section]}
        # Empty citations cause the existing agent to return its reception fallback.
        return {"answer": "Please ask at reception.", "cited_section_ids": []}

    async def stream(self, messages: Sequence[Message]) -> AsyncIterator[str]:
        yield (await self.chat(messages)).text
