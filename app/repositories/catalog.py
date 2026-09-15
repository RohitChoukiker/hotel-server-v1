"""Hotel, source, image, review, and attribute persistence."""

import uuid
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.dto.hotels import HotelSearchFilters
from app.dto.reviews import ReviewFilters
from app.models import (
    AlgorithmVersion,
    Amenity,
    Attribute,
    AttributeCategory,
    City,
    Country,
    DataSource,
    Hotel,
    HotelAmenity,
    HotelAttributeScore,
    HotelImage,
    HotelSourceMapping,
    Region,
    Review,
    ReviewAttributeMention,
    ReviewImage,
    ReviewProcessingStatus,
)


class SourceRepository:
    """Persist provider catalog metadata."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_code(self, code: str) -> DataSource | None:
        """Find an active data source by code."""
        return await self._session.scalar(
            select(DataSource).where(DataSource.code == code.upper(), DataSource.is_active)
        )


class HotelRepository:
    """Query canonical hotels without leaking ORM objects to controllers."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def _active_algorithm_id() -> Any:
        return (
            select(AlgorithmVersion.id)
            .where(AlgorithmVersion.is_active)
            .order_by(AlgorithmVersion.version.desc())
            .limit(1)
            .scalar_subquery()
        )

    def _summary_query(self) -> tuple[Any, Any]:
        source_ranked = (
            select(
                HotelSourceMapping.hotel_id,
                HotelSourceMapping.source_rating,
                HotelSourceMapping.source_review_count,
                func.row_number()
                .over(
                    partition_by=HotelSourceMapping.hotel_id,
                    order_by=(
                        HotelSourceMapping.source_review_count.desc().nullslast(),
                        HotelSourceMapping.source_rating.desc().nullslast(),
                        HotelSourceMapping.id.asc(),
                    ),
                )
                .label("rn"),
            )
            .where(HotelSourceMapping.is_active)
            .subquery()
        )
        primary_image = (
            select(
                HotelImage.hotel_id,
                HotelImage.image_url,
                func.row_number()
                .over(
                    partition_by=HotelImage.hotel_id,
                    order_by=(HotelImage.position.asc(), HotelImage.id.asc()),
                )
                .label("rn"),
            )
            .where(HotelImage.is_primary)
            .subquery()
        )
        query = (
            select(
                Hotel.id,
                Hotel.name,
                Hotel.hotel_type,
                City.name.label("city"),
                Region.name.label("region"),
                Country.name.label("country"),
                Hotel.latitude,
                Hotel.longitude,
                source_ranked.c.source_rating,
                source_ranked.c.source_review_count,
                primary_image.c.image_url.label("primary_image_url"),
            )
            .join(City, City.id == Hotel.city_id)
            .join(Region, Region.id == Hotel.region_id)
            .join(Country, Country.id == Hotel.country_id)
            .outerjoin(
                source_ranked,
                and_(
                    source_ranked.c.hotel_id == Hotel.id,
                    source_ranked.c.rn == 1,
                ),
            )
            .outerjoin(
                primary_image,
                and_(primary_image.c.hotel_id == Hotel.id, primary_image.c.rn == 1),
            )
            .where(Hotel.is_active, Hotel.deleted_at.is_(None))
        )
        return query, source_ranked

    async def search(
        self,
        filters: HotelSearchFilters,
        limit: int,
        cursor: dict[str, str],
    ) -> list[dict[str, Any]]:
        """Search hotels with keyset pagination and indexed filters."""
        query, source_ranked = self._summary_query()
        if filters.country_id:
            query = query.where(Hotel.country_id == filters.country_id)
        if filters.region_id:
            query = query.where(Hotel.region_id == filters.region_id)
        if filters.city_id:
            query = query.where(Hotel.city_id == filters.city_id)
        if filters.hotel_type:
            query = query.where(Hotel.hotel_type == filters.hotel_type)
        if filters.min_rating is not None:
            query = query.where(
                Hotel.id.in_(
                    select(HotelSourceMapping.hotel_id).where(
                        HotelSourceMapping.source_rating >= filters.min_rating,
                        HotelSourceMapping.is_active,
                    )
                )
            )
        if filters.max_rating is not None:
            query = query.where(
                Hotel.id.in_(
                    select(HotelSourceMapping.hotel_id).where(
                        HotelSourceMapping.source_rating <= filters.max_rating,
                        HotelSourceMapping.is_active,
                    )
                )
            )
        if filters.attribute_slugs:
            for slug in filters.attribute_slugs:
                query = query.where(
                    Hotel.id.in_(
                        select(HotelAttributeScore.hotel_id)
                        .join(Attribute, Attribute.id == HotelAttributeScore.attribute_id)
                        .where(
                            Attribute.slug == slug,
                            HotelAttributeScore.algorithm_version_id
                            == self._active_algorithm_id(),
                            HotelAttributeScore.score_5.is_not(None),
                        )
                    )
                )
        cursor_id = uuid.UUID(cursor["id"]) if "id" in cursor else None
        if filters.sort == "name_asc":
            if cursor_id and "name" in cursor:
                query = query.where(
                    or_(
                        Hotel.name > cursor["name"],
                        and_(Hotel.name == cursor["name"], Hotel.id > cursor_id),
                    )
                )
            query = query.order_by(Hotel.name, Hotel.id)
        elif filters.sort == "rating_desc":
            rating = func.coalesce(source_ranked.c.source_rating, -1.0)
            if cursor_id and "rating" in cursor:
                cursor_rating = float(cursor["rating"])
                query = query.where(
                    or_(rating < cursor_rating, and_(rating == cursor_rating, Hotel.id > cursor_id))
                )
            query = query.order_by(rating.desc(), Hotel.id)
        elif filters.sort == "review_count_desc":
            reviews = func.coalesce(source_ranked.c.source_review_count, -1)
            if cursor_id and "reviews" in cursor:
                cursor_reviews = int(cursor["reviews"])
                query = query.where(
                    or_(
                        reviews < cursor_reviews,
                        and_(reviews == cursor_reviews, Hotel.id > cursor_id),
                    )
                )
            query = query.order_by(reviews.desc(), Hotel.id)
        else:
            if cursor_id:
                query = query.where(Hotel.id > cursor_id)
            query = query.order_by(Hotel.id)
        query = query.limit(limit + 1)
        return [dict(row._mapping) for row in (await self._session.execute(query)).all()]

    async def nearby(
        self,
        latitude: float,
        longitude: float,
        radius_meters: int,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Return hotels within a PostGIS geography radius."""
        point = func.ST_SetSRID(func.ST_MakePoint(longitude, latitude), 4326).cast(
            Hotel.geo_location.type
        )
        distance = func.ST_Distance(Hotel.geo_location, point)
        summary_query, _ = self._summary_query()
        query = (
            summary_query
            .add_columns((distance / 1000.0).label("distance_km"))
            .where(func.ST_DWithin(Hotel.geo_location, point, radius_meters))
            .order_by(distance)
            .limit(limit)
        )
        return [dict(row._mapping) for row in (await self._session.execute(query)).all()]

    async def get_summary(self, hotel_id: uuid.UUID) -> dict[str, Any] | None:
        """Return one hotel summary projection."""
        summary_query, _ = self._summary_query()
        row = (await self._session.execute(summary_query.where(Hotel.id == hotel_id))).first()
        return dict(row._mapping) if row else None

    async def get_detail_row(self, hotel_id: uuid.UUID) -> dict[str, Any] | None:
        """Return a summary plus canonical contact/address fields."""
        summary_query, _ = self._summary_query()
        summary = summary_query.subquery()
        query = (
            select(
                *[summary.c[column] for column in summary.c.keys()],
                Hotel.address,
                Hotel.postal_code,
                Hotel.telephone,
            )
            .join(Hotel, Hotel.id == summary.c.id)
            .where(summary.c.id == hotel_id)
        )
        row = (await self._session.execute(query)).first()
        return dict(row._mapping) if row else None

    async def get_model(self, hotel_id: uuid.UUID) -> Hotel | None:
        """Return an active canonical hotel for service workflows."""
        return await self._session.scalar(
            select(Hotel).where(Hotel.id == hotel_id, Hotel.is_active, Hotel.deleted_at.is_(None))
        )

    async def images(self, hotel_id: uuid.UUID) -> list[HotelImage]:
        """List ordered hotel images."""
        return list(
            (
                await self._session.scalars(
                    select(HotelImage)
                    .where(HotelImage.hotel_id == hotel_id)
                    .order_by(HotelImage.position, HotelImage.id)
                )
            ).all()
        )

    async def upsert_image(
        self,
        hotel_id: uuid.UUID,
        source_id: uuid.UUID,
        image_url: str,
        position: int,
        is_primary: bool,
        created_at: datetime,
    ) -> None:
        """Idempotently create or refresh a source hotel image."""
        if is_primary:
            await self._session.execute(
                update(HotelImage)
                .where(HotelImage.hotel_id == hotel_id, HotelImage.is_primary)
                .values(is_primary=False)
            )
        statement = insert(HotelImage).values(
            id=uuid.uuid4(),
            hotel_id=hotel_id,
            source_id=source_id,
            image_url=image_url,
            position=position,
            is_primary=is_primary,
            created_at=created_at,
        )
        statement = statement.on_conflict_do_update(
            index_elements=["hotel_id", "image_url"],
            set_={
                "source_id": source_id,
                "position": position,
                "is_primary": is_primary,
            },
        )
        await self._session.execute(statement)

    async def source_rows(self, hotel_id: uuid.UUID) -> list[dict[str, Any]]:
        """List source mappings with source codes."""
        query = (
            select(
                DataSource.code,
                HotelSourceMapping.source_hotel_id,
                HotelSourceMapping.source_url,
                HotelSourceMapping.source_rating,
                HotelSourceMapping.source_review_count,
                HotelSourceMapping.source_rank,
                HotelSourceMapping.source_rank_text,
            )
            .join(DataSource, DataSource.id == HotelSourceMapping.source_id)
            .where(HotelSourceMapping.hotel_id == hotel_id, HotelSourceMapping.is_active)
        )
        return [dict(row._mapping) for row in (await self._session.execute(query)).all()]

    async def amenities(self, hotel_id: uuid.UUID) -> list[dict[str, Any]]:
        """Return factual amenity availability separately from review scores."""
        query = (
            select(
                Amenity.id,
                Amenity.name,
                Amenity.slug,
                Amenity.category,
                HotelAmenity.is_available,
            )
            .join(HotelAmenity, HotelAmenity.amenity_id == Amenity.id)
            .where(HotelAmenity.hotel_id == hotel_id, Amenity.is_active)
            .order_by(Amenity.category, Amenity.name)
        )
        return [dict(row._mapping) for row in (await self._session.execute(query)).all()]

    async def upsert_source_hotel(
        self,
        hotel_values: dict[str, Any],
        source_id: uuid.UUID,
        source_hotel_id: str,
        mapping_values: dict[str, Any],
    ) -> tuple[uuid.UUID, bool]:
        """Create or update a canonical hotel by exact source mapping."""
        mapping = await self._session.scalar(
            select(HotelSourceMapping).where(
                HotelSourceMapping.source_id == source_id,
                HotelSourceMapping.source_hotel_id == source_hotel_id,
            )
        )
        if mapping:
            hotel = await self._session.get(Hotel, mapping.hotel_id)
            if hotel is None:
                raise RuntimeError("Source mapping references a missing hotel")
            for key, value in hotel_values.items():
                setattr(hotel, key, value)
            for key, value in mapping_values.items():
                if key != "first_seen_at":
                    setattr(mapping, key, value)
            await self._session.flush()
            return hotel.id, False
        hotel = Hotel(**hotel_values)
        self._session.add(hotel)
        await self._session.flush()
        self._session.add(
            HotelSourceMapping(
                hotel_id=hotel.id,
                source_id=source_id,
                source_hotel_id=source_hotel_id,
                **mapping_values,
            )
        )
        await self._session.flush()
        return hotel.id, True

    async def source_mapping(
        self, source_id: uuid.UUID, source_hotel_id: str
    ) -> HotelSourceMapping | None:
        """Resolve an exact provider hotel identifier."""
        return await self._session.scalar(
            select(HotelSourceMapping).where(
                HotelSourceMapping.source_id == source_id,
                HotelSourceMapping.source_hotel_id == source_hotel_id,
                HotelSourceMapping.is_active,
            )
        )


class ReviewRepository:
    """Persist reviews and query review evidence."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, review_id: uuid.UUID) -> dict[str, Any] | None:
        """Return one review projection with its source code."""
        query = (
            select(
                Review.id,
                Review.hotel_id,
                DataSource.code.label("source_code"),
                Review.source_review_id,
                Review.source_rating,
                Review.source_rating_scale,
                Review.normalized_rating_5,
                Review.title,
                Review.review_text,
                Review.review_date,
                Review.reviewer_name,
                Review.trip_type,
                Review.language,
                Review.source_url,
                Review.scraped_at,
            )
            .join(DataSource, DataSource.id == Review.source_id)
            .where(Review.id == review_id, Review.deleted_at.is_(None))
        )
        row = (await self._session.execute(query)).first()
        return dict(row._mapping) if row else None

    async def images(self, review_id: uuid.UUID) -> list[ReviewImage]:
        """Return ordered review images."""
        return list(
            (
                await self._session.scalars(
                    select(ReviewImage)
                    .where(ReviewImage.review_id == review_id)
                    .order_by(ReviewImage.position, ReviewImage.id)
                )
            ).all()
        )

    async def upsert_image(
        self,
        review_id: uuid.UUID,
        image_url: str,
        position: int,
        created_at: datetime,
    ) -> None:
        """Idempotently create or refresh a review image."""
        statement = insert(ReviewImage).values(
            id=uuid.uuid4(),
            review_id=review_id,
            image_url=image_url,
            position=position,
            created_at=created_at,
        )
        statement = statement.on_conflict_do_update(
            index_elements=["review_id", "image_url"],
            set_={"position": position},
        )
        await self._session.execute(statement)

    async def list_for_hotel(
        self,
        hotel_id: uuid.UUID,
        filters: ReviewFilters,
        limit: int,
        cursor_value: date | float | None,
        cursor_id: uuid.UUID | None,
    ) -> list[dict[str, Any]]:
        """Keyset-page filtered reviews for one hotel."""
        query = (
            select(
                Review.id,
                Review.hotel_id,
                DataSource.code.label("source_code"),
                Review.source_review_id,
                Review.source_rating,
                Review.source_rating_scale,
                Review.normalized_rating_5,
                Review.title,
                Review.review_text,
                Review.review_date,
                Review.reviewer_name,
                Review.trip_type,
                Review.language,
                Review.source_url,
                Review.scraped_at,
            )
            .join(DataSource, DataSource.id == Review.source_id)
            .where(Review.hotel_id == hotel_id, Review.deleted_at.is_(None))
        )
        if filters.rating is not None:
            query = query.where(Review.normalized_rating_5 == filters.rating)
        if filters.trip_type:
            query = query.where(Review.trip_type == filters.trip_type)
        if filters.source:
            query = query.where(DataSource.code == filters.source.upper())
        if filters.date_from:
            query = query.where(Review.review_date >= filters.date_from)
        if filters.date_to:
            query = query.where(Review.review_date <= filters.date_to)
        if filters.attribute or filters.sentiment:
            query = query.join(
                ReviewAttributeMention, ReviewAttributeMention.review_id == Review.id
            ).join(Attribute, Attribute.id == ReviewAttributeMention.attribute_id)
            if filters.attribute:
                query = query.where(Attribute.slug == filters.attribute)
            if filters.sentiment:
                query = query.where(ReviewAttributeMention.sentiment == filters.sentiment.value)
        if filters.sort in {"newest", "oldest"}:
            null_sentinel = date.min if filters.sort == "newest" else date.max
            sort_column = func.coalesce(Review.review_date, null_sentinel)
        else:
            sort_column = Review.normalized_rating_5
        descending = filters.sort in {"newest", "rating_high"}
        if cursor_value is not None and cursor_id is not None:
            comparator = sort_column < cursor_value if descending else sort_column > cursor_value
            id_comparator = Review.id < cursor_id if descending else Review.id > cursor_id
            query = query.where(
                or_(
                    comparator,
                    and_(sort_column == cursor_value, id_comparator),
                )
            )
        ordering = sort_column.desc() if descending else sort_column.asc()
        id_ordering = Review.id.desc() if descending else Review.id.asc()
        query = query.order_by(ordering, id_ordering).limit(limit + 1)
        return [dict(row._mapping) for row in (await self._session.execute(query)).all()]

    async def upsert_review(self, values: dict[str, Any]) -> tuple[uuid.UUID, bool]:
        """Idempotently insert or update a review by provider identity."""
        existing = await self._session.scalar(
            select(Review.id).where(
                Review.source_id == values["source_id"],
                Review.source_review_id == values["source_review_id"],
            )
        )
        statement = (
            insert(Review)
            .values(**values)
            .on_conflict_do_update(
                constraint="uq_reviews_source_id",
                set_={
                    key: value
                    for key, value in values.items()
                    if key not in {"source_id", "source_review_id", "id", "created_at"}
                },
            )
            .returning(Review.id)
        )
        review_id = (await self._session.execute(statement)).scalar_one()
        return review_id, existing is None

    async def unprocessed(self, algorithm_version_id: uuid.UUID, limit: int) -> list[Review]:
        """Return reviews without a completed extraction for an algorithm version."""
        terminal_query = select(ReviewProcessingStatus.review_id).where(
            ReviewProcessingStatus.review_id == Review.id,
            ReviewProcessingStatus.algorithm_version_id == algorithm_version_id,
            or_(
                ReviewProcessingStatus.status == "COMPLETED",
                ReviewProcessingStatus.attempt_count >= 5,
            ),
        )
        return list(
            (
                await self._session.scalars(
                    select(Review)
                    .where(Review.deleted_at.is_(None), ~terminal_query.exists())
                    .order_by(Review.id)
                    .limit(limit)
                )
            ).all()
        )

    async def mark_processing_status(
        self,
        review_id: uuid.UUID,
        algorithm_version_id: uuid.UUID,
        status: str,
        mention_count: int,
        error: str | None = None,
    ) -> None:
        """Upsert terminal or retryable review extraction state."""
        now = datetime.now(UTC)
        statement = insert(ReviewProcessingStatus).values(
            review_id=review_id,
            algorithm_version_id=algorithm_version_id,
            status=status,
            attempt_count=1,
            mention_count=mention_count,
            error=error,
            processed_at=now,
            updated_at=now,
        )
        statement = statement.on_conflict_do_update(
            index_elements=["review_id", "algorithm_version_id"],
            set_={
                "status": status,
                "attempt_count": ReviewProcessingStatus.attempt_count + 1,
                "mention_count": mention_count,
                "error": error,
                "processed_at": now,
                "updated_at": now,
            },
        )
        await self._session.execute(statement)

    async def has_unprocessed(self, algorithm_version_id: uuid.UUID) -> bool:
        """Return whether another bounded extraction batch is available."""
        return bool(await self.unprocessed(algorithm_version_id, 1))


class AttributeRepository:
    """Attribute taxonomy and score persistence."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_attributes(self) -> list[dict[str, Any]]:
        """Return active attributes with category metadata."""
        query = (
            select(
                Attribute.id,
                Attribute.category_id,
                AttributeCategory.name.label("category_name"),
                Attribute.name,
                Attribute.slug,
                Attribute.description,
            )
            .join(AttributeCategory, AttributeCategory.id == Attribute.category_id)
            .where(Attribute.is_active, AttributeCategory.is_active)
            .order_by(AttributeCategory.display_order, Attribute.display_order)
        )
        return [dict(row._mapping) for row in (await self._session.execute(query)).all()]

    async def get_by_slug(self, slug: str) -> Attribute | None:
        """Return an active attribute by slug."""
        return await self._session.scalar(
            select(Attribute).where(Attribute.slug == slug, Attribute.is_active)
        )

    async def hotel_scores(
        self,
        hotel_id: uuid.UUID,
        attribute_id: uuid.UUID | None = None,
    ) -> list[dict[str, Any]]:
        """Return precomputed attribute scores for a hotel."""
        query = (
            select(
                HotelAttributeScore.attribute_id,
                Attribute.name,
                Attribute.slug,
                HotelAttributeScore.mention_count,
                HotelAttributeScore.positive_mentions,
                HotelAttributeScore.negative_mentions,
                HotelAttributeScore.neutral_mentions,
                HotelAttributeScore.score_5,
                HotelAttributeScore.score_100,
                HotelAttributeScore.z_score,
                HotelAttributeScore.relative_score_5,
                HotelAttributeScore.confidence_score,
                HotelAttributeScore.calculated_at,
            )
            .join(Attribute, Attribute.id == HotelAttributeScore.attribute_id)
            .where(
                HotelAttributeScore.hotel_id == hotel_id,
                HotelAttributeScore.algorithm_version_id
                == HotelRepository._active_algorithm_id(),
            )
            .order_by(Attribute.display_order)
        )
        if attribute_id:
            query = query.where(HotelAttributeScore.attribute_id == attribute_id)
        return [dict(row._mapping) for row in (await self._session.execute(query)).all()]

    async def active_algorithm(self) -> AlgorithmVersion | None:
        """Return the latest active scoring algorithm."""
        return await self._session.scalar(
            select(AlgorithmVersion)
            .where(AlgorithmVersion.is_active)
            .order_by(AlgorithmVersion.version.desc())
            .limit(1)
        )

    async def add_mentions(self, mentions: list[ReviewAttributeMention]) -> None:
        """Idempotently stage extracted mentions."""
        for mention in mentions:
            statement = insert(ReviewAttributeMention).values(
                id=mention.id or uuid.uuid4(),
                review_id=mention.review_id,
                attribute_id=mention.attribute_id,
                sentiment=mention.sentiment,
                sentiment_value=mention.sentiment_value,
                review_rating_used=mention.review_rating_used,
                calculated_contribution=mention.calculated_contribution,
                confidence=mention.confidence,
                evidence_text=mention.evidence_text,
                algorithm_version_id=mention.algorithm_version_id,
                created_at=mention.created_at,
            )
            statement = statement.on_conflict_do_nothing(
                index_elements=[
                    "review_id",
                    "attribute_id",
                    "evidence_text",
                    "algorithm_version_id",
                ]
            )
            await self._session.execute(statement)

    async def hotel_ids_for_scoring(self, hotel_id: uuid.UUID | None = None) -> list[uuid.UUID]:
        """Return hotels with attribute mentions."""
        query = (
            select(Review.hotel_id)
            .join(ReviewAttributeMention, ReviewAttributeMention.review_id == Review.id)
            .distinct()
        )
        if hotel_id:
            query = query.where(Review.hotel_id == hotel_id)
        return list((await self._session.scalars(query)).all())

    async def contribution_rows(
        self, hotel_id: uuid.UUID, algorithm_version_id: uuid.UUID
    ) -> list[dict[str, Any]]:
        """Aggregate mention contribution counts and averages by attribute."""
        query = (
            select(
                ReviewAttributeMention.attribute_id,
                func.count().label("mention_count"),
                func.count()
                .filter(ReviewAttributeMention.sentiment == "POSITIVE")
                .label("positive_mentions"),
                func.count()
                .filter(ReviewAttributeMention.sentiment == "NEGATIVE")
                .label("negative_mentions"),
                func.count()
                .filter(ReviewAttributeMention.sentiment == "NEUTRAL")
                .label("neutral_mentions"),
                func.avg(ReviewAttributeMention.calculated_contribution).label("raw_score"),
                func.avg(ReviewAttributeMention.confidence).label("mean_confidence"),
            )
            .join(Review, Review.id == ReviewAttributeMention.review_id)
            .where(
                Review.hotel_id == hotel_id,
                ReviewAttributeMention.algorithm_version_id == algorithm_version_id,
            )
            .group_by(ReviewAttributeMention.attribute_id)
        )
        return [dict(row._mapping) for row in (await self._session.execute(query)).all()]

    async def upsert_hotel_score(self, values: dict[str, Any]) -> None:
        """Upsert one precomputed hotel score."""
        statement = insert(HotelAttributeScore).values(**values).on_conflict_do_update(
            index_elements=["hotel_id", "attribute_id", "algorithm_version_id"],
            set_={
                key: value
                for key, value in values.items()
                if key not in {"hotel_id", "attribute_id", "algorithm_version_id"}
            },
        )
        await self._session.execute(statement)

    async def score_population(
        self,
        attribute_id: uuid.UUID,
        algorithm_version_id: uuid.UUID,
        country_id: uuid.UUID | None = None,
        region_id: uuid.UUID | None = None,
        city_id: uuid.UUID | None = None,
        hotel_type: str | None = None,
        hotel_ids: list[uuid.UUID] | None = None,
    ) -> list[HotelAttributeScore]:
        """Return a comparable population of non-missing attribute scores."""
        query = (
            select(HotelAttributeScore)
            .join(Hotel, Hotel.id == HotelAttributeScore.hotel_id)
            .where(
                HotelAttributeScore.attribute_id == attribute_id,
                HotelAttributeScore.algorithm_version_id == algorithm_version_id,
                HotelAttributeScore.score_5.is_not(None),
                Hotel.is_active,
                Hotel.deleted_at.is_(None),
            )
        )
        if country_id:
            query = query.where(Hotel.country_id == country_id)
        if region_id:
            query = query.where(Hotel.region_id == region_id)
        if city_id:
            query = query.where(Hotel.city_id == city_id)
        if hotel_type:
            query = query.where(Hotel.hotel_type == hotel_type)
        if hotel_ids:
            query = query.where(Hotel.id.in_(hotel_ids))
        return list((await self._session.scalars(query)).all())

    async def attribute_ids_with_scores(
        self, algorithm_version_id: uuid.UUID
    ) -> list[uuid.UUID]:
        """Return distinct scored attributes for an algorithm version."""
        return list(
            (
                await self._session.scalars(
                    select(HotelAttributeScore.attribute_id)
                    .where(HotelAttributeScore.algorithm_version_id == algorithm_version_id)
                    .distinct()
                )
            ).all()
        )
