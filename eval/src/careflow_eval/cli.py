"""Command-line evaluation, validation and offline author/reviewer workflows."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from careflow_eval.metrics import metric_names
from careflow_eval.reports import failing_scenario_ids, write_report

REPO = Path(__file__).resolve().parents[3]
SUITES = ("rules", "extraction_en", "extraction_ar")
DEFAULT_REPORTS = REPO / "eval/reports"
DEFAULT_DATASET = REPO / "eval/datasets/intake_en"
DATASETS = {"intake_en": DEFAULT_DATASET, "intake_ar": REPO / "eval/datasets/intake_ar"}


def _positive_seconds(value: str) -> float:
    seconds = float(value)
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError("timeout must be a finite positive number")
    return seconds


def _positive_count(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("count must be greater than zero")
    return number


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="careflow-eval",
        description="CareFlow synthetic evaluation. Research prototype; not for clinical use.",
    )
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="List implemented suites and registered metrics")

    run = commands.add_parser(
        "run", help="Run rules or extraction (English, Arabic) and write attributable reports"
    )
    run.add_argument(
        "suite",
        choices=(*SUITES, "extraction"),
        help="`extraction` runs the --lang suites and writes <output>/extraction.json and .md",
    )
    run.add_argument(
        "--lang",
        choices=("en", "ar", "all"),
        default="all",
        help="With `extraction`: the languages to run (default all)",
    )
    run.add_argument("--dataset", type=Path)
    run.add_argument("--config", type=Path, help="Provider configuration YAML")
    mode = run.add_mutually_exclusive_group()
    mode.add_argument(
        "--record", type=Path, metavar="DIR", help="Record raw model responses under DIR/<suite>"
    )
    mode.add_argument(
        "--replay",
        nargs="?",
        const="__default__",
        metavar="DIR",
        help="Replay raw responses from DIR/<suite>; default eval/replays/<suite>",
    )
    run.add_argument("--model-receipt", type=Path, metavar="JSONPATH")
    run.add_argument("--timeout", type=_positive_seconds, default=20.0)
    run.add_argument(
        "--include-unreviewed",
        action="store_true",
        help="Explicitly evaluate drafts; report their review boundary",
    )
    run.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_REPORTS,
        metavar="DIR",
        help="Report root; each suite gets its own timestamped files and LATEST.md",
    )

    validate = commands.add_parser(
        "validate", help="Validate synthetic intake scenarios and their review gate"
    )
    validate.add_argument("suite", choices=tuple(DATASETS))
    validate.add_argument("--dataset", type=Path)
    validate.add_argument("--include-unreviewed", action="store_true")

    generate = commands.add_parser(
        "generate", help="Generate offline draft scenarios; does not approve review"
    )
    generate.add_argument("--suite", choices=tuple(DATASETS), default="intake_en")
    generate.add_argument("--n", type=_positive_count, default=50)
    generate.add_argument("--dataset", type=Path)
    generate.add_argument("--config", type=Path)
    generate.add_argument(
        "--writer-model",
        help="Explicit model passed to the configured offline CLI; for intake_ar, the model "
        "that wrote --answers",
    )
    generate.add_argument(
        "--dialect", choices=("gulf", "levantine"), help="intake_ar: the answers' dialect"
    )
    generate.add_argument(
        "--answers",
        type=Path,
        help="intake_ar: the writer's answers, {scenario id: [{text, gloss_en}]} in plan order",
    )
    generate.add_argument(
        "--seed", type=int, default=7, help="intake_ar: the gold sampler's seed (default 7)"
    )

    review = commands.add_parser(
        "review", help="Review drafts with a distinct local or offline CLI model, not a clinician"
    )
    review.add_argument("--dataset", type=Path)
    review.add_argument("--config", type=Path)
    review.add_argument("--judge", choices=("local", "cli"), default="local")
    review.add_argument(
        "--reviewer-model", help="Required explicit model identity for the offline CLI reviewer"
    )
    review.add_argument(
        "--model-receipt",
        type=Path,
        metavar="JSONPATH",
        help="Required for local review; CLI review derives identity from its actual response",
    )
    review.add_argument("--batch-size", type=_positive_count, default=5)
    review.add_argument("--timeout", type=_positive_seconds, default=120.0)
    return result


def _receipt(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("model receipt must be a JSON object identifying the actual model")
    return value


def _progress(case_id: str, completed: int, total: int) -> None:
    now = datetime.now(UTC).strftime("%H:%M:%S UTC")
    print(f"[{now}] {completed}/{total} {case_id}", file=sys.stderr, flush=True)


async def _run_suite(suite: str, **kwargs: Any) -> dict[str, Any]:
    from careflow_eval.runner import run_suite

    return await run_suite(suite, **kwargs)


async def _generate(**kwargs: Any) -> dict[str, Any]:
    from careflow_eval.generate import generate_dataset

    return await generate_dataset(**kwargs)


def _generate_arabic(args: argparse.Namespace) -> dict[str, Any]:
    from careflow_eval.generate import write_arabic_drafts

    if args.dialect is None or args.answers is None or not (args.writer_model or "").strip():
        raise ValueError("intake_ar needs --dialect, --answers and --writer-model")
    result = write_arabic_drafts(
        dialect=args.dialect,
        answers_path=args.answers,
        writer_model=args.writer_model,
        seed=args.seed,
        dataset_path=args.dataset,
    )
    return {
        **result,
        "status": "generated" if not result["failures"] else "failed",
        "validation_failures": result["failures"],
        "review_required": True,
    }


def _report_path(path: Path) -> str:
    """A path for a committed report: relative to the repository, never a local absolute path."""
    try:
        return path.resolve().relative_to(REPO).as_posix()
    except ValueError:
        return os.path.basename(path)


def _run_extraction(args: argparse.Namespace, replay: Path | None) -> int:
    """Run the --lang extraction suites, write each suite's report (under
    `<output>/replay-verification/` when replaying), then `<output>/extraction.json` and `.md`."""
    from careflow_eval import extraction_report

    languages = ("en", "ar") if args.lang == "all" else (args.lang,)
    if args.dataset is not None and len(languages) > 1:
        raise ValueError("--dataset needs a single --lang")
    reports: dict[str, dict[str, Any]] = {}
    sources: dict[str, str] = {}
    for language in languages:
        suite = extraction_report.LANGUAGE_SUITES[language]
        report = asyncio.run(
            _run_suite(
                suite,
                dataset_path=args.dataset,
                provider_config=args.config,
                record_dir=args.record,
                replay_dir=replay,
                model_receipt=_receipt(args.model_receipt),
                timeout_seconds=args.timeout,
                include_unreviewed=args.include_unreviewed,
                progress=_progress,
            )
        )
        # A replay verifies a recording; it never replaces a live baseline's LATEST.md.
        root = args.output / "replay-verification" if replay is not None else args.output
        files = write_report(report, root / suite)
        reports[language] = report
        sources[language] = _report_path(files.json_path)
        print(f"{suite}: {report.get('status')} (execution status); JSON: {files.json_path}")
    combined = extraction_report.build(
        reports,
        agent_review=extraction_report.load_agent_review() if "ar" in reports else None,
        sources=sources,
    )
    json_path, markdown_path = extraction_report.write(combined, args.output)
    print(f"Missed expected alerts: {len(combined['missed_alerts'])}")
    for row in combined["missed_alerts"]:
        print(f"  {row['id']} ({row['language']}): {', '.join(row['missed'])}")
    print(f"JSON: {json_path}\nReport: {markdown_path}")
    return 0 if all(report.get("status") == "completed" for report in reports.values()) else 1


async def _review(**kwargs: Any) -> dict[str, Any]:
    from careflow_eval.review import review_dataset

    return await review_dataset(**kwargs)


def _validate(dataset: Path, include_unreviewed: bool) -> dict[str, Any]:
    from careflow_eval.datasets import load_dataset
    from careflow_eval.validation import validate_scenario

    loaded = load_dataset(dataset, include_unreviewed=include_unreviewed)
    errors: dict[str, Any] = {}
    for scenario in loaded.scenarios:
        issues = validate_scenario(scenario)
        if issues:
            errors[scenario.id] = issues
    return {
        "status": "failed" if errors else "completed",
        "count": len(loaded.scenarios),
        "dataset_sha256": loaded.sha256,
        "validation_failures": errors,
    }


def _print_workflow(result: dict[str, Any]) -> int:
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False))
    if result.get("validation_failures") or result.get("rejected"):
        return 1
    return 0 if result.get("status") in {"completed", "generated", "reviewed", "validated"} else 1


def main(argv: Sequence[str] | None = None) -> int:
    command_parser = parser()
    args = command_parser.parse_args(argv)
    if args.command == "review":
        if args.judge == "local" and args.model_receipt is None:
            command_parser.error("local review requires --model-receipt")
        if args.judge == "local" and args.reviewer_model is not None:
            command_parser.error("--reviewer-model applies only to --judge cli")
        if args.judge == "cli" and not (args.reviewer_model or "").strip():
            command_parser.error("CLI review requires an explicit --reviewer-model")
        if args.judge == "cli" and args.model_receipt is not None:
            command_parser.error("CLI review records its actual identity; omit --model-receipt")
    try:
        if args.command == "list":
            print("Implemented suites:")
            for suite in SUITES:
                print(f"  {suite}")
            print("Registered metrics:")
            for metric in metric_names():
                print(f"  {metric}")
            print("Synthetic scenarios; automated review, not clinicians or native speakers.")
            return 0
        if args.command == "run":
            replay = None
            if args.replay is not None:
                replay = (
                    REPO / "eval/replays" if args.replay == "__default__" else Path(args.replay)
                )
            if args.suite == "extraction":
                return _run_extraction(args, replay)
            report = asyncio.run(
                _run_suite(
                    args.suite,
                    dataset_path=args.dataset,
                    provider_config=args.config,
                    record_dir=args.record,
                    replay_dir=replay,
                    model_receipt=_receipt(args.model_receipt),
                    timeout_seconds=args.timeout,
                    include_unreviewed=args.include_unreviewed,
                    progress=_progress,
                )
            )
            files = write_report(report, args.output / args.suite)
            print(
                f"Run status: {report.get('status', 'unknown')} "
                "(execution status, not a clinical pass)"
            )
            print(f"Scenario issues reported: {len(failing_scenario_ids(report))}")
            for name, metric in sorted(report.get("metrics", {}).items()):
                value = metric.get("value") if isinstance(metric, dict) else metric
                print(f"{name}: {'n/a' if value is None else value}")
            print(f"JSON: {files.json_path}")
            print(f"Report: {files.markdown_path}")
            print(f"Latest: {files.latest_path}")
            return 0 if report.get("status") == "completed" else 1
        if args.command == "validate":
            return _print_workflow(
                _validate(args.dataset or DATASETS[args.suite], args.include_unreviewed)
            )
        if args.command == "generate":
            if args.suite == "intake_ar":
                return _print_workflow(_generate_arabic(args))
            return _print_workflow(
                asyncio.run(
                    _generate(
                        suite=args.suite,
                        n=args.n,
                        dataset_path=args.dataset,
                        config_path=args.config,
                        writer_model=args.writer_model,
                    )
                )
            )
        if args.command == "review":
            return _print_workflow(
                asyncio.run(
                    _review(
                        dataset_path=args.dataset,
                        config_path=args.config,
                        model_receipt=_receipt(args.model_receipt),
                        judge=args.judge,
                        reviewer_model=args.reviewer_model,
                        batch_size=args.batch_size,
                        timeout_seconds=args.timeout,
                    )
                )
            )
        raise ValueError(f"Unsupported command: {args.command}")
    except (OSError, ValueError, RuntimeError, LookupError) as exc:
        print(f"careflow-eval: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
