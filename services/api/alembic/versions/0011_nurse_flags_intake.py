"""Medical questions asked during the intake (M2-10): a second nurse_flags reason.

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-27

The intake's intent router flags a medical question the patient asks mid-intake, on the kiosk or
the phone, with reason `medical_question_intake`, beside the phone assistant's
`medical_question` (M2-09). The table is otherwise unchanged.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE nurse_flags DROP CONSTRAINT nurse_flags_reason_check")
    op.execute(
        "ALTER TABLE nurse_flags ADD CONSTRAINT nurse_flags_reason_check "
        "CHECK (reason IN ('medical_question', 'medical_question_intake'))"
    )


def downgrade() -> None:
    # Kept, as phone questions: a downgrade never drops what a nurse still needs to see.
    op.execute(
        "UPDATE nurse_flags SET reason = 'medical_question' "
        "WHERE reason = 'medical_question_intake'"
    )
    op.execute("ALTER TABLE nurse_flags DROP CONSTRAINT nurse_flags_reason_check")
    op.execute(
        "ALTER TABLE nurse_flags ADD CONSTRAINT nurse_flags_reason_check "
        "CHECK (reason IN ('medical_question'))"
    )
