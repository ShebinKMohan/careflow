import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_dockerfile_uses_bookworm_python_base_image() -> None:
    dockerfile = (PROJECT_ROOT / "Dockerfile").read_text()

    assert dockerfile.startswith("FROM python:3.12-slim-bookworm AS runtime\n")


def test_dockerfile_does_not_upgrade_pip_at_build_time() -> None:
    dockerfile = (PROJECT_ROOT / "Dockerfile").read_text()

    assert "pip install --upgrade pip" not in dockerfile


def test_dockerfile_runs_as_non_root_user_and_has_healthcheck() -> None:
    dockerfile = (PROJECT_ROOT / "Dockerfile").read_text()

    assert "RUN addgroup --system voiceagent" in dockerfile
    assert "\nUSER voiceagent\n" in dockerfile
    assert "\nHEALTHCHECK " in dockerfile


def test_dockerignore_excludes_local_state() -> None:
    ignored = {
        line.strip()
        for line in (PROJECT_ROOT / ".dockerignore").read_text().splitlines()
        if line.strip()
    }

    assert {".venv", ".env", "__pycache__", "*.pyc"}.issubset(ignored)


def test_compose_declares_voice_agent_redis_dependency_and_port() -> None:
    compose = (PROJECT_ROOT / "docker-compose.yml").read_text()

    assert "voice-agent:" in compose
    assert '      - "8080:8080"' in compose
    assert "condition: service_healthy" in compose
    assert "redis:7.4-alpine" in compose


def test_runtime_dependencies_do_not_include_unused_multipart_parser() -> None:
    project = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())
    dependencies = project["project"]["dependencies"]

    assert all(not dependency.lower().startswith("python-multipart") for dependency in dependencies)


def test_plivo_api_response_limit_docs_cover_create_and_hangup_paths() -> None:
    readme = " ".join((PROJECT_ROOT / "README.md").read_text().split())
    checklist = " ".join((PROJECT_ROOT / "DEPLOYMENT_CHECKLIST.md").read_text().split())

    assert "bounds create-call and hangup/cancel response bodies" in readme
    assert "create-call or hangup/cancel responses" in checklist


def test_deepgram_transcript_message_limit_is_documented_for_production() -> None:
    env_example = (PROJECT_ROOT / ".env.example").read_text()
    readme = " ".join((PROJECT_ROOT / "README.md").read_text().split())
    checklist = " ".join((PROJECT_ROOT / "DEPLOYMENT_CHECKLIST.md").read_text().split())

    assert "VOICE_AGENT_DEEPGRAM_TRANSCRIPT_MESSAGE_MAX_BYTES=65536" in env_example
    assert "VOICE_AGENT_DEEPGRAM_TRANSCRIPT_MESSAGE_MAX_BYTES" in readme
    assert "VOICE_AGENT_DEEPGRAM_TRANSCRIPT_MESSAGE_MAX_BYTES" in checklist
