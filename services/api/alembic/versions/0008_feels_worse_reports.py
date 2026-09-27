"""Reports of "I feel worse" (M2-07): one append-only row per press on the patient's phone.

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-27

Each press is kept: repeats are never dropped. `answer_codes` holds the danger answers as codes;
`note` is the patient's optional words (at most 280 characters) for the nurse's worklist only,
never put in an event. Clinic-scoped like 0001: ENABLE + FORCE ROW LEVEL SECURITY and the
clinic_isolation policy. The app may read and insert, never change or delete a report.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CURRENT_CLINIC = "NULLIF(current_setting('app.clinic_id', true), '')::uuid"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE feels_worse_reports (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            clinic_id uuid NOT NULL REFERENCES clinic (id),
            session_id uuid NOT NULL REFERENCES patient_session (id),
            flag_id text NOT NULL UNIQUE,
            press_number integer NOT NULL CHECK (press_number >= 1),
            answer_codes text[] NOT NULL,
            note text CHECK (char_length(note) <= 280),
            reported_at timestamptz NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ix_feels_worse_reports_clinic_id ON feels_worse_reports (clinic_id);
        CREATE INDEX ix_feels_worse_reports_session
            ON feels_worse_reports (session_id, reported_at DESC);
        """
    )
    op.execute("GRANT SELECT, INSERT ON feels_worse_reports TO careflow_app")
    op.execute("ALTER TABLE feels_worse_reports ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE feels_worse_reports FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY clinic_isolation ON feels_worse_reports "
        f"USING (clinic_id = {CURRENT_CLINIC}) WITH CHECK (clinic_id = {CURRENT_CLINIC})"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS feels_worse_reports")
