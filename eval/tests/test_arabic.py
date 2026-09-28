"""Arabic scenarios (M2-03, SHE-290): seeded gold, drafts from the writer's answers, the
back-translation stage of the review, presence scoring and the per-language report.
Scripted providers stand in for the models; no network, no model server."""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import AsyncIterator, Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest
import yaml

from careflow_contracts.generated.alert_schema import RuleId
from careflow_eval import backtranslation, cli, extraction_report
from careflow_eval.datasets import expected_entries, load_dataset
from careflow_eval.generate import plan_alerts, write_arabic_drafts
from careflow_eval.models import Scenario
from careflow_eval.review import ARABIC_NOTE, ARABIC_PROMPT_VERSION, review_dataset
from careflow_eval.sampler import CATEGORY_MIX, arabic_plan, gold_facts
from careflow_eval.suites.extraction_en import STATED, expected_slots, presence_scored
from careflow_eval.validation import validate_scenario
from careflow_providers.base import ChatResult, Message

RECEIPT = {
    "model_id": "mlx-community/Qwen3.5-9B-MLX-4bit",
    "revision": "a" * 40,
    "source": "test-only synthetic model receipt",
    "base_url": "http://localhost:8081/v1",
    "served_model": "default_model",
    "server_model_ids": ["mlx-community/Qwen3.5-9B-MLX-4bit"],
}
WRITER = "claude-opus-5-5"


@pytest.fixture
def endpoint_checks(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    async def check(local: Any, receipt: dict[str, Any]) -> dict[str, Any]:
        calls.append(local.base_url)
        return {"base_url": local.base_url, "server_model_ids": receipt["server_model_ids"]}

    monkeypatch.setattr("careflow_eval.review.verify_model_endpoint", check)
    return calls


# --- A: the seeded gold -------------------------------------------------------------------------


def test_the_plan_is_seeded_and_keeps_the_english_category_mix() -> None:
    for dialect in ("gulf", "levantine"):
        plans = arabic_plan(dialect)
        assert plans == arabic_plan(dialect)  # same seed, same plan
        assert len(plans) == 50
        assert Counter(plan["category"] for plan in plans) == CATEGORY_MIX
        assert all(plan["id"].startswith(f"ar-{dialect[:3]}-") for plan in plans)
    assert arabic_plan("gulf", seed=8) != arabic_plan("gulf")
    with pytest.raises(ValueError, match="dialect"):
        arabic_plan("egyptian")


def test_the_plan_covers_every_rule_at_least_three_times() -> None:
    covered = Counter(
        rule
        for dialect in ("gulf", "levantine")
        for plan in arabic_plan(dialect)
        for rule in plan_alerts(plan)
    )
    assert {rule.value for rule in RuleId} <= set(covered)
    assert min(covered.values()) >= 3, covered


def test_gold_facts_refuse_two_values_for_one_key() -> None:
    plan = {
        "answers": [
            {"question_key": "chief_complaint", "gold": {"chest_pain.present": "yes"}},
            {"question_key": "chest_pain.present", "gold": {"chest_pain.present": "no"}},
        ]
    }
    with pytest.raises(ValueError, match="two values"):
        gold_facts(plan)


# --- B: the writer's answers become drafts ------------------------------------------------------


def _answers(plan: dict[str, Any]) -> list[dict[str, str]]:
    """Plain answers for a plan: a number where one is planned, an unsure phrase for unknown."""
    said = []
    for answer in plan["answers"]:
        values = list(answer["gold"].values())
        if any(value == "unknown" for value in values):
            text = "ما أدري"
        elif any(isinstance(value, int) for value in values):
            text = str(next(value for value in values if isinstance(value, int)))
        elif any(value == "no" for value in values):
            text = "لا"
        else:
            text = "نعم"
        said.append({"text": text, "gloss_en": "A test gloss."})
    return said


def test_answers_merge_into_valid_drafts(tmp_path: Path) -> None:
    plans = arabic_plan("gulf")[:3]
    answers = tmp_path / "answers.yaml"
    answers.write_text(
        yaml.safe_dump({plan["id"]: _answers(plan) for plan in plans}, allow_unicode=True),
        encoding="utf-8",
    )
    result = write_arabic_drafts(
        dialect="gulf", answers_path=answers, writer_model=WRITER, dataset_path=tmp_path / "ar"
    )
    assert result["count"] == 3 and result["failures"] == {}
    drafts = load_dataset(tmp_path / "ar" / "_inbox", include_unreviewed=True).scenarios
    for case, plan in zip(drafts, sorted(plans, key=lambda p: p["id"]), strict=True):
        assert case.language.value == "ar" and case.dialect == "gulf"
        assert case.generated_by.model == WRITER and case.reviewed_by is None
        assert {key.value: value for key, value in case.expected.facts.items()} == gold_facts(plan)
        assert sorted(rule.value for rule in case.expected.alerts) == plan_alerts(plan)
        assert all(turn.gloss_en and turn.states for turn in case.turns)
        # The draft keeps readable Arabic, not escapes.
        assert "\\u" not in (tmp_path / "ar" / "_inbox" / f"{case.id}.yaml").read_text("utf-8")


def test_a_wrong_answer_count_is_a_failure_and_writes_nothing(tmp_path: Path) -> None:
    plan = arabic_plan("levantine")[0]
    answers = tmp_path / "answers.yaml"
    answers.write_text(
        yaml.safe_dump({plan["id"]: _answers(plan)[:-1]}, allow_unicode=True), encoding="utf-8"
    )
    result = write_arabic_drafts(
        dialect="levantine",
        answers_path=answers,
        writer_model=WRITER,
        dataset_path=tmp_path / "ar",
    )
    assert result["count"] == 0
    assert "answers for" in result["failures"][plan["id"]][0]


def _arabic_body() -> dict[str, Any]:
    return {
        "synthetic": True,
        "id": "ar-test-routine",
        "category": "routine",
        "language": "ar",
        "dialect": "levantine",
        "description": "Synthetic test scenario.",
        "patient": {"age_years": 30, "sex": "female"},
        "turns": [
            {
                "question_key": "chief_complaint",
                "patient_text": "كتفي بيوجعني",
                "gloss_en": "My shoulder hurts.",
                "states": ["chief_complaint", "pain_location", "pain_present"],
            },
            {
                "question_key": "pain_score",
                "patient_text": "أربعة",
                "gloss_en": "Four.",
                "states": ["pain_score", "pain_present"],
            },
            {
                "question_key": "allergies_confirm",
                "patient_text": "ما بعرف",
                "gloss_en": "I don't know.",
                "states": ["allergies_confirm"],
            },
        ],
        "expected": {
            "facts": {
                "chief_complaint": "Shoulder pain",
                "pain_location": "shoulder",
                "pain_present": "yes",
                "pain_score": 4,
                "allergies_confirm": "Unsure about medicine allergies",
            },
            "alerts": [],
            "level": 4,
            "level_rationale": "Synthetic test label only.",
        },
        "generated_by": {"tool": "coding-agent", "model": WRITER, "date": "2026-09-28"},
    }


def test_an_arabic_scenario_needs_a_dialect_and_every_gloss() -> None:
    assert validate_scenario(_arabic_body()) == []
    body = _arabic_body()
    body["dialect"] = None
    assert any("dialect" in error for error in validate_scenario(body))
    body = _arabic_body()
    body["turns"][1]["gloss_en"] = None
    assert any("gloss_en" in error for error in validate_scenario(body))
    english = _arabic_body() | {"language": "en", "dialect": None}
    assert any("Arabic scenarios only" in error for error in validate_scenario(english))


def test_an_answer_states_only_gold_keys() -> None:
    body = _arabic_body()
    body["turns"][2]["states"].append("infection.rigors")
    assert validate_scenario(body) == ["an answer states infection.rigors, which has no gold fact"]


@pytest.mark.parametrize(
    ("turn", "text", "problem"),
    [
        (1, "كتير", "pain_score gold is not supported"),  # no number in the words
        (0, "كتفي", None),  # free text rests on the statement and the review
    ],
)
def test_arabic_gold_support(turn: int, text: str, problem: str | None) -> None:
    body = _arabic_body()
    body["turns"][turn]["patient_text"] = text
    case = Scenario.model_validate(body)
    if problem is None:
        assert expected_entries(case)
    else:
        with pytest.raises(ValueError, match=problem):
            expected_entries(case)


def test_unknown_needs_an_unsure_answer() -> None:
    body = _arabic_body()
    body["turns"][2]["states"] = ["allergies_confirm"]
    body["turns"].append(
        {
            "question_key": "infection.rigors",
            "patient_text": "لأ",
            "gloss_en": "No.",
            "states": ["infection.rigors"],
        }
    )
    body["expected"]["facts"]["infection.rigors"] = "unknown"
    with pytest.raises(ValueError, match="infection.rigors gold is not supported"):
        expected_entries(Scenario.model_validate(body))
    body["turns"][3]["patient_text"] = "مش متأكدة"
    assert expected_entries(Scenario.model_validate(body))


# --- C: the review, with its back-translation stage ---------------------------------------------


class ScriptedModel:
    """Answers the back-translation, its verdict and the judge; records every call."""

    task = "eval_judge"
    model_name = "qwen3.5-9b-4bit"

    def __init__(
        self,
        backs: Mapping[str, str],
        different: frozenset[str] | set[str] = frozenset(),
        *,
        accept: bool = True,
    ) -> None:
        self.backs = dict(backs)
        self.different = set(different)
        self.accept = accept
        self.systems: list[str] = []

    async def chat(
        self,
        messages: Sequence[Message],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> ChatResult:
        system = str(messages[0]["content"])
        self.systems.append(system)
        user = str(messages[-1]["content"])
        if system.startswith(backtranslation.BACK_SYSTEM):
            items = [json.loads(line) for line in user.splitlines()[1:]]
            body = {"items": [{"id": i["id"], "english": self.backs[i["arabic"]]} for i in items]}
        elif system.startswith(backtranslation.JUDGE_SYSTEM):
            items = [json.loads(line) for line in user.splitlines()[1:]]
            body = {
                "items": [
                    {
                        "id": i["id"],
                        "same_meaning": i["gloss"] not in self.different,
                        "issue": "differs" if i["gloss"] in self.different else None,
                    }
                    for i in items
                ]
            }
        else:
            cases = json.loads(user)
            body = {
                "verdicts": [
                    {
                        "id": case["id"],
                        "content_sha256": case["content_sha256"],
                        "realistic_wording": True,
                        "facts_supported": self.accept,
                        "alerts_consistent": True,
                        "no_phi": True,
                        "reasons": [] if self.accept else ["a scripted rejection"],
                    }
                    for case in cases
                ]
            }
        return ChatResult(text=json.dumps(body), model=self.model_name)

    async def stream(self, messages: Sequence[Message]) -> AsyncIterator[str]:
        yield (await self.chat(messages)).text


def _inbox(tmp_path: Path) -> Path:
    dataset = tmp_path / "intake_ar"
    (dataset / "_inbox").mkdir(parents=True)
    body = _arabic_body()
    (dataset / "_inbox" / "ar-test-routine.yaml").write_text(
        yaml.safe_dump(body, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    return dataset


def _log(path: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in Path(path).read_text("utf-8").splitlines()]


BACKS = {"كتفي بيوجعني": "My shoulder hurts", "أربعة": "Four", "ما بعرف": "I don't know"}


async def test_a_matching_back_translation_reaches_the_judge_and_is_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, endpoint_checks: list[str]
) -> None:
    dataset = _inbox(tmp_path)
    model = ScriptedModel(BACKS)
    monkeypatch.setattr("careflow_eval.review.build_provider", lambda *args, **kwargs: model)
    result = await review_dataset(dataset_path=dataset, model_receipt=RECEIPT, batch_size=1)
    assert result["accepted"] == 1 and result["rejected"] == 0
    approved = load_dataset(dataset).scenarios[0]
    assert approved.reviewed_by == "agent:local/qwen3.5-9b-4bit"
    assert "كتفي بيوجعني" in (dataset / "ar-test-routine.yaml").read_text("utf-8")
    judge_system = model.systems[-1]
    assert ARABIC_NOTE in judge_system
    entry = _log(result["log_path"])[-1]
    assert entry["decision"] == "accepted" and entry["dialect"] == "levantine"
    assert [row["flagged"] for row in entry["backtranslation"]] == [False, False, False]
    assert entry["backtranslation"][1]["back_translation"] == "Four"
    receipts = next(dataset.parent.glob("review-receipts/*"))
    stage = json.loads((receipts / "backtranslation.receipt.json").read_text("utf-8"))
    assert stage["rows"]["ar-test-routine"] == entry["backtranslation"]
    assert stage["chrf_floor"] == 40.0
    assert [item["stage"] for item in stage["endpoint_observations"]] == ["before", "after"]
    assert (receipts / "backtranslation.cassette.json").is_file()
    batch = json.loads(next(receipts.glob("batch-*.receipt.json")).read_text("utf-8"))
    assert batch["review_prompt_version"] == ARABIC_PROMPT_VERSION


@pytest.mark.parametrize(
    ("backs", "different", "reason"),
    [
        (BACKS | {"أربعة": "Four"}, {"Four."}, "meaning differs"),
        (BACKS | {"أربعة": "Fourteen apples on the table"}, set(), "chrF"),
    ],
)
async def test_a_flagged_answer_rejects_before_the_judge(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    endpoint_checks: list[str],
    backs: dict[str, str],
    different: set[str],
    reason: str,
) -> None:
    dataset = _inbox(tmp_path)
    model = ScriptedModel(backs, different)
    monkeypatch.setattr("careflow_eval.review.build_provider", lambda *args, **kwargs: model)
    result = await review_dataset(dataset_path=dataset, model_receipt=RECEIPT)
    assert result["accepted"] == 0 and result["rejected"] == 1
    assert (dataset / "_inbox" / "ar-test-routine.yaml").exists()  # the draft is kept
    assert not any(ARABIC_NOTE in system for system in model.systems)  # no judge call
    entry = _log(result["log_path"])[-1]
    assert entry["stage"] == "back-translation" and entry["decision"] == "rejected"
    assert reason in entry["reasons"][0] and "answer 2 (pain_score)" in entry["reasons"][0]


class FakeCodex(ScriptedModel):
    """The audited Codex judge's interface, scripted (no subprocess, no model)."""

    model_name = "gpt-6-astra"

    def __init__(self, backs: Mapping[str, str], *, accept: bool = True) -> None:
        super().__init__(backs, accept=accept)
        self.verified_model_id: str | None = None
        self.raw_cli_evidence: list[dict[str, Any]] = []
        self.calls_made = 0
        self.remaining_calls = 199

    async def verify_auth(self) -> dict[str, Any]:
        return {"loggedIn": True, "authMethod": "chatgpt"}

    async def chat(
        self,
        messages: Sequence[Message],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> ChatResult:
        result = await super().chat(messages, temperature=temperature, max_tokens=max_tokens)
        self.calls_made += 1
        self.remaining_calls -= 1
        self.verified_model_id = self.model_name
        self.raw_cli_evidence.append({"call_number": self.calls_made})
        return result


def _codex(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, judge: FakeCodex) -> Path:
    monkeypatch.setenv("CAREFLOW_RUNTIME", "offline")
    monkeypatch.setattr("careflow_eval.review.AuditedCodexReviewProvider", lambda **kwargs: judge)
    config = tmp_path / "codex.yaml"
    config.write_text(
        yaml.safe_dump({"tasks": {"eval_judge": {"provider": "cli", "tool": "codex"}}})
    )
    return config


async def test_a_cli_judge_rejects_arabic_without_a_recorded_back_translation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset = _inbox(tmp_path)
    judge = FakeCodex(BACKS)
    config = _codex(tmp_path, monkeypatch, judge)
    result = await review_dataset(
        dataset_path=dataset, config_path=config, judge="cli", reviewer_model="gpt-6-astra"
    )
    assert result["accepted"] == 0 and result["rejected"] == 1
    assert judge.systems == []  # neither a back-translation nor a judge call
    entry = _log(result["log_path"])[-1]
    assert entry["stage"] == "back-translation"
    assert "no passing back-translation record" in entry["reasons"][0]


async def test_a_cli_judge_reuses_a_local_back_translation_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, endpoint_checks: list[str]
) -> None:
    dataset = _inbox(tmp_path)
    local = ScriptedModel(BACKS, accept=False)  # the local judge rejects; the check passed
    monkeypatch.setattr("careflow_eval.review.build_provider", lambda *args, **kwargs: local)
    first = await review_dataset(dataset_path=dataset, model_receipt=RECEIPT)
    assert first["rejected"] == 1 and (dataset / "_inbox" / "ar-test-routine.yaml").exists()
    checked = _log(first["log_path"])[-1]["backtranslation"]
    judge = FakeCodex(BACKS)
    config = _codex(tmp_path, monkeypatch, judge)
    second = await review_dataset(
        dataset_path=dataset, config_path=config, judge="cli", reviewer_model="gpt-6-astra"
    )
    assert second["accepted"] == 1
    assert not any(system.startswith(backtranslation.BACK_SYSTEM) for system in judge.systems)
    assert load_dataset(dataset).scenarios[0].reviewed_by == "agent:codex/gpt-6-astra"
    entry = _log(second["log_path"])[-1]
    assert entry["decision"] == "accepted" and entry["backtranslation"] == checked
    assert entry["model_receipt"]["identity_source"].startswith("verified Codex session log")
    receipts = sorted(dataset.parent.glob("review-receipts/*/backtranslation.receipt.json"))
    reused = json.loads(receipts[-1].read_text("utf-8"))["reused"]
    assert reused == {"ar-test-routine": receipts[0].relative_to(dataset.parent).as_posix()}


# --- D: presence scoring and the per-language report -------------------------------------------


def test_arabic_free_text_is_scored_by_presence_and_codes_exactly() -> None:
    case = Scenario.model_validate(_arabic_body())
    slots = {slot["key"]: slot["value"] for slot in expected_slots(case)["facts"]}
    assert slots["chief_complaint"] == slots["pain_location"] == STATED
    assert slots["allergies_confirm"] == STATED
    assert slots["pain_present"] == "yes" and slots["pain_score"] == 4
    assert presence_scored("symptoms") and not presence_scored("pain_score")


def _row(case_id: str, dialect: str | None, expected: list[str], raised: list[str]) -> dict:
    return {
        "id": case_id,
        "category": "chest_pain",
        "status": "completed",
        "dialect": dialect,
        "expected": {"facts": [{"key": "chest_pain.present", "value": "yes"}], "alerts": expected},
        "prediction": {
            "facts": [{"key": "chest_pain.present", "value": "yes"}],
            "alerts": raised,
            "extraction_incomplete": False,
        },
        "spans": {"kept": 1, "patient_stated": 1, "sliced": 1, "dropped": 0},
    }


def _suite(suite: str, rows: list[dict]) -> dict[str, Any]:
    return {"suite": suite, "status": "completed", "metadata": {"mode": "replay"}, "results": rows}


def test_the_report_lists_missed_alerts_first_and_splits_dialects() -> None:
    reports = {
        "en": _suite("extraction_en", [_row("en-1", None, ["CHEST_PAIN_ECG_10MIN"], [])]),
        "ar": _suite(
            "extraction_ar",
            [
                _row("ar-gul-1", "gulf", ["CHEST_PAIN_ECG_10MIN"], ["CHEST_PAIN_ECG_10MIN"]),
                _row("ar-lev-1", "levantine", [], []),
            ],
        ),
    }
    report = extraction_report.build(reports)
    assert list(report)[:4] == ["schema_version", "report", "publish", "missed_alerts"]
    assert report["publish"] is False
    assert [row["id"] for row in report["missed_alerts"]] == ["en-1"]
    assert set(report["by_language"]) == {"en", "ar", "ar_gulf", "ar_levantine"}
    assert report["by_language"]["ar_gulf"]["cases"] == 1
    assert report["span_verified_rate"]["ar"] == 1.0
    assert report["per_field"]["ar"]["chest_pain.present"]["scored_by"] == "exact value"
    assert any("native speakers" in caveat for caveat in report["caveats"])
    assert any(model["status"] == "skipped" for model in report["models"])
    text = extraction_report.markdown(report)
    assert text.index("## Missed expected alerts") < text.index("## By language")
    assert not re.search(r"\d+(\.\d+)?\s*%", text)  # never a percentage


def test_the_report_says_so_when_no_alert_was_missed() -> None:
    reports = {"ar": _suite("extraction_ar", [_row("ar-gul-1", "gulf", [], [])])}
    report = extraction_report.build(reports)
    assert report["missed_alerts"] == []
    assert report["missed_alerts_note"] == "Every expected alert was raised."
    assert "None: every expected alert was raised" in extraction_report.markdown(report)


def test_the_cli_lists_the_arabic_suite(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["list"]) == 0
    assert "extraction_ar" in capsys.readouterr().out


def test_run_extraction_writes_the_combined_report_from_replays(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("a replay never builds a live model")

    monkeypatch.setattr("careflow_eval.runner.build_provider", forbidden)
    assert (
        cli.main(["run", "extraction", "--lang", "en", "--replay", "--output", str(tmp_path)]) == 0
    )
    report = json.loads((tmp_path / "extraction.json").read_text("utf-8"))
    assert report["publish"] is False and set(report["by_language"]) == {"en"}
    # The frozen English baseline's one recorded timeout is a missed alert, listed by id.
    assert [row["id"] for row in report["missed_alerts"]] == ["child-parent-unsure-waking"]
    assert report["missed_alerts"][0]["missed"] == ["NG143_RED_FLAG"]
    assert report["by_language"]["en"]["field_f1"] == 0.7417475728155339
    assert report["patient_stated_slice_rate"]["en"] == 1.0
    assert report["sources"]["en"]["mode"] == "replay"
    # A replay's suite report goes to replay-verification, never the live baseline's folder.
    assert list((tmp_path / "replay-verification" / "extraction_en").glob("2*.json"))
    assert not (tmp_path / "extraction_en").exists()
    assert "## Missed expected alerts" in (tmp_path / "extraction.md").read_text("utf-8")
