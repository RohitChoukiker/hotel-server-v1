"""Persist explicit onboarding skips separately from completion."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260927_0007"
down_revision: str | None = "20260926_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the account-level marker used for access and status reporting."""
    columns = {item["name"] for item in sa.inspect(op.get_bind()).get_columns("users")}
    if "onboarding_skipped_at" not in columns:
        op.add_column(
            "users",
            sa.Column("onboarding_skipped_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    """Remove the explicit skip marker."""
    columns = {item["name"] for item in sa.inspect(op.get_bind()).get_columns("users")}
    if "onboarding_skipped_at" in columns:
        op.drop_column("users", "onboarding_skipped_at")
