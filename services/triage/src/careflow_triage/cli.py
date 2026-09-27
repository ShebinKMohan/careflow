"""`careflow-triage`: KTAS data, training and reports (M1-04)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from careflow_triage.ktas.download import download
from careflow_triage.ktas.load import file_sha256, load_ktas, profile_markdown
from careflow_triage.paths import KTAS_CSV, REPORTS_DIR
from careflow_triage.report import write_model_card, write_reports
from careflow_triage.splits import SEED

app = typer.Typer(add_completion=False, help="CareFlow urgency model (research prototype).")
ktas = typer.Typer(add_completion=False, help="The KTAS dataset.")
app.add_typer(ktas, name="ktas")
laya_app = typer.Typer(
    add_completion=False,
    help="Laya fine-tuned on KTAS (M3-01). Needs the `laya` extra: uv sync --extra laya.",
)
app.add_typer(laya_app, name="laya")
LAYA_CONFIG = Path(__file__).resolve().parents[2] / "config" / "laya_ktas_v1.yaml"


def _data(csv: Path | None) -> tuple[Path, str]:
    if csv is not None:
        return csv, "given"
    got = download()
    return got.path, got.source


@ktas.command("download")
def ktas_download() -> None:
    """Get the KTAS CSV into services/triage/data/raw/.

    Sources, in order: existing, PLOS ONE, Kaggle, else the fixture.
    """
    got = download()
    typer.echo(f"ktas: {got.source} -> {got.path}")
    if got.source == "fixture":
        typer.echo("ktas: no KTAS data available; the synthetic fixture is NOT A RESULT", err=True)


@ktas.command("profile")
def ktas_profile(csv: Annotated[Path | None, typer.Option(help="CSV path.")] = None) -> None:
    """Write the aggregate profile to eval/reports/triage/ktas_profile.md."""
    path, _ = _data(csv)
    frame = load_ktas(path)
    source_text = (
        "the synthetic fixture (NOT A RESULT)"
        if path.name == "ktas_fixture.csv"
        else "KTAS, PLOS ONE 2019 S1 Appendix (doi:10.1371/journal.pone.0216972, CC BY 4.0)"
    )
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORTS_DIR / (
        "fixture_ktas_profile.md" if "fixture" in source_text else "ktas_profile.md"
    )
    out.write_text(
        profile_markdown(frame, source=source_text, sha256=file_sha256(path)), encoding="utf-8"
    )
    typer.echo(f"ktas: {len(frame)} rows profiled -> {out}")


@app.command("train")
def train_command(
    model: Annotated[str, typer.Option("--model", help="lr or hgb.")],
    csv: Annotated[
        Path | None, typer.Option(help="CSV path (default: the downloaded KTAS).")
    ] = None,
    seed: Annotated[int, typer.Option(help="Split and model seed.")] = SEED,
) -> None:
    """Train, calibrate and evaluate one baseline.

    Writes the artifact, the report and the model card.
    """
    if model not in ("lr", "hgb"):
        raise typer.BadParameter("--model must be lr or hgb")
    from careflow_triage.train import train

    path, _ = _data(csv)
    result = train(model, path, seed=seed)  # type: ignore[arg-type]
    json_path, md_path = write_reports(csv_sha256=result.meta["csv_sha256"], seed=seed)
    card = write_model_card(
        csv_sha256=result.meta["csv_sha256"], seed=seed, report_name=md_path.name
    )
    test = result.meta["metrics"]["test"]
    under = test["under_triage_l1_2"]
    typer.echo(
        f"{result.model_version}: macro-F1 {test['macro_f1']:.3f}, under-triage L1-2 "
        f"{under['count']}/{under['of']}, ECE {test['ece_before']:.3f} -> {test['ece_after']:.3f} "
        f"(T={result.meta['temperature']:.2f}) in {result.meta['train_seconds']} s"
    )
    typer.echo(f"artifact {result.artifact_dir}\nreport {md_path} ({json_path.name}); {card.name}")


def _laya_run(run_dir: Path | None) -> Path:
    """The given run, else the newest one under artifacts/laya/."""
    if run_dir is not None:
        return run_dir
    from careflow_triage.laya_ktas.train import LAYA_DIR

    runs = sorted(
        (p for p in LAYA_DIR.glob("*laya-ktas-*") if (p / "model.safetensors").exists()),
        key=lambda p: p.stat().st_mtime,
    )
    if not runs:
        raise typer.BadParameter("no Laya run under artifacts/laya/: run `laya train` first")
    return runs[-1]


@laya_app.command("train")
def laya_train(
    config: Annotated[Path, typer.Option(help="Training config.")] = LAYA_CONFIG,
    csv: Annotated[Path | None, typer.Option(help="CSV path (default: the KTAS data).")] = None,
    max_epochs: Annotated[int | None, typer.Option(help="Override max_epochs.")] = None,
) -> None:
    """Fine-tune Laya on the KTAS train split (MPS); the best epoch by val macro-F1 is kept."""
    from careflow_triage.laya_ktas.train import train

    path, _ = _data(csv)
    run_dir = train(config, path, max_epochs=max_epochs, log=typer.echo)
    typer.echo(f"laya: run {run_dir}")


@laya_app.command("calibrate")
def laya_calibrate(
    run_dir: Annotated[Path | None, typer.Option(help="Run directory.")] = None,
    config: Annotated[Path, typer.Option(help="Training config.")] = LAYA_CONFIG,
    csv: Annotated[Path | None, typer.Option(help="CSV path.")] = None,
) -> None:
    """Fit the choice temperature on validation and write it into the run's agent config."""
    from careflow_triage.laya_ktas.calibrate import calibrate

    path, _ = _data(csv)
    result = calibrate(_laya_run(run_dir), path, config)
    fitted, used = result["temperature_fitted"], result["temperature_used"]
    typer.echo(
        f"laya: T fitted {fitted:.3f}, used {used:.3f}; "
        f"val NLL {result['val_nll_before']:.3f} -> {result['val_nll_after']:.3f}"
    )


@laya_app.command("evaluate")
def laya_evaluate(
    run_dir: Annotated[Path | None, typer.Option(help="Run directory.")] = None,
    config: Annotated[Path, typer.Option(help="Training config.")] = LAYA_CONFIG,
    csv: Annotated[Path | None, typer.Option(help="CSV path.")] = None,
    baseline: Annotated[Path | None, typer.Option(help="M1-04 artifact directory.")] = None,
) -> None:
    """Score Laya and the M1-04 baseline on test (full and kiosk-partial) and write the report."""
    from careflow_triage.laya_ktas.evaluate import write_report

    path, _ = _data(csv)
    json_path, md_path = write_report(_laya_run(run_dir), path, config, baseline_dir=baseline)
    typer.echo(f"laya: report {md_path} ({json_path.name})")


@laya_app.command("fixtures")
def laya_fixtures(
    run_dir: Annotated[Path | None, typer.Option(help="Run directory.")] = None,
) -> None:
    """Write laya_ktas/fixtures/golden.json: 50 synthetic states and their probabilities."""
    from careflow_triage.laya_ktas.golden import write_golden

    typer.echo(f"laya: golden {write_golden(_laya_run(run_dir))}")


def main() -> None:
    app()


__all__ = ["KTAS_CSV", "app", "main"]
