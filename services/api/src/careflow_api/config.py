"""Settings from the environment (the gitignored .env in development)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[4]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", extra="ignore", frozen=True)

    database_url: str = Field(description="As careflow_app (never a superuser or table owner).")
    database_url_migrator: str = Field(description="As careflow_migrator; Alembic and seed-dev.")
    redis_url: str = "redis://localhost:6379/0"
    jwt_secret: SecretStr = Field(
        validation_alias=AliasChoices("JWT_SECRET", "CAREFLOW_JWT_SECRET")
    )
    fhir_base_url: str = "http://localhost:8080/fhir"
    runtime: str = Field(default="live", validation_alias=AliasChoices("CAREFLOW_RUNTIME"))
    api_version: str = "0.1.0"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # values come from the environment


def sqlalchemy_url(url: str) -> str:
    """`postgresql://...` -> `postgresql+psycopg://...` (psycopg 3, sync and async)."""
    for prefix in ("postgresql+psycopg://", "postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    raise ValueError("DATABASE_URL must be a postgresql:// URL")
