"""ORM models. The schema itself (tables, grants, RLS) is defined by the Alembic migrations."""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID
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


__all__ = [
    "Base",
    "Clinic",
    "Device",
    "EncounterState",
    "FhirWriteback",
    "FhirWritebackStatus",
    "PatientSession",
    "StaffUser",
]
