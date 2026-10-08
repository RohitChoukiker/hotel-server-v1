"""Persistence for dynamic sentiment taxonomy and source aggregates."""

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.sentiment import (
    deterministic_attribute_slug,
    deterministic_category_code,
    normalize_taxonomy_key,
)
from app.exceptions import ImportValidationError
from app.models import (
    Attribute,
    AttributeAlias,
    AttributeCategory,
    HotelAttributeSentimentAggregate,
)


@dataclass(frozen=True, slots=True)
class TaxonomyResolution:
    """Resolved attribute and whether the importer created it."""

    attribute_id: uuid.UUID
    category_key: str
    slug: str
    auto_created: bool


class AmbiguousAttributeAlias(Exception):  # noqa: N818
    """More than one active attribute matches a category-scoped alias."""

    def __init__(self, raw_key: str, attribute_ids: tuple[uuid.UUID, ...]) -> None:
        self.raw_key = raw_key
        self.attribute_ids = attribute_ids
        super().__init__(f"AMBIGUOUS_ATTRIBUTE_ALIAS: {raw_key}")


@dataclass(frozen=True, slots=True)
class AggregateUpsertResult:
    """Persisted aggregates and insert/update classification."""

    rows: list[HotelAttributeSentimentAggregate]
    inserted: int
    updated: int


class SentimentAggregateRepository:
    """Resolve dynamic taxonomy keys and upsert aggregate evidence."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def resolve_attribute(
        self, raw_category_key: str, raw_attribute_key: str
    ) -> TaxonomyResolution:
        category_key = normalize_taxonomy_key(raw_category_key)
        attribute_key = normalize_taxonomy_key(raw_attribute_key)
        if len(category_key) > 160 or len(attribute_key) > 160:
            raise ImportValidationError("Taxonomy key exceeds the 160 character limit")

        category = await self._category(category_key, raw_category_key)
        direct = await self._session.scalar(
            select(Attribute).where(
                Attribute.category_id == category.id,
                Attribute.normalized_key == attribute_key,
                Attribute.is_active,
            )
        )
        if direct is not None:
            return TaxonomyResolution(direct.id, category_key, direct.slug, False)

        aliases = await self._session.scalars(
            select(Attribute)
            .join(AttributeAlias, AttributeAlias.attribute_id == Attribute.id)
            .where(
                Attribute.category_id == category.id,
                Attribute.is_active,
                AttributeAlias.is_active,
            )
        )
        matching: dict[uuid.UUID, Attribute] = {}
        for attribute in aliases:
            for alias in await self._aliases(attribute.id):
                try:
                    matches = normalize_taxonomy_key(alias) == attribute_key
                except Exception:
                    matches = False
                if matches:
                    matching[attribute.id] = attribute
                    break
        if len(matching) > 1:
            raise AmbiguousAttributeAlias(raw_attribute_key, tuple(sorted(matching)))
        if matching:
            attribute = next(iter(matching.values()))
            return TaxonomyResolution(attribute.id, category_key, attribute.slug, False)

        used_slugs = set((await self._session.scalars(select(Attribute.slug))).all())
        slug = deterministic_attribute_slug(category_key, attribute_key, used_slugs)
        statement = (
            insert(Attribute)
            .values(
                id=uuid.uuid4(),
                category_id=category.id,
                name=raw_attribute_key.strip()[:120],
                slug=slug,
                normalized_key=attribute_key,
                description="Source-provided sentiment attribute",
                value_type="SCORE",
                display_order=0,
                is_active=True,
            )
            .on_conflict_do_nothing(index_elements=["category_id", "normalized_key"])
        )
        await self._session.execute(statement)
        attribute = await self._session.scalar(
            select(Attribute).where(
                Attribute.category_id == category.id,
                Attribute.normalized_key == attribute_key,
            )
        )
        if attribute is None:
            raise RuntimeError("Attribute insert conflict did not resolve to an attribute")
        return TaxonomyResolution(attribute.id, category_key, attribute.slug, True)

    async def _category(self, category_key: str, raw_category_key: str) -> AttributeCategory:
        category = await self._session.scalar(
            select(AttributeCategory).where(
                AttributeCategory.normalized_key == category_key,
                AttributeCategory.is_active,
            )
        )
        if category is not None:
            return category
        statement = (
            insert(AttributeCategory)
            .values(
                id=uuid.uuid4(),
                code=deterministic_category_code(category_key),
                name=raw_category_key.strip()[:120],
                normalized_key=category_key,
                display_order=0,
                is_active=True,
            )
            .on_conflict_do_nothing(index_elements=["normalized_key"])
        )
        await self._session.execute(statement)
        category = await self._session.scalar(
            select(AttributeCategory).where(AttributeCategory.normalized_key == category_key)
        )
        if category is None:
            raise RuntimeError("Category insert conflict did not resolve to a category")
        return category

    async def _aliases(self, attribute_id: uuid.UUID) -> list[str]:
        return list(
            (
                await self._session.scalars(
                    select(AttributeAlias.alias).where(
                        AttributeAlias.attribute_id == attribute_id,
                        AttributeAlias.is_active,
                    )
                )
            ).all()
        )

    async def upsert_many(self, values: list[dict[str, Any]]) -> AggregateUpsertResult:
        """Upsert one hotel's aggregate rows using the five-part identity."""
        if not values:
            return AggregateUpsertResult([], 0, 0)
        identities = [
            (
                value["hotel_id"],
                value["attribute_id"],
                value["source_id"],
                value["analysis_version"],
                value["review_window"],
            )
            for value in values
        ]
        existing = {
            (
                row.hotel_id,
                row.attribute_id,
                row.source_id,
                row.analysis_version,
                row.review_window,
            )
            for row in (
                await self._session.scalars(
                    select(HotelAttributeSentimentAggregate).where(
                        tuple_(
                            HotelAttributeSentimentAggregate.hotel_id,
                            HotelAttributeSentimentAggregate.attribute_id,
                            HotelAttributeSentimentAggregate.source_id,
                            HotelAttributeSentimentAggregate.analysis_version,
                            HotelAttributeSentimentAggregate.review_window,
                        ).in_(identities)
                    )
                )
            ).all()
        }
        statement = insert(HotelAttributeSentimentAggregate).values(values)
        statement = statement.on_conflict_do_update(
            index_elements=[
                "hotel_id",
                "attribute_id",
                "source_id",
                "analysis_version",
                "review_window",
            ],
            set_={
                "category_name": statement.excluded.category_name,
                "raw_attribute_key": statement.excluded.raw_attribute_key,
                "sentiment": statement.excluded.sentiment,
                "positive_mentions": statement.excluded.positive_mentions,
                "negative_mentions": statement.excluded.negative_mentions,
                "total_mentions": statement.excluded.total_mentions,
                "reviews_analyzed": statement.excluded.reviews_analyzed,
                "analysis_mode": statement.excluded.analysis_mode,
                "aggregate_score_0_5": statement.excluded.aggregate_score_0_5,
                "scoring_source": statement.excluded.scoring_source,
                "metadata": statement.excluded.metadata,
                "updated_at": func.now(),
            },
        ).returning(HotelAttributeSentimentAggregate)
        rows = list((await self._session.scalars(statement)).all())
        inserted = sum(
            1
            for row in rows
            if (
                row.hotel_id,
                row.attribute_id,
                row.source_id,
                row.analysis_version,
                row.review_window,
            )
            not in existing
        )
        return AggregateUpsertResult(rows, inserted, len(rows) - inserted)
