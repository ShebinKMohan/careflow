"""Approval requires a separate model and a verdict on the unchanged synthetic input."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Sequence
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
import yaml

from careflow_eval.datasets import DatasetError, load_dataset
from careflow_eval.generate import SelectedModelCli, category_mix, generate_dataset
from careflow_eval.identity import require_distinct_models
from careflow_eval.review import review_dataset, verify_model_endpoint
from careflow_providers.base import ChatResult, Message
from careflow_providers.config import LocalTaskConfig

RECEIPT = {
    "model_id": "mlx-community/Qwen3.5-9B-MLX-4bit",
    "revision": "a" * 40,
    "source": "test-only synthetic model receipt",
    "base_url": "http://localhost:8081/v1",
    "served_model": "default_model",
    "server_model_ids": ["mlx-community/Qwen3.5-9B-MLX-4bit"],
}
LOCAL = LocalTaskConfig(provider="local", model="qwen3.5-9b-4bit")
CLAUDE_MODEL = "claude-sonnet-5"


@pytest.fixture
def endpoint_checks(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Fake-provider tests must explicitly replace the endpoint probe, never use a server."""
    calls: list[str] = []

    async def check(local: LocalTaskConfig, receipt: dict[str, Any]) -> dict[str, Any]:
        calls.append(local.base_url)
        return {
            "base_url": local.base_url,
            "served_model": local.served_model,
            "server_model_ids": receipt["server_model_ids"],
            "checked_at": "2026-09-27T10:00:00+00:00",
        }

    monkeypatch.setattr("careflow_eval.review.verify_model_endpoint", check)
    return calls


def draft(tmp_path: Path) -> Path:
    root = tmp_path / "intake_en"
    inbox = root / "_inbox"
    inbox.mkdir(parents=True)
    body = {
        "id": "routine-review",
        "description": "Synthetic ankle complaint",
        "language": "en",
        "category": "routine",
        "synthetic": True,
        "generated_by": {"tool": "coding-agent", "model": "GPT-6", "date": "2026-09-27"},
        "reviewed_by": None,
        "reviewed_at": None,
        "review_method": None,
        "reviewed_content_sha256": None,
        "patient": {"age_years": 28, "sex": "female"},
        "turns": [{"question_key": "chief_complaint", "patient_text": "My ankle hurts"}],
        "device_vitals": "normal",
        "expected": {
            "facts": {"chief_complaint": "My ankle hurts"},
            "alerts": [],
            "level": 4,
            "level_rationale": "Synthetic test label only; not clinical accuracy.",
        },
    }
    (inbox / "routine-review.yaml").write_text(yaml.safe_dump(body), encoding="utf-8")
    return root


class Judge:
    task = "eval_judge"
    model_name = "qwen3.5-9b-4bit"

    def __init__(
        self,
        *,
        hash_matches: bool = True,
        facts_supported: bool = True,
        on_call: Callable[[], None] | None = None,
        id_is_hash: bool = False,
    ) -> None:
        self.hash_matches = hash_matches
        self.facts_supported = facts_supported
        self.calls = 0
        self.on_call = on_call
        self.id_is_hash = id_is_hash
        self.payloads: list[list[dict[str, Any]]] = []

    async def chat(
        self,
        messages: Sequence[Message],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> ChatResult:
        self.calls += 1
        cases = json.loads(messages[-1]["content"])
        self.payloads.append(cases)
        if self.on_call:
            self.on_call()
        return ChatResult(
            text=json.dumps(
                {
                    "verdicts": [
                        {
                            "id": case["content_sha256"] if self.id_is_hash else case["id"],
                            "content_sha256": case["content_sha256"]
                            if self.hash_matches
                            else "b" * 64,
                            "realistic_wording": True,
                            "facts_supported": self.facts_supported,
                            "alerts_consistent": True,
                            "no_phi": True,
                            "reasons": []
                            if self.facts_supported
                            else ["Unsupported extraction gold"],
                        }
                        for case in cases
                    ]
                }
            ),
            model=self.model_name,
        )

    async def stream(self, messages: Sequence[Message]) -> AsyncIterator[str]:
        yield (await self.chat(messages)).text


class CLIJudge(Judge):
    """Controlled audited-provider results; no subprocess or model is invoked."""

    model_name = CLAUDE_MODEL

    def __init__(
        self,
        *,
        actual_model: str | None = CLAUDE_MODEL,
        retain_evidence: bool = True,
        hash_matches: bool = True,
        facts_supported: bool = True,
    ) -> None:
        super().__init__(hash_matches=hash_matches, facts_supported=facts_supported)
        self.actual_model = actual_model
        self.retain_evidence = retain_evidence
        self.verified_model_id: str | None = None
        self.raw_cli_evidence: list[dict[str, Any]] = []
        self.auth_calls = 0

    @property
    def calls_made(self) -> int:
        return self.calls

    @property
    def remaining_calls(self) -> int:
        return 199 - self.calls

    async def verify_auth(self) -> dict[str, Any]:
        self.auth_calls += 1
        return {"authMethod": "claude.ai", "subscriptionType": "max", "loggedIn": True}

    async def chat(
        self,
        messages: Sequence[Message],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> ChatResult:
        result = await super().chat(messages, temperature=temperature, max_tokens=max_tokens)
        self.verified_model_id = self.actual_model
        if self.retain_evidence:
            self.raw_cli_evidence.append(
                {
                    "requested_model": self.model_name,
                    "result": {
                        "modelUsage": {self.actual_model: {"inputTokens": 100, "outputTokens": 30}},
                        "structured_output": json.loads(result.text),
                    },
                }
            )
        return result


def cli_config(tmp_path: Path, tool: str = "claude") -> Path:
    path = tmp_path / "cli-review-config.yaml"
    path.write_text(yaml.safe_dump({"tasks": {"eval_judge": {"provider": "cli", "tool": tool}}}))
    return path


def test_default_category_mix_is_exact_and_scaled_mix_keeps_count() -> None:
    assert category_mix(50) == {
        "chest_pain": 8,
        "stroke": 6,
        "sepsis": 8,
        "child": 6,
        "breathing": 4,
        "routine": 18,
    }
    for n in (1, 7, 25, 99):
        assert sum(category_mix(n).values()) == n


def test_cli_model_is_selected_in_the_actual_argument_vector(tmp_path: Path) -> None:
    for tool in ("codex", "claude"):
        provider = SelectedModelCli(tool, "identified-test-model")
        args = provider._argv("data only", tmp_path / "reply")
        assert args[-2:] == ["--model", "identified-test-model"]
        assert provider.model_name == "identified-test-model"


def test_review_refuses_same_family_or_unspecified_writer() -> None:
    require_distinct_models("GPT-6", "mlx-community/Qwen3.5-9B-MLX-4bit")
    with pytest.raises(ValueError, match="different family"):
        require_distinct_models("qwen-4b", "qwen-9b")
    with pytest.raises(ValueError, match="not established"):
        require_distinct_models("unspecified-cli-default:codex", "qwen-9b")


async def test_generation_is_refused_on_the_live_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CAREFLOW_RUNTIME", "live")
    with pytest.raises(ValueError, match="offline"):
        await generate_dataset()


@pytest.mark.parametrize(("hash_matches", "facts_supported"), [(False, True), (True, False)])
async def test_rejected_or_mismatched_verdict_never_promotes_draft(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    hash_matches: bool,
    facts_supported: bool,
    endpoint_checks: list[str],
) -> None:
    dataset = draft(tmp_path)
    judge = Judge(hash_matches=hash_matches, facts_supported=facts_supported)
    monkeypatch.setattr("careflow_eval.review.build_provider", lambda *args, **kwargs: judge)
    result = await review_dataset(dataset_path=dataset, model_receipt=RECEIPT)
    assert result["accepted"] == 0
    assert result["rejected"] == 1
    assert (dataset / "_inbox/routine-review.yaml").exists()
    assert not (dataset / "routine-review.yaml").exists()
    assert judge.calls == 1
    assert len(endpoint_checks) == 2


async def test_approval_moves_only_the_reviewed_content_and_saves_real_gate_receipts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    endpoint_checks: list[str],
) -> None:
    dataset = draft(tmp_path)
    judge = Judge()
    monkeypatch.setattr("careflow_eval.review.build_provider", lambda *args, **kwargs: judge)
    result = await review_dataset(dataset_path=dataset, model_receipt=RECEIPT)
    assert result["accepted"] == 1 and result["rejected"] == 0
    assert not (dataset / "_inbox/routine-review.yaml").exists()
    approved = load_dataset(dataset)
    assert len(approved.scenarios) == 1
    assert approved.scenarios[0].reviewed_by == "agent:local/qwen3.5-9b-4bit"
    assert list(dataset.parent.glob("review-receipts/*/*.cassette.json"))
    assert list(dataset.parent.glob("review-receipts/*/*.receipt.json"))
    log_path = Path(result["log_path"])
    log = json.loads((await asyncio.to_thread(log_path.read_text, encoding="utf-8")).strip())
    assert log["decision"] == "accepted" and log["model_receipt"] == RECEIPT
    assert "routine-review" in (dataset.parent / "REVIEW.md").read_text(encoding="utf-8")
    assert len(endpoint_checks) == 2
    assert [item["stage"] for item in log["endpoint_observations"]] == ["before", "after"]
    assert judge.payloads[0][0]["scenario"]["resolved_device_vitals"]["heart_rate"] > 0
    assert judge.payloads[0][0]["id"] == "routine-review"
    assert "reviewed_by" not in judge.payloads[0][0]["scenario"]
    assert list(dataset.parent.glob("review-receipts/*/drafts/routine-review.yaml"))
    receipt = json.loads(next(dataset.parent.glob("review-receipts/*/*.receipt.json")).read_text())
    assert receipt["review_prompt_version"] == "independent-scenario-review-v2"


async def test_editing_live_approval_does_not_change_archived_approved_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, endpoint_checks: list[str]
) -> None:
    dataset = draft(tmp_path)
    monkeypatch.setattr("careflow_eval.review.build_provider", lambda *args, **kwargs: Judge())
    result = await review_dataset(dataset_path=dataset, model_receipt=RECEIPT)
    assert result["accepted"] == 1
    live = dataset / "routine-review.yaml"
    archive = next(dataset.parent.glob("review-receipts/*/approved/routine-review.yaml"))
    original = archive.read_bytes()
    changed = yaml.safe_load(live.read_text())
    changed["description"] = "An edit made after the independent review"
    live.write_text(yaml.safe_dump(changed))
    assert archive.read_bytes() == original
    with pytest.raises(DatasetError, match="does not match current scenario content"):
        load_dataset(dataset)
    assert not list(dataset.glob("*.pending"))


async def test_hash_instead_of_slug_id_remains_a_rejected_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, endpoint_checks: list[str]
) -> None:
    dataset = draft(tmp_path)
    judge = Judge(id_is_hash=True)
    monkeypatch.setattr("careflow_eval.review.build_provider", lambda *args, **kwargs: judge)
    result = await review_dataset(dataset_path=dataset, model_receipt=RECEIPT, batch_size=1)
    assert result["accepted"] == 0 and result["rejected"] == 1
    assert (dataset / "_inbox/routine-review.yaml").exists()
    assert not (dataset / "routine-review.yaml").exists()
    recorded = json.loads(next(dataset.parent.glob("review-receipts/*/*.receipt.json")).read_text())
    assert recorded["error"] == "judge output did not contain exactly one verdict per scenario"


@pytest.mark.parametrize(
    "updates",
    [
        {"base_url": "http://localhost:9999/v1"},
        {"served_model": "another-model"},
        {"server_model_ids": ["GPT-6"]},
        {"server_model_ids": ["default_model"]},
        {"server_model_ids": [RECEIPT["model_id"], "another-loaded-model"]},
        {"server_model_ids": "not-a-list"},
        {"server_model_ids": [RECEIPT["model_id"], RECEIPT["model_id"]]},
        {"revision": "not-a-pinned-revision"},
    ],
)
async def test_receipt_binding_rejects_mismatched_or_ambiguous_identity_before_network(
    updates: dict[str, Any],
) -> None:
    with respx.mock:
        with pytest.raises(ValueError):
            await verify_model_endpoint(LOCAL, {**RECEIPT, **updates})
        assert len(respx.calls) == 0


async def test_probe_uses_live_models_and_accepts_pinned_cache_path() -> None:
    loaded_path = "/models/models--mlx-community--Qwen3.5-9B-MLX-4bit/snapshots/" + "a" * 40
    receipt = {**RECEIPT, "server_model_ids": [loaded_path, "default_model"]}
    with respx.mock:
        route = respx.get("http://localhost:8081/v1/models").mock(
            return_value=httpx.Response(
                200, json={"data": [{"id": "default_model"}, {"id": loaded_path}]}
            )
        )
        observation = await verify_model_endpoint(LOCAL, receipt)
    assert route.call_count == 1
    assert observation["server_model_ids"] == sorted(receipt["server_model_ids"])
    assert observation["served_model"] == "default_model"


@pytest.mark.parametrize("ids", [["GPT-6"], [], [None], [RECEIPT["model_id"]] * 2])
async def test_probe_refuses_changed_or_invalid_live_identity(ids: list[Any]) -> None:
    with respx.mock:
        respx.get("http://localhost:8081/v1/models").mock(
            return_value=httpx.Response(200, json={"data": [{"id": item} for item in ids]})
        )
        with pytest.raises(ValueError):
            await verify_model_endpoint(LOCAL, RECEIPT)


async def test_server_change_after_positive_verdict_prevents_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset = draft(tmp_path)
    judge = Judge()
    monkeypatch.setattr("careflow_eval.review.build_provider", lambda *args, **kwargs: judge)
    with respx.mock:
        respx.get("http://localhost:8081/v1/models").mock(
            side_effect=[
                httpx.Response(200, json={"data": [{"id": RECEIPT["model_id"]}]}),
                httpx.Response(200, json={"data": [{"id": "GPT-6"}]}),
            ]
        )
        result = await review_dataset(dataset_path=dataset, model_receipt=RECEIPT)
    assert judge.calls == 1
    assert result["accepted"] == 0 and result["rejected"] == 1
    assert (dataset / "_inbox/routine-review.yaml").exists()
    assert not (dataset / "routine-review.yaml").exists()
    receipt_path = next(dataset.parent.glob("review-receipts/*/*.receipt.json"))
    recorded = json.loads(receipt_path.read_text())
    assert recorded["verdicts"]["routine-review"]["facts_supported"] is True
    assert "endpoint verification after judge" in recorded["error"]


async def test_edit_during_judge_call_preserves_new_draft_and_rejects_old_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, endpoint_checks: list[str]
) -> None:
    dataset = draft(tmp_path)
    source = dataset / "_inbox/routine-review.yaml"
    edited = yaml.safe_load(source.read_text())
    edited["description"] = "A newer draft the judge has not reviewed"

    def edit_draft() -> None:
        source.write_text(yaml.safe_dump(edited))

    judge = Judge(on_call=edit_draft)
    monkeypatch.setattr("careflow_eval.review.build_provider", lambda *args, **kwargs: judge)
    result = await review_dataset(dataset_path=dataset, model_receipt=RECEIPT)
    assert result["accepted"] == 0
    assert yaml.safe_load(source.read_text()) == edited
    assert not (dataset / "routine-review.yaml").exists()


async def test_replacement_between_source_read_and_claim_is_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, endpoint_checks: list[str]
) -> None:
    dataset = draft(tmp_path)
    source = dataset / "_inbox/routine-review.yaml"
    edited = yaml.safe_load(source.read_text())
    edited["description"] = "A replacement written immediately before source claim"
    rename = Path.rename

    def replace_before_rename(path: Path, target: Path) -> Path:
        if path == source:
            path.write_text(yaml.safe_dump(edited))
        return rename(path, target)

    monkeypatch.setattr(Path, "rename", replace_before_rename)
    monkeypatch.setattr("careflow_eval.review.build_provider", lambda *args, **kwargs: Judge())
    result = await review_dataset(dataset_path=dataset, model_receipt=RECEIPT)
    assert result["accepted"] == 0
    assert yaml.safe_load(source.read_text()) == edited
    assert not (dataset / "routine-review.yaml").exists()


async def test_existing_approval_is_not_overwritten_and_claimed_draft_is_restored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, endpoint_checks: list[str]
) -> None:
    dataset = draft(tmp_path)
    source = dataset / "_inbox/routine-review.yaml"
    original = source.read_bytes()
    destination = dataset / "routine-review.yaml"
    destination.write_text("existing approved evidence")
    monkeypatch.setattr("careflow_eval.review.build_provider", lambda *args, **kwargs: Judge())
    result = await review_dataset(dataset_path=dataset, model_receipt=RECEIPT)
    assert result["accepted"] == 0
    assert source.read_bytes() == original
    assert destination.read_text() == "existing approved evidence"
    archive = next(dataset.parent.glob("review-receipts/*/drafts/routine-review.yaml"))
    source.write_text("A later edit to the restored draft")
    assert archive.read_bytes() == original
    assert not list(source.parent.glob("*.pending"))


async def test_timeout_records_failure_without_promotion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, endpoint_checks: list[str]
) -> None:
    dataset = draft(tmp_path)

    class SlowJudge(Judge):
        async def chat(
            self,
            messages: Sequence[Message],
            *,
            temperature: float = 0.0,
            max_tokens: int | None = None,
        ) -> ChatResult:
            await asyncio.Event().wait()
            raise AssertionError("The timed-out judge must never complete")

    monkeypatch.setattr("careflow_eval.review.build_provider", lambda *args, **kwargs: SlowJudge())
    result = await review_dataset(dataset_path=dataset, model_receipt=RECEIPT, timeout_seconds=0.01)
    assert result["accepted"] == 0 and result["rejected"] == 1
    assert (dataset / "_inbox/routine-review.yaml").exists()
    assert not (dataset / "routine-review.yaml").exists()
    recorded = json.loads(next(dataset.parent.glob("review-receipts/*/*.receipt.json")).read_text())
    assert recorded["verdicts"] == {}
    assert recorded["error"] == "judge call failed: TimeoutError"


async def test_receipt_write_failure_never_moves_or_approves_draft(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, endpoint_checks: list[str]
) -> None:
    dataset = draft(tmp_path)
    source = dataset / "_inbox/routine-review.yaml"
    original = source.read_bytes()
    write_text = Path.write_text

    def fail_receipt_write(path: Path, data: str, *args: Any, **kwargs: Any) -> int:
        if path.name.endswith(".receipt.json"):
            raise OSError("Synthetic receipt storage failure")
        return write_text(path, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_receipt_write)
    monkeypatch.setattr("careflow_eval.review.build_provider", lambda *args, **kwargs: Judge())
    with pytest.raises(OSError, match="receipt storage failure"):
        await review_dataset(dataset_path=dataset, model_receipt=RECEIPT)
    assert source.read_bytes() == original
    assert not (dataset / "routine-review.yaml").exists()


async def test_cli_review_retains_actual_identity_raw_result_and_canonical_recording(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, endpoint_checks: list[str]
) -> None:
    monkeypatch.setenv("CAREFLOW_RUNTIME", "offline")
    dataset = draft(tmp_path)
    judge = CLIJudge()
    selected: dict[str, Any] = {}

    def audited_provider(**kwargs: Any) -> CLIJudge:
        selected.update(kwargs)
        return judge

    monkeypatch.setattr("careflow_eval.review.AuditedCLIReviewProvider", audited_provider)
    result = await review_dataset(
        dataset_path=dataset,
        config_path=cli_config(tmp_path),
        judge="cli",
        reviewer_model=CLAUDE_MODEL,
    )
    assert result["accepted"] == 1 and result["model"] == CLAUDE_MODEL
    assert judge.calls_made == 1 and judge.auth_calls == 1
    assert selected["model_id"] == CLAUDE_MODEL and selected["tool"] == "claude"
    assert selected["schema"]["properties"]["verdicts"]["type"] == "array"
    assert endpoint_checks == []
    assert load_dataset(dataset).scenarios[0].reviewed_by == f"agent:claude/{CLAUDE_MODEL}"
    receipt = json.loads(next(dataset.parent.glob("review-receipts/*/*.receipt.json")).read_text())
    assert receipt["judge"] == "cli"
    assert receipt["model_receipt"]["model_id"] == CLAUDE_MODEL
    assert receipt["model_receipt"]["auth"]["authMethod"] == "claude.ai"
    assert receipt["model_receipt"]["remaining_calls"] == 198
    assert receipt["raw_cli_evidence"] == judge.raw_cli_evidence
    recorded = json.loads(
        next(dataset.parent.glob("review-receipts/*/*.cassette.json")).read_text()
    )
    response = next(iter(recorded.values()))
    assert response["model"] == CLAUDE_MODEL
    assert json.loads(response["text"])["verdicts"][0]["id"] == "routine-review"


@pytest.mark.parametrize(
    "options",
    [
        {"actual_model": None},
        {"actual_model": "claude-haiku-4-5"},
        {"retain_evidence": False},
        {"hash_matches": False},
        {"facts_supported": False},
    ],
)
async def test_cli_review_preserves_identity_hash_and_checklist_approval_gates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, options: dict[str, Any]
) -> None:
    monkeypatch.setenv("CAREFLOW_RUNTIME", "offline")
    dataset = draft(tmp_path)
    judge = CLIJudge(**options)
    monkeypatch.setattr("careflow_eval.review.AuditedCLIReviewProvider", lambda **kwargs: judge)
    result = await review_dataset(
        dataset_path=dataset,
        config_path=cli_config(tmp_path),
        judge="cli",
        reviewer_model=CLAUDE_MODEL,
    )
    assert result["accepted"] == 0 and result["rejected"] == 1
    assert (dataset / "_inbox/routine-review.yaml").exists()
    assert not (dataset / "routine-review.yaml").exists()


@pytest.mark.parametrize("runtime", [None, "live"])
async def test_cli_review_requires_explicit_offline_runtime(
    monkeypatch: pytest.MonkeyPatch, runtime: str | None
) -> None:
    if runtime is None:
        monkeypatch.delenv("CAREFLOW_RUNTIME", raising=False)
    else:
        monkeypatch.setenv("CAREFLOW_RUNTIME", runtime)
    with pytest.raises(ValueError, match="CAREFLOW_RUNTIME=offline"):
        await review_dataset(judge="cli", reviewer_model=CLAUDE_MODEL)


async def test_cli_review_requires_explicit_claude_config_and_no_local_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CAREFLOW_RUNTIME", "offline")
    dataset = draft(tmp_path)
    with pytest.raises(ValueError, match="explicit reviewer_model"):
        await review_dataset(dataset_path=dataset, judge="cli")
    with pytest.raises(ValueError, match="omit the local model receipt"):
        await review_dataset(
            dataset_path=dataset,
            judge="cli",
            reviewer_model=CLAUDE_MODEL,
            model_receipt=RECEIPT,
        )
    # Codex is a CLI judge too (M2-03), but only with an explicit Codex model id.
    with pytest.raises(ValueError, match="explicit Codex model ID"):
        await review_dataset(
            dataset_path=dataset,
            config_path=cli_config(tmp_path, "codex"),
            judge="cli",
            reviewer_model=CLAUDE_MODEL,
        )


async def test_cli_same_family_writer_is_rejected_before_auth_or_model_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CAREFLOW_RUNTIME", "offline")
    dataset = draft(tmp_path)
    source = dataset / "_inbox/routine-review.yaml"
    body = yaml.safe_load(source.read_text())
    body["generated_by"]["model"] = "claude-opus-4-6"
    source.write_text(yaml.safe_dump(body))
    judge = CLIJudge()
    monkeypatch.setattr("careflow_eval.review.AuditedCLIReviewProvider", lambda **kwargs: judge)
    with pytest.raises(ValueError, match="different family"):
        await review_dataset(
            dataset_path=dataset,
            config_path=cli_config(tmp_path),
            judge="cli",
            reviewer_model=CLAUDE_MODEL,
        )
    assert judge.auth_calls == judge.calls_made == 0


@pytest.mark.parametrize("deterministic_rejection", [False, True])
async def test_cancellation_preserves_log_of_prior_completed_decisions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    endpoint_checks: list[str],
    deterministic_rejection: bool,
) -> None:
    dataset = draft(tmp_path)
    second = yaml.safe_load((dataset / "_inbox/routine-review.yaml").read_text())
    second["id"] = "routine-second"
    (dataset / "_inbox/routine-second.yaml").write_text(yaml.safe_dump(second))
    waiting = asyncio.Event()
    if deterministic_rejection:
        monkeypatch.setattr(
            "careflow_eval.review.validate_scenario",
            lambda case: (
                ["Synthetic deterministic rejection"] if case.id == "routine-review" else []
            ),
        )

    class InterruptedJudge(Judge):
        async def chat(
            self,
            messages: Sequence[Message],
            *,
            temperature: float = 0.0,
            max_tokens: int | None = None,
        ) -> ChatResult:
            if deterministic_rejection or self.calls == 1:
                waiting.set()
                await asyncio.Event().wait()
                raise AssertionError("The interrupted batch must never complete")
            return await super().chat(messages, temperature=temperature, max_tokens=max_tokens)

    monkeypatch.setattr(
        "careflow_eval.review.build_provider", lambda *args, **kwargs: InterruptedJudge()
    )
    task = asyncio.create_task(
        review_dataset(dataset_path=dataset, model_receipt=RECEIPT, batch_size=1)
    )
    try:
        await asyncio.wait_for(waiting.wait(), timeout=2.0)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    decisions = [
        json.loads(line) for line in (dataset.parent / "review-log.jsonl").read_text().splitlines()
    ]
    assert len(decisions) == 1
    decision = decisions[0]
    assert decision["id"] == "routine-review"
    assert (dataset / "_inbox/routine-second.yaml").exists()
    assert not (dataset / "routine-second.yaml").exists()
    assert "routine-review" in (dataset.parent / "REVIEW.md").read_text()
    if deterministic_rejection:
        assert decision["decision"] == "rejected"
        assert decision["stage"] == "deterministic-validation"
        assert not (dataset / "routine-review.yaml").exists()
    else:
        assert decision["decision"] == "accepted"
        approved = load_dataset(dataset).scenarios[0]
        assert decision["content_sha256"] == approved.reviewed_content_sha256
        receipt = json.loads(
            next(
                dataset.parent.glob("review-receipts/*/batch-001.cassette.receipt.json")
            ).read_text()
        )
        assert receipt["case_hashes"][approved.id] == approved.reviewed_content_sha256
        assert (
            receipt["verdicts"][approved.id]["content_sha256"] == approved.reviewed_content_sha256
        )
        assert (dataset.parent / decision["recording"]).exists()


async def test_completed_batches_are_logged_once_without_final_duplicates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, endpoint_checks: list[str]
) -> None:
    dataset = draft(tmp_path)
    second = yaml.safe_load((dataset / "_inbox/routine-review.yaml").read_text())
    second["id"] = "routine-second"
    (dataset / "_inbox/routine-second.yaml").write_text(yaml.safe_dump(second))
    monkeypatch.setattr("careflow_eval.review.build_provider", lambda *args, **kwargs: Judge())
    result = await review_dataset(dataset_path=dataset, model_receipt=RECEIPT, batch_size=1)
    assert result["accepted"] == 2
    raw_log = await asyncio.to_thread(Path(result["log_path"]).read_text)
    decisions = [json.loads(line) for line in raw_log.splitlines()]
    assert [entry["id"] for entry in decisions] == ["routine-review", "routine-second"]
    assert all(entry["decision"] == "accepted" for entry in decisions)
