"""Tests for scripts/check_banned_deps.py (run as a subprocess, the way CI runs it)."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "check_banned_deps.py"
REPO_PYPROJECTS = sorted(
    p.relative_to(REPO_ROOT)
    for p in REPO_ROOT.glob("**/pyproject.toml")
    if not {".venv", "node_modules"} & set(p.parts)
)


def run_check(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root)],
        capture_output=True,
        text=True,
        check=False,
    )


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def mirror_pyprojects(dest: Path) -> None:
    for rel in REPO_PYPROJECTS:
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO_ROOT / rel, dest / rel)


def test_repo_is_clean() -> None:
    result = run_check(REPO_ROOT)
    assert result.returncode == 0, result.stderr


def test_repo_has_the_expected_pyprojects() -> None:
    assert Path("pyproject.toml") in REPO_PYPROJECTS
    assert len(REPO_PYPROJECTS) >= 9  # the root plus the eight workspace members


@pytest.mark.parametrize("rel", REPO_PYPROJECTS, ids=str)
def test_adding_litellm_to_any_pyproject_fails(tmp_path: Path, rel: Path) -> None:
    mirror_pyprojects(tmp_path)
    assert run_check(tmp_path).returncode == 0
    target = tmp_path / rel
    text = target.read_text(encoding="utf-8")
    if "\ndependencies = []" in text:
        text = text.replace("\ndependencies = []", '\ndependencies = ["litellm>=1.0"]')
    else:
        text += '\n[project]\nname = "x"\ndependencies = ["litellm>=1.0"]\n'
    target.write_text(text, encoding="utf-8")
    result = run_check(tmp_path)
    assert result.returncode == 1
    assert "litellm" in result.stderr


@pytest.mark.parametrize(
    "req",
    [
        "LiteLLM==1.0",
        "LITELLM>=1",
        "litellm[proxy]~=1.0",
        "litellm @ https://example.invalid/x.whl",
    ],
)
def test_litellm_spellings_are_banned(tmp_path: Path, req: str) -> None:
    write(tmp_path / "pyproject.toml", f'[project]\nname = "x"\ndependencies = ["{req}"]\n')
    assert run_check(tmp_path).returncode == 1


def test_unconstrained_dependency_fails(tmp_path: Path) -> None:
    write(tmp_path / "pyproject.toml", '[project]\nname = "x"\ndependencies = ["requests"]\n')
    result = run_check(tmp_path)
    assert result.returncode == 1
    assert "no version constraint" in result.stderr


def test_unconstrained_build_requirement_fails(tmp_path: Path) -> None:
    write(tmp_path / "pyproject.toml", '[build-system]\nrequires = ["hatchling"]\n')
    assert run_check(tmp_path).returncode == 1


@pytest.mark.parametrize(
    "req",
    [
        "requests>=2,<3",
        "pyright[nodejs]==1.1.414",
        "numpy>=2; sys_platform == 'darwin'",
        "pkg @ https://example.invalid/pkg-1.0-py3-none-any.whl",
    ],
)
def test_constrained_dependencies_pass(tmp_path: Path, req: str) -> None:
    write(tmp_path / "pyproject.toml", f'[project]\nname = "x"\ndependencies = ["{req}"]\n')
    assert run_check(tmp_path).returncode == 0


def test_workspace_members_are_exempt(tmp_path: Path) -> None:
    write(tmp_path / "a" / "pyproject.toml", '[project]\nname = "careflow-a"\ndependencies = []\n')
    write(
        tmp_path / "b" / "pyproject.toml",
        '[project]\nname = "careflow-b"\ndependencies = ["careflow-a"]\n'
        "[tool.uv.sources]\ncareflow-a = { workspace = true }\n",
    )
    assert run_check(tmp_path).returncode == 0


def test_litellm_in_uv_lock_fails(tmp_path: Path) -> None:
    write(tmp_path / "uv.lock", 'version = 1\n\n[[package]]\nname = "litellm"\nversion = "1.0"\n')
    assert run_check(tmp_path).returncode == 1


def test_litellm_in_package_json_fails(tmp_path: Path) -> None:
    write(tmp_path / "package.json", json.dumps({"devDependencies": {"litellm": "1.0.0"}}))
    assert run_check(tmp_path).returncode == 1


def test_litellm_in_pnpm_lock_fails(tmp_path: Path) -> None:
    write(
        tmp_path / "pnpm-lock.yaml",
        "lockfileVersion: '9.0'\npackages:\n  litellm@1.0.0:\n    resolution: {integrity: x}\n",
    )
    assert run_check(tmp_path).returncode == 1


def test_node_modules_is_skipped(tmp_path: Path) -> None:
    write(
        tmp_path / "node_modules" / "x" / "package.json",
        json.dumps({"dependencies": {"litellm": "1.0.0"}}),
    )
    assert run_check(tmp_path).returncode == 0
