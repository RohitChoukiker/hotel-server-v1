"""Enforce normalized city-name uniqueness within each region.

Revision ID: 20260916_0003
Revises: 20260915_0002
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260916_0003"
down_revision: str | None = "20260915_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX_NAME = "uq_cities_region_normalized_name"


def upgrade() -> None:
    """Reject existing duplicates, then enforce normalized regional identity."""
    duplicate = (
        op.get_bind()
        .execute(
            sa.text(
                """
                SELECT region_id, lower(btrim(name)) AS normalized_name, count(*) AS total
                FROM cities
                GROUP BY region_id, lower(btrim(name))
                HAVING count(*) > 1
                LIMIT 1
                """
            )
        )
        .mappings()
        .first()
    )
    if duplicate is not None:
        raise RuntimeError(
            "Cannot enforce case-insensitive city uniqueness: "
            f"region {duplicate['region_id']} has {duplicate['total']} cities named "
            f"{duplicate['normalized_name']!r} after trimming and lowercasing. "
            "Resolve the duplicate city records before retrying the migration."
        )

    op.create_index(
        INDEX_NAME,
        "cities",
        ["region_id", sa.text("lower(btrim(name))")],
        unique=True,
        if_not_exists=True,
    )


def downgrade() -> None:
    """Remove only the normalized uniqueness index added by this revision."""
    op.drop_index(INDEX_NAME, table_name="cities", if_exists=True)
