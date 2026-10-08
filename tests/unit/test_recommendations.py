"""Coverage for recommendation behavior without onboarding preferences."""

import uuid

from app.services.recommendations import RecommendationService


def test_no_preference_recommendations_are_neutral_and_catalog_ordered() -> None:
    first = uuid.uuid4()
    second = uuid.uuid4()
    ranked = RecommendationService._rank_without_preferences(
        [
            {
                "hotel_id": first,
                "name": "First hotel",
                "source_rating": 4.5,
                "source_review_count": 10,
                "source_rank": 1,
            },
            {
                "hotel_id": first,
                "name": "First hotel",
                "source_rating": 4.5,
                "source_review_count": 10,
                "source_rank": 1,
            },
            {
                "hotel_id": second,
                "name": "Second hotel",
                "source_rating": 4.0,
                "source_review_count": 8,
                "source_rank": 2,
            },
        ]
    )

    assert [item.hotel_id for item in ranked] == [first, second]
    assert all(item.personalized_rating == 0.0 for item in ranked)
    assert all(item.match_score == 0.0 for item in ranked)
    assert all(item.explanation["matched_attributes"] == [] for item in ranked)
