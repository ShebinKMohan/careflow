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
    #: Signs the one-time QR hand-over tokens (M2-06); never the JWT secret. Without it the QR
    #: routes answer 503.
    handover_signing_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("CAREFLOW_HANDOVER_SIGNING_KEY", "HANDOVER_SIGNING_KEY"),
    )
    #: The FHIR endpoint override (`FHIR_BASE_URL`); unset, the hospital adapter file's URL is
    #: used. Read it through `careflow_api.hospital.fhir_endpoint()`.
    fhir_base_url: str | None = None
    #: The CareFlow voice service (M1-14), probed by the admin status; unset, VOICE_PORT on this
    #: host (default 8090).
    voice_base_url: str | None = Field(
        default=None, validation_alias=AliasChoices("CAREFLOW_VOICE_URL")
    )
    runtime: str = Field(default="live", validation_alias=AliasChoices("CAREFLOW_RUNTIME"))
    #: "dev" enables the /v1/dev test hooks; any other value (the default) leaves them out.
    env: str = Field(default="prod", validation_alias=AliasChoices("CAREFLOW_ENV"))
    #: Simulated vitals device pace (1 = real time, 0 = no waiting; tests).
    vitals_sim_speed: float = Field(
        default=1.0, ge=0, validation_alias=AliasChoices("VITALS_SIM_SPEED")
    )
    api_version: str = "0.1.0"
    #: Run the queue's grace-window expiry in the API process (M1-10).
    queue_grace_task: bool = Field(
        default=True, validation_alias=AliasChoices("CAREFLOW_QUEUE_GRACE_TASK")
    )
    #: Run the outbox relay in the API process; off when `careflow-api relay` runs it (M1-11).
    outbox_relay: bool = Field(default=True, validation_alias=AliasChoices("CAREFLOW_OUTBOX_RELAY"))
    #: Push live events to this process's WebSocket clients (M1-11).
    live_events: bool = Field(default=True, validation_alias=AliasChoices("CAREFLOW_LIVE_EVENTS"))
    event_stream: str = Field(
        default="careflow:events", validation_alias=AliasChoices("CAREFLOW_EVENT_STREAM")
    )
    #: Browser origins allowed to call the API (the apps under `make up`), comma-separated.
    cors_origins: str = Field(
        default="http://localhost:3000,http://localhost:3001,http://localhost:3002",
        validation_alias=AliasChoices("CAREFLOW_CORS_ORIGINS"),
    )
    #: Run the intake graph behind the patient and nurse routes (M1-12).
    intake_runtime: bool = Field(
        default=True, validation_alias=AliasChoices("CAREFLOW_INTAKE_RUNTIME")
    )
    #: Gate local-demo readiness on real EN/AR extraction warmup, never a patient request.
    warm_extraction: bool = Field(
        default=False, validation_alias=AliasChoices("CAREFLOW_WARM_EXTRACTION")
    )
    #: Consume `triage.confirmed` as the `fhir-writeback` group (M1-09 write-back safety net).
    writeback_consumer: bool = Field(
        default=True, validation_alias=AliasChoices("CAREFLOW_WRITEBACK_CONSUMER")
    )
    ws_heartbeat_seconds: float = Field(
        default=20.0, gt=0, validation_alias=AliasChoices("CAREFLOW_WS_HEARTBEAT_S")
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # values come from the environment


def sqlalchemy_url(url: str) -> str:
    """`postgresql://...` -> `postgresql+psycopg://...` (psycopg 3, sync and async)."""
    for prefix in ("postgresql+psycopg://", "postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    raise ValueError("DATABASE_URL must be a postgresql:// URL")
