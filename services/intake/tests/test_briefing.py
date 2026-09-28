"""The doctor briefing (M1-16) on the seven synthetic personas.

Every item must carry a valid source and the right tag (safety rule 4); a model-inferred item
always shows the nurse's level (safety rule 3); the optional summary is dropped whenever it adds
anything, and disabled when it is off or its provider is unavailable. The plain list is always
complete.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Sequence
from datetime import timedelta
from typing import Any

import openai
import pytest

from careflow_contracts.api_models import (
    Briefing,
    BriefingItem,
    BriefingNoteCode,
    BriefingSectionId,
    SummaryStatus,
)
from careflow_contracts.generated.fact_schema import (
    NurseSource,
    Provenance,
    TranscriptSpanSource,
)
from careflow_contracts.testing import error_messages, openapi_validator
from careflow_intake.briefing import BriefingNotReady, make_briefing
from careflow_intake.briefing.builder import SECTION_TITLES, briefing_json, level_text
from careflow_intake.briefing.check import PAIRS, check_briefing
from careflow_intake.briefing.record import (
    FIXTURES,
    PERSONAS,
    cassette,
    expected,
    load_persona,
    replay_summarizer,
)
from careflow_intake.briefing.summary import (
    PROMPT_VERSION,
    Summarizer,
    SummaryDraft,
    check_summary,
    fact_items,
)
from careflow_intake.state import IntakeState, structured_facts
from careflow_intake.wiring import IntakeConfig, briefing_summarizer
from careflow_providers.base import ChatResult, Message
from careflow_providers.errors import ProviderUnavailable

HALLUCINATED = json.loads((FIXTURES / "hallucinated.json").read_text(encoding="utf-8"))


def decided(state: IntakeState) -> Any:
    decision = state.get("nurse_decision")
    assert decision is not None
    return decision.decided_at + timedelta(minutes=1)


async def plain(state: IntakeState) -> Briefing:
    result = await make_briefing(state, now=decided(state))
    assert result.problems == []
    return result.briefing


def items(briefing: Briefing) -> list[tuple[BriefingSectionId, BriefingItem]]:
    return [(section.id, item) for section in briefing.sections for item in section.items]


class Scripted:
    """A summary provider that replies with a fixed text (a recorded or hand-written reply)."""

    def __init__(self, reply: str) -> None:
        self.task = "summary"
        self.model_name = "scripted"
        self.reply = reply
        self.calls = 0

    async def chat(
        self,
        messages: Sequence[Message],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> ChatResult:
        self.calls += 1
        return ChatResult(text=self.reply, model=self.model_name)

    async def stream(self, messages: Sequence[Message]) -> AsyncIterator[str]:
        yield self.reply


class Down:
    """A local server that is not running."""

    task = "summary"
    model_name = "qwen3.5-9b-4bit"

    async def chat(self, messages: Sequence[Message], **_: Any) -> ChatResult:
        raise openai.APIConnectionError(request=None)  # type: ignore[arg-type]

    async def stream(self, messages: Sequence[Message]) -> AsyncIterator[str]:
        raise openai.APIConnectionError(request=None)  # type: ignore[arg-type]
        yield ""


class Slow(Scripted):
    async def chat(self, messages: Sequence[Message], **_: Any) -> ChatResult:
        await asyncio.sleep(5)
        return await super().chat(messages)


def summary_reply(state: IntakeState, reply: Any) -> str:
    """A hand-written reply as the model's text, with `{f:<fact path>}` replaced by the id."""
    body = reply if isinstance(reply, str) else json.dumps(reply)
    for path, fact in _facts(state):
        body = body.replace("{f:" + path + "}", fact.fact_id)
    return body


def _facts(state: IntakeState) -> list[tuple[str, Any]]:
    from careflow_contracts.validators import iter_facts

    return list(iter_facts(structured_facts(state)))


@pytest.fixture(params=PERSONAS)
def persona(request: pytest.FixtureRequest) -> str:
    return str(request.param)


# --- the plain briefing -------------------------------------------------------------------


async def test_every_item_has_a_valid_source_and_the_right_tag(persona: str) -> None:
    state = load_persona(persona)
    briefing = await plain(state)
    facts = {fact.fact_id: fact for _, fact in _facts(state)}
    found = items(briefing)
    assert found, f"{persona}: no items"
    for section, item in found:
        assert not item.source_missing, f"{persona} {section.value}: {item.text}"
        assert isinstance(item.source, PAIRS[item.provenance]), item.text
        if item.fact_id is not None:
            fact = facts[item.fact_id]
            assert (fact.provenance, fact.source) == (item.provenance, item.source)


async def test_the_briefing_is_valid_contract_json(persona: str) -> None:
    briefing = await plain(load_persona(persona))
    body = briefing_json(briefing)
    assert error_messages(openapi_validator("Briefing"), body) == []
    assert body["summary"] is None
    assert body["summary_status"] == "disabled"


async def test_sections_come_in_the_fixed_order(persona: str) -> None:
    briefing = await plain(load_persona(persona))
    assert [(s.id, s.title) for s in briefing.sections] == list(SECTION_TITLES.items())
    assert [s.title for s in briefing.sections] == [
        "Alerts and triage",
        "Presenting complaint",
        "Danger-screen answers",
        "Vitals and NEWS2",
        "Record (EHR)",
        "Model output",
        "Data quality",
    ]


async def test_every_fact_of_the_session_is_in_the_briefing(persona: str) -> None:
    state = load_persona(persona)
    briefing = await plain(state)
    cited = {item.fact_id for _, item in items(briefing)}
    assert {fact.fact_id for _, fact in _facts(state)} <= cited


async def test_patient_stated_items_quote_the_patient(persona: str) -> None:
    briefing = await plain(load_persona(persona))
    for _, item in items(briefing):
        if item.provenance is Provenance.PATIENT_STATED:
            assert isinstance(item.source, TranscriptSpanSource)
            assert f'Patient: "{item.source.quote}"' in item.text


async def test_model_inferred_items_show_the_nurse_level(persona: str) -> None:
    state = load_persona(persona)
    briefing = await plain(state)
    nurse_level = level_text(state["nurse_decision"])  # type: ignore[arg-type]
    inferred = [item for _, item in items(briefing) if item.provenance is Provenance.MODEL_INFERRED]
    assert inferred
    for item in inferred:
        assert f"Nurse's level: {nurse_level}." in item.text
    assert briefing.triage.confirmed.level == state["nurse_decision"].final_level  # type: ignore[union-attr]


async def test_an_ecg_done_fact_is_a_nurse_item_under_alerts_and_triage() -> None:
    state = load_persona("chest-pain")
    briefing = await plain(state)
    ecg = next(fact for path, fact in _facts(state) if path == "tasks.ecg_done_at")
    alerts = briefing.sections[0]
    assert alerts.id is BriefingSectionId.alerts_triage
    item = next(item for item in alerts.items if item.fact_id == ecg.fact_id)
    assert item.provenance is Provenance.NURSE_CONFIRMED
    assert isinstance(item.source, NurseSource)
    assert item.text == "ECG done: 7 min after arrival."


async def test_the_triage_block_keeps_the_suggestion_beside_the_decision() -> None:
    chest = await plain(load_persona("chest-pain"))
    assert chest.triage.suggested is not None
    assert (int(chest.triage.suggested.level), int(chest.triage.confirmed.level)) == (3, 2)
    assert chest.triage.override_reason is not None
    child = await plain(load_persona("child-drowsy"))
    assert child.triage.suggested is None
    model = next(s for s in child.sections if s.id is BriefingSectionId.model_output)
    assert [n.code for n in model.notes or []] == [BriefingNoteCode.suggestion_unavailable]


async def test_data_quality_warnings_are_notes() -> None:
    quality = {
        persona: next(
            s for s in (await plain(load_persona(persona))).sections if s.id.value == "data_quality"
        )
        for persona in ("qr-phone", "no-show", "routine")
    }
    assert [n.code for n in quality["qr-phone"].notes or []] == [
        BriefingNoteCode.extraction_incomplete,
        BriefingNoteCode.facts_dropped,
        BriefingNoteCode.intake_stopped_early,
    ]
    assert (quality["qr-phone"].notes or [])[1].count == 2
    assert [n.code for n in quality["no-show"].notes or []] == [BriefingNoteCode.ehr_unavailable]
    assert [n.code for n in quality["routine"].notes or []] == [BriefingNoteCode.none_recorded]
    for section in quality.values():
        assert section.items == []


async def test_an_empty_section_says_so() -> None:
    briefing = await plain(load_persona("qr-phone"))
    notes = {s.id: [n.code for n in s.notes or []] for s in briefing.sections if not s.items}
    assert notes[BriefingSectionId.vitals_news2] == [BriefingNoteCode.none_recorded]
    assert notes[BriefingSectionId.danger_screens] == [BriefingNoteCode.none_recorded]


async def test_a_partial_news2_is_never_shown_as_a_full_score() -> None:
    briefing = await plain(load_persona("routine"))
    vitals = next(s for s in briefing.sections if s.id is BriefingSectionId.vitals_news2)
    news2 = vitals.items[-1]
    assert news2.text.startswith("Partial NEWS2 0 from 3 of 7 parameters")
    assert news2.text.endswith("Not a full score.")
    assert news2.provenance is Provenance.DEVICE_MEASURED


async def test_news2_does_not_apply_to_a_child() -> None:
    briefing = await plain(load_persona("child-drowsy"))
    vitals = next(s for s in briefing.sections if s.id is BriefingSectionId.vitals_news2)
    assert not any("NEWS2" in item.text for item in vitals.items)
    assert [n.code for n in vitals.notes or []] == [BriefingNoteCode.news2_not_applicable]


async def test_no_briefing_before_the_nurse_decides() -> None:
    state = load_persona("routine")
    state["nurse_decision"] = None
    with pytest.raises(BriefingNotReady):
        await make_briefing(state, now=state["arrived_at"])


# --- the check ----------------------------------------------------------------------------


def tamper(briefing: Briefing, section: BriefingSectionId, index: int, **update: Any) -> Briefing:
    sections = []
    for s in briefing.sections:
        if s.id is section:
            changed = list(s.items)
            changed[index] = changed[index].model_copy(update=update)
            s = s.model_copy(update={"items": changed})
        sections.append(s)
    return briefing.model_copy(update={"sections": sections})


def first_patient_item(briefing: Briefing) -> tuple[BriefingSectionId, int, BriefingItem]:
    for section in briefing.sections:
        for index, item in enumerate(section.items):
            if isinstance(item.source, TranscriptSpanSource):
                return section.id, index, item
    raise AssertionError("no patient item")


@pytest.mark.parametrize(
    "case",
    ["wrong_quote", "unknown_fact", "wrong_tag", "unknown_nurse", "assistant_turn", "not_a_fact"],
)
async def test_the_check_marks_an_invalid_source_as_missing(
    case: str, caplog: pytest.LogCaptureFixture
) -> None:
    state = load_persona("chest-pain")
    briefing = await plain(state)
    section, index, item = first_patient_item(briefing)
    span = item.source
    assert isinstance(span, TranscriptSpanSource)
    changes: dict[str, dict[str, Any]] = {
        "wrong_quote": {"source": span.model_copy(update={"quote": "I have no pain"})},
        "unknown_fact": {"fact_id": "f_" + "Z" * 26},
        "wrong_tag": {"provenance": Provenance.EHR},
        "unknown_nurse": {
            "provenance": Provenance.NURSE_CONFIRMED,
            "source": NurseSource(kind="nurse", staff_user_id="usr_someone_else"),
            "fact_id": None,
        },
        "assistant_turn": {"source": span.model_copy(update={"turn_id": "trn_001"})},
        "not_a_fact": {"fact_id": None, "source": span.model_copy(update={"start": 0, "end": 3})},
    }
    broken = tamper(briefing, section, index, **changes[case])
    with caplog.at_level(logging.ERROR, logger="careflow_intake.briefing.check"):
        checked, problems = check_briefing(broken, state)
    flagged = [item for _, item in items(checked) if item.source_missing]
    assert len(flagged) == 1 and len(problems) == 1
    assert any(r.message == "briefing.source_missing" for r in caplog.records)
    assert error_messages(openapi_validator("Briefing"), briefing_json(checked)) == []


async def test_a_quote_that_no_longer_matches_its_turn_is_missing() -> None:
    """The fact and the item agree, but the session's transcript says something else."""
    state = load_persona("routine")
    briefing = await plain(state)
    turns = state.get("turns", [])
    first = next(turn for turn in turns if turn.role.value == "patient")
    changed = first.model_copy(update={"text": "I have a headache."})
    state["turns"] = [changed if turn is first else turn for turn in turns]
    checked, problems = check_briefing(briefing, state)
    flagged = [item for _, item in items(checked) if item.source_missing]
    on_turn = [
        item
        for _, item in items(briefing)
        if isinstance(item.source, TranscriptSpanSource) and item.source.turn_id == first.turn_id
    ]
    assert len(flagged) == len(on_turn) == len(problems) == 2
    assert all("the quote does not match turn" in problem for problem in problems)


async def test_an_item_the_builder_could_not_source_stays_missing() -> None:
    state = load_persona("chest-pain")
    briefing = await plain(state)
    broken = tamper(briefing, BriefingSectionId.alerts_triage, 0, source_missing=True)
    checked, problems = check_briefing(broken, state)
    assert problems == ["alerts_triage[0]: the builder found no source"]
    assert checked.sections[0].items[0].source_missing is True


async def test_an_alert_without_its_evidence_is_still_shown(
    caplog: pytest.LogCaptureFixture,
) -> None:
    state = load_persona("chest-pain")
    alert = state.get("alerts", [])[0]
    state["alerts"] = [alert.model_copy(update={"evidence_fact_ids": ["f_" + "Y" * 26]})]
    decision = state.get("nurse_decision")
    assert decision is not None
    state["nurse_decision"] = decision.model_copy(update={"acknowledged_alert_ids": []})
    result = await make_briefing(state, now=decided(state))
    first = result.briefing.sections[0].items[0]
    assert first.text.startswith("Critical alert: Chest pain: ECG within 10 minutes")
    assert first.source_missing is True
    assert result.problems == ["alerts_triage[0]: the builder found no source"]


# --- the summary --------------------------------------------------------------------------


async def test_briefing_summary_false_disables_the_summary() -> None:
    state = load_persona("chest-pain")
    assert briefing_summarizer(IntakeConfig(briefing_summary=False)) is None
    result = await make_briefing(state, now=decided(state), summarizer=None)
    assert result.briefing.summary_status is SummaryStatus.disabled
    assert result.briefing.summary is None
    assert result.briefing.versions.prompt is None


@pytest.mark.parametrize("reason", ["no_key", "server_down", "timeout"])
async def test_an_unavailable_provider_disables_the_summary(reason: str) -> None:
    state = load_persona("sepsis")
    baseline = await plain(state)

    def no_key() -> Any:
        raise ProviderUnavailable("summary", "ANTHROPIC_API_KEY is not set")

    providers = {"no_key": no_key, "server_down": Down, "timeout": lambda: Slow("{}")}
    summarizer = Summarizer(provider=providers[reason], timeout_seconds=0.05)
    result = await make_briefing(state, now=decided(state), summarizer=summarizer)
    briefing = result.briefing
    assert briefing.summary_status is SummaryStatus.disabled
    assert briefing.summary is None
    assert briefing.sections == baseline.sections  # the full plain list
    assert result.summary_reasons[0].startswith("summary provider unavailable")


@pytest.mark.parametrize("case", sorted(HALLUCINATED["cases"]))
async def test_a_hallucinated_summary_is_dropped(case: str) -> None:
    state = load_persona("chest-pain")
    spec = HALLUCINATED["cases"][case]
    provider = Scripted(summary_reply(state, spec["reply"]))
    result = await make_briefing(
        state, now=decided(state), summarizer=Summarizer(provider=lambda: provider)
    )
    briefing = result.briefing
    assert briefing.summary_status is SummaryStatus.dropped, result.summary_reasons
    assert briefing.summary is None
    assert any(spec["reason"] in reason for reason in result.summary_reasons), (
        result.summary_reasons
    )
    assert briefing.versions.prompt == PROMPT_VERSION
    assert briefing.sections == (await plain(state)).sections


async def test_a_summary_that_only_rephrases_cited_facts_is_kept() -> None:
    state = load_persona("chest-pain")
    provider = Scripted(summary_reply(state, HALLUCINATED["valid"]))
    result = await make_briefing(
        state, now=decided(state), summarizer=Summarizer(provider=lambda: provider)
    )
    briefing = result.briefing
    assert briefing.summary_status is SummaryStatus.ok, result.summary_reasons
    assert briefing.summary is not None
    assert len(briefing.summary.sentences) == 2
    assert error_messages(openapi_validator("Briefing"), briefing_json(briefing)) == []


def test_numbers_compare_by_value() -> None:
    item = BriefingItem.model_validate(
        {
            "text": "Pain score: 8/10.",
            "provenance": "NURSE_CONFIRMED",
            "source": {"kind": "nurse", "staff_user_id": "usr_nurse_01"},
            "fact_id": "f_" + "A" * 26,
            "recorded_at": "2026-09-27T09:00:00Z",
        }
    )
    ok = SummaryDraft.model_validate(
        {"sentences": [{"text": "Pain 08 of 10.0.", "fact_ids": [item.fact_id]}]}
    )
    assert check_summary(ok, [item]) == (check_summary(ok, [item])[0], [])
    bad = SummaryDraft.model_validate(
        {"sentences": [{"text": "Pain 9 of 10.", "fact_ids": [item.fact_id]}]}
    )
    assert check_summary(bad, [item])[1] == [
        "sentence 1 has a number that is not in its cited facts (9)"
    ]


@pytest.mark.skipif(not cassette("chest-pain").exists(), reason="no recorded summaries yet")
async def test_the_recorded_summaries_replay(persona: str) -> None:
    """The local model's recorded replies (M1-16 record run) give the recorded outcome."""
    state = load_persona(persona)
    recorded = json.loads(expected(persona).read_text(encoding="utf-8"))
    result = await make_briefing(state, now=decided(state), summarizer=replay_summarizer(persona))
    briefing = result.briefing
    assert briefing.summary_status.value == recorded["summary_status"], result.summary_reasons
    assert result.summary_reasons == recorded["reasons"]
    sentences = [s.text for s in briefing.summary.sentences] if briefing.summary else []
    assert sentences == recorded["sentences"]
    if briefing.summary is not None:
        cited = {item.fact_id for item in fact_items(briefing)}
        assert all(set(s.fact_ids) <= cited for s in briefing.summary.sentences)


async def test_reception_uses_sourced_briefing_without_calling_summary_provider() -> None:
    state = load_persona("chest-pain")
    baseline = await plain(state)
    state["reception_context"] = {"reason": "Chest pain", "department_id": "emergency"}

    def forbidden_provider() -> Any:
        raise AssertionError("Reception must not wait for a summary model")

    result = await make_briefing(
        state, now=decided(state), summarizer=Summarizer(provider=forbidden_provider)
    )
    assert result.problems == []
    assert result.briefing.sections == baseline.sections
    assert result.briefing.triage == baseline.triage
    assert result.briefing.summary is None
    assert result.briefing.summary_status is SummaryStatus.disabled
    assert result.briefing.versions.prompt is None
    assert result.summary_reasons == ["structured reception uses the sourced briefing"]
