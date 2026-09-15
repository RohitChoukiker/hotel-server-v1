"""Pure approved contribution and bell-curve scoring rules."""

import math
import statistics
from dataclasses import dataclass

from app.enums import Sentiment
from app.exceptions import ScoringError


def normalize_rating(source_rating: float, source_scale: float) -> float:
    """Convert an upstream rating scale to 0–5 while preserving raw values elsewhere."""
    if source_scale <= 0:
        raise ScoringError("Rating scale must be positive")
    return max(0.0, min(5.0, source_rating / source_scale * 5.0))


def calculate_contribution(rating_5: float, sentiment: Sentiment) -> float:
    """Apply the fixed +1/-1 business rule and clamp to 0–5."""
    adjustment = {
        Sentiment.POSITIVE: 1.0,
        Sentiment.NEGATIVE: -1.0,
        Sentiment.NEUTRAL: 0.0,
    }[sentiment]
    return max(0.0, min(5.0, rating_5 + adjustment))


def score_100(score_5_value: float) -> float:
    """Convert a 0–5 score to 0–100."""
    return max(0.0, min(100.0, score_5_value * 20.0))


def relative_score(z_score: float) -> float:
    """Map a z-score through the standard-normal CDF onto a relative 1–5 scale."""
    cdf = 0.5 * (1.0 + math.erf(z_score / math.sqrt(2.0)))
    return max(1.0, min(5.0, 1.0 + 4.0 * cdf))


@dataclass(frozen=True, slots=True)
class PopulationStats:
    """Bell-curve population statistics."""

    mean: float
    standard_deviation: float
    size: int


def population_statistics(values: list[float]) -> PopulationStats:
    """Calculate mean and population standard deviation."""
    if not values:
        raise ScoringError("Cannot normalize an empty population")
    return PopulationStats(
        mean=statistics.fmean(values),
        standard_deviation=statistics.pstdev(values),
        size=len(values),
    )


def normalize_value(value: float, stats: PopulationStats) -> tuple[float, float]:
    """Return z-score and relative score; equal populations center at 3."""
    z_score = (
        0.0
        if stats.standard_deviation == 0
        else (value - stats.mean) / stats.standard_deviation
    )
    return z_score, relative_score(z_score)

