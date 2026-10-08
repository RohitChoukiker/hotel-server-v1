"""Tests for pure parsing and deterministic aggregate sentiment rules."""

import hashlib
import unittest

from app.domain.sentiment import (
    AttributeSentimentValue,
    HotelSentimentRecord,
    aggregate_score_0_5,
    aggregate_support_confidence,
    deterministic_attribute_slug,
    deterministic_category_code,
    normalize_taxonomy_key,
)
from app.enums import Sentiment
from app.exceptions import ImportValidationError


class SentimentDomainTests(unittest.TestCase):
    def test_normalizes_dynamic_taxonomy_keys(self) -> None:
        self.assertEqual(normalize_taxonomy_key("  Good_location  "), "good-location")
        self.assertEqual(normalize_taxonomy_key("Ｒｏｏｍ Size"), "room-size")

    def test_rejects_empty_normalized_key(self) -> None:
        with self.assertRaises(ImportValidationError):
            normalize_taxonomy_key("___")

    def test_category_code_is_deterministic_and_normalized(self) -> None:
        self.assertEqual(deterministic_category_code("Good_location"), "GOOD_LOCATION")
        self.assertEqual(
            deterministic_category_code("Good_location"),
            deterministic_category_code("Good_location"),
        )

    def test_category_code_long_key_uses_stable_digest(self) -> None:
        key = "a" * 65
        self.assertEqual(
            deterministic_category_code(key),
            "A" * 53 + "_" + hashlib.sha256(key.encode()).hexdigest()[:10],
        )

    def test_deterministic_slug_uses_category_prefix_on_global_collision(self) -> None:
        used = {"comfort"}
        first = deterministic_attribute_slug("room", "comfort", used)
        second = deterministic_attribute_slug("room", "comfort", used)
        self.assertEqual(first, "room--comfort")
        self.assertEqual(second, first)

    def test_deterministic_slug_uses_stable_digest_when_both_candidates_are_taken(self) -> None:
        used = {"comfort", "room--comfort"}
        self.assertEqual(
            deterministic_attribute_slug("room", "comfort", used),
            "room--comfort--" + hashlib.sha256(b"room\0comfort").hexdigest()[:10],
        )

    def test_digest_slug_is_at_most_120_characters(self) -> None:
        category = "c" * 80
        attribute = "a" * 80
        self.assertLessEqual(
            len(
                deterministic_attribute_slug(
                    category, attribute, {attribute, category + "--" + attribute}
                )
            ),
            120,
        )

    def test_deterministic_slug_preserves_free_attribute_slug(self) -> None:
        self.assertEqual(deterministic_attribute_slug("room", "comfort", set()), "comfort")

    def test_balance_maps_to_zero_through_five(self) -> None:
        self.assertEqual(aggregate_score_0_5(10, 0, 10), 5.0)
        self.assertEqual(aggregate_score_0_5(5, 5, 10), 2.5)
        self.assertEqual(aggregate_score_0_5(0, 10, 10), 0.0)

    def test_score_requires_positive_total(self) -> None:
        with self.assertRaises(ImportValidationError):
            aggregate_score_0_5(0, 0, 0)

    def test_support_confidence_saturates_at_reference_count(self) -> None:
        self.assertAlmostEqual(aggregate_support_confidence(10), 1.0)
        self.assertLess(aggregate_support_confidence(1), 1.0)

    def test_attribute_counts_must_be_consistent(self) -> None:
        with self.assertRaisesRegex(ImportValidationError, "mention counts"):
            AttributeSentimentValue.from_payload(
                {
                    "sentiment": "positive",
                    "mentions": 2,
                    "positive_mentions": 2,
                    "negative_mentions": 1,
                }
            )

    def test_boolean_is_not_an_integer_count(self) -> None:
        with self.assertRaises(ImportValidationError):
            AttributeSentimentValue.from_payload(
                {
                    "sentiment": "positive",
                    "mentions": True,
                    "positive_mentions": 1,
                    "negative_mentions": 0,
                }
            )

    def test_negative_counts_are_rejected(self) -> None:
        with self.assertRaises(ImportValidationError):
            AttributeSentimentValue.from_payload(
                {
                    "sentiment": "negative",
                    "mentions": 1,
                    "positive_mentions": 0,
                    "negative_mentions": -1,
                }
            )

    def test_attribute_parses_neutral_remainder(self) -> None:
        value = AttributeSentimentValue.from_payload(
            {
                "sentiment": " neutral ",
                "mentions": 5,
                "positive_mentions": 2,
                "negative_mentions": 1,
            }
        )
        self.assertEqual(value.sentiment, Sentiment.NEUTRAL)
        self.assertEqual(value.neutral_mentions, 2)

    def test_invalid_sentiment_is_rejected(self) -> None:
        with self.assertRaises(ImportValidationError):
            AttributeSentimentValue.from_payload(
                {
                    "sentiment": "mixed",
                    "mentions": 1,
                    "positive_mentions": 1,
                    "negative_mentions": 0,
                }
            )

    def test_hotel_requires_nonblank_id(self) -> None:
        with self.assertRaises(ImportValidationError):
            HotelSentimentRecord.from_payload(
                {
                    "hotel_id": "  ",
                    "analysis_mode": "m",
                    "analysis_version": "v",
                    "review_window": "w",
                    "reviews_analyzed": 0,
                    "has_recent_reviews": False,
                    "attributes": {},
                }
            )

    def test_hotel_id_rejects_bool_and_containers(self) -> None:
        for hotel_id in (True, None, [], {}):
            with self.subTest(hotel_id=hotel_id), self.assertRaises(ImportValidationError):
                HotelSentimentRecord.from_payload(
                    {
                        "hotel_id": hotel_id,
                        "analysis_mode": "m",
                        "analysis_version": "v",
                        "review_window": "w",
                        "reviews_analyzed": 0,
                        "has_recent_reviews": False,
                        "attributes": {},
                    }
                )

    def test_hotel_requires_nonblank_provenance(self) -> None:
        for field in ("analysis_mode", "analysis_version", "review_window"):
            payload = {
                "hotel_id": "h1",
                "analysis_mode": "m",
                "analysis_version": "v",
                "review_window": "w",
                "reviews_analyzed": 0,
                "has_recent_reviews": False,
                "attributes": {},
            }
            payload[field] = "  "
            with self.subTest(field=field), self.assertRaises(ImportValidationError):
                HotelSentimentRecord.from_payload(payload)

    def test_real_contract_allows_optional_provenance_fields(self) -> None:
        record = HotelSentimentRecord.from_payload(
            {
                "hotel_id": 501153,
                "analysis_mode": "all_reviews",
                "reviews_analyzed": 75,
                "attributes": {},
            }
        )
        self.assertEqual(record.analysis_version, "unspecified")
        self.assertEqual(record.review_window, "unspecified")
        self.assertIsNone(record.has_recent_reviews)

    def test_hotel_requires_boolean_recent_reviews_flag(self) -> None:
        with self.assertRaises(ImportValidationError):
            HotelSentimentRecord.from_payload(
                {
                    "hotel_id": "h1",
                    "analysis_mode": "m",
                    "analysis_version": "v",
                    "review_window": "w",
                    "reviews_analyzed": 1,
                    "has_recent_reviews": 1,
                    "attributes": {},
                }
            )

    def test_hotel_rejects_negative_reviews_analyzed(self) -> None:
        with self.assertRaises(ImportValidationError):
            HotelSentimentRecord.from_payload(
                {
                    "hotel_id": "h1",
                    "analysis_mode": "m",
                    "analysis_version": "v",
                    "review_window": "w",
                    "reviews_analyzed": -1,
                    "has_recent_reviews": False,
                    "attributes": {},
                }
            )

    def test_hotel_requires_object_attributes(self) -> None:
        for attributes in (None, [], "bad"):
            with self.subTest(attributes=attributes), self.assertRaises(ImportValidationError):
                HotelSentimentRecord.from_payload(
                    {
                        "hotel_id": "h1",
                        "analysis_mode": "m",
                        "analysis_version": "v",
                        "review_window": "w",
                        "reviews_analyzed": 0,
                        "has_recent_reviews": False,
                        "attributes": attributes,
                    }
                )


if __name__ == "__main__":
    unittest.main()
