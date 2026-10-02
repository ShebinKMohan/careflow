"""Internal synthetic FHIR persistence for the explicitly enabled hosted demo.

Revision ID: 0013
Revises: 0012
"""

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE demo_fhir_resource (kind text NOT NULL, id text NOT NULL, "
        "version integer NOT NULL, body jsonb NOT NULL, PRIMARY KEY(kind,id))"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON demo_fhir_resource TO careflow_app")


def downgrade() -> None:
    op.execute("DROP TABLE demo_fhir_resource")
