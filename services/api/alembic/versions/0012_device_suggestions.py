"""On-device urgency suggestions (M3-02): device_suggestions, one row per kiosk post.

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-27

The kiosk runs the fine-tuned Laya model (int8 ONNX) and posts its five probabilities. Every post
is kept for analysis, whether or not it became the session's suggestion (`applied`): the model
family and version, the SHA-256 of the input it read, the probabilities, the derived level and
confidence, the reason keys and the measured latency. No free text. Clinic-scoped like 0001:
ENABLE + FORCE ROW LEVEL SECURITY and the clinic_isolation policy. The app may read and insert.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CURRENT_CLINIC = "NULLIF(current_setting('app.clinic_id', true), '')::uuid"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE device_suggestions (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            clinic_id uuid NOT NULL REFERENCES clinic (id),
            session_id uuid NOT NULL REFERENCES patient_session (id),
            suggestion_id text NOT NULL,
            model_family text NOT NULL,
            model_version text NOT NULL,
            facts_sha256 text NOT NULL CHECK (facts_sha256 ~ '^[0-9a-f]{64}$'),
            probabilities double precision[] NOT NULL CHECK (cardinality(probabilities) = 5),
            level smallint NOT NULL CHECK (level BETWEEN 1 AND 5),
            confidence double precision NOT NULL CHECK (confidence BETWEEN 0 AND 1),
            reason_keys text[] NOT NULL,
            latency_ms integer NOT NULL CHECK (latency_ms >= 0),
            applied boolean NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ix_device_suggestions_clinic_id ON device_suggestions (clinic_id);
        CREATE INDEX ix_device_suggestions_session ON device_suggestions (session_id, created_at);
        """
    )
    op.execute("GRANT SELECT, INSERT ON device_suggestions TO careflow_app")
    op.execute("ALTER TABLE device_suggestions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE device_suggestions FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY clinic_isolation ON device_suggestions "
        f"USING (clinic_id = {CURRENT_CLINIC}) WITH CHECK (clinic_id = {CURRENT_CLINIC})"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS device_suggestions")
