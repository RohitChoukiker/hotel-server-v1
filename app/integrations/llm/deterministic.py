"""Safe deterministic fallback text interpreter for local/offline operation."""

import re
from collections.abc import Iterable

from app.enums import ChatIntent, Sentiment
from app.integrations.llm.base import AttributeMentionOutput, ChatIntentOutput, PreferenceOutput

POSITIVE_WORDS = {"clean", "excellent", "great", "good", "comfortable", "quiet", "fast", "amazing"}
NEGATIVE_WORDS = {"dirty", "poor", "bad", "noisy", "slow", "broken", "uncomfortable", "terrible"}


class DeterministicTextInterpreter:
    """Keyword-based fallback that never decides recommendation rank."""

    async def extract_review_mentions(
        self,
        text: str,
        allowed_attribute_slugs: list[str],
    ) -> list[AttributeMentionOutput]:
        """Extract conservative keyword mentions for known attribute slugs."""
        lowered = text.casefold()
        outputs: list[AttributeMentionOutput] = []
        for slug in allowed_attribute_slugs:
            phrases = {slug.replace("-", " "), slug.replace("-", "")}
            matched_phrase = next(
                (phrase for phrase in phrases if phrase in lowered), None
            )
            if matched_phrase is None:
                continue
            clauses = re.split(r"\b(?:but|however|although|though|while)\b", lowered)
            context = next(
                (clause for clause in clauses if matched_phrase in clause), lowered
            )
            sentiment = self._sentiment(context.split())
            outputs.append(
                AttributeMentionOutput(
                    attribute_slug=slug,
                    sentiment=sentiment,
                    confidence=0.6,
                    evidence_text=text[:500],
                )
            )
        return outputs

    async def interpret_onboarding(
        self,
        answers: list[dict[str, object]],
        allowed_attribute_slugs: list[str],
    ) -> list[PreferenceOutput]:
        """Map explicit attribute/weight answers into validated preferences."""
        allowed = set(allowed_attribute_slugs)
        merged: dict[str, PreferenceOutput] = {}
        for item in answers:
            answer = item.get("answer")
            if not isinstance(answer, dict):
                continue
            weights = answer.get("weights")
            if isinstance(weights, dict):
                for slug, raw_weight in weights.items():
                    if str(slug) in allowed and isinstance(raw_weight, int | float):
                        merged[str(slug)] = PreferenceOutput(
                            attribute_slug=str(slug), weight=max(0.0, min(1.0, float(raw_weight)))
                        )
            selected = answer.get("selected")
            if isinstance(selected, list):
                for slug in selected:
                    if str(slug) in allowed:
                        merged[str(slug)] = PreferenceOutput(attribute_slug=str(slug), weight=0.8)
        return list(merged.values())

    async def extract_chat_intent(self, message: str) -> ChatIntentOutput:
        """Classify explicit hotel operations without hidden reasoning."""
        text = message.casefold()
        rules: list[tuple[Iterable[str], ChatIntent]] = [
            (("negative review", "bad review", "complaint"), ChatIntent.SHOW_NEGATIVE_REVIEWS),
            (("compare", "versus", " vs "), ChatIntent.COMPARE_HOTELS),
            (("image", "photo"), ChatIntent.SHOW_IMAGES),
            (("attribute", "score"), ChatIntent.SHOW_ATTRIBUTES),
            (("why recommended", "explain recommendation"), ChatIntent.EXPLAIN_RECOMMENDATION),
            (("update preference", "prefer", "important"), ChatIntent.UPDATE_TRIP_PREFERENCE),
            (("create trip", "plan a trip"), ChatIntent.CREATE_TRIP),
            (("recommend", "best hotel"), ChatIntent.RECOMMEND_HOTELS),
            (("review",), ChatIntent.SHOW_REVIEWS),
            (("search", "find hotel", "hotels in"), ChatIntent.SEARCH_HOTELS),
        ]
        for phrases, intent in rules:
            if any(phrase in text for phrase in phrases):
                attribute = next(
                    (slug for slug in ("wifi", "cleanliness", "quietness", "pool") if slug in text),
                    None,
                )
                return ChatIntentOutput(intent=intent, attribute_slug=attribute)
        return ChatIntentOutput(intent=ChatIntent.GENERAL_HOTEL_QUESTION)

    @staticmethod
    def _sentiment(words: list[str]) -> Sentiment:
        positive = sum(word.strip(".,!?:;") in POSITIVE_WORDS for word in words)
        negative = sum(word.strip(".,!?:;") in NEGATIVE_WORDS for word in words)
        if positive > negative:
            return Sentiment.POSITIVE
        if negative > positive:
            return Sentiment.NEGATIVE
        return Sentiment.NEUTRAL
