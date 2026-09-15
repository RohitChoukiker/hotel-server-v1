"""Idempotently seed India, source, taxonomy, algorithm, and onboarding data."""

import asyncio
import uuid
from datetime import UTC, datetime

from sqlalchemy.dialects.postgresql import insert

from app.config import get_settings
from app.database import create_engine, create_session_factory
from app.models import (
    AlgorithmVersion,
    Attribute,
    AttributeAlias,
    AttributeCategory,
    Country,
    DataSource,
    OnboardingQuestion,
    OnboardingQuestionSet,
    Region,
)

NAMESPACE = uuid.UUID("5e516df0-e74d-4c48-8e28-e115d94dbe66")


def stable_id(value: str) -> uuid.UUID:
    """Generate deterministic reference-data UUIDs."""
    return uuid.uuid5(NAMESPACE, value)


INDIA_REGIONS = [
    ("Andhra Pradesh", "AP", "STATE"),
    ("Arunachal Pradesh", "AR", "STATE"),
    ("Assam", "AS", "STATE"),
    ("Bihar", "BR", "STATE"),
    ("Chhattisgarh", "CG", "STATE"),
    ("Goa", "GA", "STATE"),
    ("Gujarat", "GJ", "STATE"),
    ("Haryana", "HR", "STATE"),
    ("Himachal Pradesh", "HP", "STATE"),
    ("Jharkhand", "JH", "STATE"),
    ("Karnataka", "KA", "STATE"),
    ("Kerala", "KL", "STATE"),
    ("Madhya Pradesh", "MP", "STATE"),
    ("Maharashtra", "MH", "STATE"),
    ("Manipur", "MN", "STATE"),
    ("Meghalaya", "ML", "STATE"),
    ("Mizoram", "MZ", "STATE"),
    ("Nagaland", "NL", "STATE"),
    ("Odisha", "OD", "STATE"),
    ("Punjab", "PB", "STATE"),
    ("Rajasthan", "RJ", "STATE"),
    ("Sikkim", "SK", "STATE"),
    ("Tamil Nadu", "TN", "STATE"),
    ("Telangana", "TS", "STATE"),
    ("Tripura", "TR", "STATE"),
    ("Uttar Pradesh", "UP", "STATE"),
    ("Uttarakhand", "UK", "STATE"),
    ("West Bengal", "WB", "STATE"),
    ("Andaman and Nicobar Islands", "AN", "UNION_TERRITORY"),
    ("Chandigarh", "CH", "UNION_TERRITORY"),
    ("Dadra and Nagar Haveli and Daman and Diu", "DH", "UNION_TERRITORY"),
    ("Delhi", "DL", "UNION_TERRITORY"),
    ("Jammu and Kashmir", "JK", "UNION_TERRITORY"),
    ("Ladakh", "LA", "UNION_TERRITORY"),
    ("Lakshadweep", "LD", "UNION_TERRITORY"),
    ("Puducherry", "PY", "UNION_TERRITORY"),
]

ATTRIBUTE_GROUPS = {
    "ACCOMMODATION": [
        "Cleanliness",
        "Room Size",
        "Bed Comfort",
        "Bathroom",
        "Room View",
        "Room Condition",
        "Noise Level",
        "Room Comfort",
    ],
    "AMENITIES": [
        "WiFi",
        "Breakfast",
        "Pool",
        "Gym",
        "Spa",
        "Parking",
        "Restaurant",
        "Room Service",
    ],
    "SURROUNDING": [
        "Beach Access",
        "Quietness",
        "Airport Accessibility",
        "City Center Access",
        "Public Transport",
        "Nightlife",
        "Nearby Restaurants",
        "Tourist Attractions",
    ],
}


def slugify(value: str) -> str:
    """Create stable lowercase hyphenated slugs."""
    return value.casefold().replace(" ", "-")


async def seed() -> None:
    """Insert reference data idempotently."""
    settings = get_settings()
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    now = datetime.now(UTC)
    india_id = stable_id("country:IN")
    question_set_id = stable_id("onboarding:hotel-preferences:v1")
    async with factory() as session:
        await session.execute(
            insert(Country)
            .values(
                id=india_id,
                iso2_code="IN",
                iso3_code="IND",
                name="India",
                is_active=True,
            )
            .on_conflict_do_nothing(index_elements=["iso2_code"])
        )
        for name, code, region_type in INDIA_REGIONS:
            await session.execute(
                insert(Region)
                .values(
                    id=stable_id(f"region:IN:{code}"),
                    country_id=india_id,
                    name=name,
                    code=code,
                    region_type=region_type,
                    is_active=True,
                )
                .on_conflict_do_nothing(index_elements=["country_id", "name"])
            )
        await session.execute(
            insert(DataSource)
            .values(
                id=stable_id("source:TRIPADVISOR"),
                code="TRIPADVISOR",
                name="TripAdvisor",
                source_type="HOTEL_AND_REVIEW",
                base_url="https://www.tripadvisor.in",
                is_active=True,
            )
            .on_conflict_do_nothing(index_elements=["code"])
        )
        for category_order, (code, names) in enumerate(ATTRIBUTE_GROUPS.items(), start=1):
            category_id = stable_id(f"attribute-category:{code}")
            await session.execute(
                insert(AttributeCategory)
                .values(
                    id=category_id,
                    code=code,
                    name=code.title(),
                    display_order=category_order,
                    is_active=True,
                )
                .on_conflict_do_nothing(index_elements=["code"])
            )
            for display_order, name in enumerate(names, start=1):
                slug = slugify(name)
                attribute_id = stable_id(f"attribute:{slug}")
                await session.execute(
                    insert(Attribute)
                    .values(
                        id=attribute_id,
                        category_id=category_id,
                        name=name,
                        slug=slug,
                        description=f"Review-derived {name.lower()} score",
                        value_type="SCORE",
                        display_order=display_order,
                        is_active=True,
                    )
                    .on_conflict_do_nothing(index_elements=["slug"])
                )
                await session.execute(
                    insert(AttributeAlias)
                    .values(
                        id=stable_id(f"attribute-alias:{slug}:en:{name.casefold()}"),
                        attribute_id=attribute_id,
                        alias=name.casefold(),
                        language="en",
                        is_active=True,
                        created_at=now,
                    )
                    .on_conflict_do_nothing(
                        index_elements=["attribute_id", "alias", "language"]
                    )
                )
        await session.execute(
            insert(AlgorithmVersion)
            .values(
                id=stable_id("algorithm:hotel-attribute:1"),
                name="hotel-attribute-scoring",
                version=1,
                description="Fixed review rating +/- 1 contribution with bell-curve normalization",
                configuration={
                    "positive_adjustment": 1,
                    "negative_adjustment": -1,
                    "attribute_scale": 100,
                    "relative_scale": 5,
                    "normalization": "bell_curve",
                    "relative_mapping": "standard_normal_cdf",
                },
                is_active=True,
                created_at=now,
            )
            .on_conflict_do_nothing(index_elements=["name", "version"])
        )
        await session.execute(
            insert(OnboardingQuestionSet)
            .values(
                id=question_set_id,
                name="hotel-preferences",
                version=1,
                is_active=True,
            )
            .on_conflict_do_nothing(index_elements=["name", "version"])
        )
        questions: list[tuple[str, str, list[str]]] = [
            (
                "room_priorities",
                "Which room qualities matter most?",
                ["cleanliness", "room-comfort", "bed-comfort", "room-size"],
            ),
            (
                "room_environment",
                "What room environment do you prefer?",
                ["quietness", "noise-level", "room-view", "room-condition"],
            ),
            (
                "essential_amenities",
                "Which amenities are essential?",
                ["wifi", "breakfast", "pool", "parking"],
            ),
            ("wellness", "How important are wellness facilities?", ["gym", "spa", "pool"]),
            (
                "food",
                "What food services do you value?",
                ["breakfast", "restaurant", "room-service"],
            ),
            (
                "connectivity",
                "Which transport connections matter?",
                ["airport-accessibility", "public-transport", "city-center-access"],
            ),
            (
                "surroundings",
                "What surroundings fit your trip?",
                ["beach-access", "quietness", "nightlife", "nearby-restaurants"],
            ),
            (
                "sightseeing",
                "How important is nearby sightseeing?",
                ["tourist-attractions", "city-center-access"],
            ),
        ]
        for position, (code, prompt, options) in enumerate(questions, start=1):
            await session.execute(
                insert(OnboardingQuestion)
                .values(
                    id=stable_id(f"onboarding-question:v1:{code}"),
                    question_set_id=question_set_id,
                    code=code,
                    prompt=prompt,
                    answer_type="MULTI_WEIGHT",
                    options=[
                        {"value": item, "label": item.replace("-", " ").title()}
                        for item in options
                    ],
                    position=position,
                    is_required=True,
                )
                .on_conflict_do_nothing(index_elements=["question_set_id", "position"])
            )
        await session.commit()
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed())
