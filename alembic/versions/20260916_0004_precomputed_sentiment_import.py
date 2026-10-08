"""Persist precomputed hotel sentiment aggregates and their provenance.

The first revision creates tables from the ORM metadata, so this migration is
deliberately idempotent when it is applied to a fresh database as well as to a
database that already has revisions 0001-0003.
"""

import unicodedata
from collections import defaultdict
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260916_0004"
down_revision: str | None = "20260916_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip().casefold()
    pieces: list[str] = []
    pending_separator = False
    for character in normalized:
        if character.isalnum():
            if pending_separator and pieces:
                pieces.append("-")
            pieces.append(character)
            pending_separator = False
        else:
            pending_separator = True
    result = "".join(pieces)
    if not result:
        raise RuntimeError(f"Cannot normalize empty taxonomy key from {value!r}")
    return result


def _assert_no_normalized_collisions(kind: str, rows: list[dict[str, object]]) -> None:
    grouped: dict[tuple[object, str], list[str]] = defaultdict(list)
    for row in rows:
        raw_key = str(row["raw_key"])
        normalized = _normalize(raw_key)
        scope = row.get("scope_id") if kind == "attribute" else None
        grouped[(scope, normalized)].append(str(row["id"]))
    collisions = {key: ids for key, ids in grouped.items() if len(ids) > 1}
    if collisions:
        details = "; ".join(
            f"normalized_key={normalized!r}, ids={sorted(ids)!r}"
            for (_, normalized), ids in sorted(collisions.items(), key=str)
        )
        raise RuntimeError(f"{kind.title()} taxonomy normalized-key collision: {details}")


def _inspector() -> sa.Inspector:
    return sa.inspect(op.get_bind())


def _add_column_if_missing(table: str, column: sa.Column[object]) -> None:
    if column.name not in {item["name"] for item in _inspector().get_columns(table)}:
        op.add_column(table, column)


def _create_constraint_if_missing(table: str, name: str, columns: list[str]) -> None:
    names = {item["name"] for item in _inspector().get_unique_constraints(table)}
    if name not in names:
        op.create_unique_constraint(name, table, columns)


def _populate_taxonomy_keys() -> None:
    bind = op.get_bind()
    categories = list(
        bind.execute(
            sa.text("SELECT id::text AS id, code AS raw_key FROM attribute_categories")
        ).mappings()
    )
    attributes = list(
        bind.execute(
            sa.text(
                "SELECT id::text AS id, category_id::text AS scope_id, "
                "slug AS raw_key FROM attributes"
            )
        ).mappings()
    )
    _assert_no_normalized_collisions("category", [dict(row) for row in categories])
    _assert_no_normalized_collisions("attribute", [dict(row) for row in attributes])
    for row in categories:
        bind.execute(
            sa.text("UPDATE attribute_categories SET normalized_key=:key WHERE id=:id"),
            {"key": _normalize(str(row["raw_key"])), "id": row["id"]},
        )
    for row in attributes:
        bind.execute(
            sa.text("UPDATE attributes SET normalized_key=:key WHERE id=:id"),
            {"key": _normalize(str(row["raw_key"])), "id": row["id"]},
        )


def _create_aggregate_table_if_missing() -> None:
    if "hotel_attribute_sentiment_aggregates" in _inspector().get_table_names():
        return
    op.create_table(
        "hotel_attribute_sentiment_aggregates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "hotel_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("hotels.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "attribute_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("attributes.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "source_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("data_sources.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("category_name", sa.String(160), nullable=False),
        sa.Column("raw_attribute_key", sa.String(160), nullable=False),
        sa.Column("sentiment", sa.String(16), nullable=False),
        sa.Column("positive_mentions", sa.Integer(), nullable=False),
        sa.Column("negative_mentions", sa.Integer(), nullable=False),
        sa.Column("total_mentions", sa.Integer(), nullable=False),
        sa.Column("reviews_analyzed", sa.Integer(), nullable=False),
        sa.Column("analysis_mode", sa.String(160), nullable=False),
        sa.Column("analysis_version", sa.String(160), nullable=False),
        sa.Column("review_window", sa.String(160), nullable=False),
        sa.Column("aggregate_score_0_5", sa.Numeric(7, 4), nullable=False),
        sa.Column(
            "scoring_source",
            sa.String(32),
            nullable=False,
            server_default="aggregate_sentiment",
        ),
        sa.Column(
            "metadata", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "hotel_id",
            "attribute_id",
            "source_id",
            "analysis_version",
            "review_window",
            name="uq_sentiment_aggregate_identity",
        ),
        sa.CheckConstraint(
            "sentiment IN ('POSITIVE', 'NEGATIVE', 'NEUTRAL')",
            name="sentiment_value",
        ),
        sa.CheckConstraint(
            "total_mentions >= 0 AND positive_mentions >= 0 AND negative_mentions >= 0",
            name="mention_counts_nonnegative",
        ),
        sa.CheckConstraint(
            "positive_mentions + negative_mentions <= total_mentions",
            name="mention_counts_consistent",
        ),
        sa.CheckConstraint("reviews_analyzed >= 0", name="reviews_analyzed_nonnegative"),
        sa.CheckConstraint(
            "aggregate_score_0_5 >= 0 AND aggregate_score_0_5 <= 5",
            name="aggregate_score_range",
        ),
        sa.CheckConstraint(
            "scoring_source = 'aggregate_sentiment'",
            name="aggregate_scoring_source",
        ),
    )
    op.create_index(
        "ix_sentiment_aggregates_hotel", "hotel_attribute_sentiment_aggregates", ["hotel_id"]
    )
    op.create_index(
        "ix_sentiment_aggregates_attribute",
        "hotel_attribute_sentiment_aggregates",
        ["attribute_id"],
    )
    op.create_index(
        "ix_sentiment_aggregates_source", "hotel_attribute_sentiment_aggregates", ["source_id"]
    )
    op.create_index(
        "ix_sentiment_aggregates_window",
        "hotel_attribute_sentiment_aggregates",
        ["analysis_version", "review_window"],
    )


def upgrade() -> None:
    """Add normalized taxonomy, score provenance, counters, and aggregates."""
    _add_column_if_missing("attribute_categories", sa.Column("normalized_key", sa.String(160)))
    _add_column_if_missing("attributes", sa.Column("normalized_key", sa.String(160)))
    _populate_taxonomy_keys()
    op.alter_column("attribute_categories", "normalized_key", nullable=False)
    op.alter_column("attributes", "normalized_key", nullable=False)
    _create_constraint_if_missing(
        "attribute_categories", "uq_attribute_categories_normalized_key", ["normalized_key"]
    )
    _create_constraint_if_missing(
        "attributes", "uq_attributes_category_normalized_key", ["category_id", "normalized_key"]
    )

    _add_column_if_missing(
        "hotel_attribute_scores",
        sa.Column("scoring_source", sa.String(32), server_default="review_based"),
    )
    _add_column_if_missing("hotel_attribute_scores", sa.Column("analysis_version", sa.String(160)))
    _add_column_if_missing("hotel_attribute_scores", sa.Column("review_window", sa.String(160)))
    _add_column_if_missing(
        "hotel_attribute_scores", sa.Column("imported_at", sa.DateTime(timezone=True))
    )
    op.execute(
        sa.text(
            "UPDATE hotel_attribute_scores SET scoring_source='review_based' "
            "WHERE scoring_source IS NULL"
        )
    )
    op.alter_column("hotel_attribute_scores", "scoring_source", nullable=False)

    counter_names = (
        "hotels_read",
        "hotels_matched",
        "hotels_failed",
        "attributes_seen",
        "attributes_processed",
        "attributes_inserted",
        "attributes_updated",
        "attributes_skipped_zero_mentions",
        "attributes_failed",
        "score_updates_applied",
        "score_updates_skipped_precedence",
        "unknown_hotels",
        "auto_created_attributes",
    )
    existing_job_columns = {item["name"] for item in _inspector().get_columns("import_jobs")}
    for name in counter_names:
        if name not in existing_job_columns:
            op.add_column(
                "import_jobs",
                sa.Column(name, sa.Integer(), nullable=False, server_default="0"),
            )
    _create_aggregate_table_if_missing()


def downgrade() -> None:
    """Refuse to discard imported aggregates or aggregate-derived scores."""
    bind = op.get_bind()
    aggregate_count = bind.execute(
        sa.text("SELECT count(*) FROM hotel_attribute_sentiment_aggregates")
    ).scalar_one()
    score_count = bind.execute(
        sa.text(
            "SELECT count(*) FROM hotel_attribute_scores WHERE scoring_source='aggregate_sentiment'"
        )
    ).scalar_one()
    if aggregate_count or score_count:
        raise RuntimeError(
            "Cannot downgrade precomputed sentiment import while data remains: "
            f"{aggregate_count} aggregate rows and {score_count} aggregate-derived scores"
        )
    if "hotel_attribute_sentiment_aggregates" in _inspector().get_table_names():
        op.drop_table("hotel_attribute_sentiment_aggregates")
    for name in (
        "auto_created_attributes",
        "unknown_hotels",
        "score_updates_skipped_precedence",
        "score_updates_applied",
        "attributes_failed",
        "attributes_skipped_zero_mentions",
        "attributes_updated",
        "attributes_inserted",
        "attributes_processed",
        "attributes_seen",
        "hotels_failed",
        "hotels_matched",
        "hotels_read",
    ):
        if name in {item["name"] for item in _inspector().get_columns("import_jobs")}:
            op.drop_column("import_jobs", name)
    for table, name in (
        ("hotel_attribute_scores", "imported_at"),
        ("hotel_attribute_scores", "review_window"),
        ("hotel_attribute_scores", "analysis_version"),
        ("hotel_attribute_scores", "scoring_source"),
        ("attributes", "normalized_key"),
        ("attribute_categories", "normalized_key"),
    ):
        if name in {item["name"] for item in _inspector().get_columns(table)}:
            op.drop_column(table, name)
