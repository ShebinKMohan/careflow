#!/usr/bin/env python3
"""Run an attributed local review/baseline inside scripts/with_llm.sh.

Redirect that wrapper's stderr to a fresh file and pass it as --launch-log. The log
attributes the pinned launch; it is not cryptographic weight or process attestation.
This helper never starts or stops a server. Dataset review remains automated, not clinical.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import re
import sys
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import yaml

from careflow_contracts.generated.extraction_output_schema import ExtractionOutput
from careflow_eval.reports import write_report
from careflow_eval.review import review_dataset, verify_model_endpoint
from careflow_eval.runner import run_suite
from careflow_intake.extraction.prompt import build_messages
from careflow_intake.wiring import providers_config
from careflow_providers.base import StructuredFailure, structured
from careflow_providers.config import LocalTaskConfig, ProvidersConfig
from careflow_providers.factory import build_provider

REPO = Path(__file__).resolve().parents[1]
WRAPPER = REPO / "scripts/with_llm.sh"


def pinned_model(wrapper: str, environment: Mapping[str, str]) -> tuple[str, str]:
    """Read the wrapper's simple defaults and apply exactly its model/revision overrides."""
    defaults = {}
    for name in ("default_model", "default_revision"):
        match = re.search(rf"^{name}=([^\s#]+)", wrapper, re.MULTILINE)
        if match is None:
            raise ValueError(f"Cannot establish {name} from scripts/with_llm.sh")
        defaults[name] = match[1]
    model = environment.get("CAREFLOW_LLM_MODEL") or defaults["default_model"]
    revision = environment.get("CAREFLOW_LLM_REVISION") or (
        defaults["default_revision"] if model == defaults["default_model"] else ""
    )
    if not re.fullmatch(r"[^/\s]+/[^/\s]+", model) or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError(
            "A Hugging Face model and explicit 40-character pinned revision are required"
        )
    return model, revision


def fresh_launch(log: str, *, model: str, revision: str, base_url: str) -> str:
    """Reject reuse/ended logs; the caller must pass this invocation's freshly redirected log."""
    endpoint = urlsplit(base_url)
    if (
        endpoint.scheme != "http"
        or endpoint.hostname != "127.0.0.1"
        or endpoint.port is None
        or endpoint.path != "/v1"
        or endpoint.username is not None
        or endpoint.query
        or endpoint.fragment
    ):
        raise ValueError("Use the local endpoint exported by scripts/with_llm.sh")
    lines = log.splitlines()
    starts = [line for line in lines if line.startswith("with_llm: starting mlx_lm.server")]
    expected = (
        f"with_llm: starting mlx_lm.server ({model}@{revision}) "
        f"on {base_url.removesuffix('/v1')}; log "
    )
    ended = ("reusing the server", "stopping mlx_lm.server", "exited early", "not ready after")
    ready = [line for line in lines if line.startswith("with_llm: server ready after ")]
    if (
        len(starts) != 1
        or not starts[0].startswith(expected)
        or len(ready) != 1
        or lines.index(ready[0]) < lines.index(starts[0])
        or any(marker in line for marker in ended for line in lines)
    ):
        raise ValueError(
            "A fresh, ready, pinned wrapper launch was not established by --launch-log"
        )
    return starts[0]


def observed_snapshot(body: Any, *, model: str, revision: str) -> tuple[str, list[str]]:
    """Select the exact advertised pinned path, never the default_model alias."""
    rows = body.get("data") if isinstance(body, dict) else None
    if not isinstance(rows, list) or not rows:
        raise ValueError("The model endpoint did not return a nonempty model list")
    ids: list[str] = []
    for row in rows:
        item = row.get("id") if isinstance(row, dict) else None
        if not isinstance(item, str) or not item.strip():
            raise ValueError("The model endpoint returned invalid model IDs")
        ids.append(item)
    if len(set(ids)) != len(ids):
        raise ValueError("The model endpoint returned duplicate model IDs")
    suffix = f"models--{model.replace('/', '--')}/snapshots/{revision}"
    selected = [item for item in ids if item == suffix or item.endswith("/" + suffix)]
    if len(selected) != 1:
        raise ValueError("Endpoint must advertise one unambiguous exact pinned snapshot")
    return selected[0], sorted(ids)


def positive_seconds(value: str) -> float:
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise argparse.ArgumentTypeError("timeout must be finite and positive")
    return result


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="mode", required=True)
    for mode in ("review", "baseline"):
        command = commands.add_parser(mode)
        command.add_argument("--launch-log", type=Path, required=True)
        command.add_argument("--config", type=Path, default=REPO / "config/providers.yaml")
        command.add_argument("--dataset", type=Path)
        command.add_argument("--output-root", type=Path, default=REPO / "eval/reports")
        command.add_argument(
            "--timeout", type=positive_seconds, default=300.0 if mode == "review" else 20.0
        )
        if mode == "review":
            command.add_argument("--batch-size", type=int, choices=range(1, 11), default=5)
        else:
            command.add_argument("--record-root", type=Path, default=REPO / "eval/replays")
            command.add_argument("--warmup-timeout", type=positive_seconds, default=120.0)
    return result


def write_json(path: Path, body: Any) -> None:
    with path.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(body, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


async def warmup(config: ProvidersConfig, timeout_seconds: float) -> dict[str, Any]:
    """Warm the real extraction prompt/schema with no transcript or expected case content."""
    provider = build_provider("extraction", config)
    started = time.perf_counter()
    response = await asyncio.wait_for(
        structured(provider, build_messages([], {}), ExtractionOutput, max_tokens=100),
        timeout=timeout_seconds,
    )
    if isinstance(response, StructuredFailure):
        raise ValueError("Production extraction schema warm-up failed validation")
    return {
        "duration_ms": (time.perf_counter() - started) * 1000,
        "output_sha256": hashlib.sha256(response.raw_text.encode()).hexdigest(),
        "raw_text": response.raw_text,
        "profile": "production extraction prompt and schema, empty transcript, no case input",
        "included_in_case_latencies": False,
    }


async def run(args: argparse.Namespace) -> int:
    base_url = os.environ.get("CAREFLOW_LOCAL_LLM_BASE_URL", "").rstrip("/")
    if not base_url:
        raise ValueError("Invoke this helper through scripts/with_llm.sh with a fresh --launch-log")
    model, revision = pinned_model(WRAPPER.read_text(encoding="utf-8"), os.environ)
    launch_text = args.launch_log.read_text(encoding="utf-8")
    launch = fresh_launch(launch_text, model=model, revision=revision, base_url=base_url)
    config = providers_config(args.config)
    local = config.tasks.get("extraction")
    if not isinstance(local, LocalTaskConfig):
        raise ValueError("Local evaluation requires the configured local extraction provider")
    async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
        response = await client.get(base_url + "/models")
        response.raise_for_status()
        selected, ids = observed_snapshot(response.json(), model=model, revision=revision)
    local = local.model_copy(update={"served_model": selected})
    config = config.model_copy(update={"tasks": {**config.tasks, "extraction": local}})
    receipt = {
        "model_id": model,
        "revision": revision,
        "source": "Fresh scripts/with_llm.sh launch from the pinned Hugging Face snapshot",
        "launch_evidence": launch,
        "launch_log_sha256": hashlib.sha256(launch_text.encode()).hexdigest(),
        "identity_limit": (
            "Launch-log attribution and endpoint observations, not weight attestation"
        ),
        "base_url": local.base_url,
        "served_model": selected,
        "server_model_ids": ids,
        "observed_at": datetime.now(UTC).isoformat(),
    }
    await verify_model_endpoint(local, receipt)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
    evidence = args.output_root / "local-runs" / f"{stamp}-{args.mode}"
    evidence.mkdir(parents=True, exist_ok=False)
    (evidence / "launch.log").write_text(launch_text, encoding="utf-8")
    config_path = evidence / "providers.yaml"
    config_path.write_text(yaml.safe_dump(config.model_dump(mode="json")), encoding="utf-8")
    write_json(evidence / "model-receipt.json", receipt)
    print(f"Observed pinned snapshot: {selected}\nRun evidence: {evidence}", flush=True)
    try:
        if args.mode == "review":
            result = await review_dataset(
                dataset_path=args.dataset,
                config_path=config_path,
                model_receipt=receipt,
                batch_size=args.batch_size,
                timeout_seconds=args.timeout,
            )
            write_json(evidence / "summary.json", result)
            print(json.dumps(result, indent=2), flush=True)
            return 0 if result.get("status") == "reviewed" and result.get("rejected") == 0 else 1
        warmed = await warmup(config, args.warmup_timeout)
        write_json(evidence / "warmup.json", warmed)
        print(f"Warm-up: {warmed['duration_ms']:.0f} ms; excluded from case latency", flush=True)

        def progress(case_id: str, done: int, total: int) -> None:
            print(datetime.now(UTC).isoformat(), f"{done}/{total}", case_id, flush=True)

        result = await run_suite(
            "extraction_en",
            dataset_path=args.dataset,
            provider_config=config,
            record_dir=args.record_root,
            model_receipt=receipt,
            timeout_seconds=args.timeout,
            progress=progress,
        )
        result["metadata"]["warmup"] = warmed
        result["metadata"]["local_run_evidence"] = str(evidence)
        paths = write_report(result, args.output_root / "extraction_en")
        write_json(
            evidence / "summary.json",
            {"status": result["status"], "report": str(paths.json_path)},
        )
        print(f"Reports: {paths.json_path}\n{paths.markdown_path}", flush=True)
        print("Status:", result["status"], "Metrics:", json.dumps(result["metrics"]), flush=True)
        print("Completion describes execution, not a quality or clinical pass.", flush=True)
        return 0 if result["status"] == "completed" else 1
    except Exception as exc:
        write_json(evidence / "failure.json", {"status": "failed", "error": str(exc)})
        raise


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return asyncio.run(run(args))
    except (OSError, ValueError, RuntimeError, LookupError, httpx.HTTPError) as exc:
        print(f"eval_local: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
