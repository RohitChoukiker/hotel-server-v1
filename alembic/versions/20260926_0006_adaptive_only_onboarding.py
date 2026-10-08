"""Make new onboarding sessions independent of legacy static question sets."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260926_0006"
down_revision: str | None = "20260925_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Allow adaptive sessions to retain no legacy question-set reference."""
    op.alter_column(
        "onboarding_sessions",
        "question_set_id",
        existing_type=sa.UUID(),
        nullable=True,
    )
    op.alter_column(
        "onboarding_sessions",
        "mode",
        existing_type=sa.String(length=16),
        server_default="ADAPTIVE",
    )


def downgrade() -> None:
    """Restore the legacy default; existing adaptive rows must be migrated first."""
    op.alter_column(
        "onboarding_sessions",
        "mode",
        existing_type=sa.String(length=16),
        server_default="STATIC",
    )
    op.alter_column(
        "onboarding_sessions",
        "question_set_id",
        existing_type=sa.UUID(),
        nullable=False,
    )
