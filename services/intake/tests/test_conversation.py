"""The intake conversation (M1-07): the output filter, the question picker, phrasing with a
template fallback, and the Conversation port."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from careflow_contracts.generated.alert_schema import Alert
from careflow_contracts.generated.structured_facts_schema import (
    EhrContext,
    FactKey,
    Language,
    NeededFact,
    StructuredFacts,
    Turn,
    TurnRole,
)
from careflow_intake.conversation import ChecklistConversation
from careflow_intake.conversation.checklist import load_templates
from careflow_intake.conversation.output_filter import check_patient_facing
from careflow_intake.conversation.phrasing import Phraser
from careflow_intake.conversation.picker import pick_next_question
from careflow_intake.facts import FactEntry, stable_fact_id, to_structured_facts
from careflow_intake.nodes.conversation import ask
from careflow_intake.state import IntakeSettings, IntakeState
from careflow_intake.stubs import stub_ports
from careflow_intake.wiring import CareflowRules, RealPortsSettings, build_real_ports
from careflow_providers.base import ChatResult, Message

FIXTURES = Path(__file__).parent / "fixtures"
T0 = datetime(2026, 9, 27, 9, 0, tzinfo=UTC)
TEMPLATES = load_templates()
NURSE_COMING = "Thank you. A nurse will be with you shortly. Please stay here."


def lines(name: str) -> list[str]:
    text = (FIXTURES / name).read_text(encoding="utf-8")
    return [line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")]


def entry(key: str, value: Any, n: int = 0) -> FactEntry:
    return FactEntry.model_validate(
        {
            "key": key,
            "fact": {
                "fact_id": stable_fact_id("conv", key, str(n)),
                "provenance": "PATIENT_STATED",
                "source": {
                    "kind": "transcript_span",
                    "turn_id": "trn_002",
                    "start": 0,
                    "end": 3,
                    "quote": "yes",
                },
                "recorded_at": T0,
                "value": value,
            },
        }
    )


def facts(**values: Any) -> StructuredFacts:
    entries = [
        entry(key.replace("__", "."), value, n) for n, (key, value) in enumerate(values.items())
    ]
    return to_structured_facts(
        session_id="ses_1", clinic_id="clinic_a", language=Language.en, entries=entries
    )


def need(key: FactKey, reason: str, audience: str = "patient") -> NeededFact:
    return NeededFact.model_validate({"key": key, "audience": audience, "reason": reason})


def alert(rule_id: str, severity: str) -> Alert:
    return Alert.model_validate(
        {
            "alert_id": f"alr_{rule_id.lower()}",
            "session_id": "ses_1",
            "rule_id": rule_id,
            "ruleset_version": "2026.2.0",
            "severity": severity,
            "evidence_fact_ids": [stable_fact_id("evidence")],
            "required_action": "review_now",
            "partial": False,
            "raised_at": "2026-09-27T09:00:00Z",
        }
    )


# --- The output filter -------------------------------------------------------------------------


def test_every_adversarial_output_is_blocked() -> None:
    adversarial = lines("filter_block.txt")
    assert len(adversarial) >= 30
    passed = [text for text in adversarial if check_patient_facing(text).allowed]
    assert passed == []  # 100% blocked


def test_good_questions_are_allowed() -> None:
    good = lines("filter_allow.txt")
    assert len(good) >= 20
    blocked = [
        (text, check_patient_facing(text).reasons)
        for text in good
        if not check_patient_facing(text).allowed
    ]
    assert blocked == []


def test_every_template_passes_the_filter() -> None:
    ehr = EhrContext.model_validate({"allergies": [allergy("Penicillin"), allergy("Latex")]})
    texts = [TEMPLATES.nurse_coming] + [TEMPLATES.render(key, ehr) for key in TEMPLATES.keys()]
    texts += [TEMPLATES.render(FactKey.allergies_confirm, None)]
    assert len(texts) == 25
    assert [t for t in texts if not check_patient_facing(t).allowed] == []
    assert TEMPLATES.nurse_coming == NURSE_COMING


def test_the_filter_names_its_reasons() -> None:
    result = check_patient_facing("You probably have a heart attack, you should rest. Ok? Ok?")
    assert not result.allowed
    assert set(result.reasons) == {
        "diagnosis",
        "condition_name",
        "advice",
        "more_than_one_question",
    }


def allergy(name: str) -> dict[str, Any]:
    return {
        "fact_id": stable_fact_id("allergy", name),
        "provenance": "EHR",
        "source": {"kind": "fhir", "resource_type": "AllergyIntolerance", "id": name.lower()},
        "recorded_at": "2026-01-01T00:00:00Z",
        "value": {"system": "http://snomed.info/sct", "code": "1", "display": name},
    }


def test_the_allergy_question_names_the_record_or_asks_openly() -> None:
    ehr = EhrContext.model_validate({"allergies": [allergy("Penicillin"), allergy("Latex")]})
    assert TEMPLATES.render(FactKey.allergies_confirm, ehr) == (
        "Your record lists Penicillin and Latex. Is that still correct?"
    )
    assert TEMPLATES.render(FactKey.allergies_confirm, None) == "Are you allergic to any medicines?"


# --- The picker --------------------------------------------------------------------------------


def test_the_complaint_comes_first() -> None:
    needed = [need(FactKey.chest_pain_radiates, "chest_pain_screen")]
    assert pick_next_question(facts(), needed, {}) is FactKey.chief_complaint


def test_patient_screens_come_before_the_checklist_and_nurse_facts_are_skipped() -> None:
    known = facts(chief_complaint="Chest pain", chest_pain__present="yes")
    needed = [
        need(FactKey.vitals_respiratory_rate, "news2_incomplete", audience="nurse"),
        need(FactKey.chest_pain_radiates, "chest_pain_screen"),
    ]
    assert (
        pick_next_question(known, needed, {FactKey.chief_complaint: 1})
        is FactKey.chest_pain_radiates
    )


def test_the_most_severe_alert_screen_is_asked_first() -> None:
    known = facts(chief_complaint="Unwell")
    needed = [
        need(FactKey.child_danger_hard_to_wake, "ng143_parent_screen"),
        need(FactKey.fast_face_droop, "fast_screen"),
    ]
    amber_only = [alert("NG143_AMBER_FLAG", "high")]
    assert (
        pick_next_question(known, needed, {}, alerts=amber_only)
        is FactKey.child_danger_hard_to_wake
    )
    both = [*amber_only, alert("FAST_STROKE", "critical")]
    assert pick_next_question(known, needed, {}, alerts=both) is FactKey.fast_face_droop
    assert pick_next_question(known, needed, {}) is FactKey.fast_face_droop  # no alert: rule order


def test_after_a_critical_alert_only_screens_are_asked() -> None:
    known = facts(chief_complaint="Chest pain", chest_pain__present="yes")
    critical = [alert("CHEST_PAIN_ECG_10MIN", "critical")]
    assert pick_next_question(known, [], {}, alerts=critical) is None
    assert pick_next_question(known, [], {}) is FactKey.symptom_onset_at  # without the alert


def test_a_key_is_asked_once_and_re_asked_once_when_the_answer_gave_no_fact() -> None:
    assert pick_next_question(facts(), [], {FactKey.chief_complaint: 1}) is FactKey.chief_complaint
    assert pick_next_question(facts(), [], {FactKey.chief_complaint: 2}) is FactKey.symptom_onset_at


def test_the_checklist_skips_questions_the_answers_make_pointless() -> None:
    known = facts(
        chief_complaint="Chest pain",
        chest_pain__present="yes",
        chest_pain__onset="7 am",
        pain_score=6,
    )
    # onset known from chest_pain.onset; severity is the pain score; the location is the chest.
    assert pick_next_question(known, [], {}) is FactKey.allergies_confirm
    no_pain = facts(chief_complaint="Fever", symptom_onset_at="Monday", pain_present="no")
    assert pick_next_question(no_pain, [], {}) is FactKey.severity
    ankle = facts(chief_complaint="Ankle", symptom_onset_at="an hour ago", pain_score=4)
    assert pick_next_question(ankle, [], {}) is FactKey.pain_location


# --- Phrasing ----------------------------------------------------------------------------------


class FakeChat:
    task = "conversation"
    model_name = "fake"

    def __init__(self, reply: str | None, *, delay: float = 0.0, fail: bool = False) -> None:
        self.reply, self.delay, self.fail = reply, delay, fail
        self.prompts: list[str] = []

    async def chat(
        self,
        messages: Sequence[Message],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> ChatResult:
        self.prompts.append(messages[-1]["content"])
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise RuntimeError("provider down")
        return ChatResult(text=self.reply or "", model=self.model_name)

    async def stream(self, messages: Sequence[Message]) -> AsyncIterator[str]:
        yield (await self.chat(messages)).text


TEMPLATE = "Are you short of breath?"


@pytest.mark.parametrize(
    ("chat", "expected"),
    [
        (
            FakeChat("Are you finding it hard to breathe right now?"),
            ("Are you finding it hard to breathe right now?", "llm"),
        ),
        (
            FakeChat("You might be having a heart attack. Are you short of breath?"),
            (TEMPLATE, "template"),
        ),
        (FakeChat("Are you short of breath? Do you feel faint?"), (TEMPLATE, "template")),
        (FakeChat("Please tell me."), (TEMPLATE, "template")),
        (FakeChat(" ".join(["very"] * 45) + " breathless?"), (TEMPLATE, "template")),
        (FakeChat(None, fail=True), (TEMPLATE, "template")),
        (FakeChat("Are you breathless?", delay=1.0), (TEMPLATE, "template")),
    ],
    ids=[
        "rephrased",
        "diagnosis_blocked",
        "two_questions",
        "not_a_question",
        "too_long",
        "error",
        "timeout",
    ],
)
async def test_phrasing_falls_back_to_the_template(
    chat: FakeChat, expected: tuple[str, str]
) -> None:
    phraser = Phraser(chat, timeout_seconds=0.05)
    assert await phraser.phrase(FactKey.chest_pain_short_of_breath, TEMPLATE) == expected
    assert chat.prompts == [
        "Rephrase this question kindly in plain English, one question, max 25 words, no advice, "
        "no diagnosis: Are you short of breath?"
    ]


async def test_templates_only_never_calls_the_model() -> None:
    chat = FakeChat("Are you breathless?")
    assert await Phraser(chat, enabled=False).phrase(FactKey.chest_pain_sweating, TEMPLATE) == (
        TEMPLATE,
        "template",
    )
    assert chat.prompts == []


# --- The Conversation port and the ask node -----------------------------------------------------


def state(**fields: Any) -> IntakeState:
    base: IntakeState = {
        "session_id": "ses_1",
        "clinic_id": "clinic_a",
        "language": Language.en,
        "arrived_at": T0,
    }
    base.update(fields)  # type: ignore[typeddict-item]
    return base


async def test_templates_only_questions_are_deterministic() -> None:
    conversation = ChecklistConversation()
    first = [await conversation.next_question(state()) for _ in range(2)]
    assert first[0] == first[1]
    assert first[0] is not None and first[0].text == "What is the main reason for your visit today?"
    assert first[0].source == "template" and first[0].question_id == "q_chief_complaint_1"


async def test_a_critical_alert_brings_the_nurse_coming_message_first() -> None:
    conversation = ChecklistConversation()
    critical = [alert("CHEST_PAIN_ECG_10MIN", "critical")]
    known = [entry("chief_complaint", "Chest pain"), entry("chest_pain.present", "yes", 1)]
    needed = [need(FactKey.chest_pain_radiates, "chest_pain_screen")]
    question = await conversation.next_question(
        state(facts=known, alerts=critical, needed_facts=needed)
    )
    assert question is not None and question.key is FactKey.chest_pain_radiates
    assert question.notice == NURSE_COMING
    announced = Turn(turn_id="trn_003", role=TurnRole.assistant, text=NURSE_COMING, created_at=T0)
    later = await conversation.next_question(
        state(facts=known, alerts=critical, needed_facts=needed, turns=[announced])
    )
    assert later is not None and later.notice is None
    # No screen left and not yet announced: the message comes on its own, to acknowledge.
    alone = await conversation.next_question(state(facts=known, alerts=critical))
    assert alone is not None and alone.key is None and alone.text == NURSE_COMING
    assert (
        await conversation.next_question(state(facts=known, alerts=critical, turns=[announced]))
        is None
    )


async def test_the_ask_node_records_the_notice_before_the_question() -> None:
    conversation = ChecklistConversation()
    critical = [alert("CHEST_PAIN_ECG_10MIN", "critical")]
    known = [entry("chief_complaint", "Chest pain"), entry("chest_pain.present", "yes", 1)]
    needed = [need(FactKey.chest_pain_radiates, "chest_pain_screen")]
    ports = stub_ports(conversation=conversation)
    update = await ask(
        state(facts=known, alerts=critical, needed_facts=needed), ports, IntakeSettings()
    )
    turns = update["turns"]
    assert [(t.turn_id, t.text, t.question_key) for t in turns] == [
        ("trn_001", NURSE_COMING, None),
        (
            "trn_002",
            "Does the pain spread to your arm, neck, jaw or back?",
            FactKey.chest_pain_radiates,
        ),
    ]


def test_the_rules_adapter_runs_the_real_engine() -> None:
    known = facts(chief_complaint="Chest pain", chest_pain__present="yes")
    outcome = CareflowRules().run(known, now=T0, arrived_at=T0)
    assert [a.rule_id.value for a in outcome.alerts] == ["CHEST_PAIN_ECG_10MIN"]
    asked = [n.key.value for n in outcome.needed_facts]
    assert asked == [
        "chest_pain.radiates",
        "chest_pain.short_of_breath",
        "chest_pain.sweating",
        "chest_pain.onset",
    ]


def test_build_real_ports_follows_config_intake_yaml(tmp_path: Path) -> None:
    config = tmp_path / "intake.yaml"
    config.write_text("llm_phrasing: false\nmax_turns: 8\n", encoding="utf-8")
    ports = build_real_ports(RealPortsSettings(intake=config))
    assert isinstance(ports.conversation, ChecklistConversation)
    assert ports.conversation.phraser.enabled is False
    assert isinstance(ports.rules, CareflowRules)
    on = build_real_ports(
        RealPortsSettings(llm_phrasing=True, conversation_provider=FakeChat("Hi?"))
    )
    assert isinstance(on.conversation, ChecklistConversation) and on.conversation.phraser.enabled
