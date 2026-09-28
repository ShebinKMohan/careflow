"""The child flow (M2-04, SHE-293): a parent's danger answers reach the rules without a model.

Explicit yes / no / not sure answers are read in any input mode before the extractor. A child's red
question whose answer could not be read becomes unknown after its last ask, never no. The child
branch never waits for vitals, runs the rules before the suggestion, and gets no suggestion from the
adults-only urgency model. Every test name contains "child" (`pytest services/intake -k child`).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

import pytest

from careflow_contracts.generated.fact_schema import TriState, TriStateFact
from careflow_contracts.generated.structured_facts_schema import FactKey, Language, Turn, TurnRole
from careflow_intake.checkpoint import memory_checkpointer
from careflow_intake.conversation import ChecklistConversation
from careflow_intake.conversation.child_questions import CHILD_AMBER, CHILD_BANK, CHILD_RED
from careflow_intake.facts import FactEntry, fact_class
from careflow_intake.graph import IntakeGraph, answer, build_graph, pending, start_intake
from careflow_intake.nodes.triage import CHILD_NO_SUGGESTION, suggest_urgency
from careflow_intake.patient_details import patient_turns_and_facts
from careflow_intake.ports import ExtractionResult
from careflow_intake.state import IntakeSettings, IntakeState
from careflow_intake.stubs import START, StubUrgencyModel, stub_ports
from careflow_intake.tap import answer_fact, explicit_answer, tap_value
from careflow_intake.wiring import CareflowRules

REPO: Final = Path(__file__).resolve().parents[3]
HARD_TO_WAKE: Final = FactKey.child_danger_hard_to_wake
PARENT_UNSURE: Final = "parent unsure"  # the qualifier's English line (alert_templates.yaml)

# (answer, reading, whether it says nothing more)
EXPLICIT: Final = [
    ("I am not sure.", TriState.unknown, True),  # the SHE-314 baseline's answer
    ("i'm not sure", TriState.unknown, True),
    ("I’m not sure, she's sleepy", TriState.unknown, False),  # a typographic apostrophe
    ("Not sure", TriState.unknown, True),
    ("not sure?", TriState.unknown, True),
    ("I don't know", TriState.unknown, True),
    ("No idea", TriState.unknown, True),  # begins with "no": unsure comes first
    ("maybe", TriState.unknown, True),
    ("Yes", TriState.yes, True),
    ("yes.", TriState.yes, True),
    ("Yes, since this morning.", TriState.yes, False),
    ("yeah she is", TriState.yes, False),
    ("No.", TriState.no, True),
    ("nope", TriState.no, True),
    ("No, she isn't.", TriState.no, True),
    ("no he does not", TriState.no, True),
    ("نعم", TriState.yes, True),
    ("نعم، منذ الصباح", TriState.yes, False),
    ("لا", TriState.no, True),
    ("لا.", TriState.no, True),
    ("لا أعرف", TriState.unknown, True),  # "I don't know" begins with "no" (لا)
    ("لا أدري", TriState.unknown, True),
    ("لست متأكداً", TriState.unknown, True),  # the kiosk's button
    ("ما أدري، نايمة كثير", TriState.unknown, False),
    # A filler before "I don't know" (M2-03: the model read the first one as no).
    ("والله ما أدري، ما انتبهت", TriState.unknown, False),
    ("والله، ما أدري", TriState.unknown, True),
    ("يعني مو متأكدة", TriState.unknown, True),
    ("Um, not sure", TriState.unknown, True),
    ("honestly I don't know", TriState.unknown, True),
]
NOT_EXPLICIT: Final = [
    "No, but she is very hard to wake",  # never "no"
    "no she is not hard to wake, she is very drowsy",
    "لا، لكنه يصعب إيقاظه جداً",
    "not really",
    "She is fine",
    "yesterday she was sleepy",
    "yes?",  # a question, not an answer
    "Sure",
    "",
    "والله",  # a filler alone
    "والله لا",  # a filler only goes before unsure: this no is left to the extractor
    "um yes",
]


@pytest.mark.parametrize(("text", "value", "whole"), EXPLICIT)
def test_a_child_explicit_answer_is_read_without_a_model(
    text: str, value: TriState, whole: bool
) -> None:
    assert explicit_answer(text) == (value, whole)


@pytest.mark.parametrize("text", NOT_EXPLICIT)
def test_a_child_answer_that_is_not_explicit_goes_to_the_extractor(text: str) -> None:
    assert explicit_answer(text) is None


def test_every_shipped_child_answer_label_maps_without_a_model() -> None:
    """The kiosk's and the phone's Yes / No / Not sure buttons, in English and Arabic, are read
    as taps and as explicit words (a voice or typed "Not sure" is the same answer)."""
    expected = {"yes": TriState.yes, "no": TriState.no, "notSure": TriState.unknown}
    checked = 0
    for app in ("kiosk", "companion"):
        for locale in ("en", "ar"):
            path = REPO / "apps" / app / "messages" / f"{locale}.json"
            labels = json.loads(path.read_text(encoding="utf-8"))["intake"]["answers"]
            for name, value in expected.items():
                assert tap_value(HARD_TO_WAKE, labels[name]) == value.value, (app, locale, name)
                assert explicit_answer(labels[name]) == (value, True), (app, locale, name)
                checked += 1
    assert checked == 12


def test_the_child_bank_is_all_yes_no_questions() -> None:
    assert all(issubclass(fact_class(key), TriStateFact) for key in CHILD_BANK)
    assert len(CHILD_RED) == 8 and len(CHILD_AMBER) == 8


# --- The child branch of the graph, with the real rules and a model that is slow, down or unsure --


@dataclass
class Extractor:
    """The complaint as said; for any other question, by `mode`: "fail" raises (as the adapter
    does on its timeout), "hang" never answers, "none" finds nothing, or the TriState value
    found for the question on screen."""

    mode: str = "fail"
    calls: list[str] = field(default_factory=list[str])

    async def extract(
        self,
        turns: Sequence[Turn],
        existing_facts: Sequence[FactEntry],
        question_keys: Mapping[str, FactKey],
        language: Language = Language.en,
    ) -> ExtractionResult:
        latest = turns[-1]
        key = question_keys.get(latest.turn_id)
        self.calls.append(latest.text)
        if key is FactKey.chief_complaint:
            complaint = answer_fact(latest, key, latest.text)
            return ExtractionResult(facts=[complaint] if complaint else [], provider="test")
        if self.mode == "fail":
            raise TimeoutError("the extractor timed out (test)")
        if self.mode == "hang":
            await asyncio.Event().wait()
        if self.mode == "none" or key is None:
            return ExtractionResult(provider="test")
        found = answer_fact(latest, key, self.mode)
        return ExtractionResult(facts=[found] if found else [], provider="test")


def child_graph(extractor: Extractor, urgency: StubUrgencyModel | None = None) -> IntakeGraph:
    ports = stub_ports(
        conversation=ChecklistConversation(),
        rules=CareflowRules(),
        extractor=extractor,
        urgency=urgency or StubUrgencyModel(),
    )
    return build_graph(ports, memory_checkpointer())


async def start_child(
    graph: IntakeGraph, session_id: str, *, years: int, months: int | None = None
) -> dict[str, Any]:
    """Check in, choose "My child" with the age (as the kiosk's POST /patient does), and answer
    the complaint: the graph then asks the bank."""
    await start_intake(graph, session_id=session_id, clinic_id="clinic_a", arrived_at=START)
    config = {"configurable": {"thread_id": session_id}}
    values = (await graph.aget_state(config)).values  # type: ignore[arg-type]
    turns, facts = patient_turns_and_facts(
        language=Language.en,
        turn_number=len(values.get("turns", [])) + 1,
        patient_type="child",
        age_years=years,
        age_months=months,
        now=START,
    )
    await graph.aupdate_state(
        config,  # type: ignore[arg-type]
        {"patient_type": "child", "turns": turns, "facts": facts},
    )
    return await answer(graph, session_id, "Fever since yesterday")


def question_key(result: dict[str, Any]) -> FactKey | None:
    question = (pending(result) or {}).get("question") or {}
    key = question.get("key")
    return FactKey(key) if key else None


def value_of(result: dict[str, Any], key: FactKey) -> tuple[str, str, str] | None:
    """(value, provenance, quote) of the current fact at `key`, or None."""
    found = [entry for entry in result.get("facts", []) if entry.key is key]
    if not found:
        return None
    fact = found[-1].fact
    return fact.value.value, fact.provenance.value, fact.source.quote


def alerts(result: dict[str, Any]) -> dict[str, str]:
    return {alert.rule_id.value: alert.display_text or "" for alert in result.get("alerts", [])}


async def test_a_child_not_sure_in_words_raises_the_red_alert_without_a_model() -> None:
    """SHE-314's baseline: "I am not sure." to "hard to wake", spoken, while the model hangs.
    The answer is read without it: unknown, PATIENT_STATED, citing the answer, and the red
    alert with the parent-unsure qualifier."""
    extractor = Extractor(mode="hang")
    graph = child_graph(extractor)
    result = await start_child(graph, "ses_child_unsure", years=2)
    assert question_key(result) is HARD_TO_WAKE
    result = await asyncio.wait_for(
        answer(graph, "ses_child_unsure", "I am not sure.", "voice"), timeout=10
    )
    assert value_of(result, HARD_TO_WAKE) == ("unknown", "PATIENT_STATED", "I am not sure.")
    assert PARENT_UNSURE in alerts(result)["NG143_RED_FLAG"]
    assert extractor.calls == ["Fever since yesterday"]  # never for the danger answer
    assert question_key(result) is CHILD_BANK[1]  # the bank moves on


@pytest.mark.parametrize("mode", ["fail", "none"])
async def test_a_child_red_answer_no_model_can_read_is_asked_again_then_unknown(mode: str) -> None:
    """The model is down ("fail") or reads nothing ("none"): the question is asked once more,
    then recorded as unknown, citing the parent's answer, so the red alert is raised. It never
    becomes "no"."""
    graph = child_graph(Extractor(mode=mode))
    said = "No, but she is very hard to wake"
    await start_child(graph, "ses_child_unread", years=3)
    first = await answer(graph, "ses_child_unread", said)
    assert value_of(first, HARD_TO_WAKE) is None
    assert first["flags"].get("extraction_incomplete") is (True if mode == "fail" else None)
    assert question_key(first) is HARD_TO_WAKE  # asked again
    assert "NG143_RED_FLAG" not in alerts(first)
    second = await answer(graph, "ses_child_unread", said)
    assert value_of(second, HARD_TO_WAKE) == ("unknown", "PATIENT_STATED", said)
    assert second["flags"]["extraction_incomplete"] is True  # the nurse checks the answers
    assert PARENT_UNSURE in alerts(second)["NG143_RED_FLAG"]
    assert question_key(second) is CHILD_BANK[1]


async def test_a_child_aged_9_unread_hard_to_wake_raises_child_hard_to_wake() -> None:
    graph = child_graph(Extractor(mode="fail"))
    await start_child(graph, "ses_child_nine", years=9)
    await answer(graph, "ses_child_nine", "he keeps nodding off")
    result = await answer(graph, "ses_child_nine", "he keeps nodding off")
    assert value_of(result, HARD_TO_WAKE) == ("unknown", "PATIENT_STATED", "he keeps nodding off")
    assert PARENT_UNSURE in alerts(result)["CHILD_HARD_TO_WAKE"]
    assert "NG143_RED_FLAG" not in alerts(result)  # NG143 is for under 5s (ADR-035)


async def test_a_child_amber_answer_no_model_can_read_stays_unanswered() -> None:
    """Amber needs a yes: nothing is invented for it, and the nurse sees the incomplete
    extraction."""
    graph = child_graph(Extractor(mode="fail"))
    result = await start_child(graph, "ses_child_amber", years=1, months=4)
    for _ in CHILD_RED:
        result = await answer(graph, "ses_child_amber", "No")
    first_amber = CHILD_AMBER[0]
    assert question_key(result) is first_amber
    for _ in range(2):
        result = await answer(graph, "ses_child_amber", "she's a bit quieter than usual")
    assert value_of(result, first_amber) is None
    assert result["flags"]["extraction_incomplete"] is True
    assert question_key(result) is CHILD_AMBER[1]
    assert alerts(result) == {}


async def test_a_child_yes_with_more_words_keeps_its_yes_when_the_model_fails() -> None:
    """The words after "Yes" still go to the extractor (they may hold other facts); when it
    fails, the yes is kept."""
    extractor = Extractor(mode="fail")
    graph = child_graph(extractor)
    await start_child(graph, "ses_child_yes", years=2)
    said = "Yes, since this morning."
    result = await answer(graph, "ses_child_yes", said)
    assert value_of(result, HARD_TO_WAKE) == ("yes", "PATIENT_STATED", said)
    assert extractor.calls[-1] == said
    assert result["flags"]["extraction_incomplete"] is True
    assert PARENT_UNSURE not in alerts(result)["NG143_RED_FLAG"]


@pytest.mark.parametrize(("found", "kept"), [("no", "unknown"), ("unknown", "unknown")])
async def test_a_child_not_sure_is_never_turned_into_no_by_the_model(found: str, kept: str) -> None:
    graph = child_graph(Extractor(mode=found))
    await start_child(graph, "ses_child_keep", years=2)
    result = await answer(graph, "ses_child_keep", "Not sure, probably not")
    assert value_of(result, HARD_TO_WAKE) == (kept, "PATIENT_STATED", "Not sure, probably not")
    assert "NG143_RED_FLAG" in alerts(result)


async def test_a_child_not_sure_with_more_words_takes_the_models_yes() -> None:
    """Of two readings of one answer the one that alerts sooner wins: the model's yes to an
    amber question over the explicit "not sure" (which alone would not alert)."""
    graph = child_graph(Extractor(mode="yes"))
    result = await start_child(graph, "ses_child_amber_yes", years=2)
    for _ in CHILD_RED:
        result = await answer(graph, "ses_child_amber_yes", "No")
    said = "Not sure, but she has barely moved today"
    result = await answer(graph, "ses_child_amber_yes", said)
    assert value_of(result, CHILD_AMBER[0]) == ("yes", "PATIENT_STATED", said)
    assert "NG143_AMBER_FLAG" in alerts(result)


async def test_a_child_session_runs_rules_before_the_suggestion_and_never_waits_for_vitals() -> (
    None
):
    """Taps only: a "Yes" to hard to wake and "No" to the rest. The intake ends at the nurse's
    review without a vitals or device step, the rules run right before the suggestion step, and
    the adults-only model is never asked."""
    urgency = StubUrgencyModel()
    extractor = Extractor(mode="none")
    graph = child_graph(extractor, urgency)
    result = await start_child(graph, "ses_child_path", years=2)
    answers = 0
    while (pending(result) or {}).get("kind") == "patient_input":
        key = question_key(result)
        text = "Yes" if key is HARD_TO_WAKE else "OK" if key is None else "No"
        result = await answer(graph, "ses_child_path", text, "tap")
        answers += 1
        assert answers <= len(CHILD_BANK) + 5, "the child intake does not end"
    assert (pending(result) or {}).get("kind") == "nurse_review"  # the nurse confirms the level
    trace: list[str] = result["node_trace"]
    first = trace.index("suggest_urgency")
    assert trace[first - 1] == "run_rules"
    assert not [node for node in trace if "vital" in node or "device" in node]
    assert result["suggestion"] is None
    assert result["suggestion_unavailable"] == CHILD_NO_SUGGESTION
    assert urgency.calls == 0
    assert extractor.calls == ["Fever since yesterday"]  # every tap was read without a model
    assert value_of(result, HARD_TO_WAKE) == ("yes", "PATIENT_STATED", "Yes")
    assert set(alerts(result)) == {"NG143_RED_FLAG"}


def triage_state(patient_type: str, years: int) -> IntakeState:
    age = answer_fact(
        Turn(turn_id="trn_004", role=TurnRole.patient, text=f"{years}", created_at=START),
        FactKey.patient_age_years,
        years,
    )
    assert age is not None
    return {
        "session_id": "ses_fifteen",
        "clinic_id": "clinic_a",
        "language": Language.en,
        "arrived_at": START,
        "patient_type": patient_type,  # type: ignore[typeddict-item]
        "facts": [age],
    }


async def test_a_child_of_15_gets_no_suggestion_but_an_adult_of_15_can() -> None:
    """The model's own guard stops at 15 (its training data start there); a child is under 16,
    so a 15-year-old checked in as "My child" must still get no suggestion."""
    urgency = StubUrgencyModel()
    ports = stub_ports(urgency=urgency)
    child = await suggest_urgency(triage_state("child", 15), ports, IntakeSettings())
    assert child["suggestion"] is None
    assert child["suggestion_unavailable"] == CHILD_NO_SUGGESTION
    assert urgency.calls == 0
    adult = await suggest_urgency(triage_state("adult", 15), ports, IntakeSettings())
    assert adult["suggestion"] is not None and urgency.calls == 1
