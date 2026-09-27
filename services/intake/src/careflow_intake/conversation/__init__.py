"""The intake conversation (M1-07): code picks the question, the LLM only rephrases it, and the
output filter checks every string that reaches the patient."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping

from careflow_contracts.generated.structured_facts_schema import (
    EhrContext,
    FactKey,
    Language,
    TurnRole,
)
from careflow_intake.conversation.checklist import Templates, load_templates, templates_for
from careflow_intake.conversation.child_questions import next_child_question
from careflow_intake.conversation.output_filter import check_patient_facing
from careflow_intake.conversation.phrasing import Phraser
from careflow_intake.conversation.picker import has_critical_alert, pick_next_question
from careflow_intake.ports import Question
from careflow_intake.state import IntakeState, structured_facts


class ChecklistConversation:
    """The M1-05 `Conversation` port.

    When a critical alert is raised, the next patient-facing text is the fixed nurse-coming message
    (as the question's `notice`, or on its own when no screen question is left); after it only the
    remaining danger screens are asked. The session language picks the approved templates and the
    phrasing prompt (M2-03).
    """

    def __init__(
        self,
        *,
        templates: Templates | None = None,
        phraser: Phraser | None = None,
        templates_by_language: Mapping[Language, Templates] | None = None,
    ) -> None:
        self.templates = templates or load_templates()
        self.by_language: dict[Language, Templates] = {
            Language.en: self.templates,
            Language.ar: templates_for(Language.ar),
            **(templates_by_language or {}),
        }
        self.phraser = phraser or Phraser(None, enabled=False)

    @staticmethod
    def _approved(templates: Templates, key: FactKey, ehr_context: EhrContext | None) -> str:
        for text in (templates.render(key, ehr_context), templates.fallback(key)):
            if text is not None and check_patient_facing(text).allowed:
                return text
        raise RuntimeError(f"no approved wording for {key.value} passes the output filter")

    async def next_question(self, state: IntakeState) -> Question | None:
        language = Language(state.get("language", Language.en))
        templates = self.by_language[language]
        turns = state.get("turns", [])
        prompts = [turn for turn in turns if turn.role is TurnRole.assistant]
        asked = Counter(turn.question_key for turn in prompts if turn.question_key is not None)
        nurse_coming = templates.nurse_coming
        alerts = state.get("alerts", [])
        announced = any(turn.text == nurse_coming for turn in prompts)
        notice = nurse_coming if has_critical_alert(alerts) and not announced else None
        facts = structured_facts(state)
        if state.get("patient_type") == "child":
            child_key = next_child_question(facts, asked)
            if child_key is not None:  # the fixed bank, verbatim (M2-04)
                text = templates.render_child(child_key)
                if not check_patient_facing(text).allowed:
                    raise RuntimeError(f"the child question {child_key.value} fails the filter")
                return Question(
                    question_id=f"q_{child_key.value}_{asked[child_key] + 1}",
                    text=text,
                    key=child_key,
                    source="template",
                    notice=notice,
                )
        key = pick_next_question(
            facts,
            state.get("needed_facts", []),
            asked,
            state.get("ehr_context"),
            alerts=alerts,
            askable=templates.keys(),
        )
        if key is None:
            if notice is None:
                return None
            return Question(question_id="nurse_coming", text=notice, source="template")
        approved = self._approved(templates, key, state.get("ehr_context"))
        text, source = await self.phraser.phrase(key, approved, language)
        number = asked[key] + 1
        return Question(
            question_id=f"q_{key.value}_{number}", text=text, key=key, source=source, notice=notice
        )


__all__ = ["ChecklistConversation"]
