"""Transactional outbox and consumer idempotency (M1-11).

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-27

outbox_events holds every event, written in the transaction of the change it reports;
the relay publishes unpublished rows in `seq` order and stamps published_at. An idempotency key
is unique per clinic. processed_events records which consumer group handled which event, so a
re-delivered event runs its handler only once. Both are clinic-scoped, as in 0001: ENABLE + FORCE
ROW LEVEL SECURITY and the clinic_isolation policy; the relay reads each clinic in turn.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

GRANTS = {"outbox_events": "SELECT, INSERT, UPDATE", "processed_events": "SELECT, INSERT"}
CURRENT_CLINIC = "NULLIF(current_setting('app.clinic_id', true), '')::uuid"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE outbox_events (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            seq bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
            event_type text NOT NULL,
            schema_version text NOT NULL,
            clinic_id uuid NOT NULL REFERENCES clinic (id),
            session_id text,
            idempotency_key text NOT NULL,
            payload jsonb NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            published_at timestamptz,
            attempts integer NOT NULL DEFAULT 0,
            last_error text CHECK (char_length(last_error) <= 500),
            UNIQUE (clinic_id, idempotency_key)
        );
        CREATE INDEX ix_outbox_events_unpublished ON outbox_events (clinic_id, seq)
            WHERE published_at IS NULL;
        CREATE TABLE processed_events (
            consumer_group text NOT NULL,
            event_id uuid NOT NULL,
            clinic_id uuid NOT NULL REFERENCES clinic (id),
            processed_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (consumer_group, event_id)
        );
        CREATE INDEX ix_processed_events_clinic_id ON processed_events (clinic_id);
        """
    )
    for table, grants in GRANTS.items():
        op.execute(f"GRANT {grants} ON {table} TO careflow_app")
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY clinic_isolation ON {table} "
            f"USING (clinic_id = {CURRENT_CLINIC}) WITH CHECK (clinic_id = {CURRENT_CLINIC})"
        )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS processed_events, outbox_events")
