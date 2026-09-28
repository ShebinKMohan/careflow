"""Tests for scripts/stack.py: ports, the stack environment and ownership (no Docker needed)."""

from __future__ import annotations

import importlib.util
import stat
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "stack.py"
SECRETS = {
    "POSTGRES_PASSWORD": "admin-pw",
    "CAREFLOW_DB_APP_PASSWORD": "app-pw",
    "CAREFLOW_DB_MIGRATOR_PASSWORD": "migrator/pw",  # quoted in the URL
}


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("stack", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["stack"] = module
    spec.loader.exec_module(module)
    return module


stack = load()


def test_offset_zero_is_the_conventions_ports() -> None:
    assert stack.ports(0) == {
        "CAREFLOW_POSTGRES_PORT": 5432,
        "CAREFLOW_VALKEY_PORT": 6379,
        "CAREFLOW_HAPI_PORT": 8080,
        "CAREFLOW_API_PORT": 8000,
        "CAREFLOW_KIOSK_PORT": 3000,
        "CAREFLOW_STAFF_PORT": 3001,
        "CAREFLOW_COMPANION_PORT": 3002,
        "VOICE_PORT": 8090,
    }


def test_explicit_triage_model_selection_survives_stack_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured = {**SECRETS, "TRIAGE_MODEL_KIND": "laya", "TRIAGE_MODEL_DEVICE": "cpu"}
    monkeypatch.delenv("TRIAGE_MODEL_KIND", raising=False)
    monkeypatch.delenv("TRIAGE_MODEL_DEVICE", raising=False)
    monkeypatch.setenv("TRIAGE_MODEL_THREADS", "2")
    env = stack.stack_env("careflow", 0, configured)
    assert env["TRIAGE_MODEL_KIND"] == "laya"
    assert env["TRIAGE_MODEL_DEVICE"] == "cpu"
    assert env["TRIAGE_MODEL_THREADS"] == "2"
    monkeypatch.setenv("TRIAGE_MODEL_KIND", "hgb")
    assert stack.stack_env("careflow", 0, configured)["TRIAGE_MODEL_KIND"] == "hgb"


def test_every_port_moves_by_the_offset_and_stays_valid() -> None:
    moved = stack.ports(10_000)
    assert all(moved[name] == port + 10_000 for name, port in stack.PORTS.items())
    with pytest.raises(stack.StackError, match="above 65535"):
        stack.ports(60_000)
    with pytest.raises(stack.StackError, match="negative"):
        stack.ports(-1)


def test_the_environment_points_every_url_at_the_stacks_ports() -> None:
    env = stack.stack_env("careflow-test", 10_000, SECRETS)
    assert (env["CAREFLOW_STACK"], env["COMPOSE_PROJECT_NAME"]) == ("careflow-test",) * 2
    assert env["DATABASE_URL"] == "postgresql://careflow_app:app-pw@localhost:15432/careflow"
    assert env["DATABASE_URL_MIGRATOR"] == (
        "postgresql://careflow_migrator:migrator%2Fpw@localhost:15432/careflow"
    )
    assert env["CAREFLOW_TEST_ADMIN_URL"] == (
        "postgresql+psycopg://postgres:admin-pw@localhost:15432/postgres"
    )
    assert env["REDIS_URL"] == "redis://localhost:16379/0"
    assert env["FHIR_BASE_URL"] == "http://localhost:18080/fhir"
    assert env["CAREFLOW_API_URL"] == "http://localhost:18000"


def test_offset_zero_reproduces_the_dev_urls() -> None:
    env = stack.stack_env("careflow", 0, SECRETS)
    assert env["DATABASE_URL"] == "postgresql://careflow_app:app-pw@localhost:5432/careflow"
    assert (env["REDIS_URL"], env["FHIR_BASE_URL"]) == (
        "redis://localhost:6379/0",
        "http://localhost:8080/fhir",
    )


def test_the_api_and_its_voice_service_find_each_other_on_the_stacks_ports() -> None:
    env = stack.stack_env("careflow-e2e", 30000, SECRETS)
    assert env["CAREFLOW_VOICE_URL"] == "http://127.0.0.1:38090"  # the API's voice service
    assert env["CAREFLOW_API_BASE_URL"] == "http://127.0.0.1:38000"  # the voice service's API
    dev = stack.stack_env("careflow", 0, SECRETS)
    assert dev["CAREFLOW_API_BASE_URL"] == "http://127.0.0.1:8000"


def test_by_default_the_apps_and_the_api_stay_on_localhost() -> None:
    env = stack.stack_env("careflow", 0, SECRETS)
    assert env["CAREFLOW_API_HOST"] == "127.0.0.1"
    assert "CAREFLOW_PUBLIC_HOST" not in env
    assert env["NEXT_PUBLIC_API_BASE_URL"] == "http://localhost:8000"
    assert env["NEXT_PUBLIC_COMPANION_URL"] == "http://localhost:3002"


def test_a_public_host_makes_the_qr_code_and_the_api_reachable_from_a_phone() -> None:
    env = stack.stack_env("careflow", 0, SECRETS, public_host="192.168.1.20")
    assert env["NEXT_PUBLIC_COMPANION_URL"] == "http://192.168.1.20:3002"  # in the QR code
    assert env["NEXT_PUBLIC_API_BASE_URL"] == "http://192.168.1.20:3002"
    assert "http://192.168.1.20:3002" in env["CAREFLOW_CORS_ORIGINS"].split(",")
    assert "http://localhost:3000" in env["CAREFLOW_CORS_ORIGINS"].split(",")
    assert env["CAREFLOW_API_HOST"] == "0.0.0.0"  # noqa: S104 - the opt-in LAN binding
    assert env["CAREFLOW_PUBLIC_HOST"] == "192.168.1.20"
    assert env["CAREFLOW_API_URL"] == "http://localhost:8000"  # local tools stay local
    assert env["NEXT_PUBLIC_VOICE_WS_URL"].startswith("ws://localhost:")  # the kiosk's voice


def test_lan_proxy_follows_offset_and_device_setup_can_be_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CAREFLOW_KIOSK_AUTO_SETUP", raising=False)
    monkeypatch.delenv("SIM_BP_CUFF", raising=False)
    env = stack.stack_env("careflow-test", 10_000, SECRETS, public_host="192.168.1.20")
    assert env["NEXT_PUBLIC_API_BASE_URL"] == "http://192.168.1.20:13002"
    assert env["CAREFLOW_API_URL"] == "http://localhost:18000"
    assert env["CAREFLOW_KIOSK_AUTO_SETUP"] == "1"
    assert env["SIM_BP_CUFF"] == "1"
    monkeypatch.setenv("CAREFLOW_KIOSK_AUTO_SETUP", "0")
    assert stack.stack_env("careflow", 0, SECRETS)["CAREFLOW_KIOSK_AUTO_SETUP"] == "0"
    monkeypatch.setenv("SIM_BP_CUFF", "0")
    assert stack.stack_env("careflow", 0, SECRETS)["SIM_BP_CUFF"] == "0"


@pytest.mark.parametrize("host", ["http://192.168.1.20", "a b", "host:3002", "x/", "", "-a"])
def test_a_public_host_is_only_a_name_or_address(host: str) -> None:
    with pytest.raises(stack.StackError, match="CAREFLOW_PUBLIC_HOST"):
        stack.stack_env("careflow", 0, SECRETS, public_host=host)


@pytest.mark.parametrize("name", ["Careflow", "c", "care_flow", "-care", "a" * 41])
def test_stack_names_are_simple(name: str) -> None:
    with pytest.raises(stack.StackError, match="stack name"):
        stack.stack_env(name, 0, SECRETS)


def test_missing_passwords_say_what_to_run() -> None:
    with pytest.raises(stack.StackError, match="gen_dev_env.py"):
        stack.stack_env("careflow-test", 0, {"POSTGRES_PASSWORD": "x"})


def test_the_env_file_is_private_and_parses_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(stack, "STACKS", tmp_path / "stacks")
    env = stack.stack_env("careflow-test", 10_000, SECRETS)
    path = stack.write_env("careflow-test", env)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stack.parse_env(path.read_text()) == env


def test_env_prints_the_path_never_a_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    dot_env = tmp_path / ".env"
    dot_env.write_text("".join(f"{key}={value}\n" for key, value in SECRETS.items()))
    monkeypatch.setattr(stack, "DOT_ENV", dot_env)
    monkeypatch.setattr(stack, "REPO", tmp_path)
    monkeypatch.setattr(stack, "STACKS", tmp_path / ".run" / "stacks")
    assert stack.main(["env", "--stack", "careflow-test"]) == 0
    out = capsys.readouterr().out
    assert out.strip() == ".run/stacks/careflow-test.env"
    assert not any(secret in out for secret in SECRETS.values())


def test_parse_env_skips_comments_and_quotes() -> None:
    text = "# comment\n\nA=1\nB='two'\nC=\"three\"\nD = four \nnot a pair\n"
    assert stack.parse_env(text) == {"A": "1", "B": "two", "C": "three", "D": "four"}


def test_only_another_checkouts_stack_is_foreign(tmp_path: Path) -> None:
    mine = tmp_path / "careflow" / "infra"
    theirs = tmp_path / "other-clone" / "infra"
    assert stack.foreign_owners({str(mine)}, infra=mine) == set()
    assert stack.foreign_owners({str(mine), str(theirs)}, infra=mine) == {str(theirs)}
    assert stack.foreign_owners(set(), infra=mine) == set()


@pytest.mark.parametrize(
    ("text", "size"),
    [
        ("1.468GiB", int(1.468 * 1024**3)),
        ("161.4MiB", int(161.4 * 1024**2)),
        ("50.57MiB", int(50.57 * 1024**2)),
        ("0B", 0),
        ("12kB", 12_000),
    ],
)
def test_docker_stats_sizes(text: str, size: int) -> None:
    assert stack.parse_size(text) == size


def test_the_dev_stacks_volumes_are_never_deleted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(stack, "check_owner", lambda name: {str(stack.INFRA)})
    with pytest.raises(stack.StackError, match="never deleted"):
        stack.down("careflow", volumes=True)
