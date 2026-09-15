"""Create the complete production schema.

Revision ID: 20260911_0001
Revises: None
"""

from collections.abc import Sequence

from alembic import op

from app.models import Base

revision: str = "20260911_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Enable PostGIS and create all version-one tables and indexes."""
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    Base.metadata.create_all(bind=op.get_bind(), checkfirst=False)


def downgrade() -> None:
    """Drop version-one application tables while retaining shared PostGIS."""
    Base.metadata.drop_all(bind=op.get_bind(), checkfirst=True)
