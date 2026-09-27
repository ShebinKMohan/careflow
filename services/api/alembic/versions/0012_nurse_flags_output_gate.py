"""A fourth nurse_flags category (M2-09, SHE-306): `output_gate`.

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-27

The ask-the-clinic agent's output gate refuses an answer that would have reached the patient with
medical content (a dose, a diagnosis, advice, or a medical term the sources do not contain), and
flags the question to the nurse like any refused medical question. `category` says which layer
decided: the lexicon, the model, a failure (fail safe), or now the output gate.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE nurse_flags DROP CONSTRAINT nurse_flags_category_check")
    op.execute(
        "ALTER TABLE nurse_flags ADD CONSTRAINT nurse_flags_category_check "
        "CHECK (category IN ('lexicon', 'model', 'fail_safe', 'output_gate'))"
    )


def downgrade() -> None:
    # Kept, as fail-safe refusals: a downgrade never drops what a nurse still needs to see.
    op.execute("UPDATE nurse_flags SET category = 'fail_safe' WHERE category = 'output_gate'")
    op.execute("ALTER TABLE nurse_flags DROP CONSTRAINT nurse_flags_category_check")
    op.execute(
        "ALTER TABLE nurse_flags ADD CONSTRAINT nurse_flags_category_check "
        "CHECK (category IN ('lexicon', 'model', 'fail_safe'))"
    )
