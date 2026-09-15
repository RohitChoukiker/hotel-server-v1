"""Typed structured-text interpretation contracts."""

import uuid
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.enums import ChatIntent, Sentiment


class AttributeMentionOutput(BaseModel):
    """Validated review mention produced by an extractor."""

    model_config = ConfigDict(extra="forbid")

    attribute_slug: str
    sentiment: Sentiment
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_text: str = Field(min_length=1, max_length=500)


class PreferenceOutput(BaseModel):
    """Validated onboarding preference."""

    model_config = ConfigDict(extra="forbid")

    attribute_slug: str
    weight: float = Field(ge=0.0, le=1.0)
    preferred_value: str | None = None


class ChatIntentOutput(BaseModel):
    """Validated chat intent and explicit entities."""

    model_config = ConfigDict(extra="forbid")

    intent: ChatIntent
    hotel_ids: list[uuid.UUID] = Field(default_factory=list)
    trip_id: uuid.UUID | None = None
    attribute_slug: str | None = None
    sentiment: Sentiment | None = None
    city: str | None = None
    weight: float | None = Field(default=None, ge=0.0, le=1.0)


class StructuredTextInterpreter(Protocol):
    """No-embedding interface for constrained structured LLM outputs."""

    async def extract_review_mentions(
        self,
        text: str,
        allowed_attribute_slugs: list[str],
    ) -> list[AttributeMentionOutput]:
        """Extract attribute sentiment evidence from review text."""

    async def interpret_onboarding(
        self,
        answers: list[dict[str, object]],
        allowed_attribute_slugs: list[str],
    ) -> list[PreferenceOutput]:
        """Interpret questionnaire answers as attribute weights."""

    async def extract_chat_intent(self, message: str) -> ChatIntentOutput:
        """Extract one supported assistant intent."""
