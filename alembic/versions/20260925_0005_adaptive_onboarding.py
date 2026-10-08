"""Add persisted adaptive onboarding questions and session state."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260925_0005"
down_revision: str | None = "20260916_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _columns(table: str) -> set[str]:
    return {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table)}


def _add_column(table: str, column: sa.Column[object]) -> None:
    if column.name not in _columns(table):
        op.add_column(table, column)


def _constraints(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {
        item["name"]
        for item in inspector.get_check_constraints(table)
        if item.get("name")
    }


def upgrade() -> None:
    """Preserve static onboarding while adding adaptive persistence."""
    _add_column(
        "onboarding_sessions",
        sa.Column("mode", sa.String(16), nullable=False, server_default="STATIC"),
    )
    _add_column(
        "onboarding_sessions",
        sa.Column("generation_state", sa.String(24), nullable=False, server_default="IDLE"),
    )
    _add_column("onboarding_sessions", sa.Column("ai_model", sa.String(160)))
    _add_column("onboarding_sessions", sa.Column("prompt_version", sa.String(80)))
    _add_column("onboarding_sessions", sa.Column("completion_reason", sa.String(80)))
    _add_column("onboarding_sessions", sa.Column("profile_summary", sa.Text()))
    _add_column("onboarding_sessions", sa.Column("profile_confidence", sa.Numeric(5, 4)))
    _add_column("onboarding_sessions", sa.Column("profile_payload", postgresql.JSONB()))
    constraints = _constraints("onboarding_sessions")
    if "onboarding_session_mode" not in constraints:
        op.create_check_constraint(
            "onboarding_session_mode",
            "onboarding_sessions",
            "mode IN ('STATIC', 'ADAPTIVE')",
        )
    if "onboarding_generation_state" not in constraints:
        op.create_check_constraint(
            "onboarding_generation_state",
            "onboarding_sessions",
            "generation_state IN ('IDLE', 'GENERATING', 'FAILED', 'PROFILE_GENERATING')",
        )

    if "onboarding_session_questions" not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table(
            "onboarding_session_questions",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "session_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("onboarding_sessions.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("position", sa.Integer(), nullable=False),
            sa.Column("prompt", sa.Text(), nullable=False),
            sa.Column("helper_text", sa.Text()),
            sa.Column("answer_type", sa.String(32), nullable=False),
            sa.Column("options", postgresql.JSONB()),
            sa.Column("scale_min", sa.Numeric(7, 3)),
            sa.Column("scale_max", sa.Numeric(7, 3)),
            sa.Column("scale_labels", postgresql.JSONB()),
            sa.Column("is_required", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("question_kind", sa.String(24), nullable=False, server_default="standard"),
            sa.Column(
                "semantic_dimensions", postgresql.JSONB(), nullable=False, server_default="[]"
            ),
            sa.Column("provider", sa.String(32), nullable=False),
            sa.Column("ai_model", sa.String(160)),
            sa.Column("prompt_version", sa.String(80)),
            sa.Column(
                "generation_metadata",
                postgresql.JSONB(),
                nullable=False,
                server_default=sa.text("'{}'::jsonb"),
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.UniqueConstraint("session_id", "position"),
        )
        op.create_index(
            "ix_onboarding_session_questions_session",
            "onboarding_session_questions",
            ["session_id", "position"],
        )

    _add_column(
        "onboarding_answers",
        sa.Column(
            "session_question_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("onboarding_session_questions.id", ondelete="CASCADE"),
        ),
    )
    if "question_id" in _columns("onboarding_answers"):
        op.alter_column("onboarding_answers", "question_id", nullable=True)
    if "ix_onboarding_answers_session_question" not in {
        item["name"] for item in sa.inspect(op.get_bind()).get_indexes("onboarding_answers")
    }:
        op.create_index(
            "ix_onboarding_answers_session_question",
            "onboarding_answers",
            ["session_id", "session_question_id"],
        )
    unique_names = {
        item["name"]
        for item in sa.inspect(op.get_bind()).get_unique_constraints("onboarding_answers")
    }
    if "uq_onboarding_answers_session_question" not in unique_names:
        op.create_unique_constraint(
            "uq_onboarding_answers_session_question",
            "onboarding_answers",
            ["session_id", "session_question_id"],
        )


def downgrade() -> None:
    """Remove adaptive-only persistence while retaining static onboarding history."""
    op.drop_index("ix_onboarding_answers_session_question", table_name="onboarding_answers")
    op.drop_constraint(
        "uq_onboarding_answers_session_question", "onboarding_answers", type_="unique"
    )
    op.drop_column("onboarding_answers", "session_question_id")
    op.alter_column("onboarding_answers", "question_id", nullable=False)
    op.drop_index(
        "ix_onboarding_session_questions_session", table_name="onboarding_session_questions"
    )
    op.drop_table("onboarding_session_questions")
    op.drop_constraint("onboarding_generation_state", "onboarding_sessions", type_="check")
    op.drop_constraint("onboarding_session_mode", "onboarding_sessions", type_="check")
    for column in (
        "profile_payload",
        "profile_confidence",
        "profile_summary",
        "completion_reason",
        "prompt_version",
        "ai_model",
        "generation_state",
        "mode",
    ):
        op.drop_column("onboarding_sessions", column)
