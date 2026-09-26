"""The intake checklist and its approved templates (`templates_en.yaml`)."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any, Final

import yaml

from careflow_contracts.generated.structured_facts_schema import EhrContext, FactKey

TEMPLATES_PATH: Final = Path(__file__).with_name("templates_en.yaml")

# The checklist, in order (M1-07): complaint, onset, pain, severity, pain location, allergies.
CHECKLIST: Final = (
    FactKey.chief_complaint,
    FactKey.symptom_onset_at,
    FactKey.pain_score,
    FactKey.severity,
    FactKey.pain_location,
    FactKey.allergies_confirm,
)


@dataclass(frozen=True)
class Templates:
    version: str
    nurse_coming: str
    questions: dict[str, Any]

    def keys(self) -> set[FactKey]:
        return {FactKey(key) for key in self.questions}

    def render(self, key: FactKey, ehr_context: EhrContext | None = None) -> str:
        """The approved question for `key`. The allergy question names the EHR allergies when there
        are any."""
        template = self.questions[key.value]
        if key is not FactKey.allergies_confirm:
            return str(template)
        allergies = (
            [a.value.display or a.value.code for a in (ehr_context.allergies or [])]
            if ehr_context
            else []
        )
        if not allergies:
            return str(template["without_record"])
        return str(template["with_record"]).replace("{allergies}", _joined(allergies))

    def fallback(self, key: FactKey) -> str | None:
        """A second approved wording, used when the first one does not pass the filter."""
        if key is FactKey.allergies_confirm:
            return str(self.questions[key.value]["without_record"])
        return None


def _joined(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


@cache
def load_templates(path: Path = TEMPLATES_PATH) -> Templates:
    body = yaml.safe_load(path.read_text(encoding="utf-8"))
    return Templates(
        version=str(body["version"]),
        nurse_coming=str(body["nurse_coming"]),
        questions=dict(body["questions"]),
    )


__all__ = ["CHECKLIST", "TEMPLATES_PATH", "Templates", "load_templates"]
