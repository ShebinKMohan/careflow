#!/usr/bin/env python3
"""Run a CareFlow stack under its own name and ports, beside any other one (M1-12, M1-19).

    python3 scripts/stack.py up   [--stack careflow-test] [--offset 10000]
    python3 scripts/stack.py run  [--stack careflow-test] -- uv run pytest services/api -m hapi
    python3 scripts/stack.py down [--stack careflow-test] [--volumes]
    python3 scripts/stack.py env  [--stack careflow-test] [--offset 10000]
    python3 scripts/stack.py ps

A stack is a Docker Compose project (Postgres, Valkey, HAPI FHIR) plus the ports its API and apps
use. Every port is the CONVENTIONS default plus `--offset`: offset 0 is the dev stack `careflow`
(5432, 6379, 8080, 8000, 3000-3002, 8090); the test stack `careflow-test` defaults to +10000.
`env` writes `.run/stacks/<stack>.env` (mode 0600; its values are never printed): the ports, the
project name, and the connection URLs rebuilt on the stack's ports from `.env`'s passwords.
`run` executes a command with that environment, so tests reach only that stack.

Ownership: Docker Compose labels each container with the directory it was started from. `up`
refuses a stack that another checkout runs and `down` stops only a stack this checkout started,
so a second checkout or a test run never stops the owner's services. `up` also refuses to start
when the containers already running plus this stack would pass the 6 GiB Docker budget. `up`
seeds the seven synthetic personas into the stack's HAPI. Standard library only.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

REPO = Path(__file__).resolve().parent.parent
INFRA = REPO / "infra"
COMPOSE_FILE = INFRA / "docker-compose.yml"
DOT_ENV = REPO / ".env"
STACKS = REPO / ".run" / "stacks"
DEFAULT_STACK = "careflow"
TEST_STACK = "careflow-test"
TEST_OFFSET = 10_000
BUDGET_BYTES = 6 * 1024**3
#: One stack's containers once HAPI is warm: docker stats on 27 Sep 2026 showed HAPI 1.47 GiB,
#: Postgres 0.16 GiB and Valkey 0.05 GiB; rounded up.
STACK_ESTIMATE_BYTES = 2 * 1024**3
#: The CONVENTIONS ports of a stack at offset 0.
PORTS: dict[str, int] = {
    "CAREFLOW_POSTGRES_PORT": 5432,
    "CAREFLOW_VALKEY_PORT": 6379,
    "CAREFLOW_HAPI_PORT": 8080,
    "CAREFLOW_API_PORT": 8000,
    "CAREFLOW_KIOSK_PORT": 3000,
    "CAREFLOW_STAFF_PORT": 3001,
    "CAREFLOW_COMPANION_PORT": 3002,
    "VOICE_PORT": 8090,
}
SECRETS = ("POSTGRES_PASSWORD", "CAREFLOW_DB_APP_PASSWORD", "CAREFLOW_DB_MIGRATOR_PASSWORD")
NAME = re.compile(r"^[a-z][a-z0-9-]{1,39}$")
PROJECT_LABEL = "com.docker.compose.project"
WORKDIR_LABEL = "com.docker.compose.project.working_dir"
_SIZE = re.compile(r"^\s*([\d.]+)\s*([KMGT]?i?B)\s*$", re.IGNORECASE)
_UNITS = {"b": 1, "kb": 1000, "mb": 1000**2, "gb": 1000**3, "tb": 1000**4}
_UNITS |= {"kib": 1024, "mib": 1024**2, "gib": 1024**3, "tib": 1024**4}


class StackError(RuntimeError):
    """A stack cannot be used as asked; the message says why and what to do."""


def parse_env(text: str) -> dict[str, str]:
    """`KEY=value` lines (comments and blanks skipped, one pair of quotes removed)."""
    values: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def ports(offset: int) -> dict[str, int]:
    if offset < 0:
        raise StackError("the port offset cannot be negative")
    moved = {name: port + offset for name, port in PORTS.items()}
    if max(moved.values()) > 65_535:
        raise StackError(f"offset {offset} puts a port above 65535")
    return moved


def stack_env(stack: str, offset: int, secrets: dict[str, str]) -> dict[str, str]:
    """The stack's environment: its name, ports and connection URLs (with passwords)."""
    if not NAME.match(stack):
        raise StackError(f"stack name {stack!r}: use lowercase letters, digits and dashes")
    missing = [key for key in SECRETS if not secrets.get(key)]
    if missing:
        raise StackError(f".env lacks {', '.join(missing)}: run python3 scripts/gen_dev_env.py")
    p = ports(offset)
    admin, app, migrator = (quote(secrets[key], safe="") for key in SECRETS)
    pg = p["CAREFLOW_POSTGRES_PORT"]
    apps = [p[f"CAREFLOW_{name}_PORT"] for name in ("KIOSK", "STAFF", "COMPANION")]
    origins = [f"http://{host}:{port}" for port in apps for host in ("localhost", "127.0.0.1")]
    api = f"http://localhost:{p['CAREFLOW_API_PORT']}"
    return {
        "CAREFLOW_STACK": stack,
        "COMPOSE_PROJECT_NAME": stack,
        "CAREFLOW_PORT_OFFSET": str(offset),
        **{name: str(port) for name, port in p.items()},
        "DATABASE_URL": f"postgresql://careflow_app:{app}@localhost:{pg}/careflow",
        "DATABASE_URL_MIGRATOR": f"postgresql://careflow_migrator:{migrator}@localhost:{pg}/careflow",
        "CAREFLOW_TEST_ADMIN_URL": f"postgresql+psycopg://postgres:{admin}@localhost:{pg}/postgres",
        "REDIS_URL": f"redis://localhost:{p['CAREFLOW_VALKEY_PORT']}/0",
        "FHIR_BASE_URL": f"http://localhost:{p['CAREFLOW_HAPI_PORT']}/fhir",
        "CAREFLOW_API_URL": api,
        # The API lets the stack's own apps call it, and finds its voice service (M1-19).
        "CAREFLOW_CORS_ORIGINS": ",".join(origins),
        "CAREFLOW_VOICE_URL": f"http://127.0.0.1:{p['VOICE_PORT']}",
        # What the stack's apps are built with (public values: URLs only, never a secret).
        "NEXT_PUBLIC_API_BASE_URL": api,
        "NEXT_PUBLIC_COMPANION_URL": f"http://localhost:{p['CAREFLOW_COMPANION_PORT']}",
        "NEXT_PUBLIC_FHIR_BASE_URL": f"http://localhost:{p['CAREFLOW_HAPI_PORT']}/fhir",
        "NEXT_PUBLIC_VOICE_WS_URL": f"ws://localhost:{p['VOICE_PORT']}/kiosk/audio",
    }


def env_file(stack: str) -> Path:
    return STACKS / f"{stack}.env"


def write_env(stack: str, env: dict[str, str]) -> Path:
    path = env_file(stack)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(f"{key}={value}\n" for key, value in env.items())
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(f"# {stack}: written by scripts/stack.py; do not commit.\n{body}")
    path.chmod(0o600)
    return path


def read_env(stack: str) -> dict[str, str]:
    path = env_file(stack)
    if not path.is_file():
        hint = f"run python3 scripts/stack.py up --stack {stack}"
        raise StackError(f"no {path.relative_to(REPO)}: {hint}")
    return parse_env(path.read_text(encoding="utf-8"))


def docker(*args: str, capture: bool = True) -> str:
    executable = shutil.which("docker")
    if executable is None:
        raise StackError("docker is not installed or not on PATH")
    result = subprocess.run(  # noqa: S603 - fixed program, arguments built here
        [executable, *args], capture_output=capture, text=True, check=False
    )
    if result.returncode != 0:
        detail = (result.stderr or "").strip().splitlines()[-1:] if capture else []
        raise StackError(f"docker {' '.join(args[:3])} failed{': ' + detail[0] if detail else ''}")
    return result.stdout if capture else ""


def compose(stack: str, *args: str) -> None:
    docker(
        "compose",
        "--env-file",
        str(DOT_ENV),
        "--env-file",
        str(env_file(stack)),
        "-f",
        str(COMPOSE_FILE),
        "-p",
        stack,
        *args,
        capture=False,
    )


def owners(stack: str) -> set[str]:
    """The directories the stack's containers (running or stopped) were started from."""
    out = docker(
        "ps",
        "-a",
        "--filter",
        f"label={PROJECT_LABEL}={stack}",
        "--format",
        f'{{{{.Label "{WORKDIR_LABEL}"}}}}',
    )
    return {line.strip() for line in out.splitlines() if line.strip()}


def foreign_owners(found: set[str], infra: Path = INFRA) -> set[str]:
    return {owner for owner in found if Path(owner).resolve() != infra.resolve()}


def check_owner(stack: str) -> set[str]:
    found = owners(stack)
    others = foreign_owners(found)
    if others:
        raise StackError(
            f"stack {stack!r} was started from {', '.join(sorted(others))}, not this checkout; "
            "it is left alone. Choose another --stack and --offset."
        )
    return found


def parse_size(text: str) -> int:
    match = _SIZE.match(text)
    if not match:
        raise ValueError(f"unrecognised size {text!r}")
    return int(float(match.group(1)) * _UNITS[match.group(2).lower()])


def memory_in_use() -> int:
    """Bytes used by every running container (`docker stats`), all projects."""
    out = docker("stats", "--no-stream", "--format", "{{.MemUsage}}")
    return sum(parse_size(line.split("/")[0]) for line in out.splitlines() if "/" in line)


def wait_http(url: str, timeout_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:  # noqa: S310 - local URL
                if response.status == 200:
                    return
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            pass
        if time.monotonic() > deadline:
            raise StackError(f"{url} did not answer within {timeout_s:.0f} s")
        time.sleep(3)


def gib(value: int) -> str:
    return f"{value / 1024**3:.2f} GiB"


def up(stack: str, offset: int, *, seed: bool = True) -> int:
    found = check_owner(stack)
    subprocess.run([sys.executable, str(REPO / "scripts/gen_dev_env.py")], check=True)  # noqa: S603
    env = stack_env(stack, offset, parse_env(DOT_ENV.read_text(encoding="utf-8")))
    write_env(stack, env)
    running = docker(
        "ps", "--filter", f"label={PROJECT_LABEL}={stack}", "--format", "{{.Names}}"
    ).split()
    if not running:
        in_use = memory_in_use()
        if in_use + STACK_ESTIMATE_BYTES > BUDGET_BYTES:
            estimate = gib(STACK_ESTIMATE_BYTES)
            raise StackError(
                f"containers already use {gib(in_use)}; another stack (~{estimate}) would pass "
                f"the {gib(BUDGET_BYTES)} Docker budget. Stop one first."
            )
    print(f"stack: {stack} ({'reusing' if found else 'new'}; ports +{offset})", flush=True)
    compose(stack, "up", "-d", "--build", "--wait", "postgres", "valkey")
    compose(stack, "up", "-d", "hapi")
    fhir = env["FHIR_BASE_URL"]
    wait_http(f"{fhir}/metadata", 300)
    seed_env = {**os.environ, **env}
    for script in ("infra/seed/load_fhir.py", "infra/seed/add_mock_ids.py") if seed else ():
        extra = ["--personas-only"] if script.endswith("load_fhir.py") else []
        subprocess.run(  # noqa: S603 - fixed argv
            ["uv", "run", "python", script, *extra, "--base-url", fhir],  # noqa: S607
            cwd=REPO,
            env=seed_env,
            check=True,
        )
    total = memory_in_use()
    seeded = " (personas seeded)" if seed else ""
    print(
        f"stack: {stack} is up: Postgres :{env['CAREFLOW_POSTGRES_PORT']}, Valkey "
        f":{env['CAREFLOW_VALKEY_PORT']}, HAPI :{env['CAREFLOW_HAPI_PORT']}{seeded}."
    )
    print(f"stack: all containers now use {gib(total)} (budget {gib(BUDGET_BYTES)}).")
    print(f"stack: run commands against it with: python3 scripts/stack.py run --stack {stack} -- …")
    return 0 if total <= BUDGET_BYTES else 1


def down(stack: str, volumes: bool) -> int:
    found = check_owner(stack)
    if not found:
        print(f"stack: {stack} has no containers; nothing to stop.")
        return 0
    if volumes and stack == DEFAULT_STACK:
        raise StackError("the dev stack's volumes hold its data: they are never deleted here")
    if not env_file(stack).is_file():
        write_env(stack, stack_env(stack, 0, parse_env(DOT_ENV.read_text(encoding="utf-8"))))
    compose(stack, "down", *(["--volumes"] if volumes else []))
    print(f"stack: {stack} stopped{' and its volumes removed' if volumes else ''}.")
    return 0


def run(stack: str, command: list[str]) -> int:
    if not command:
        raise StackError("run needs a command after --")
    env = {**os.environ, **read_env(stack)}
    return subprocess.run(command, env=env, check=False).returncode  # noqa: S603


def ps() -> int:
    out = docker(
        "ps",
        "-a",
        "--filter",
        f"label={PROJECT_LABEL}",
        "--format",
        f'{{{{.Label "{PROJECT_LABEL}"}}}}\t{{{{.Names}}}}\t{{{{.Status}}}}\t'
        f'{{{{.Label "{WORKDIR_LABEL}"}}}}',
    )
    print(out.rstrip() or "no Compose containers")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("up", "env", "down", "run"):
        command = sub.add_parser(name)
        command.add_argument("--stack", default=TEST_STACK)
        if name in ("up", "env"):
            command.add_argument("--offset", type=int, default=None)
        if name == "down":
            command.add_argument("--volumes", action="store_true")
        if name == "up":
            command.add_argument("--no-seed", action="store_true", help="leave HAPI empty")
        if name == "run":
            command.add_argument("cmd", nargs=argparse.REMAINDER)
    sub.add_parser("ps")
    args = parser.parse_args(argv)
    try:
        if args.command == "ps":
            return ps()
        offset = getattr(args, "offset", None)
        if offset is None:
            offset = TEST_OFFSET if args.stack == TEST_STACK else 0
        if args.command == "env":
            secrets = parse_env(DOT_ENV.read_text(encoding="utf-8")) if DOT_ENV.is_file() else {}
            path = write_env(args.stack, stack_env(args.stack, offset, secrets))
            print(path.relative_to(REPO))
            return 0
        if args.command == "up":
            return up(args.stack, offset, seed=not args.no_seed)
        if args.command == "down":
            return down(args.stack, args.volumes)
        command = args.cmd[1:] if args.cmd[:1] == ["--"] else args.cmd
        return run(args.stack, command)
    except (StackError, subprocess.CalledProcessError) as exc:
        print(f"stack: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
