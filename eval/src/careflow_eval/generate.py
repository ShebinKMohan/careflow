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
