"""Intake sessions behind the API (M1-12): nurse_decisions, and the session's display and activity.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-27

- patient_session gains patient_display (initials only, e.g. "M. S.") and last_activity_at. The
  session row is the intake session: its id is the graph's thread id, device_id the kiosk,
  fhir_patient_id the EHR patient and status the patient-facing state.
- nurse_decisions: one row per nurse decision (confirm or override), for the override dataset
  (M5-02) and the automation-bias dashboard (M5-03). facts_snapshot holds coded values only, never
  free text.

Clinic-scoped like 0001: ENABLE + FORCE ROW LEVEL SECURITY and the clinic_isolation policy. The app
may read and insert decisions, never change or delete them.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CURRENT_CLINIC = "NULLIF(current_setting('app.clinic_id', true), '')::uuid"


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE patient_session
            ADD COLUMN patient_display text CHECK (char_length(patient_display) <= 8),
            ADD COLUMN last_activity_at timestamptz NOT NULL DEFAULT now();
        CREATE TABLE nurse_decisions (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            clinic_id uuid NOT NULL REFERENCES clinic (id),
            session_id uuid NOT NULL REFERENCES patient_session (id),
            decision_id text NOT NULL UNIQUE,
            nurse_id text NOT NULL,
            suggestion_id text,
            suggested_level smallint CHECK (suggested_level BETWEEN 1 AND 5),
            final_level smallint NOT NULL CHECK (final_level BETWEEN 1 AND 5),
            scale text NOT NULL,
            decision text NOT NULL CHECK (decision IN ('confirm', 'override')),
            override_reason_code text,
            model_version text,
            facts_snapshot jsonb NOT NULL,
            suggested_at timestamptz,
            decided_at timestamptz NOT NULL,
            presented_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ix_nurse_decisions_clinic_id ON nurse_decisions (clinic_id);
        CREATE INDEX ix_nurse_decisions_session_id ON nurse_decisions (session_id);
        """
    )
    op.execute("GRANT SELECT, INSERT ON nurse_decisions TO careflow_app")
    op.execute("ALTER TABLE nurse_decisions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE nurse_decisions FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY clinic_isolation ON nurse_decisions "
        f"USING (clinic_id = {CURRENT_CLINIC}) WITH CHECK (clinic_id = {CURRENT_CLINIC})"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS nurse_decisions")
    op.execute(
        "ALTER TABLE patient_session DROP COLUMN IF EXISTS patient_display, "
        "DROP COLUMN IF EXISTS last_activity_at"
    )
