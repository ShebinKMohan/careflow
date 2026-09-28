"""Explicit live/record/replay modes and measured, attributable evaluation results."""

from __future__ import annotations

import json
import math
import shutil
import subprocess
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict
from pathlib import Path
from typing import Any

from careflow_contracts import Language
from careflow_eval.datasets import load_dataset
from careflow_eval.metrics import NORMALIZATION_VERSION
from careflow_eval.providers import (
    ReceiptError,
    canonical_hash,
    complete_endpoint_recording,
    file_hash,
    read_receipt,
)
from careflow_eval.suites import extraction_en, rules
from careflow_eval.suites.extraction_en import ARABIC_SCORING
from careflow_intake.extraction.extractor import DEFAULT_TIMEOUT_SECONDS
from careflow_intake.extraction.prompt import PROMPT_DIR, prompt_version
from careflow_intake.wiring import providers_config
from careflow_providers.adapters.local import LocalProvider
from careflow_providers.base import ChatProvider
from careflow_providers.config import LocalTaskConfig, ProvidersConfig
from careflow_providers.factory import build_provider
from careflow_rules import RULESET_VERSION

REPO = Path(__file__).resolve().parents[3]
DEFAULT_DATASET = REPO / "eval" / "datasets" / "intake_en"
DEFAULT_RULES = REPO / "eval" / "scenarios" / "rules"
# The extraction suites and their scenario language (M2-03 adds Arabic).
EXTRACTION_SUITES = {"extraction_en": Language.en, "extraction_ar": Language.ar}
DEFAULT_DATASETS = {
    "extraction_en": DEFAULT_DATASET,
    "extraction_ar": REPO / "eval" / "datasets" / "intake_ar",
}


def normalization_version(suite: str) -> str:
    """What the field scores compare: Arabic free text is scored by presence (see the suite)."""
    if suite == "extraction_ar":
        return f"{NORMALIZATION_VERSION}+ar-{ARABIC_SCORING}"
    return NORMALIZATION_VERSION


SOURCE_ROOTS = (
    "eval/src",
    "eval/schemas",
    "packages/contracts/schemas",
    "packages/contracts/src",
    "packages/providers/src",
    "services/intake/src",
    "services/rules/src",
)


def git_sha() -> str | None:
    executable = shutil.which("git")
    if executable is None:
        return None
    try:
        return subprocess.check_output(  # noqa: S603 — fixed read-only arguments, no shell
            [executable, "rev-parse", "HEAD"], cwd=REPO, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def source_provenance() -> dict[str, Any]:
    """Identify the checked-out source, including relevant uncommitted/untracked changes.

    This is provenance, not a replay restriction: future code is expected to run old cassettes.
    Gold labels have their separate dataset hash. Cache files and generated reports are excluded.
    """
    files = {
        path.relative_to(REPO).as_posix(): file_hash(path)
        for root in SOURCE_ROOTS
        for path in (REPO / root).rglob("*")
        if path.is_file() and path.suffix in {".py", ".md", ".yaml", ".yml", ".json"}
    }
    for name in (
        "uv.lock",
        "pyproject.toml",
        "eval/pyproject.toml",
        "config/providers.yaml",
        "scripts/eval_local.py",
        "scripts/with_llm.sh",
    ):
        path = REPO / name
        if path.is_file():
            files[name] = file_hash(path)
    executable = shutil.which("git")
    dirty = None
    if executable is not None:
        try:
            dirty = bool(
                subprocess.check_output(  # noqa: S603 — fixed arguments, no shell
                    [executable, "status", "--porcelain", "--untracked-files=normal"],
                    cwd=REPO,
                    stderr=subprocess.DEVNULL,
                )
            )
        except (OSError, subprocess.CalledProcessError):
            pass
    return {"git_sha": git_sha(), "worktree_dirty": dirty, "source_sha256": canonical_hash(files)}


def _rule_receipt(
    results: list[dict[str, Any]],
    bindings: dict[str, Any],
    *,
    record: Path | None,
    replay: Path | None,
    provenance: Mapping[str, Any],
) -> dict[str, Any] | None:
    outputs = []
    for result in results:
        copied = {key: value for key, value in result.items() if key != "latency_source"}
        copied["prediction"] = {
            key: value for key, value in result["prediction"].items() if key != "latency_ms"
        }
        outputs.append(copied)
    digest = canonical_hash(outputs)
    if record is not None:
        path = record / "rules" / "receipt.json"
        if path.exists():
            raise ReceiptError("rules: recording already exists; use a fresh recording directory")
        body = {
            "schema_version": 1,
            "bindings": bindings,
            "output_sha256": digest,
            "source_provenance": dict(provenance),
            "durations_ms": {row["id"]: row["prediction"]["latency_ms"] for row in results},
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n")
    if replay is not None:
        path = replay / "rules" / "receipt.json"
        if not path.exists():
            raise ReceiptError("rules: missing recording receipt")
        body = json.loads(path.read_text())
        if body.get("schema_version") != 1 or body.get("bindings") != bindings:
            raise ReceiptError("rules: recording bindings differ")
        if body.get("output_sha256") != digest:
            raise ReceiptError("rules: current output differs from recording")
        for row in results:
            duration = body["durations_ms"].get(row["id"])
            if isinstance(duration, bool) or not isinstance(duration, int | float):
                raise ReceiptError("rules: missing measured duration")
            if not math.isfinite(duration) or duration < 0:
                raise ReceiptError("rules: invalid measured duration")
            row["replay_wall_ms"] = row["prediction"]["latency_ms"]
            row["prediction"]["latency_ms"] = duration
            row["latency_source"] = "recorded_rules_execution"
        return body.get("source_provenance")
    return dict(provenance) if record is not None else None


async def run_suite(
    suite: str,
    *,
    dataset_path: Path | None = None,
    provider_config: ProvidersConfig | Path | None = None,
    provider: ChatProvider | None = None,
    record_dir: Path | None = None,
    replay_dir: Path | None = None,
    model_receipt: Mapping[str, Any] | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    include_unreviewed: bool = False,
    progress: Callable[[str, int, int], None] | None = None,
) -> dict[str, Any]:
    """Return a JSON-friendly report; the caller owns publishing JSON/Markdown and exit policy.

    Recorded replies and validated extractor-confirmed deadline events can be replayed. Other
    failures are explicitly not_run and the report is incomplete. Missing, changed, or mismatched
    successful recordings fail hard. Neither replay mode nor the pure rules suite builds a live
    provider. Latency values in replay are historical measurements, never replay wall time.
    """
    if suite not in {"rules", *EXTRACTION_SUITES}:
        raise ValueError(f"unknown evaluation suite: {suite}")
    if record_dir is not None and replay_dir is not None:
        raise ValueError("record and replay are mutually exclusive")
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive and finite")
    started = time.perf_counter()
    provenance = source_provenance()
    recording_provenance = dict(provenance) if record_dir is not None else None
    mode = "replay" if replay_dir is not None else "record" if record_dir is not None else "live"
    model_name, kind = None, None
    endpoint_checks: list[dict[str, Any]] = []
    local_settings = None
    version = prompt_version(EXTRACTION_SUITES[suite]) if suite in EXTRACTION_SUITES else None
    prompt_sha = file_hash(PROMPT_DIR / f"{version}.md") if version is not None else None
    if suite == "rules":
        results, dataset_sha = rules.run(Path(dataset_path or DEFAULT_RULES))
        recording_provenance = _rule_receipt(
            results,
            {"dataset_sha256": dataset_sha, "ruleset_version": RULESET_VERSION},
            record=record_dir,
            replay=replay_dir,
            provenance=provenance,
        )
    else:
        dataset = load_dataset(
            Path(dataset_path or DEFAULT_DATASETS[suite]), include_unreviewed=include_unreviewed
        )
        dataset_sha = dataset.sha256
        bindings = {
            "dataset_sha256": dataset_sha,
            "prompt_version": version,
            "prompt_sha256": prompt_sha,
            "ruleset_version": RULESET_VERSION,
            "normalization_version": normalization_version(suite),
            "timeout_seconds": timeout_seconds,
        }
        if replay_dir is not None:
            identities = []
            for scenario in dataset.scenarios:
                receipt = read_receipt(
                    replay_dir / suite, scenario.id, bindings, require_replayable=False
                )
                identity = {
                    key: receipt.get(key)
                    for key in (
                        "logical_model",
                        "provider_kind",
                        "model_receipt",
                        "source_provenance",
                    )
                }
                if identities and identity != identities[0]:
                    raise ReceiptError("replay contains different model identities")
                identities.append(identity)
            if identities:
                identity = identities[0]
                model_name, kind = identity["logical_model"], identity["provider_kind"]
                if model_receipt is not None and dict(model_receipt) != identity["model_receipt"]:
                    raise ReceiptError("replay model receipt differs from supplied model identity")
                model_receipt = identity["model_receipt"]
                recording_provenance = identity["source_provenance"]
        if replay_dir is None:
            if provider is None:
                config = (
                    provider_config
                    if isinstance(provider_config, ProvidersConfig)
                    else (
                        providers_config(provider_config)
                        if provider_config is not None
                        else providers_config()
                    )
                )
                provider = build_provider("extraction", config)
                kind = config.tasks["extraction"].provider
            else:
                kind = type(provider).__name__.removesuffix("Provider").lower()
            model_name = provider.model_name
            if isinstance(provider, LocalProvider) and model_receipt is not None:
                from careflow_eval.review import verify_model_endpoint

                local_settings = LocalTaskConfig(
                    provider="local",
                    model=provider.model_name,
                    base_url=provider.base_url,
                    served_model=provider.served_model,
                )
                endpoint_checks.append(
                    await verify_model_endpoint(local_settings, dict(model_receipt))
                )
        results = []
        for scenario in dataset.scenarios:
            result = await extraction_en.run_case(
                scenario,
                bindings=bindings,
                provider=provider,
                provider_kind=kind or "replay",
                record_dir=record_dir / suite if record_dir is not None else None,
                replay_dir=replay_dir / suite if replay_dir is not None else None,
                timeout_seconds=timeout_seconds,
                model_receipt=model_receipt,
                source_provenance=provenance,
                model_endpoint_verification_required=local_settings is not None,
                language=EXTRACTION_SUITES[suite],
            )
            results.append(result)
            if progress is not None:
                progress(scenario.id, len(results), len(dataset.scenarios))
        if local_settings is not None and model_receipt is not None:
            from careflow_eval.review import verify_model_endpoint

            endpoint_checks.append(await verify_model_endpoint(local_settings, dict(model_receipt)))
            if record_dir is not None:
                complete_endpoint_recording(
                    record_dir / suite,
                    [case.id for case in dataset.scenarios],
                    model_receipt=model_receipt,
                    observations=endpoint_checks,
                )
    completed = [row for row in results if row["status"] == "completed"]
    # Imported here so registry plugins are loaded only when a suite actually runs.
    from careflow_eval.metrics import get_metric, metric_names

    predictions, expected = (
        [row["prediction"] for row in completed],
        [row["expected"] for row in completed],
    )
    names = (
        metric_names()
        if suite in EXTRACTION_SUITES
        else ("alert_recall", "false_alert_rate", "latency_p50_ms", "latency_p95_ms")
    )
    measured = {name: get_metric(name)(predictions, expected) for name in names}
    metrics = {name: asdict(value) for name, value in measured.items()}
    for row in completed:
        if suite in EXTRACTION_SUITES:
            field_score = get_metric("field_f1")([row["prediction"]], [row["expected"]])
            row["field_f1"] = field_score.value
            row["quality_passed"] = (
                field_score.details["fp"] == 0
                and field_score.details["fn"] == 0
                and row["rules_validation"]
                and not row["prediction"]["extraction_incomplete"]
            )
        else:
            row["quality_passed"] = row["rules_validation"]
    return {
        "schema_version": 1,
        "suite": suite,
        "status": "completed" if len(completed) == len(results) else "incomplete",
        "metadata": {
            **provenance,
            "recording_provenance": recording_provenance,
            "dataset_sha256": dataset_sha,
            "mode": mode,
            "provider": kind,
            "logical_model": model_name,
            "model_receipt": dict(model_receipt) if model_receipt is not None else None,
            "model_endpoint_checks": endpoint_checks,
            "prompt_version": version,
            "prompt_sha256": prompt_sha,
            "ruleset_version": RULESET_VERSION,
            "normalization_version": normalization_version(suite),
            "language": EXTRACTION_SUITES[suite].value if suite in EXTRACTION_SUITES else None,
            "timeout_seconds": timeout_seconds if suite in EXTRACTION_SUITES else None,
            "case_count": len(results),
            "completed_count": len(completed),
            "missing_ids": [row["id"] for row in results if row["status"] != "completed"],
            "metrics_comparable": len(completed) == len(results),
            "latency_source": sorted({row["latency_source"] for row in completed}),
            "replay_exact": len(completed) == len(results) if replay_dir is not None else None,
            "failure_event_replay_count": sum(
                bool(row.get("failure_event_replayed")) for row in completed
            ),
            "replay_exact_scope": "outputs_quality_metrics_and_recorded_latency"
            if replay_dir is not None
            else None,
            "wall_ms": (time.perf_counter() - started) * 1000,
            "rules_validation": all(row.get("rules_validation", False) for row in results),
            "include_unreviewed": include_unreviewed,
            "synthetic_only": True,
        },
        "results": results,
        "metrics": metrics,
        "failed_ids": [row["id"] for row in results if not row.get("quality_passed", False)],
    }


__all__ = [
    "DEFAULT_DATASET",
    "DEFAULT_DATASETS",
    "DEFAULT_RULES",
    "EXTRACTION_SUITES",
    "normalization_version",
    "run_suite",
]
