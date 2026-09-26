"""Tests for scripts/gen_dev_env.py: idempotent, never overwrites, never prints a value."""

from __future__ import annotations

import base64
import re
import stat
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "gen_dev_env.py"
_LINE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


def run(env_file: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--env-file", str(env_file)],
        capture_output=True,
        text=True,
        check=False,
    )


def values(env_file: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if m := _LINE.match(line):
            out[m.group(1)] = m.group(2)
    return out


def keys_in(dict_name: str) -> list[str]:
    source = SCRIPT.read_text(encoding="utf-8")
    block = source.split(f"{dict_name}: dict", 1)[1].split("\n}\n", 1)[0]
    return re.findall(r'^\s+"([A-Z0-9_]+)":', block, re.M)


def generated_keys() -> list[str]:
    return keys_in("GENERATORS") + keys_in("DERIVED")


def test_creates_all_keys_with_private_mode(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    result = run(env)
    assert result.returncode == 0, result.stderr
    got = values(env)
    assert set(generated_keys()) <= set(got)
    assert all(got[k] for k in generated_keys())
    assert stat.S_IMODE(env.stat().st_mode) == 0o600


def test_second_run_changes_nothing(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    run(env)
    before = env.read_bytes()
    mtime = env.stat().st_mtime_ns
    result = run(env)
    assert result.returncode == 0
    assert "no changes" in result.stdout
    assert env.read_bytes() == before
    assert env.stat().st_mtime_ns == mtime


def test_never_overwrites_and_fills_empty_values(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("CAREFLOW_JWT_SECRET=keep-me\nPOSTGRES_PASSWORD=\nOPENAI_API_KEY=\n")
    run(env)
    got = values(env)
    assert got["CAREFLOW_JWT_SECRET"] == "keep-me"
    assert got["POSTGRES_PASSWORD"]  # empty value was filled
    assert got["OPENAI_API_KEY"] == ""  # vendor keys are never generated


def test_never_prints_a_value(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    result = run(env)
    output = result.stdout + result.stderr
    for value in values(env).values():
        assert value not in output


def test_value_formats(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    run(env)
    got = values(env)
    assert len(base64.urlsafe_b64decode(got["CAREFLOW_FERNET_KEY"])) == 32
    assert re.fullmatch(r"[0-9a-f]{64}", got["LANGFUSE_ENCRYPTION_KEY"])
    assert got["LANGFUSE_INIT_PROJECT_PUBLIC_KEY"].startswith("pk-lf-")
    assert got["LANGFUSE_INIT_PROJECT_SECRET_KEY"].startswith("sk-lf-")
    assert got["CAREFLOW_WEBHOOK_SIGNING_SECRET"].startswith("whsec_")


def test_env_example_lists_every_generated_name_without_values() -> None:
    example = values(REPO_ROOT / ".env.example")
    assert set(generated_keys()) <= set(example)
    assert all(value == "" for value in example.values())


def test_derived_urls_use_the_generated_passwords(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    run(env)
    got = values(env)
    app, migrator = got["CAREFLOW_DB_APP_PASSWORD"], got["CAREFLOW_DB_MIGRATOR_PASSWORD"]
    assert got["DATABASE_URL"] == f"postgresql://careflow_app:{app}@localhost:5432/careflow"
    assert got["DATABASE_URL_MIGRATOR"].startswith(f"postgresql://careflow_migrator:{migrator}@")
    assert got["FHIR_BASE_URL"] == "http://localhost:8080/fhir"


def test_derived_keys_are_added_to_an_existing_env(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("CAREFLOW_DB_APP_PASSWORD=keep-me\n")
    run(env)
    assert values(env)["DATABASE_URL"].startswith("postgresql://careflow_app:keep-me@")
