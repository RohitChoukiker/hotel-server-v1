"""Allow hotels without source coordinates.

Revision ID: 20260915_0002
Revises: 20260911_0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from geoalchemy2 import Geography

from alembic import op

revision: str = "20260915_0002"
down_revision: str | None = "20260911_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Allow scalar and spatial hotel coordinates to be null."""
    op.alter_column(
        "hotels",
        "latitude",
        existing_type=sa.Numeric(precision=9, scale=6),
        nullable=True,
    )
    op.alter_column(
        "hotels",
        "longitude",
        existing_type=sa.Numeric(precision=9, scale=6),
        nullable=True,
    )
    op.alter_column(
        "hotels",
        "geo_location",
        existing_type=Geography(geometry_type="POINT", srid=4326),
        nullable=True,
    )


def downgrade() -> None:
    """Restore required hotel coordinates when no null rows exist."""
    op.alter_column(
        "hotels",
        "geo_location",
        existing_type=Geography(geometry_type="POINT", srid=4326),
        nullable=False,
    )
    op.alter_column(
        "hotels",
        "longitude",
        existing_type=sa.Numeric(precision=9, scale=6),
        nullable=False,
    )
    op.alter_column(
        "hotels",
        "latitude",
        existing_type=sa.Numeric(precision=9, scale=6),
        nullable=False,
    )
