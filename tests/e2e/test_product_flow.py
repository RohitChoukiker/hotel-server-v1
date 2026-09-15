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
    User,
)
from scripts.seed_reference_data import seed, stable_id

pytestmark = pytest.mark.e2e


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
        source = await session.scalar(
            select(DataSource).where(DataSource.code == "TRIPADVISOR")
        )
        algorithm = await session.scalar(
            select(AlgorithmVersion).where(AlgorithmVersion.is_active)
        )
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
        await session.execute(
            update(User).where(User.id == user_id).values(role="ADMIN")
        )
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
    with TestClient(create_app()) as client:
        user_id, headers = _register(client, "owner")
        _, other_headers = _register(client, "other")

        started = client.post(
            "/api/v1/onboarding/start", json={}, headers=headers
        )
        assert started.status_code == 200, started.text
        onboarding_id = started.json()["data"]["id"]
        questions = client.get(
            f"/api/v1/onboarding/{onboarding_id}/questions", headers=headers
        )
        assert questions.status_code == 200
        question_rows = questions.json()["data"]
        assert len(question_rows) == 8
        answers = [
            {
                "question_id": question["id"],
                "answer": {"selected": [question["options"][0]["value"]]},
            }
            for question in question_rows
        ]
        answered = client.post(
            f"/api/v1/onboarding/{onboarding_id}/answers",
            json={"answers": answers},
            headers=headers,
        )
        assert answered.status_code == 200, answered.text
        completed = client.post(
            f"/api/v1/onboarding/{onboarding_id}/complete", headers=headers
        )
        assert completed.status_code == 200, completed.text
        assert completed.json()["data"]["preferences_created"] > 0

        manual = client.patch(
            f"/api/v1/users/me/preferences/{ids['wifi']}",
            json={"attribute_id": str(ids["wifi"]), "importance_weight": 1.0},
            headers=headers,
        )
        assert manual.status_code == 200, manual.text
        preference = next(
            item
            for item in manual.json()["data"]
            if item["attribute_id"] == str(ids["wifi"])
        )
        assert preference["preference_source"] == "USER_MANUAL"

        trip_payload = {
            "destination_country_id": str(ids["country"]),
            "destination_region_id": str(ids["region"]),
            "destination_city_id": str(ids["city"]),
            "trip_purpose": "LEISURE",
        }
        trip_response = client.post(
            "/api/v1/trips", json=trip_payload, headers=headers
        )
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
        assert run["results"][0]["personalized_rating"] >= run["results"][1][
            "personalized_rating"
        ]
        assert run["results"][0]["explanation"]["matched_attributes"]
        run_id = run["recommendation_run_id"]
        cached_run = client.get(
            f"/api/v1/recommendations/{run_id}", headers=headers
        )
        assert cached_run.status_code == 200
        denied_run = client.get(
            f"/api/v1/recommendations/{run_id}", headers=other_headers
        )
        assert denied_run.status_code == 404

        nearby = client.get(
            "/api/v1/hotels/nearby?lat=15.49&lng=73.83&radius=10",
            headers=headers,
        )
        assert nearby.status_code == 200, nearby.text
        assert len(nearby.json()["data"]) == 2
        detail = client.get(f"/api/v1/hotels/{ids['hotel_a']}")
        assert detail.status_code == 200, detail.text
        reviews = client.get(
            f"/api/v1/hotels/{ids['hotel_a']}/reviews"
            "?attribute=wifi&sentiment=NEGATIVE"
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
        history = client.get(
            f"/api/v1/chat/conversations/{conversation_id}", headers=headers
        )
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
