"""Tests for live deterministic personalized ranking."""

import unittest
import uuid

from app.domain.ranking import EffectivePreference, rank_hotels
from app.exceptions import ScoringError


class RankingTests(unittest.TestCase):
    """Verify weighting, precedence-ready inputs, constraints and explanations."""

    def setUp(self) -> None:
        self.cleanliness = uuid.UUID("11111111-1111-1111-1111-111111111111")
        self.wifi = uuid.UUID("22222222-2222-2222-2222-222222222222")
        self.hotel_a = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
        self.hotel_b = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
        self.rows = [
            self._row(self.hotel_a, "A", self.cleanliness, "Cleanliness", 4.8),
            self._row(self.hotel_a, "A", self.wifi, "WiFi", 3.0),
            self._row(self.hotel_b, "B", self.cleanliness, "Cleanliness", 4.0),
            self._row(self.hotel_b, "B", self.wifi, "WiFi", 4.7),
        ]

    @staticmethod
    def _row(
        hotel_id: uuid.UUID,
        name: str,
        attribute_id: uuid.UUID,
        attribute_name: str,
        relative: float | None,
        score: float | None = None,
    ) -> dict[str, object]:
        return {
            "hotel_id": hotel_id,
            "name": name,
            "attribute_id": attribute_id,
            "attribute_name": attribute_name,
            "relative_score_5": relative,
            "score_5": score,
        }

    def test_weighted_ranking_descends(self) -> None:
        preferences = [
            EffectivePreference(self.cleanliness, 1.0, None, False),
            EffectivePreference(self.wifi, 0.2, None, False),
        ]
        ranked = rank_hotels(self.rows, preferences)
        self.assertEqual([item.hotel_id for item in ranked], [self.hotel_a, self.hotel_b])
        self.assertGreater(ranked[0].personalized_rating, ranked[1].personalized_rating)

    def test_weight_change_reranks_live(self) -> None:
        preferences = [
            EffectivePreference(self.cleanliness, 0.1, None, False),
            EffectivePreference(self.wifi, 1.0, None, False),
        ]
        self.assertEqual(rank_hotels(self.rows, preferences)[0].hotel_id, self.hotel_b)

    def test_mandatory_missing_attribute_rejects_hotel(self) -> None:
        rows = [self.rows[0], *self.rows[2:]]
        preferences = [EffectivePreference(self.wifi, 1.0, 3.0, True)]
        ranked = rank_hotels(rows, preferences)
        self.assertEqual([item.hotel_id for item in ranked], [self.hotel_b])

    def test_mandatory_threshold_rejects_low_score(self) -> None:
        preferences = [EffectivePreference(self.wifi, 1.0, 4.0, True)]
        ranked = rank_hotels(self.rows, preferences)
        self.assertEqual([item.hotel_id for item in ranked], [self.hotel_b])

    def test_missing_optional_data_reduces_coverage_not_to_zero(self) -> None:
        rows = [self.rows[0]]
        preferences = [
            EffectivePreference(self.cleanliness, 1.0, None, False),
            EffectivePreference(self.wifi, 1.0, None, False),
        ]
        result = rank_hotels(rows, preferences)[0]
        self.assertEqual(result.personalized_rating, 4.8)
        self.assertEqual(result.coverage_score, 0.5)

    def test_relative_missing_falls_back_to_absolute_score(self) -> None:
        rows = [self._row(self.hotel_a, "A", self.wifi, "WiFi", None, 4.2)]
        result = rank_hotels(rows, [EffectivePreference(self.wifi, 1.0, None, False)])[0]
        self.assertEqual(result.personalized_rating, 4.2)

    def test_zero_total_weight_raises(self) -> None:
        with self.assertRaises(ScoringError):
            rank_hotels(self.rows, [EffectivePreference(self.wifi, 0.0, None, False)])

    def test_no_evidence_returns_no_result(self) -> None:
        rows = [{"hotel_id": self.hotel_a, "name": "A", "attribute_id": None}]
        result = rank_hotels(
            rows, [EffectivePreference(self.wifi, 1.0, None, False)]
        )
        self.assertEqual(result, [])

    def test_explanation_is_persistable_and_complete(self) -> None:
        result = rank_hotels(
            self.rows, [EffectivePreference(self.cleanliness, 1.0, None, False)]
        )[0]
        self.assertIn("matched_attributes", result.explanation)
        self.assertIn("strengths", result.explanation)
        self.assertIn("weaknesses", result.explanation)
        self.assertIn("reasons", result.explanation)
        self.assertEqual(result.match_score, 96.0)

    def test_weakness_is_explained(self) -> None:
        rows = [self._row(self.hotel_a, "A", self.wifi, "WiFi", 2.0)]
        result = rank_hotels(rows, [EffectivePreference(self.wifi, 1.0, None, False)])[0]
        self.assertEqual(result.explanation["weaknesses"], ["WiFi"])
        self.assertEqual(
            result.explanation["reasons"],
            ["Best available match for your weighted preferences"],
        )


if __name__ == "__main__":
    unittest.main()

