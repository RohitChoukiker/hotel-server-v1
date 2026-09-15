"""Tests for the fixed deterministic scoring rules."""

import math
import unittest

from app.domain.scoring import (
    PopulationStats,
    calculate_contribution,
    normalize_rating,
    normalize_value,
    population_statistics,
    relative_score,
    score_100,
)
from app.enums import Sentiment
from app.exceptions import ScoringError


class ScoringTests(unittest.TestCase):
    """Verify rating conversion, contribution, clamping and normalization."""

    def test_positive_contribution_adds_one(self) -> None:
        self.assertEqual(calculate_contribution(4.0, Sentiment.POSITIVE), 5.0)

    def test_negative_contribution_subtracts_one(self) -> None:
        self.assertEqual(calculate_contribution(4.0, Sentiment.NEGATIVE), 3.0)

    def test_neutral_contribution_is_unchanged(self) -> None:
        self.assertEqual(calculate_contribution(3.5, Sentiment.NEUTRAL), 3.5)

    def test_positive_contribution_clamps_upper_bound(self) -> None:
        self.assertEqual(calculate_contribution(5.0, Sentiment.POSITIVE), 5.0)

    def test_negative_contribution_clamps_lower_bound(self) -> None:
        self.assertEqual(calculate_contribution(0.0, Sentiment.NEGATIVE), 0.0)

    def test_normalize_ten_point_rating(self) -> None:
        self.assertEqual(normalize_rating(8.0, 10.0), 4.0)

    def test_normalize_hundred_point_rating(self) -> None:
        self.assertAlmostEqual(normalize_rating(72.0, 100.0), 3.6)

    def test_normalize_rating_clamps(self) -> None:
        self.assertEqual(normalize_rating(120.0, 100.0), 5.0)
        self.assertEqual(normalize_rating(-5.0, 100.0), 0.0)

    def test_invalid_rating_scale_raises(self) -> None:
        with self.assertRaises(ScoringError):
            normalize_rating(4.0, 0.0)

    def test_score_100(self) -> None:
        self.assertEqual(score_100(4.25), 85.0)
        self.assertEqual(score_100(9.0), 100.0)
        self.assertEqual(score_100(-1.0), 0.0)

    def test_population_statistics(self) -> None:
        stats = population_statistics([1.0, 2.0, 3.0, 4.0, 5.0])
        self.assertEqual(stats.mean, 3.0)
        self.assertEqual(stats.size, 5)
        self.assertAlmostEqual(stats.standard_deviation, math.sqrt(2.0))

    def test_empty_population_raises(self) -> None:
        with self.assertRaises(ScoringError):
            population_statistics([])

    def test_zero_variance_population_centers_at_three(self) -> None:
        self.assertEqual(normalize_value(4.0, PopulationStats(4.0, 0.0, 3)), (0.0, 3.0))

    def test_relative_score_is_monotonic_and_bounded(self) -> None:
        values = [relative_score(item) for item in (-100.0, -1.0, 0.0, 1.0, 100.0)]
        self.assertEqual(values, sorted(values))
        self.assertGreaterEqual(values[0], 1.0)
        self.assertLessEqual(values[-1], 5.0)

    def test_z_score(self) -> None:
        z_score, relative = normalize_value(5.0, PopulationStats(3.0, 1.0, 10))
        self.assertEqual(z_score, 2.0)
        self.assertGreater(relative, 4.0)


if __name__ == "__main__":
    unittest.main()
