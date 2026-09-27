"""Doctor briefings (M1-16): one row per session, the briefing JSON and its FHIR document.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-27

Clinic-scoped like 0001/0002: ENABLE + FORCE ROW LEVEL SECURITY and the clinic_isolation policy.
The app may read, insert and update rows, never delete them. A rebuilt briefing replaces the row.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CURRENT_CLINIC = "NULLIF(current_setting('app.clinic_id', true), '')::uuid"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE briefings (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            clinic_id uuid NOT NULL REFERENCES clinic (id),
            session_id uuid NOT NULL UNIQUE REFERENCES patient_session (id),
            briefing jsonb NOT NULL,
            summary_status text NOT NULL CHECK (summary_status IN ('ok', 'dropped', 'disabled')),
            source_missing integer NOT NULL DEFAULT 0 CHECK (source_missing >= 0),
            document_status text CHECK (document_status IN ('ok', 'failed', 'skipped')),
            document_id text CHECK (char_length(document_id) <= 64),
            document_error text CHECK (char_length(document_error) <= 500),
            updated_at timestamptz NOT NULL DEFAULT now(),
            created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ix_briefings_clinic_id ON briefings (clinic_id);
        """
    )
    op.execute("GRANT SELECT, INSERT, UPDATE ON briefings TO careflow_app")
    op.execute("ALTER TABLE briefings ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE briefings FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY clinic_isolation ON briefings "
        f"USING (clinic_id = {CURRENT_CLINIC}) WITH CHECK (clinic_id = {CURRENT_CLINIC})"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS briefings")
