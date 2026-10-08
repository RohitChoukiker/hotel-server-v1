"""Cross-domain acceptance flow using real PostgreSQL/PostGIS and Redis."""

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from geoalchemy2.elements import WKTElement
from sqlalchemy import select, update

from app.config import get_settings
from app.database import create_engine, create_session_factory
from app.dto.onboarding import (
    AdaptiveQuestion,
    NextQuestionDecision,
)
from app.main import create_app
from app.models import (
    AlgorithmVersion,
    Attribute,
    City,
    DataSource,
    Hotel,
    HotelAttributeScore,
    HotelImage,
    HotelSourceMapping,
    Review,
    ReviewAttributeMention,
    ReviewImage,
    Trip,
    User,
)
from scripts.seed_reference_data import seed, stable_id

pytestmark = pytest.mark.e2e


class E2EClaudeProvider:
    """Mock Claude responses while keeping the production provider boundary intact."""

    provider_name = "anthropic"
    model = "claude-e2e"
    prompt_version = "e2e"

    async def generate_next_question(self, context: dict[str, object]) -> NextQuestionDecision:
        answered = int(context.get("answered_count", 0))
        if answered >= 8:
            return NextQuestionDecision(
                questionnaire_complete=True,
                confidence=0.95,
                reasoning_summary="Canonical hotel-selection signals are available.",
                completion_reason="SUFFICIENT_SIGNAL",
            )
        steps = {
            1: (
                "Who's travelling with you?",
                "travel_group",
                [
                    {"value": "couple", "label": "Couple"},
                    {"value": "solo", "label": "Solo"},
                    {"value": "friends", "label": "Friends"},
                ],
                "standard",
            ),
            2: (
                "Which price and value balance feels right?",
                "price_value",
                [
                    {"value": "balanced_value", "label": "Balanced value"},
                    {"value": "best_value", "label": "Best value"},
                ],
                "standard",
            ),
            3: (
                "What pace suits this trip?",
                "travel_pace",
                [
                    {"value": "mixed_pace", "label": "A mix"},
                    {"value": "restful", "label": "Restful"},
                ],
                "standard",
            ),
            4: (
                "How do you prefer to handle meals?",
                "food_preference",
                [
                    {"value": "mix", "label": "A mix"},
                    {"value": "eating_out", "label": "Mostly out"},
                ],
                "standard",
            ),
            5: (
                "For your destination, which setting is the better starting point?",
                "destination_branch",
                [
                    {"value": "mountains_hills", "label": "Mountains and hills"},
                    {"value": "city_town", "label": "City or town"},
                ],
                "clarification",
            ),
            6: (
                "Which destination experience should your hotel support?",
                "destination_detail",
                [
                    {"value": "scenery", "label": "Scenery"},
                    {"value": "outdoors", "label": "Outdoors"},
                ],
                "standard",
            ),
            7: (
                "What matters most for you as a couple?",
                "couple_style",
                [
                    {"value": "romance", "label": "Romance"},
                    {"value": "adventure", "label": "Adventure"},
                    {"value": "decompression", "label": "Decompression"},
                ],
                "standard",
            ),
        }
        prompt, key, options, question_kind = steps[answered]
        return NextQuestionDecision(
            questionnaire_complete=False,
            confidence=0.8,
            reasoning_summary="Collecting a canonical onboarding signal.",
            question=AdaptiveQuestion(
                prompt=prompt,
                answer_type="single_choice",
                options=options,
                question_key=key,
                question_kind=question_kind,
                semantic_dimensions=["location"],
            ),
        )


class CountingE2EClaudeProvider(E2EClaudeProvider):
    """Count provider calls so skip/start boundaries remain observable."""

    def __init__(self) -> None:
        self.calls = 0

    async def generate_next_question(self, context: dict[str, object]) -> NextQuestionDecision:
        self.calls += 1
        return await super().generate_next_question(context)


async def _prepare_catalog() -> dict[str, uuid.UUID]:
    await seed()
    engine = create_engine(get_settings())
    factory = create_session_factory(engine)
    now = datetime.now(UTC)
    ids: dict[str, uuid.UUID] = {
        "country": stable_id("country:IN"),
        "region": stable_id("region:IN:GA"),
        "city": uuid.uuid4(),
        "hotel_a": uuid.uuid4(),
        "hotel_b": uuid.uuid4(),
        "review": uuid.uuid4(),
    }
    async with factory() as session:
        source = await session.scalar(select(DataSource).where(DataSource.code == "TRIPADVISOR"))
        algorithm = await session.scalar(select(AlgorithmVersion).where(AlgorithmVersion.is_active))
        attributes = list((await session.scalars(select(Attribute))).all())
        by_slug = {item.slug: item for item in attributes}
        ids["wifi"] = by_slug["wifi"].id
        ids["cleanliness"] = by_slug["cleanliness"].id
        assert source is not None and algorithm is not None
        session.add(
            City(
                id=ids["city"],
                region_id=ids["region"],
                name=f"E2E City {ids['city']}",
                latitude=15.49,
                longitude=73.83,
                timezone="Asia/Kolkata",
                is_active=True,
            )
        )
        hotels = [
            Hotel(
                id=ids["hotel_a"],
                name="E2E Quiet Resort",
                country_id=ids["country"],
                region_id=ids["region"],
                city_id=ids["city"],
                hotel_type="RESORT",
                latitude=15.49,
                longitude=73.83,
                geo_location=WKTElement("POINT(73.83 15.49)", srid=4326),
                is_active=True,
            ),
            Hotel(
                id=ids["hotel_b"],
                name="E2E City Hotel",
                country_id=ids["country"],
                region_id=ids["region"],
                city_id=ids["city"],
                hotel_type="HOTEL",
                latitude=15.50,
                longitude=73.84,
                geo_location=WKTElement("POINT(73.84 15.50)", srid=4326),
                is_active=True,
            ),
        ]
        session.add_all(hotels)
        await session.flush()
        session.add_all(
            [
                HotelSourceMapping(
                    hotel_id=hotel.id,
                    source_id=source.id,
                    source_hotel_id=f"e2e-{hotel.id}",
                    source_rating=4.5 if hotel.id == ids["hotel_a"] else 4.0,
                    source_review_count=100,
                    first_seen_at=now,
                    last_seen_at=now,
                    is_active=True,
                )
                for hotel in hotels
            ]
        )
        session.add(
            HotelImage(
                hotel_id=ids["hotel_a"],
                source_id=source.id,
                image_url="https://example.com/hotel.jpg",
                position=0,
                is_primary=True,
                created_at=now,
            )
        )
        review = Review(
            id=ids["review"],
            hotel_id=ids["hotel_a"],
            source_id=source.id,
            source_review_id=f"e2e-review-{ids['review']}",
            source_rating=3,
            source_rating_scale=5,
            normalized_rating_5=3,
            title="Slow WiFi",
            review_text="The room was clean but WiFi was slow.",
            review_date=now.date(),
            reviewer_name="E2E",
            trip_type="LEISURE",
            language="en",
            scraped_at=now,
        )
        session.add(review)
        await session.flush()
        session.add_all(
            [
                ReviewImage(
                    review_id=review.id,
                    image_url="https://example.com/review.jpg",
                    position=0,
                    created_at=now,
                ),
                ReviewAttributeMention(
                    review_id=review.id,
                    attribute_id=ids["wifi"],
                    sentiment="NEGATIVE",
                    sentiment_value=-1,
                    review_rating_used=3,
                    calculated_contribution=2,
                    confidence=0.95,
                    evidence_text="WiFi was slow",
                    algorithm_version_id=algorithm.id,
                    created_at=now,
                ),
            ]
        )
        score_values = {
            ids["hotel_a"]: {"wifi": 4.8, "cleanliness": 4.7},
            ids["hotel_b"]: {"wifi": 3.2, "cleanliness": 4.0},
        }
        for hotel_id, values in score_values.items():
            for slug, score in values.items():
                session.add(
                    HotelAttributeScore(
                        hotel_id=hotel_id,
                        attribute_id=by_slug[slug].id,
                        algorithm_version_id=algorithm.id,
                        mention_count=10,
                        positive_mentions=8,
                        negative_mentions=1,
                        neutral_mentions=1,
                        raw_score=score,
                        score_5=score,
                        score_100=score * 20,
                        mean=4,
                        standard_deviation=0.5,
                        z_score=(score - 4) / 0.5,
                        relative_score_5=score,
                        confidence_score=0.9,
                        calculated_at=now,
                    )
                )
        await session.commit()
    await engine.dispose()
    return ids


async def _promote_admin(user_id: uuid.UUID) -> None:
    engine = create_engine(get_settings())
    factory = create_session_factory(engine)
    async with factory() as session:
        await session.execute(update(User).where(User.id == user_id).values(role="ADMIN"))
        await session.commit()
    await engine.dispose()


def _register(client: TestClient, prefix: str) -> tuple[uuid.UUID, dict[str, str]]:
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": f"{prefix}-{uuid.uuid4()}@example.com",
            "password": "StrongPassword123",
            "first_name": "Product",
            "last_name": "Flow",
        },
    )
    assert response.status_code == 201, response.text
    data = response.json()["data"]
    return uuid.UUID(data["user"]["id"]), {
        "Authorization": f"Bearer {data['tokens']['access_token']}"
    }


def test_complete_product_flow_and_ownership() -> None:
    ids = asyncio.run(_prepare_catalog())
    with TestClient(
        create_app(adaptive_provider=E2EClaudeProvider()), base_url="http://localhost"
    ) as client:
        user_id, headers = _register(client, "owner")
        _, other_headers = _register(client, "other")

        started = client.post("/api/v1/onboarding/start", json={}, headers=headers)
        assert started.status_code == 200, started.text
        onboarding = started.json()["data"]
        onboarding_id = onboarding["id"]
        assert onboarding["mode"] == "ADAPTIVE"
        assert onboarding["current_question"] is not None
        assert onboarding["current_question"]["code"] == "adaptive_location"
        assert onboarding["current_question"]["prompt"] == "Where are you travelling to?"
        assert onboarding["current_question"]["answer_type"] == "text"
        assert onboarding["question_count"] == 1
        questions = client.get(f"/api/v1/onboarding/{onboarding_id}/questions", headers=headers)
        assert questions.status_code == 200
        question_rows = questions.json()["data"]
        assert len(question_rows) == 1
        assert question_rows[0]["code"] == "adaptive_location"
        assert question_rows[0]["options"] is None
        adaptive_prompts: list[str] = []
        for _ in range(10):
            question = onboarding["current_question"]
            if onboarding["status"] == "READY_TO_COMPLETE":
                break
            assert question is not None
            if question["code"] != "adaptive_location":
                adaptive_prompts.append(question["prompt"])
            if question["answer_type"] == "multi_choice":
                selected = [item["value"] for item in question["options"][:2]]
                answer = {"selected": selected}
            elif question["answer_type"] == "single_choice":
                answer = {"selected": [question["options"][0]["value"]]}
            elif question["answer_type"] == "scale":
                answer = {"value": question["scale_min"]}
            else:
                answer = {"value": "Aizawl, Mizoram"}
            answered = client.post(
                f"/api/v1/onboarding/{onboarding_id}/answers",
                json={"answers": [{"question_id": question["id"], "answer": answer}]},
                headers=headers,
            )
            assert answered.status_code == 200, answered.text
            onboarding = answered.json()["data"]
            if question["code"] == "adaptive_location":
                assert onboarding["answered_count"] == 1
                assert onboarding["question_count"] == 2
                assert onboarding["current_question"]["code"] != "adaptive_location"
                history = client.get(
                    f"/api/v1/onboarding/{onboarding_id}/questions", headers=headers
                )
                assert history.status_code == 200
                assert history.json()["data"][0]["answer"]["value"] == "Aizawl, Mizoram"
        assert onboarding["status"] == "READY_TO_COMPLETE"
        assert onboarding["answered_count"] >= 5
        assert len(adaptive_prompts) >= 2
        assert len(adaptive_prompts) == len(set(adaptive_prompts))
        completed = client.post(f"/api/v1/onboarding/{onboarding_id}/complete", headers=headers)
        assert completed.status_code == 200, completed.text
        assert completed.json()["data"]["preferences_created"] > 0
        assert (
            completed.json()["data"]["profile"]["context"]["destination_raw"] == "Aizawl, Mizoram"
        )

        manual = client.patch(
            f"/api/v1/users/me/preferences/{ids['wifi']}",
            json={"attribute_id": str(ids["wifi"]), "importance_weight": 1.0},
            headers=headers,
        )
        assert manual.status_code == 200, manual.text
        preference = next(
            item for item in manual.json()["data"] if item["attribute_id"] == str(ids["wifi"])
        )
        assert preference["preference_source"] == "USER_MANUAL"

        trip_payload = {
            "destination_country_id": str(ids["country"]),
            "destination_region_id": str(ids["region"]),
            "destination_city_id": str(ids["city"]),
            "trip_purpose": "LEISURE",
        }
        trip_response = client.post("/api/v1/trips", json=trip_payload, headers=headers)
        assert trip_response.status_code == 201, trip_response.text
        trip_id = trip_response.json()["data"]["id"]
        denied_trip = client.get(f"/api/v1/trips/{trip_id}", headers=other_headers)
        assert denied_trip.status_code == 404

        override = client.put(
            f"/api/v1/trips/{trip_id}/preferences",
            json={
                "preferences": [
                    {
                        "attribute_id": str(ids["cleanliness"]),
                        "weight": 1.0,
                        "minimum_required_score": 3.5,
                        "is_mandatory": True,
                    }
                ]
            },
            headers=headers,
        )
        assert override.status_code == 200, override.text

        recommendation = client.post(
            "/api/v1/recommendations",
            json={"trip_id": trip_id, "limit": 10},
            headers=headers,
        )
        assert recommendation.status_code == 200, recommendation.text
        run = recommendation.json()["data"]
        assert len(run["results"]) == 2
        assert run["results"][0]["personalized_rating"] >= run["results"][1]["personalized_rating"]
        assert run["results"][0]["explanation"]["matched_attributes"]
        run_id = run["recommendation_run_id"]
        cached_run = client.get(f"/api/v1/recommendations/{run_id}", headers=headers)
        assert cached_run.status_code == 200
        denied_run = client.get(f"/api/v1/recommendations/{run_id}", headers=other_headers)
        assert denied_run.status_code == 404

        nearby = client.get(
            "/api/v1/hotels/nearby?lat=15.49&lng=73.83&radius=10&limit=2",
            headers=headers,
        )
        assert nearby.status_code == 200, nearby.text
        assert len(nearby.json()["data"]) == 2
        detail = client.get(f"/api/v1/hotels/{ids['hotel_a']}")
        assert detail.status_code == 200, detail.text
        reviews = client.get(
            f"/api/v1/hotels/{ids['hotel_a']}/reviews?attribute=wifi&sentiment=NEGATIVE"
        )
        assert reviews.status_code == 200, reviews.text
        assert reviews.json()["data"][0]["source_review_id"].startswith("e2e-review")

        negative_chat = client.post(
            "/api/v1/chat",
            json={
                "message": "show negative WiFi reviews",
                "trip_id": trip_id,
                "context": {"hotel_id": str(ids["hotel_a"])},
            },
            headers=headers,
        )
        assert negative_chat.status_code == 200, negative_chat.text
        chat_data = negative_chat.json()["data"]
        assert chat_data["intent"] == "SHOW_NEGATIVE_REVIEWS"
        conversation_id = chat_data["conversation_id"]
        history = client.get(f"/api/v1/chat/conversations/{conversation_id}", headers=headers)
        assert history.status_code == 200
        assert len(history.json()["data"]["messages"]) == 2

        chat_update = client.post(
            "/api/v1/chat",
            json={
                "message": "update preference for WiFi",
                "conversation_id": conversation_id,
                "trip_id": trip_id,
                "context": {"attribute_slug": "wifi", "weight": 0.9},
            },
            headers=headers,
        )
        assert chat_update.status_code == 200, chat_update.text
        reranked = client.post(
            "/api/v1/recommendations",
            json={"trip_id": trip_id, "limit": 10},
            headers=headers,
        )
        assert reranked.status_code == 200
        assert reranked.json()["data"]["recommendation_run_id"] != run_id

        denied_admin = client.get("/api/v1/admin/dashboard", headers=other_headers)
        assert denied_admin.status_code == 403
        asyncio.run(_promote_admin(user_id))
        dashboard = client.get("/api/v1/admin/dashboard", headers=headers)
        assert dashboard.status_code == 200, dashboard.text
        setting = client.put(
            "/api/v1/admin/settings/recommendation.display",
            json={"value": {"max_results": 20}},
            headers=headers,
        )
        assert setting.status_code == 200, setting.text


def test_update_trip_returns_refreshed_timestamp_and_persists_fields() -> None:
    ids = asyncio.run(_prepare_catalog())
    with TestClient(create_app(), base_url="http://localhost") as client:
        _, headers = _register(client, "trip-update")
        created = client.post(
            "/api/v1/trips",
            json={
                "destination_country_id": str(ids["country"]),
                "destination_region_id": str(ids["region"]),
                "destination_city_id": str(ids["city"]),
                "trip_purpose": "LEISURE",
            },
            headers=headers,
        )
        assert created.status_code == 201, created.text
        trip_id = created.json()["data"]["id"]

        updated = client.put(
            f"/api/v1/trips/{trip_id}",
            json={
                "destination_country_id": str(ids["country"]),
                "destination_region_id": str(ids["region"]),
                "destination_city_id": str(ids["city"]),
                "check_in_date": "2026-10-10",
                "check_out_date": "2026-10-15",
                "trip_purpose": "BUSINESS",
                "budget_min": 100.0,
                "budget_max": 250.0,
                "status": "ACTIVE",
            },
            headers=headers,
        )
        assert updated.status_code == 200, updated.text
        response_trip = updated.json()["data"]
        assert response_trip["check_in_date"] == "2026-10-10"
        assert response_trip["check_out_date"] == "2026-10-15"
        assert response_trip["trip_purpose"] == "BUSINESS"
        assert response_trip["budget_min"] == 100.0
        assert response_trip["budget_max"] == 250.0
        assert response_trip["status"] == "ACTIVE"
        assert response_trip["updated_at"]

    async def read_persisted_trip() -> Trip | None:
        engine = create_engine(get_settings())
        factory = create_session_factory(engine)
        try:
            async with factory() as session:
                return await session.scalar(select(Trip).where(Trip.id == uuid.UUID(trip_id)))
        finally:
            await engine.dispose()

    persisted = asyncio.run(read_persisted_trip())
    assert persisted is not None
    assert persisted.check_in_date.isoformat() == "2026-10-10"
    assert persisted.check_out_date.isoformat() == "2026-10-15"
    assert persisted.trip_purpose == "BUSINESS"
    assert float(persisted.budget_min) == 100.0
    assert float(persisted.budget_max) == 250.0
    assert persisted.status == "ACTIVE"


def test_skip_allows_product_access_and_restarts_adaptive_onboarding() -> None:
    ids = asyncio.run(_prepare_catalog())
    provider = CountingE2EClaudeProvider()
    with TestClient(create_app(adaptive_provider=provider), base_url="http://localhost") as client:
        _, headers = _register(client, "skip")
        initial = client.get("/api/v1/onboarding/status", headers=headers)
        assert initial.status_code == 200, initial.text
        assert initial.json()["data"] == {
            "completed": False,
            "skipped": False,
            "active_session_id": None,
            "answered_count": 0,
            "question_count": 0,
            "mode": "ADAPTIVE",
            "generation_state": "IDLE",
            "completion_reason": None,
        }

        skipped = client.post("/api/v1/onboarding/skip", headers=headers)
        assert skipped.status_code == 200, skipped.text
        assert skipped.json()["data"]["skipped"] is True
        assert skipped.json()["data"]["completed"] is False
        assert provider.calls == 0

        skipped_status = client.get("/api/v1/onboarding/status", headers=headers)
        assert skipped_status.status_code == 200, skipped_status.text
        assert skipped_status.json()["data"]["skipped"] is True
        assert skipped_status.json()["data"]["active_session_id"] is None

        trip = client.post(
            "/api/v1/trips",
            json={
                "destination_country_id": str(ids["country"]),
                "destination_region_id": str(ids["region"]),
                "destination_city_id": str(ids["city"]),
                "trip_purpose": "LEISURE",
            },
            headers=headers,
        )
        assert trip.status_code == 201, trip.text
        recommendation = client.post(
            "/api/v1/recommendations",
            json={"trip_id": trip.json()["data"]["id"], "limit": 10},
            headers=headers,
        )
        assert recommendation.status_code == 200, recommendation.text
        assert len(recommendation.json()["data"]["results"]) == 2

        restarted = client.post("/api/v1/onboarding/start", json={}, headers=headers)
        assert restarted.status_code == 200, restarted.text
        restarted_data = restarted.json()["data"]
        assert restarted_data["status"] == "IN_PROGRESS"
        assert restarted_data["current_question"]["code"] == "adaptive_location"
        assert restarted_data["current_question"]["prompt"] == "Where are you travelling to?"
        assert provider.calls == 0

        answered = client.post(
            f"/api/v1/onboarding/{restarted_data['id']}/answers",
            json={
                "answers": [
                    {
                        "question_id": restarted_data["current_question"]["id"],
                        "answer": {"value": "Aizawl, Mizoram"},
                    }
                ]
            },
            headers=headers,
        )
        assert answered.status_code == 200, answered.text
        assert provider.calls == 1

        skipped_again = client.post("/api/v1/onboarding/skip", headers=headers)
        assert skipped_again.status_code == 200, skipped_again.text
        assert skipped_again.json()["data"]["status"] == "SKIPPED"
