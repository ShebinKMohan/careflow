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


def main() -> None:
    app()


__all__ = ["KTAS_CSV", "app", "main"]
