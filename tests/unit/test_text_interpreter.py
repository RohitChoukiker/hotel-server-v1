"""Deterministic structured-text fallback tests."""

import unittest

from app.enums import ChatIntent, Sentiment
from app.integrations.llm.deterministic import DeterministicTextInterpreter


class DeterministicInterpreterTests(unittest.IsolatedAsyncioTestCase):
    """Verify offline extraction remains structured and conservative."""

    async def asyncSetUp(self) -> None:
        self.interpreter = DeterministicTextInterpreter()

    async def test_review_mentions_use_allowed_attributes(self) -> None:
        mentions = await self.interpreter.extract_review_mentions(
            "The room cleanliness was excellent but wifi was slow.",
            ["cleanliness", "wifi", "pool"],
        )
        self.assertEqual(
            {item.attribute_slug for item in mentions}, {"cleanliness", "wifi"}
        )
        sentiments = {item.attribute_slug: item.sentiment for item in mentions}
        self.assertEqual(
            sentiments,
            {"cleanliness": Sentiment.POSITIVE, "wifi": Sentiment.NEGATIVE},
        )

    async def test_onboarding_only_accepts_allowlisted_slugs(self) -> None:
        output = await self.interpreter.interpret_onboarding(
            [
                {
                    "answer": {
                        "weights": {"wifi": 2.0, "unknown": 1.0},
                        "selected": ["pool"],
                    }
                }
            ],
            ["wifi", "pool"],
        )
        by_slug = {item.attribute_slug: item.weight for item in output}
        self.assertEqual(by_slug, {"wifi": 1.0, "pool": 0.8})

    async def test_supported_chat_intents(self) -> None:
        cases = {
            "find hotels in Goa": ChatIntent.SEARCH_HOTELS,
            "recommend the best hotel": ChatIntent.RECOMMEND_HOTELS,
            "compare these hotels": ChatIntent.COMPARE_HOTELS,
            "show negative wifi reviews": ChatIntent.SHOW_NEGATIVE_REVIEWS,
            "show hotel photos": ChatIntent.SHOW_IMAGES,
            "show attribute scores": ChatIntent.SHOW_ATTRIBUTES,
            "explain recommendation": ChatIntent.EXPLAIN_RECOMMENDATION,
            "update preference for wifi": ChatIntent.UPDATE_TRIP_PREFERENCE,
            "create trip": ChatIntent.CREATE_TRIP,
            "hello": ChatIntent.GENERAL_HOTEL_QUESTION,
        }
        for message, expected in cases.items():
            with self.subTest(message=message):
                result = await self.interpreter.extract_chat_intent(message)
                self.assertIs(result.intent, expected)


if __name__ == "__main__":
    unittest.main()
