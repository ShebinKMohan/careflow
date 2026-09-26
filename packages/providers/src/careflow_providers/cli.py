"""`careflow` command line: `careflow providers show` and `careflow providers bench`."""

from __future__ import annotations

import asyncio
import datetime as dt
import statistics
import time
from pathlib import Path
from typing import Annotated

import typer

from careflow_providers.config import LIVE_TASKS, CliTaskConfig, describe, load_config
from careflow_providers.errors import ProviderUnavailable
from careflow_providers.factory import build_provider

REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_CONFIG = REPO_ROOT / "config" / "providers.yaml"
BENCH_PROMPT = (
    "A synthetic patient at a triage kiosk says: 'I have crushing chest pain since 20 minutes, "
    "it goes to my left arm and I am sweating.' Summarise the key facts in two short sentences."
)

app = typer.Typer(help="CareFlow developer tools. Research prototype; synthetic data only.")
providers_app = typer.Typer(help="Model providers per AI task (config/providers.yaml).")
app.add_typer(providers_app, name="providers")

ConfigOption = Annotated[Path, typer.Option("--config", help="providers.yaml to read")]


def provider_rows(config_path: Path) -> list[tuple[str, str, str, str]]:
    """task | provider | model/vendor/tool | live path allowed."""
    config = load_config(config_path)
    rows: list[tuple[str, str, str, str]] = []
    for task, task_config in config.tasks.items():
        live = "no (offline only)" if isinstance(task_config, CliTaskConfig) else "yes"
        if task in LIVE_TASKS and live != "yes":
            live = "FORBIDDEN"
        rows.append((task, task_config.provider, describe(task_config), live))
    return rows


@providers_app.command("show")
def show(config: ConfigOption = DEFAULT_CONFIG) -> None:
    """Print which provider serves each task."""
    rows = [
        ("task", "provider", "model / vendor / tool", "live path allowed"),
        *provider_rows(config),
    ]
    widths = [max(len(row[i]) for row in rows) for i in range(4)]
    for index, row in enumerate(rows):
        typer.echo("  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True)))
        if index == 0:
            typer.echo("  ".join("-" * width for width in widths))


async def _bench(task: str, n: int, config_path: Path) -> dict[str, float | str]:
    provider = build_provider(task, load_config(config_path))
    ttfts: list[float] = []
    rates: list[float] = []
    for _ in range(n):
        start = time.perf_counter()
        first: float | None = None
        text = ""
        async for delta in provider.stream([{"role": "user", "content": BENCH_PROMPT}]):
            if first is None:
                first = time.perf_counter() - start
            text += delta
        total = time.perf_counter() - start
        if first is None:
            raise RuntimeError("the provider streamed nothing")
        ttfts.append(first)
        tokens = max(len(text) / 4, 1.0)  # approximate: ~4 characters per token
        rates.append(tokens / max(total - first, 1e-6))
    return {
        "model": provider.model_name,
        "ttft_p50_s": statistics.median(ttfts),
        "ttft_max_s": max(ttfts),
        "tokens_per_s_p50": statistics.median(rates),
    }


@providers_app.command("bench")
def bench(
    task: Annotated[str, typer.Option(help="task to bench")] = "extraction",
    n: Annotated[int, typer.Option(help="number of runs")] = 5,
    config: ConfigOption = DEFAULT_CONFIG,
    report_dir: Annotated[Path, typer.Option(help="where to write the report")] = REPO_ROOT
    / "eval"
    / "reports",
    label: Annotated[str, typer.Option(help="the model the server actually runs")] = "",
) -> None:
    """Measure time-to-first-token and tokens per second; append to the dated report."""
    try:
        result = asyncio.run(_bench(task, n, config))
    except ProviderUnavailable as exc:
        typer.echo(f"not_run: {exc}", err=True)
        raise typer.Exit(1) from exc
    report_dir.mkdir(parents=True, exist_ok=True)
    report = report_dir / f"provider-bench-{dt.date.today().isoformat()}.md"
    if not report.exists():
        report.write_text(
            "# Provider bench\n\nTime to first token (TTFT) and decode speed per model, measured "
            "with `careflow providers bench` on the owner's MacBook Pro M3 Pro (18 GB). Tokens "
            "are approximated as characters / 4. Synthetic prompt only.\n\n"
            "| Served model | Config model | Task | Runs | TTFT p50 (s) | TTFT max (s) | "
            "Tokens/s p50 |\n|---|---|---|---|---|---|---|\n",
            encoding="utf-8",
        )
    with report.open("a", encoding="utf-8") as handle:
        handle.write(
            f"| {label or result['model']} | {result['model']} | {task} | {n} | "
            f"{result['ttft_p50_s']:.2f} | {result['ttft_max_s']:.2f} | "
            f"{result['tokens_per_s_p50']:.1f} |\n"
        )
    typer.echo(f"bench: {result} -> {report}")


def main() -> None:
    app()
