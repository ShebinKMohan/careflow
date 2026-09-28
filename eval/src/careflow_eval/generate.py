"""Offline scenario generation through the existing subscription-CLI adapter.

The coding agent may also author drafts directly, as permitted by M1-18. Neither
path grants review approval. Only the independent review gate does that.
"""

from __future__ import annotations

import os
from collections import Counter
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import yaml
from pydantic import BaseModel, ConfigDict

from careflow_eval.models import Scenario
from careflow_eval.validation import validate_scenario
from careflow_providers.adapters.cli import CliProvider
from careflow_providers.base import ChatResult, Message, StructuredFailure, structured
from careflow_providers.config import CliTaskConfig, load_config

REPO: Final = Path(__file__).resolve().parents[3]
DEFAULT_DATASET: Final = REPO / "eval/datasets/intake_en"
CATEGORY_MIX: Final = {
    "chest_pain": 8,
    "stroke": 6,
    "sepsis": 8,
    "child": 6,
    "breathing": 4,
    "routine": 18,
}
REVIEW_FIELDS: Final = (
    "reviewed_by",
    "reviewed_at",
    "review_method",
    "reviewed_content_sha256",
)


class GenerationBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenarios: list[Scenario]


class SelectedModelCli(CliProvider):
    """Reuse the CLI adapter's offline guard, timeout and parsing with an explicit model."""

    def __init__(self, tool: str, model: str | None) -> None:
        super().__init__("test_data", tool)
        self.selected_model = model
        self.model_name = model or f"unspecified-cli-default:{tool}"

    def _argv(self, prompt: str, last_message: Path) -> list[str]:
        argv = super()._argv(prompt, last_message)
        if self.selected_model:
            argv.extend(["--model", self.selected_model])
        return argv

    async def chat(
        self,
        messages: Sequence[Message],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> ChatResult:
        result = await super().chat(messages, temperature=temperature, max_tokens=max_tokens)
        return replace(result, model=self.model_name)


def category_mix(n: int) -> dict[str, int]:
    """Scale the specified 50-case mix by largest remainder; the default stays exact."""
    if not 1 <= n <= 500:
        raise ValueError("n must be between 1 and 500 (offline generation is bounded)")
    counts = {key: weight * n // 50 for key, weight in CATEGORY_MIX.items()}
    remaining = n - sum(counts.values())
    order = sorted(CATEGORY_MIX, key=lambda key: -(CATEGORY_MIX[key] * n % 50))
    for key in order[:remaining]:
        counts[key] += 1
    return counts


def rule_definitions() -> str:
    directory = REPO / "services/rules/src/careflow_rules/rules"
    return "\n\n".join(
        f"FILE {path.name}\n{path.read_text(encoding='utf-8')}"
        for path in sorted(directory.glob("*.py"))
        if path.name != "__init__.py"
    )


async def generate_dataset(
    *,
    suite: str = "intake_en",
    n: int = 50,
    dataset_path: Path | None = None,
    config_path: Path | None = None,
    writer_model: str | None = None,
) -> dict[str, Any]:
    if suite != "intake_en":
        raise ValueError("Only the intake_en synthetic generation suite is implemented")
    if os.environ.get("CAREFLOW_RUNTIME") != "offline":
        raise ValueError("Generation requires CAREFLOW_RUNTIME=offline; never run it in the API")
    counts = category_mix(n)
    config = load_config(config_path or REPO / "config/providers.yaml")
    task = config.tasks.get("test_data")
    if not isinstance(task, CliTaskConfig):
        raise ValueError("test_data must use the offline CLI adapter for scenario generation")
    provider = SelectedModelCli(task.tool, writer_model)
    inbox = (dataset_path or DEFAULT_DATASET) / "_inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    categories = [name for name, count in counts.items() for _ in range(count)]
    written: list[str] = []
    failures: dict[str, list[str]] = {}
    for offset in range(0, n, 25):
        wanted = Counter(categories[offset : offset + 25])
        prompt = (
            "Author synthetic English CareFlow intake test scenarios. Return data only; do not "
            "run commands or edit files. Never include real names, contact details or identifiers. "
            "Use realistic varied patient wording, negation and uncertainty. Patient age is in "
            "years. Expected extracted facts must be supported by patient turns; keep EHR/device "
            "context separate. Expected levels are synthetic labels, not clinical accuracy. "
            "Leave all review fields null. Use unique lowercase hyphenated ids. "
            f"Required category counts for this batch: {dict(wanted)}. "
            f"Prefix every id with gen-{offset + 1}-. Use generated_by tool={task.tool}, "
            f"model={provider.model_name}, date={datetime.now(UTC).date().isoformat()}. "
            "Use these actual deterministic rule definitions to set expected alerts:\n"
            + rule_definitions()
        )
        outcome = await structured(
            provider,
            [{"role": "user", "content": prompt}],
            GenerationBatch,
            max_tokens=16000,
        )
        if isinstance(outcome, StructuredFailure):
            raise ValueError(f"Generation batch at {offset} failed schema validation")
        cases = outcome.value.scenarios
        if Counter(case.category for case in cases) != wanted:
            raise ValueError(f"Generation batch at {offset} did not match the category mix")
        for case in cases:
            body = case.model_dump(mode="json")
            body.update(dict.fromkeys(REVIEW_FIELDS))
            body["generated_by"] = {
                "tool": task.tool,
                "model": provider.model_name,
                "date": datetime.now(UTC).date().isoformat(),
            }
            draft = Scenario.model_validate(body)
            path = inbox / f"{draft.id}.yaml"
            # Exclusive creation keeps existing drafts and approved evidence intact.
            with path.open("x", encoding="utf-8") as handle:
                yaml.safe_dump(draft.model_dump(mode="json"), handle, sort_keys=False)
            written.append(str(path))
            findings = validate_scenario(draft)
            if findings:
                failures[draft.id] = findings
    return {
        "status": "generated",
        "written": written,
        "count": len(written),
        "validation_failures": failures,
        "writer_model": provider.model_name,
        "review_required": True,
        "identity_note": (
            None
            if writer_model
            else "CLI default model is unspecified; use --writer-model to select an identified "
            "model before independent review. Unqualified drafts cannot be approved."
        ),
    }


# --- Arabic (M2-03, SHE-290): seeded gold (careflow_eval.sampler), written answers, drafts ---

ARABIC_DATASET: Final = REPO / "eval/datasets/intake_ar"
PLAN_AT: Final = "2026-09-27T09:00:00Z"
FREE_TEXT: Final = frozenset({"chest_pain.onset", "fast.last_known_well"})


def plan_alerts(plan: dict[str, Any]) -> list[str]:
    """The rule ids the rules engine raises on a plan's gold facts, age and vitals."""
    from careflow_eval.datasets import NOW
    from careflow_eval.sampler import gold_facts
    from careflow_rules import evaluate
    from careflow_rules.testing import facts_from_entries

    def text(value: Any) -> str:
        return ("yes" if value else "no") if isinstance(value, bool) else str(value)

    entries = [{"path": "patient.age_years", "value": str(plan["age_years"]), "provenance": "EHR"}]
    for source, provenance in (
        ("device_vitals", "DEVICE_MEASURED"),
        ("nurse_vitals", "NURSE_CONFIRMED"),
    ):
        for name, value in plan[source].items():
            entries.append(
                {"path": f"vitals.{name}", "value": text(value), "provenance": provenance}
            )
    for key, value in gold_facts(plan).items():
        # The rules read dotted yes/no facts (and vitals); free text and the top-level pain and
        # severity answers are no rule input.
        if "." in key and not isinstance(value, int) and key not in FREE_TEXT:
            entries.append({"path": key, "value": text(value), "provenance": "PATIENT_STATED"})
    facts = facts_from_entries([{**entry, "recorded_at": PLAN_AT} for entry in entries])
    return sorted({alert.rule_id.value for alert in evaluate(facts, NOW).alerts})


def _level(alerts: list[str]) -> tuple[int, str]:
    critical = {
        "FAST_STROKE",
        "CHEST_PAIN_ECG_10MIN",
        "SEPSIS_NEWS2",
        "NG143_RED_FLAG",
        "CHILD_HARD_TO_WAKE",
    }
    if critical & set(alerts):
        level = 2
    elif alerts:
        level = 3
    else:
        level = 4
    rationale = (
        f"Synthetic illustrative level {level} from the M2-03 sampler: the rules engine raises "
        f"{', '.join(alerts) if alerts else 'no alert'} on the seeded gold facts and vitals. "
        "Not a clinical label."
    )
    return level, rationale


def write_arabic_drafts(
    *,
    dialect: str,
    answers_path: Path,
    writer_model: str,
    seed: int = 7,
    dataset_path: Path | None = None,
) -> dict[str, Any]:
    """Merge the writer's answers (`{id: [{text, gloss_en, also_states?}]}`, one per planned
    answer, in order) into the seeded plan, and write the drafts to `<dataset>/_inbox`. The
    gold, the questions and the alerts come from the plan; only the words come from the writer.
    Drafts need the independent review (`careflow-eval review`) before they count."""
    from careflow_eval.sampler import arabic_plan, gold_facts

    written: list[str] = []
    failures: dict[str, list[str]] = {}
    answers = yaml.safe_load(answers_path.read_text(encoding="utf-8")) or {}
    inbox = (dataset_path or ARABIC_DATASET) / "_inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    for plan in arabic_plan(dialect, seed=seed):
        said = answers.get(plan["id"])
        if said is None:
            continue
        if len(said) != len(plan["answers"]):
            failures[plan["id"]] = [f"{len(said)} answers for {len(plan['answers'])} questions"]
            continue
        turns = [
            {
                "question_key": planned["question_key"],
                "patient_text": answer["text"],
                "gloss_en": answer["gloss_en"],
                "states": sorted({*planned["gold"], *answer.get("also_states", [])}),
            }
            for planned, answer in zip(plan["answers"], said, strict=True)
        ]
        alerts = plan_alerts(plan)
        level, rationale = _level(alerts)
        body = {
            "synthetic": True,
            "id": plan["id"],
            "category": plan["category"],
            "language": "ar",
            "dialect": plan["dialect"],
            "description": (
                f"Seeded {plan['category'].replace('_', ' ')} intake in {dialect.title()} Arabic "
                f"(M2-03 sampler, seed {seed}); expected alerts: {', '.join(alerts) or 'none'}."
            ),
            "patient": {"age_years": plan["age_years"], "sex": plan["sex"]},
            "turns": turns,
            "device_vitals": plan["device_vitals"],
            "nurse_vitals": plan["nurse_vitals"],
            "expected": {
                "facts": gold_facts(plan),
                "alerts": alerts,
                "level": level,
                "level_rationale": rationale,
            },
            "generated_by": {
                "tool": "coding-agent",
                "model": writer_model,
                "date": datetime.now(UTC).date().isoformat(),
            },
            **dict.fromkeys(REVIEW_FIELDS),
        }
        findings = validate_scenario(body)
        if findings:
            failures[plan["id"]] = findings
            continue
        draft = Scenario.model_validate(body)
        path = inbox / f"{draft.id}.yaml"
        with path.open("x", encoding="utf-8") as handle:
            yaml.safe_dump(
                draft.model_dump(mode="json"), handle, sort_keys=False, allow_unicode=True
            )
        written.append(str(path))
    return {"status": "drafted", "count": len(written), "written": written, "failures": failures}
