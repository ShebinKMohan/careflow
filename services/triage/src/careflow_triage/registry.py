"""The versioned model registry (M3-02): `services/triage/registry/registry.json`.

One entry per released model: `{version, family, artifact, sha256, files[{path, sha256, bytes}],
datasets, metrics, gate_report, status, created_at}`, plus `current: {family: version}`, the shape
M5-02 extends with its gated `register` and `promote`. `artifact` is the git-ignored directory the
files are in; `sha256` digests the sorted `path sha256` lines, so a changed, missing or extra file
fails `verify`. Each entry has a card in `registry/cards/<family>-<version>.md`. `add` is the
bootstrap until M5-02.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, Literal

from careflow_triage.paths import REPO_ROOT, REPORTS_DIR, SERVICE_DIR

REGISTRY_DIR: Final = SERVICE_DIR / "registry"
REGISTRY: Final = REGISTRY_DIR / "registry.json"
CARDS_DIR: Final = REGISTRY_DIR / "cards"
SCHEMA: Final = "careflow-model-registry/v1"
type Status = Literal["released", "candidate", "retired"]


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def manifest(artifact: Path) -> list[dict[str, Any]]:
    """Every file under the artifact, sorted by path, with its sha256 and size."""
    files = sorted(p for p in artifact.rglob("*") if p.is_file() and p.name != ".DS_Store")
    return [
        {
            "path": p.relative_to(artifact).as_posix(),
            "sha256": file_sha256(p),
            "bytes": p.stat().st_size,
        }
        for p in files
    ]


def manifest_sha256(files: list[dict[str, Any]]) -> str:
    lines = "".join(f"{f['path']} {f['sha256']}\n" for f in sorted(files, key=lambda f: f["path"]))
    return hashlib.sha256(lines.encode()).hexdigest()


def load() -> dict[str, Any]:
    if not REGISTRY.exists():
        return {"schema": SCHEMA, "entries": [], "current": {}}
    body: dict[str, Any] = json.loads(REGISTRY.read_text(encoding="utf-8"))
    return body


def save(body: dict[str, Any]) -> None:
    REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
    body["entries"].sort(key=lambda e: (e["family"], e["version"]))
    REGISTRY.write_text(json.dumps(body, indent=1) + "\n", encoding="utf-8")


def entry(body: dict[str, Any], family: str, version: str) -> dict[str, Any] | None:
    return next(
        (e for e in body["entries"] if e["family"] == family and e["version"] == version), None
    )


def current(family: str) -> dict[str, Any] | None:
    body = load()
    version = body["current"].get(family)
    return entry(body, family, version) if version else None


def _relative(path: Path) -> str:
    return path.resolve().relative_to(REPO_ROOT).as_posix()


def _newest(pattern: str) -> Path | None:
    found = sorted(REPORTS_DIR.glob(pattern), reverse=True)
    return found[0] if found else None


def family_details(family: str, artifact: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """(datasets, metrics) for a family, from its artifact's meta and its newest report."""
    if family == "hgb":
        meta = json.loads((artifact / "meta.json").read_text(encoding="utf-8"))
        test = meta["metrics"]["test"]
        datasets = {
            "ktas": {
                "csv_sha256": meta["csv_sha256"],
                "split": meta["split_file"],
                "split_counts": meta["split_counts"],
            }
        }
        metrics = {
            "source": f"{meta['model_version']} meta.json (M1-04)",
            "test_full": _summary(test),
        }
        return datasets, metrics
    if family == "laya":
        run = artifact.parent
        meta = json.loads((run / "meta.json").read_text(encoding="utf-8"))
        datasets = {
            "ktas": {
                "csv_sha256": meta["csv_sha256"],
                "split": meta["split_file"],
                "split_counts": meta["split_counts"],
            }
        }
        metrics: dict[str, Any] = {"run": meta["run_id"], "bundle": artifact.name}
        report = _newest("laya_ktas_*.json")
        if report is not None:
            body = json.loads(report.read_text(encoding="utf-8"))
            laya = body["laya"]
            metrics["source"] = f"{report.name} (the fp32 run, M3-01)"
            metrics["test_full"] = _summary(laya["test"]["full"])
            metrics["test_kiosk_partial"] = _summary(laya["test"]["kiosk_partial"])
            metrics["temperature"] = laya["temperature"]
        return datasets, metrics
    raise ValueError(f"unknown model family {family!r}")


def _summary(test: dict[str, Any]) -> dict[str, Any]:
    under = test["under_triage_l1_2"]
    return {
        "n": test["n"],
        "under_triage_l1_2": {"count": under["count"], "of": under["of"], "ci95": under["ci95"]},
        "macro_f1": test["macro_f1"],
        "ece_after": test.get("ece_after"),
    }


def add(
    family: str,
    version: str,
    artifact: Path,
    *,
    status: Status = "released",
    make_current: bool = True,
    notes: str = "",
) -> dict[str, Any]:
    if not artifact.is_dir():
        raise FileNotFoundError(f"no artifact directory {artifact}")
    body = load()
    if entry(body, family, version) is not None:
        raise ValueError(f"{family} {version} is already registered")
    files = manifest(artifact)
    datasets, metrics = family_details(family, artifact)
    record = {
        "version": version,
        "family": family,
        "artifact": _relative(artifact),
        "sha256": manifest_sha256(files),
        "files": files,
        "datasets": datasets,
        "metrics": metrics,
        "gate_report": None,
        "status": status,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    body["entries"].append(record)
    if make_current and status == "released":
        body["current"][family] = version
    save(body)
    write_card(record, notes=notes)
    return record


def verify(family: str | None = None) -> list[str]:
    """Problems found (empty: every checked entry's files match), for the current entries."""
    body = load()
    problems: list[str] = []
    families = [family] if family else sorted(body["current"])
    for name in families:
        record = entry(body, name, body["current"].get(name, ""))
        if record is None:
            problems.append(f"{name}: no current entry")
            continue
        artifact = REPO_ROOT / record["artifact"]
        if not artifact.is_dir():
            problems.append(f"{name} {record['version']}: artifact {record['artifact']} is missing")
            continue
        files = manifest(artifact)
        if manifest_sha256(files) != record["sha256"]:
            expected = {f["path"]: f["sha256"] for f in record["files"]}
            found = {f["path"]: f["sha256"] for f in files}
            for path in sorted(set(expected) | set(found)):
                if expected.get(path) != found.get(path):
                    problems.append(f"{name} {record['version']}: {path} differs")
    return problems


def write_card(record: dict[str, Any], *, notes: str = "") -> Path:
    CARDS_DIR.mkdir(parents=True, exist_ok=True)
    path = CARDS_DIR / f"{record['family']}-{record['version']}.md"
    metrics = record["metrics"]
    lines = [
        f"# {record['family']} {record['version']}",
        "",
        f"Status: {record['status']}. Registered {record['created_at']}. Research prototype; "
        "not for clinical use. The model only suggests a level: the rules run first and a nurse "
        "confirms every level.",
        "",
        f"- Artifact: `{record['artifact']}` (git-ignored), manifest sha256 `{record['sha256']}`, "
        f"{len(record['files'])} files, {sum(f['bytes'] for f in record['files']) / 1e6:.0f} MB.",
        f"- Data: KTAS (PLOS ONE 2019, CC BY 4.0), split `{record['datasets']['ktas']['split']}`, "
        f"CSV sha256 `{record['datasets']['ktas']['csv_sha256'][:12]}...`.",
    ]
    for view in ("test_full", "test_kiosk_partial"):
        if view in metrics:
            m = metrics[view]
            under = m["under_triage_l1_2"]
            lines.append(
                f"- {view.replace('_', ' ')}: under-triage L1-2 {under['count']}/{under['of']} "
                f"(95% CI {under['ci95'][0]:.1%}-{under['ci95'][1]:.1%}), macro-F1 "
                f"{m['macro_f1']:.3f}, ECE after scaling {m['ece_after']:.3f} (n={m['n']})."
            )
    if "source" in metrics:
        lines.append(f"- Metrics from {metrics['source']}.")
    if notes:
        lines += ["", notes]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


__all__ = [
    "CARDS_DIR",
    "REGISTRY",
    "SCHEMA",
    "add",
    "current",
    "entry",
    "file_sha256",
    "load",
    "manifest",
    "manifest_sha256",
    "verify",
    "write_card",
]
