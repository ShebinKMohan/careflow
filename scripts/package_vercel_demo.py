"""Assemble a secret-free Python deployment; use locked cloud dependencies, no GPU extras.

The source paths retain their repository layout so existing config/schema lookup still works.
Does not deploy or modify any UI. The reusable package goes in gitignored .run/vercel-api.
"""

from __future__ import annotations

import json
import shutil
import tomllib
from pathlib import Path

from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / ".run/vercel-api"


def main() -> None:
    DEST.mkdir(parents=True, exist_ok=True)
    ignore = shutil.ignore_patterns(
        "__pycache__", "tests", "*.pyc", ".env*", ".venv", "node_modules"
    )
    for base in ("packages", "services"):
        for project in (ROOT / base).iterdir():
            if not (project / "src").is_dir() or project.name == "voice":
                continue
            shutil.copytree(
                project / "src",
                DEST / base / project.name / "src",
                dirs_exist_ok=True,
                ignore=ignore,
            )
    for relative in (
        "config",
        "packages/contracts/schemas",
        "packages/contracts/fhir",
        "services/rag/handbook",
    ):
        source = ROOT / relative
        if source.exists():
            shutil.copytree(source, DEST / relative, dirs_exist_ok=True, ignore=ignore)
    for file in ("app.py", "vercel.json"):
        shutil.copy2(ROOT / "deploy/vercel" / file, DEST / file)
    (DEST / ".python-version").write_text("3.12\n")
    locked = tomllib.loads((ROOT / "uv.lock").read_text())["package"]
    packages = {p["name"]: p for p in locked if "registry" in p.get("source", {})}
    pending = {"psycopg-binary", "psycopg-pool"}
    for relative in (
        "services/api",
        "services/intake",
        "services/rag",
        "services/integration",
        "packages/providers",
        "packages/contracts",
        "services/rules",
    ):
        project = tomllib.loads((ROOT / relative / "pyproject.toml").read_text())["project"]
        for spec in project["dependencies"]:
            requirement = Requirement(spec)
            name = requirement.name.lower().replace("_", "-").replace(".", "-")
            if not name.startswith("careflow-"):
                pending.add(name)
                for extra in requirement.extras:
                    pending.update(
                        d["name"]
                        for d in packages[name].get("optional-dependencies", {}).get(extra, [])
                    )
    chosen = set()
    while pending:
        name = pending.pop()
        if name in chosen:
            continue
        package = packages[name]
        chosen.add(name)
        for dep in package.get("dependencies", []):
            if dep["name"] not in chosen:
                pending.add(dep["name"])
            for extra in dep.get("extra", []):
                pending.update(
                    d["name"]
                    for d in packages[dep["name"]].get("optional-dependencies", {}).get(extra, [])
                    if d["name"] not in chosen
                )
    forbidden = {"torch", "mlx", "shap", "scikit-learn", "numba", "pandas"}
    if chosen & forbidden:
        raise RuntimeError("Unexpected local/training dependency in cloud runtime")
    requirements = [f"{name}=={packages[name]['version']}" for name in sorted(chosen)]
    (DEST / "requirements.txt").write_text("\n".join(requirements) + "\n")
    (DEST / "pyproject.toml").write_text(
        '[project]\nname = "careflow-hosted-demo"\nversion = "0.0.0"\n'
        'requires-python = ">=3.12,<3.13"\ndependencies = '
        + json.dumps(requirements, indent=2)
        + "\n"
    )
    print(f"Packaged original API with {len(chosen)} locked cloud dependencies: {DEST}")


if __name__ == "__main__":
    main()
