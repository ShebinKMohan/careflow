"""The intake checklist and its approved templates, one file per session language
(`templates_en.yaml`, and `templates_ar.yaml` since M2-03)."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any, Final

import yaml

from careflow_contracts.generated.structured_facts_schema import EhrContext, FactKey, Language

TEMPLATES_PATHS: Final = {
    Language.en: Path(__file__).with_name("templates_en.yaml"),
    Language.ar: Path(__file__).with_name("templates_ar.yaml"),
}
TEMPLATES_PATH: Final = TEMPLATES_PATHS[Language.en]

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
    # How a list is joined ("A, B and C"; Arabic "A، B وC").
    list_separator: str = ", "
    list_last_separator: str = " and "

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
        return str(template["with_record"]).replace("{allergies}", self._joined(allergies))

    def fallback(self, key: FactKey) -> str | None:
        """A second approved wording, used when the first one does not pass the filter."""
        if key is FactKey.allergies_confirm:
            return str(self.questions[key.value]["without_record"])
        return None

    def _joined(self, items: list[str]) -> str:
        if len(items) == 1:
            return items[0]
        return self.list_separator.join(items[:-1]) + self.list_last_separator + items[-1]


@cache
def load_templates(path: Path = TEMPLATES_PATH) -> Templates:
    body = yaml.safe_load(path.read_text(encoding="utf-8"))
    joins = body.get("list_join") or {}
    return Templates(
        version=str(body["version"]),
        nurse_coming=str(body["nurse_coming"]),
        questions=dict(body["questions"]),
        list_separator=str(joins.get("separator", ", ")),
        list_last_separator=str(joins.get("last", " and ")),
    )


def templates_for(language: Language | str) -> Templates:
    """The approved questions in the session's language."""
    return load_templates(TEMPLATES_PATHS[Language(language)])


__all__ = [
    "CHECKLIST",
    "TEMPLATES_PATH",
    "TEMPLATES_PATHS",
    "Templates",
    "load_templates",
    "templates_for",
]
