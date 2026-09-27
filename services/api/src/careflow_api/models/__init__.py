"""ORM models. The schema itself (tables, grants, RLS) is defined by the Alembic migrations."""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class _Row:
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Clinic(_Row, Base):
    __tablename__ = "clinic"
    name: Mapped[str] = mapped_column(String(120), unique=True)
    timezone: Mapped[str] = mapped_column(String(64))


class _ClinicScoped(_Row):
    clinic_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clinic.id"), index=True
    )


class Device(_ClinicScoped, Base):
    __tablename__ = "device"
    kind: Mapped[str] = mapped_column(String(16))  # kiosk | board | integration
    label: Mapped[str] = mapped_column(String(120))
    secret_hash: Mapped[str] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))


class StaffUser(_ClinicScoped, Base):
    __tablename__ = "staff_user"
    email: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(String(100))
    role: Mapped[str] = mapped_column(String(16))  # nurse | doctor | admin
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))


class PatientSession(_ClinicScoped, Base):
    __tablename__ = "patient_session"
    device_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("device.id"))
    fhir_patient_id: Mapped[str | None] = mapped_column(String(64))
    language: Mapped[str] = mapped_column(String(2))
    status: Mapped[str] = mapped_column(String(24), server_default=text("'intake'"))
    started_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    #: Initials only (e.g. "M. S."), for the staff screens (M1-12).
    patient_display: Mapped[str | None] = mapped_column(String(8))
    last_activity_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class EncounterState(_ClinicScoped, Base):
    __tablename__ = "encounter_state"
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("patient_session.id"), unique=True
    )
    graph_thread_id: Mapped[str] = mapped_column(String(64))
    stage: Mapped[str] = mapped_column(String(32))
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class FhirWritebackStatus(_ClinicScoped, Base):
    """The outcome of a session's FHIR write-back (M1-09): ok | failed | skipped."""

    __tablename__ = "fhir_writeback_status"
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("patient_session.id"), unique=True
    )
    status: Mapped[str] = mapped_column(String(16))
    attempts: Mapped[int] = mapped_column(Integer)
    bundle_id: Mapped[str | None] = mapped_column(String(64))
    last_error: Mapped[str | None] = mapped_column(String(500))
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class FhirWriteback(_ClinicScoped, Base):
    """One resource the hospital's FHIR server holds for a session (M1-09)."""

    __tablename__ = "fhir_writebacks"
    __table_args__ = (UniqueConstraint("session_id", "local_id"),)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("patient_session.id")
    )
    local_id: Mapped[str] = mapped_column(String(80))
    resource_type: Mapped[str] = mapped_column(String(64))
    resource_id: Mapped[str] = mapped_column(String(64))
    version_id: Mapped[str | None] = mapped_column(String(64))
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class BriefingRow(_ClinicScoped, Base):
    """A session's doctor briefing (M1-16): the contract JSON and its FHIR document's status."""

    __tablename__ = "briefings"
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("patient_session.id"), unique=True
    )
    briefing: Mapped[dict[str, object]] = mapped_column(JSONB)
    summary_status: Mapped[str] = mapped_column(String(16))
    source_missing: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    document_status: Mapped[str | None] = mapped_column(String(16))
    document_id: Mapped[str | None] = mapped_column(String(64))
    document_error: Mapped[str | None] = mapped_column(String(500))
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class NurseDecisionRow(_ClinicScoped, Base):
    """One nurse decision (M1-12), for M5-02 and M5-03. Coded values only, never free text."""

    __tablename__ = "nurse_decisions"
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("patient_session.id")
    )
    decision_id: Mapped[str] = mapped_column(String(64), unique=True)
    nurse_id: Mapped[str] = mapped_column(String(64))
    suggestion_id: Mapped[str | None] = mapped_column(String(64))
    suggested_level: Mapped[int | None] = mapped_column(SmallInteger)
    final_level: Mapped[int] = mapped_column(SmallInteger)
    scale: Mapped[str] = mapped_column(String(8))
    decision: Mapped[str] = mapped_column(String(16))  # confirm | override
    override_reason_code: Mapped[str | None] = mapped_column(String(32))
    model_version: Mapped[str | None] = mapped_column(String(64))
    facts_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB)
    suggested_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    presented_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class QueueTokenRow(_ClinicScoped, Base):
    """One queue token (M1-10). Status: waiting | called | arrived | no_show | closed."""

    __tablename__ = "queue_tokens"
    department: Mapped[str] = mapped_column(String(32))
    token_code: Mapped[str] = mapped_column(String(5))
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("patient_session.id")
    )
    level: Mapped[int] = mapped_column(SmallInteger)
    #: When the patient arrived at the clinic (the session start); the first queue position.
    arrived_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    issued_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    enqueued_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16))
    called_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    room_id: Mapped[str | None] = mapped_column(String(32))
    recall_count: Mapped[int] = mapped_column(SmallInteger, server_default=text("0"))
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class TokenCounter(Base):
    """The last token number per clinic, department and day (M1-10)."""

    __tablename__ = "token_counters"
    clinic_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clinic.id"), primary_key=True
    )
    department: Mapped[str] = mapped_column(String(32), primary_key=True)
    day: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    last_number: Mapped[int] = mapped_column(Integer)


class OutboxEvent(Base):
    """One event, written in the transaction of the change it reports (M1-11)."""

    __tablename__ = "outbox_events"
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True), unique=True)
    event_type: Mapped[str] = mapped_column(String(64))
    schema_version: Mapped[str] = mapped_column(String(32))
    clinic_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clinic.id"))
    session_id: Mapped[str | None] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(200))
    payload: Mapped[dict[str, object]] = mapped_column(JSONB)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()")
    )
    published_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    last_error: Mapped[str | None] = mapped_column(String(500))


class ProcessedEvent(Base):
    """A consumer group handled this event (M1-11): a re-delivery is skipped."""

    __tablename__ = "processed_events"
    consumer_group: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    clinic_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clinic.id"))
    processed_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


__all__ = [
    "Base",
    "BriefingRow",
    "Clinic",
    "Device",
    "EncounterState",
    "FhirWriteback",
    "FhirWritebackStatus",
    "NurseDecisionRow",
    "OutboxEvent",
    "PatientSession",
    "ProcessedEvent",
    "QueueTokenRow",
    "StaffUser",
    "TokenCounter",
]
