"""Chat intent-to-existing-service workflow; ranking remains deterministic."""

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.dto.chat import ChatRequest, ChatResponse
from app.dto.hotels import HotelSearchFilters
from app.dto.recommendations import RecommendationRequest
from app.dto.reviews import ReviewFilters
from app.dto.trips import TripPreferenceWrite, TripPreferencesReplace, TripWrite
from app.enums import ChatIntent, Sentiment
from app.exceptions import ImportValidationError, NotFoundError
from app.integrations.llm.base import ChatIntentOutput, StructuredTextInterpreter
from app.repositories.catalog import AttributeRepository
from app.repositories.chat import ChatRepository
from app.repositories.preferences import PreferenceRepository
from app.repositories.trips import TripRepository
from app.services.catalog import HotelService, ReviewService
from app.services.recommendations import RecommendationService
from app.services.trips import TripService


class ChatOrchestrator:
    """Translate an intent into existing domain service calls."""

    def __init__(
        self,
        session: AsyncSession,
        interpreter: StructuredTextInterpreter,
    ) -> None:
        self._session = session
        self._interpreter = interpreter
        self._chat = ChatRepository(session)

    async def handle(self, user_id: uuid.UUID, payload: ChatRequest) -> ChatResponse:
        """Persist a turn, extract intent outside a transaction, and dispatch it."""
        conversation = await self._resolve_conversation(user_id, payload)
        await self._chat.add_message(conversation, "user", payload.message)
        await self._session.commit()
        intent = await self._interpreter.extract_chat_intent(payload.message)
        data, message = await self._dispatch(user_id, payload, intent)
        await self._chat.add_message(
            conversation,
            "assistant",
            message,
            {**intent.model_dump(mode="json"), "response_data": data},
        )
        await self._session.commit()
        return ChatResponse(
            conversation_id=conversation.id,
            message=message,
            intent=intent.intent,
            data=data,
        )

    async def _resolve_conversation(
        self, user_id: uuid.UUID, payload: ChatRequest
    ) -> Any:
        if payload.conversation_id:
            conversation = await self._chat.get_owned(payload.conversation_id, user_id)
            if conversation is None:
                raise NotFoundError("Conversation not found")
            return conversation
        if payload.trip_id is not None:
            trip = await TripRepository(self._session).get_owned(
                payload.trip_id, user_id
            )
            if trip is None:
                raise NotFoundError("Trip not found")
        conversation = await self._chat.create_conversation(user_id, payload.trip_id)
        return conversation

    async def _dispatch(
        self,
        user_id: uuid.UUID,
        payload: ChatRequest,
        intent: ChatIntentOutput,
    ) -> tuple[dict[str, Any], str]:
        context = payload.context
        hotel_service = HotelService(self._session)
        review_service = ReviewService(self._session)
        if intent.intent is ChatIntent.SEARCH_HOTELS:
            filters = HotelSearchFilters.model_validate(context.get("filters", {}))
            hotels, _ = await hotel_service.search(
                filters, min(int(context.get("limit", 10)), 50), None
            )
            return {
                "hotels": [item.model_dump(mode="json") for item in hotels]
            }, f"I found {len(hotels)} hotels."
        if intent.intent is ChatIntent.RECOMMEND_HOTELS:
            trip_id = intent.trip_id or payload.trip_id
            if trip_id is None:
                raise ImportValidationError("A trip is required for personalized recommendations")
            result = await RecommendationService(self._session).generate(
                user_id,
                RecommendationRequest(
                    trip_id=trip_id,
                    limit=min(int(context.get("limit", 10)), 50),
                ),
            )
            return (
                result.model_dump(mode="json"),
                f"I ranked {len(result.results)} hotels using your preferences.",
            )
        if intent.intent is ChatIntent.COMPARE_HOTELS:
            ids = self._hotel_ids(intent, context)
            details = [await hotel_service.detail(item) for item in ids[:5]]
            return (
                {"hotels": [item.model_dump(mode="json") for item in details]},
                "Here is a factual side-by-side hotel comparison.",
            )
        if intent.intent in {ChatIntent.SHOW_REVIEWS, ChatIntent.SHOW_NEGATIVE_REVIEWS}:
            hotel_id = self._single_hotel_id(intent, context)
            sentiment = (
                Sentiment.NEGATIVE
                if intent.intent is ChatIntent.SHOW_NEGATIVE_REVIEWS
                else None
            )
            filters = ReviewFilters(attribute=intent.attribute_slug, sentiment=sentiment)
            reviews, _ = await review_service.for_hotel(hotel_id, filters, 20, None)
            return (
                {"reviews": [item.model_dump(mode="json") for item in reviews]},
                f"I found {len(reviews)} matching reviews.",
            )
        if intent.intent is ChatIntent.SHOW_IMAGES:
            images = await hotel_service.images(self._single_hotel_id(intent, context))
            return (
                {"images": [item.model_dump(mode="json") for item in images]},
                f"I found {len(images)} hotel images.",
            )
        if intent.intent is ChatIntent.SHOW_ATTRIBUTES:
            attributes = await hotel_service.attributes(self._single_hotel_id(intent, context))
            return (
                {"attributes": [item.model_dump(mode="json") for item in attributes]},
                "These scores come from preprocessed review evidence.",
            )
        if intent.intent is ChatIntent.EXPLAIN_RECOMMENDATION:
            run_id = uuid.UUID(str(context["recommendation_run_id"]))
            hotel_id = self._single_hotel_id(intent, context)
            detail = await RecommendationService(self._session).detail(user_id, run_id, hotel_id)
            return (
                detail.model_dump(mode="json"),
                "This explanation shows the exact weighted attributes used.",
            )
        if intent.intent is ChatIntent.CREATE_TRIP:
            trip = await TripService(self._session).create(
                user_id, TripWrite.model_validate(context["trip"])
            )
            return trip.model_dump(mode="json"), "Your trip has been created."
        if intent.intent is ChatIntent.UPDATE_TRIP_PREFERENCE:
            data = await self._update_trip_preference(user_id, payload, intent)
            return data, "Your trip preference was updated; the next ranking will use it."
        return (
            {},
            "I can search, compare, show reviews or images, create a trip, "
            "and rank hotels from your saved preferences.",
        )

    async def _update_trip_preference(
        self, user_id: uuid.UUID, payload: ChatRequest, intent: ChatIntentOutput
    ) -> dict[str, Any]:
        trip_id = intent.trip_id or payload.trip_id
        slug = intent.attribute_slug or str(payload.context.get("attribute_slug", ""))
        weight = (
            intent.weight
            if intent.weight is not None
            else float(payload.context.get("weight", 0.8))
        )
        if trip_id is None or not slug:
            raise ImportValidationError("Trip and attribute are required")
        await TripService(self._session).get(user_id, trip_id)
        attribute = await AttributeRepository(self._session).get_by_slug(slug)
        if attribute is None:
            raise ImportValidationError("Unknown attribute")
        repository = PreferenceRepository(self._session)
        existing = await repository.trip_preferences(trip_id)
        merged = {
            item.attribute_id: TripPreferenceWrite.model_validate(item) for item in existing
        }
        merged[attribute.id] = TripPreferenceWrite(
            attribute_id=attribute.id,
            weight=weight,
            minimum_required_score=payload.context.get("minimum_required_score"),
            is_mandatory=bool(payload.context.get("is_mandatory", False)),
        )
        result = await TripService(self._session).replace_preferences(
            user_id, trip_id, TripPreferencesReplace(preferences=list(merged.values()))
        )
        return {"preferences": [item.model_dump(mode="json") for item in result]}

    @staticmethod
    def _hotel_ids(intent: ChatIntentOutput, context: dict[str, Any]) -> list[uuid.UUID]:
        ids = intent.hotel_ids or [uuid.UUID(str(item)) for item in context.get("hotel_ids", [])]
        if not ids:
            raise ImportValidationError("At least one hotel_id is required")
        return ids

    @classmethod
    def _single_hotel_id(cls, intent: ChatIntentOutput, context: dict[str, Any]) -> uuid.UUID:
        if context.get("hotel_id"):
            return uuid.UUID(str(context["hotel_id"]))
        return cls._hotel_ids(intent, context)[0]
