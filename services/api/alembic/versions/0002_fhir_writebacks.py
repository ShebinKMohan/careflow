"""FHIR write-back record (M1-09): fhir_writeback_status (one row per session) and fhir_writebacks
(one row per resource written).

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-27

Both tables are clinic-scoped, as in 0001: ENABLE + FORCE ROW LEVEL SECURITY and the
clinic_isolation policy. The app may read, insert and update them, never delete.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("fhir_writeback_status", "fhir_writebacks")
CURRENT_CLINIC = "NULLIF(current_setting('app.clinic_id', true), '')::uuid"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE fhir_writeback_status (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            clinic_id uuid NOT NULL REFERENCES clinic (id),
            session_id uuid NOT NULL UNIQUE REFERENCES patient_session (id),
            status text NOT NULL CHECK (status IN ('ok', 'failed', 'skipped')),
            attempts integer NOT NULL CHECK (attempts >= 0),
            bundle_id text,
            last_error text CHECK (char_length(last_error) <= 500),
            updated_at timestamptz NOT NULL DEFAULT now(),
            created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE TABLE fhir_writebacks (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            clinic_id uuid NOT NULL REFERENCES clinic (id),
            session_id uuid NOT NULL REFERENCES patient_session (id),
            local_id text NOT NULL,
            resource_type text NOT NULL,
            resource_id text NOT NULL,
            version_id text,
            updated_at timestamptz NOT NULL DEFAULT now(),
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (session_id, local_id)
        );
        CREATE INDEX ix_fhir_writeback_status_clinic_id ON fhir_writeback_status (clinic_id);
        CREATE INDEX ix_fhir_writebacks_clinic_id ON fhir_writebacks (clinic_id);
        """
    )
    for table in TABLES:
        op.execute(f"GRANT SELECT, INSERT, UPDATE ON {table} TO careflow_app")
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY clinic_isolation ON {table} "
            f"USING (clinic_id = {CURRENT_CLINIC}) WITH CHECK (clinic_id = {CURRENT_CLINIC})"
        )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS fhir_writebacks, fhir_writeback_status")
