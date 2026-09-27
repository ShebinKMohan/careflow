"""Consent records (M2-05): the kiosk's consent choices, one append-only row per POST.

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-27

consent_records holds booleans, the consent text's version and the session language: never free
text. The latest row of a session wins (get_consent). Clinic-scoped like 0001: ENABLE + FORCE ROW
LEVEL SECURITY and the clinic_isolation policy. The app may read and insert, never change or
delete a record.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CURRENT_CLINIC = "NULLIF(current_setting('app.clinic_id', true), '')::uuid"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE consent_records (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            clinic_id uuid NOT NULL REFERENCES clinic (id),
            session_id uuid NOT NULL REFERENCES patient_session (id),
            camera boolean NOT NULL,
            voice boolean NOT NULL,
            data_use boolean NOT NULL,
            consent_version text NOT NULL CHECK (char_length(consent_version) <= 32),
            language text NOT NULL CHECK (language IN ('en', 'ar')),
            recorded_at timestamptz NOT NULL DEFAULT now(),
            created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ix_consent_records_clinic_id ON consent_records (clinic_id);
        CREATE INDEX ix_consent_records_session_recorded
            ON consent_records (session_id, recorded_at DESC);
        """
    )
    op.execute("GRANT SELECT, INSERT ON consent_records TO careflow_app")
    op.execute("ALTER TABLE consent_records ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE consent_records FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY clinic_isolation ON consent_records "
        f"USING (clinic_id = {CURRENT_CLINIC}) WITH CHECK (clinic_id = {CURRENT_CLINIC})"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS consent_records")
