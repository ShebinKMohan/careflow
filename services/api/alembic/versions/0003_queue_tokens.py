"""Queue tokens and per-day token counters (M1-10).

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-27

queue_tokens keeps one row per token; at most one active token (waiting, called or arrived) per
session, and an active token code is unique in its clinic and department. token_counters numbers
tokens per clinic, department and day (allocated with SELECT ... FOR UPDATE). Both tables are
clinic-scoped, as in 0001: ENABLE + FORCE ROW LEVEL SECURITY and the clinic_isolation policy. The
app may read, insert and update them, never delete.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("queue_tokens", "token_counters")
CURRENT_CLINIC = "NULLIF(current_setting('app.clinic_id', true), '')::uuid"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE queue_tokens (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            clinic_id uuid NOT NULL REFERENCES clinic (id),
            department text NOT NULL,
            token_code text NOT NULL CHECK (token_code ~ '^[A-Z]-[0-9]{3}$'),
            session_id uuid NOT NULL REFERENCES patient_session (id),
            level smallint NOT NULL CHECK (level BETWEEN 1 AND 5),
            arrived_at timestamptz NOT NULL,
            issued_at timestamptz NOT NULL,
            enqueued_at timestamptz NOT NULL,
            status text NOT NULL
                CHECK (status IN ('waiting', 'called', 'arrived', 'no_show', 'closed')),
            called_at timestamptz,
            room_id text,
            recall_count smallint NOT NULL DEFAULT 0 CHECK (recall_count IN (0, 1)),
            updated_at timestamptz NOT NULL DEFAULT now(),
            created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE UNIQUE INDEX uq_queue_tokens_active_code
            ON queue_tokens (clinic_id, department, token_code)
            WHERE status IN ('waiting', 'called', 'arrived');
        CREATE UNIQUE INDEX uq_queue_tokens_active_session ON queue_tokens (session_id)
            WHERE status IN ('waiting', 'called', 'arrived');
        CREATE INDEX ix_queue_tokens_clinic_id ON queue_tokens (clinic_id);
        CREATE INDEX ix_queue_tokens_department_status
            ON queue_tokens (clinic_id, department, status);
        CREATE TABLE token_counters (
            clinic_id uuid NOT NULL REFERENCES clinic (id),
            department text NOT NULL,
            day date NOT NULL,
            last_number integer NOT NULL CHECK (last_number BETWEEN 0 AND 999),
            PRIMARY KEY (clinic_id, department, day)
        );
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
    op.execute("DROP TABLE IF EXISTS queue_tokens, token_counters")
