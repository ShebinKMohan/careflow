"""Medical questions refused on the phone (M2-09): nurse_flags, one row per refusal.

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-27

The ask-the-clinic agent never answers a medical question (safety rule 2). It gives the fixed
refusal and flags the question to the nurse: this row keeps the question's words and language for
the nurse's worklist, and `category` says which classifier layer decided (lexicon, model or
fail_safe). The `nurse.flag` and `rag.refused` events never carry the words. The rows are deleted
when the session closes or expires. Clinic-scoped like 0001: ENABLE + FORCE ROW LEVEL SECURITY and
the clinic_isolation policy. The app may read, insert and delete, never change a flag.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CURRENT_CLINIC = "NULLIF(current_setting('app.clinic_id', true), '')::uuid"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE nurse_flags (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            clinic_id uuid NOT NULL REFERENCES clinic (id),
            session_id uuid NOT NULL REFERENCES patient_session (id),
            flag_id text NOT NULL UNIQUE,
            reason text NOT NULL CHECK (reason IN ('medical_question')),
            question_text text NOT NULL
                CHECK (char_length(question_text) BETWEEN 1 AND 500),
            lang text NOT NULL CHECK (lang IN ('en', 'ar')),
            category text NOT NULL CHECK (category IN ('lexicon', 'model', 'fail_safe')),
            raised_at timestamptz NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ix_nurse_flags_clinic_id ON nurse_flags (clinic_id);
        CREATE INDEX ix_nurse_flags_session ON nurse_flags (session_id, raised_at DESC);
        """
    )
    op.execute("GRANT SELECT, INSERT, DELETE ON nurse_flags TO careflow_app")
    op.execute("ALTER TABLE nurse_flags ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE nurse_flags FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY clinic_isolation ON nurse_flags "
        f"USING (clinic_id = {CURRENT_CLINIC}) WITH CHECK (clinic_id = {CURRENT_CLINIC})"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS nurse_flags")
