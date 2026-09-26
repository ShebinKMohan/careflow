"""Scripted synthetic patients through the real intake (M1-07): the real extractor (its replies
replayed from the live model's recording), the real rules and the checklist conversation."""

from __future__ import annotations

from typing import Any

import pytest

from careflow_contracts.generated.structured_facts_schema import FactKey, NeededFactAudience
from careflow_intake.conversation.checklist import load_templates
from careflow_intake.scripts import ScriptRun, load_script, replay, run_script, script_ports

NURSE_COMING = "Thank you. A nurse will be with you shortly. Please stay here."
SCRIPTS = ("chest_pain", "sepsis", "sprained_ankle")


async def run(name: str) -> ScriptRun:
    script = load_script(name)
    if not script.cassette.exists():
        pytest.skip(f"{name} is not recorded (python -m careflow_intake.scripts --record {name})")
    return await run_script(script, script_ports(script, replay(script)))


def values(run: ScriptRun) -> dict[str, Any]:
    return {e.key.value: e.base.model_dump(mode="json")["value"] for e in run.result["facts"]}


@pytest.mark.parametrize("name", SCRIPTS)
async def test_script(name: str) -> None:
    expect = load_script(name).expect
    result = (done := await run(name)).result
    assert result["__interrupt__"][0].value["kind"] == "nurse_review"  # reached the nurse
    raised = sorted(alert.rule_id.value for alert in result.get("alerts", []))
    assert raised == sorted(expect["alerts"])
    for rule in expect.get("partial", []):
        assert next(a for a in result["alerts"] if a.rule_id.value == rule).partial is True
    got = values(done)
    for key, value in expect["facts"].items():
        assert got.get(key) == value, (key, got.get(key))
    assert done.patient_turns <= expect["max_patient_turns"]
    assert result["conversation_complete"] is expect["complete"]
    assert "intake_truncated" not in result.get("flags", {})
    patient_needs = [n for n in result["needed_facts"] if n.audience is NeededFactAudience.patient]
    assert patient_needs == []  # a complete fact set: no screen question is left
    shown = [turn.text for turn in result["turns"] if turn.role.value == "assistant"]
    assert (NURSE_COMING in shown) is expect["notice"]


async def test_chest_pain_asks_the_screens_after_the_alert() -> None:
    done = await run("chest_pain")
    assert done.asked[0] == "chief_complaint"
    screens = load_script("chest_pain").expect["screens"]
    assert [key for key in done.asked if key in screens] == screens  # in the rule's order
    first_screen = next(
        t for t in done.result["turns"] if t.question_key and t.question_key.value == screens[0]
    )
    turns = done.result["turns"]
    assert turns[turns.index(first_screen) - 1].text == NURSE_COMING  # the notice comes first
    assert set(done.asked) <= {"chief_complaint", *screens, "chest_pain.onset"}  # screens only


async def test_sepsis_lists_the_nurse_needed_facts() -> None:
    done = await run("sepsis")
    nurse = [
        n.key.value for n in done.result["needed_facts"] if n.audience is NeededFactAudience.nurse
    ]
    assert nurse == load_script("sepsis").expect["nurse_needed"]
    [alert] = done.result["alerts"]
    assert alert.missing_inputs and alert.partial


async def test_the_sprained_ankle_is_routine() -> None:
    done = await run("sprained_ankle")
    assert done.result.get("alerts", []) == [] and done.patient_turns <= 8
    assert done.asked[0] == "chief_complaint" and "" not in done.asked  # no nurse-coming notice


async def test_templates_only_gives_identical_questions_across_two_runs() -> None:
    first, second = await run("chest_pain"), await run("chest_pain")
    assert first.questions == second.questions and first.asked == second.asked
    templates = load_templates()
    approved = {
        templates.render(key) for key in templates.keys() if key.value != "allergies_confirm"
    }
    approved |= {templates.nurse_coming, templates.render(FactKey.allergies_confirm)}
    assert set(first.questions) <= approved  # approved templates, word for word
