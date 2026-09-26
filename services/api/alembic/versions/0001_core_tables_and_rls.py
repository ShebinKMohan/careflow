"""Core tables (clinic, device, staff_user, patient_session, encounter_state) and RLS.

Revision ID: 0001
Revises:
Create Date: 2026-09-26

Every clinic-scoped table: ENABLE + FORCE ROW LEVEL SECURITY and the clinic_isolation policy,
which fails closed when app.clinic_id is unset (NULLIF turns '' into NULL, which matches nothing).
Two SELECT-only exceptions exist for authentication before a clinic is known (ADR-014).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CLINIC_SCOPED = ("device", "staff_user", "patient_session", "encounter_state")
CURRENT_CLINIC = "NULLIF(current_setting('app.clinic_id', true), '')::uuid"


def upgrade() -> None:
    op.execute("GRANT USAGE ON SCHEMA public TO careflow_app")
    op.execute(
        """
        CREATE TABLE clinic (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            name text NOT NULL UNIQUE,
            timezone text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE TABLE device (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            clinic_id uuid NOT NULL REFERENCES clinic (id),
            kind text NOT NULL CHECK (kind IN ('kiosk', 'board', 'integration')),
            label text NOT NULL,
            secret_hash text NOT NULL,
            is_active boolean NOT NULL DEFAULT true,
            created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE TABLE staff_user (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            clinic_id uuid NOT NULL REFERENCES clinic (id),
            email text NOT NULL UNIQUE CHECK (email = lower(email)),
            password_hash text NOT NULL,
            role text NOT NULL CHECK (role IN ('nurse', 'doctor', 'admin')),
            is_active boolean NOT NULL DEFAULT true,
            created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE TABLE patient_session (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            clinic_id uuid NOT NULL REFERENCES clinic (id),
            device_id uuid REFERENCES device (id),
            fhir_patient_id text,
            language text NOT NULL CHECK (language IN ('en', 'ar')),
            status text NOT NULL DEFAULT 'intake',
            started_at timestamptz NOT NULL DEFAULT now(),
            expires_at timestamptz NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE TABLE encounter_state (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            clinic_id uuid NOT NULL REFERENCES clinic (id),
            session_id uuid NOT NULL UNIQUE REFERENCES patient_session (id),
            graph_thread_id text NOT NULL,
            stage text NOT NULL,
            updated_at timestamptz NOT NULL DEFAULT now(),
            created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ix_device_clinic_id ON device (clinic_id);
        CREATE INDEX ix_staff_user_clinic_id ON staff_user (clinic_id);
        CREATE INDEX ix_patient_session_clinic_id ON patient_session (clinic_id);
        CREATE INDEX ix_encounter_state_clinic_id ON encounter_state (clinic_id);
        """
    )
    # The app reads clinics but never creates them; clinic-scoped tables are fully usable under RLS.
    op.execute("GRANT SELECT ON clinic TO careflow_app")
    for table in CLINIC_SCOPED:
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO careflow_app")
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY clinic_isolation ON {table} "
            f"USING (clinic_id = {CURRENT_CLINIC}) WITH CHECK (clinic_id = {CURRENT_CLINIC})"
        )
    # Authentication before a clinic is known: the transaction names the one row it may read.
    op.execute(
        "CREATE POLICY login_lookup ON staff_user FOR SELECT "
        "USING (email = NULLIF(current_setting('app.auth_email', true), ''))"
    )
    op.execute(
        "CREATE POLICY device_token_lookup ON device FOR SELECT "
        "USING (id = NULLIF(current_setting('app.auth_device_id', true), '')::uuid)"
    )


def downgrade() -> None:
    # Schema USAGE for careflow_app predates this migration (infra/postgres/init); keep it.
    op.execute(
        "DROP TABLE IF EXISTS encounter_state, patient_session, staff_user, device, clinic CASCADE"
    )
