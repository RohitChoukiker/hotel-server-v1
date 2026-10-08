"""Propagate source-provided sentiment aggregates into hotel scores."""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.scoring import score_100
from app.domain.sentiment import aggregate_support_confidence
from app.enums import ScoringSource
from app.exceptions import ScoringError
from app.models import HotelAttributeSentimentAggregate
from app.repositories.catalog import AttributeRepository


@dataclass(frozen=True, slots=True)
class ScorePropagationResult:
    """Counts returned by conditional aggregate score propagation."""

    applied: int
    skipped_precedence: int


class AggregateScoringService:
    """Write aggregate scores without invoking review extraction."""

    def __init__(self, session: AsyncSession) -> None:
        self._attributes = AttributeRepository(session)

    async def propagate(
        self, aggregates: list[HotelAttributeSentimentAggregate]
    ) -> ScorePropagationResult:
        algorithm = await self._attributes.active_algorithm()
        if algorithm is None:
            raise ScoringError("No active algorithm version")
        now = datetime.now(UTC)
        rows = [
            {
                "hotel_id": aggregate.hotel_id,
                "attribute_id": aggregate.attribute_id,
                "algorithm_version_id": algorithm.id,
                "mention_count": aggregate.total_mentions,
                "positive_mentions": aggregate.positive_mentions,
                "negative_mentions": aggregate.negative_mentions,
                "neutral_mentions": (
                    aggregate.total_mentions
                    - aggregate.positive_mentions
                    - aggregate.negative_mentions
                ),
                "raw_score": aggregate.aggregate_score_0_5,
                "score_5": aggregate.aggregate_score_0_5,
                "score_100": score_100(float(aggregate.aggregate_score_0_5)),
                "mean": None,
                "standard_deviation": None,
                "z_score": None,
                "relative_score_5": None,
                "confidence_score": aggregate_support_confidence(aggregate.total_mentions),
                "scoring_source": ScoringSource.AGGREGATE_SENTIMENT.value,
                "analysis_version": aggregate.analysis_version,
                "review_window": aggregate.review_window,
                "imported_at": now,
                "calculated_at": now,
            }
            for aggregate in aggregates
        ]
        applied = await self._attributes.upsert_aggregate_hotel_scores(rows)
        return ScorePropagationResult(applied, len(rows) - applied)
