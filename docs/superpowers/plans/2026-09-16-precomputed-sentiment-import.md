# Precomputed Hotel Sentiment Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a streamed, idempotent JSONL admin import that stores dynamic hotel sentiment aggregates and propagates balance-based scores without fabricating review evidence.

**Architecture:** A dedicated sentiment JSONL orchestrator reuses immutable file staging, import jobs, source-hotel mapping, data-quality issues, the attribute taxonomy, and score normalization while remaining separate from `CSVImportOrchestrator`. A new aggregate repository owns taxonomy resolution and aggregate UPSERTs; the scoring repository owns the atomic `review_based > aggregate_sentiment` precedence rule.

**Tech Stack:** Python 3.12, FastAPI, Pydantic 2, async SQLAlchemy 2, PostgreSQL 16/PostGIS, Alembic, Celery, pytest/pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-09-16-precomputed-sentiment-import-design.md`

## Global Constraints

- Do not create or modify `review_attribute_mentions` from sentiment imports.
- Do not invoke an LLM or fabricate review ratings for aggregate sentiment.
- Keep the existing hotel/review CSV import and per-review `rating ± 1` scoring behavior intact.
- Resolve input `hotel_id` through exact `(source_id, source_hotel_id)` mapping; never assume it is the internal hotel UUID.
- Keep aggregate identity exactly `(hotel_id, attribute_id, source_id, analysis_version, review_window)` with non-null `source_id` and an FK using `RESTRICT`.
- Keep score precedence centralized and atomic: `review_based > aggregate_sentiment`.
- Keep `records_read == hotels_read`, `failed == hotels_failed`, and `failed <= records_read` for sentiment jobs.
- Preserve all existing public attribute slugs; deterministic collision handling applies only to auto-created attributes.
- Abort taxonomy backfill on normalized-key collisions; never merge, rename, or delete existing taxonomy during migration.
- Run normalization after either `COMPLETED` or `PARTIAL` when `score_updates_applied > 0`.
- Preserve unrelated uncommitted user work currently present in `README.md`, `app/orchestrators/imports.py`, `tests/integration/test_imports.py`, and the city-normalization files. Stage only task-specific paths at each commit.

---

## File Structure

### New files

- `app/domain/sentiment.py` — pure normalization, validation, deterministic slug, aggregate score, and support-confidence functions.
- `app/repositories/sentiment.py` — category/attribute resolution, normalized alias handling, and aggregate persistence.
- `app/services/aggregate_scoring.py` — converts persisted aggregates into hotel score rows and invokes shared normalization.
- `app/orchestrators/sentiment_imports.py` — streamed JSONL job execution, per-hotel savepoints, counters, issues, and checkpoints.
- `alembic/versions/20260916_0004_precomputed_sentiment_import.py` — taxonomy keys, aggregate table, score provenance, and import counters.
- `tests/unit/test_sentiment_domain.py` — pure contract and formula tests.
- `tests/unit/test_sentiment_migration.py` — migration collision and downgrade-guard tests.
- `tests/integration/test_sentiment_repository.py` — real PostgreSQL taxonomy, aggregate UPSERT, and score precedence tests.
- `tests/integration/test_sentiment_imports.py` — streamed multi-hotel import behavior and counter tests.
- `tests/integration/test_sentiment_import_api.py` — authenticated multipart endpoint and staging/dispatch acceptance test.

### Modified files

- `app/models/scoring.py` — normalized taxonomy keys, aggregate ORM model, and score provenance.
- `app/models/operations.py` — explicit sentiment import counters.
- `app/models/__init__.py` — export the aggregate model.
- `app/repositories/catalog.py` — atomic aggregate/review score precedence operations and explicit review provenance.
- `app/services/scoring.py` — share normalization without requiring a text interpreter and mark review recalculations `review_based`.
- `app/enums.py` — add `SENTIMENT_ANALYSIS` import type and a `ScoringSource` enum with `REVIEW_BASED` and `AGGREGATE_SENTIMENT`.
- `app/dto/admin.py` — expose explicit counters.
- `app/services/imports.py` — accept `.jsonl` for sentiment jobs while retaining `.csv` for existing jobs.
- `app/integrations/tasks.py` — add a dedicated sentiment task dispatch method.
- `app/workers/tasks/jobs.py` — execute the dedicated orchestrator; retain the existing CSV and review-extraction task behavior.
- `app/api/v1/admin.py` — add the multipart endpoint.
- `tests/unit/test_task_dispatcher.py` — verify the dedicated Celery task name.
- `tests/integration/test_database.py` — verify aggregate schema and seeded normalized taxonomy.
- `tests/integration/test_imports.py` — only if a compatibility assertion belongs beside existing CSV tests; preserve current city-import edits.
- `README.md` — document the endpoint, JSONL contract, counters, identity, and distinct score methods.

---

### Task 1: Pure sentiment normalization, parsing, and score rules

**Files:**
- Create: `app/domain/sentiment.py`
- Create: `tests/unit/test_sentiment_domain.py`

**Interfaces:**
- Produces: `normalize_taxonomy_key(str) -> str`
- Produces: `deterministic_category_code(str) -> str`
- Produces: `deterministic_attribute_slug(str, str, set[str]) -> str`
- Produces: `aggregate_score_0_5(int, int, int) -> float`
- Produces: `aggregate_support_confidence(int) -> float`
- Produces: `HotelSentimentRecord.from_payload(dict[str, object])`
- Produces: `AttributeSentimentValue.from_payload(dict[str, object])`
- Consumes: `ImportValidationError` and the existing canonical `Sentiment` enum.

- [ ] **Step 1: Write failing normalization, deterministic slug, validation, and scoring tests**

```python
class SentimentDomainTests(unittest.TestCase):
    def test_normalizes_dynamic_taxonomy_keys(self) -> None:
        self.assertEqual(normalize_taxonomy_key("  Good_location  "), "good-location")
        self.assertEqual(normalize_taxonomy_key("Ｒｏｏｍ Size"), "room-size")

    def test_rejects_empty_normalized_key(self) -> None:
        with self.assertRaises(ImportValidationError):
            normalize_taxonomy_key("___")

    def test_deterministic_slug_uses_category_prefix_on_global_collision(self) -> None:
        first = deterministic_attribute_slug("room", "comfort", {"comfort"})
        second = deterministic_attribute_slug("room", "comfort", {"comfort"})
        self.assertEqual(first, "room--comfort")
        self.assertEqual(second, first)

    def test_balance_maps_to_zero_through_five(self) -> None:
        self.assertEqual(aggregate_score_0_5(10, 0, 10), 5.0)
        self.assertEqual(aggregate_score_0_5(5, 5, 10), 2.5)
        self.assertEqual(aggregate_score_0_5(0, 10, 10), 0.0)

    def test_attribute_counts_must_be_consistent(self) -> None:
        with self.assertRaisesRegex(ImportValidationError, "mention counts"):
            AttributeSentimentValue.from_payload(
                {
                    "sentiment": "positive",
                    "mentions": 2,
                    "positive_mentions": 2,
                    "negative_mentions": 1,
                }
            )

    def test_boolean_is_not_an_integer_count(self) -> None:
        with self.assertRaises(ImportValidationError):
            AttributeSentimentValue.from_payload(
                {
                    "sentiment": "positive",
                    "mentions": True,
                    "positive_mentions": 1,
                    "negative_mentions": 0,
                }
            )
```

Also add literal cases for negative counts, invalid sentiment, missing/non-object `attributes`, blank `hotel_id`, non-boolean `has_recent_reviews`, negative `reviews_analyzed`, the neutral remainder, globally free slugs, and the stable digest fallback when both base candidates are occupied.

- [ ] **Step 2: Run the tests and verify the feature is absent**

Run: `uv run pytest tests/unit/test_sentiment_domain.py -q`

Expected: collection fails because `app.domain.sentiment` does not exist.

- [ ] **Step 3: Implement the pure domain module**

Use frozen dataclasses so parsing stays independent of SQLAlchemy:

```python
@dataclass(frozen=True, slots=True)
class AttributeSentimentValue:
    sentiment: Sentiment
    total_mentions: int
    positive_mentions: int
    negative_mentions: int

    @property
    def neutral_mentions(self) -> int:
        return self.total_mentions - self.positive_mentions - self.negative_mentions

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "AttributeSentimentValue":
        sentiment_raw = payload.get("sentiment")
        if not isinstance(sentiment_raw, str):
            raise ImportValidationError("INVALID_SENTIMENT")
        try:
            sentiment = Sentiment(sentiment_raw.strip().upper())
        except ValueError as exc:
            raise ImportValidationError("INVALID_SENTIMENT") from exc
        values: dict[str, int] = {}
        for source_name, target_name in (
            ("mentions", "total_mentions"),
            ("positive_mentions", "positive_mentions"),
            ("negative_mentions", "negative_mentions"),
        ):
            value = payload.get(source_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ImportValidationError("INVALID_MENTION_COUNTS")
            values[target_name] = value
        if values["positive_mentions"] + values["negative_mentions"] > values["total_mentions"]:
            raise ImportValidationError("INVALID_MENTION_COUNTS")
        return cls(sentiment=sentiment, **values)
```

Implement normalization with `unicodedata.normalize("NFKC", value).strip().casefold()`, replace every run that is not `str.isalnum()` with one hyphen, and reject an empty result. Generate category codes and collision slugs with a SHA-256 digest over `f"{category_key}\0{attribute_key}"`, making the fallback independent of database order. Implement the score exactly as `2.5 + 2.5 * ((positive - negative) / total)` and reject `total <= 0`. Implement confidence as `min(1.0, math.log1p(total) / math.log(11))`.

- [ ] **Step 4: Run the unit tests and existing scoring tests**

Run: `uv run pytest tests/unit/test_sentiment_domain.py tests/unit/test_scoring.py -q`

Expected: all tests pass and the review-derived contribution tests remain unchanged.

- [ ] **Step 5: Commit the pure domain behavior**

```bash
git add app/domain/sentiment.py tests/unit/test_sentiment_domain.py
git commit -m "feat: add aggregate sentiment domain rules"
```

---

### Task 2: Database migration and ORM schema

**Files:**
- Create: `alembic/versions/20260916_0004_precomputed_sentiment_import.py`
- Create: `tests/unit/test_sentiment_migration.py`
- Modify: `app/models/scoring.py:14-39,123-153`
- Modify: `app/models/operations.py:14-43`
- Modify: `app/models/__init__.py:42-96`
- Modify: `scripts/seed_reference_data.py:150-202`
- Modify: `tests/integration/test_database.py:9-46`

**Interfaces:**
- Consumes: the normalization contract from Task 1; the migration carries an equivalent local helper so historical migrations do not import runtime application code.
- Produces: ORM model `HotelAttributeSentimentAggregate`.
- Produces: taxonomy `normalized_key` columns, score provenance columns, and explicit import counters.
- Produces: Alembic revision `20260916_0004`, revising `20260916_0003`.

- [ ] **Step 1: Write failing migration and metadata tests**

Create unit tests that load the migration module by path and call its pure collision helper with literal rows:

```python
def test_taxonomy_collision_message_lists_conflicting_ids() -> None:
    rows = [
        {"id": "a", "scope_id": None, "raw_key": "Room Size"},
        {"id": "b", "scope_id": None, "raw_key": "room_size"},
    ]
    with pytest.raises(RuntimeError) as error:
        migration._assert_no_normalized_collisions("category", rows)
    assert "room-size" in str(error.value)
    assert "a" in str(error.value)
    assert "b" in str(error.value)
```

Add a second test proving identical attribute keys in different category IDs are allowed, a third proving same-category collisions abort, and a downgrade test proving aggregate rows cause a `RuntimeError` before any `drop_table` or `drop_column` call.

Extend `test_postgis_and_complete_schema` to require `hotel_attribute_sentiment_aggregates`, and add inspector/text assertions for the five-column unique constraint and non-null `source_id`.

- [ ] **Step 2: Run the migration tests and observe the expected failures**

Run: `uv run pytest tests/unit/test_sentiment_migration.py tests/integration/test_database.py -q`

Expected: the unit test cannot load revision `20260916_0004`; the integration schema test lacks the aggregate table.

- [ ] **Step 3: Add ORM columns and the aggregate model**

Add `normalized_key` to category and attribute models and declare the intended constraints without changing `Attribute.slug`:

```python
class AttributeCategory(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "attribute_categories"
    normalized_key: Mapped[str] = mapped_column(String(160), unique=True, nullable=False)


class Attribute(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "attributes"
    __table_args__ = (
        UniqueConstraint("category_id", "slug"),
        UniqueConstraint("category_id", "normalized_key"),
    )
    normalized_key: Mapped[str] = mapped_column(String(160), nullable=False)
```

Retain the existing globally unique `slug` mapped column and existing `(category_id, slug)` constraint; removing or rewriting existing slugs is forbidden. Add `HotelAttributeSentimentAggregate` with all spec fields, `CheckConstraint`s for sentiment/counts/score/source, the five-column `UniqueConstraint`, and indexes for hotel, attribute, source, and analysis window. Map the database column named `metadata` through a Python attribute named `metadata_payload`, matching the existing `AuditLog` reserved-name pattern. Add `scoring_source`, `analysis_version`, `review_window`, and `imported_at` to `HotelAttributeScore`, with the ORM default for `scoring_source` set to `review_based` so existing constructors remain compatible. Add all 13 explicit sentiment counters to `ImportJob`, each non-null with a zero default.

- [ ] **Step 4: Implement the collision-safe migration**

The upgrade must execute in this order:

```python
revision = "20260916_0004"
down_revision = "20260916_0003"

def upgrade() -> None:
    op.add_column("attribute_categories", sa.Column("normalized_key", sa.String(160)))
    op.add_column("attributes", sa.Column("normalized_key", sa.String(160)))
    bind = op.get_bind()
    categories = list(bind.execute(sa.text(
        "SELECT id::text AS id, NULL::text AS scope_id, code AS raw_key FROM attribute_categories"
    )).mappings())
    attributes = list(bind.execute(sa.text(
        "SELECT id::text AS id, category_id::text AS scope_id, slug AS raw_key FROM attributes"
    )).mappings())
    _assert_no_normalized_collisions("category", categories)
    _assert_no_normalized_collisions("attribute", attributes)
```

Then update every row with the local NFKC normalizer, make both columns non-null, and create `uq_attribute_categories_normalized_key` plus `uq_attributes_category_normalized_key`. Add score provenance with a temporary server default of `review_based`, backfill, set `scoring_source` non-null, and remove the server default only if application defaults supply it. Add all import counters with server default `0`. Create the aggregate table with `source_id` non-null and `ondelete="RESTRICT"`.

Before downgrade, query aggregate row count and aggregate-sourced score count. Raise an actionable `RuntimeError` if either is nonzero; otherwise remove only objects introduced by this revision in reverse dependency order.

- [ ] **Step 5: Apply the migration and seed normalized keys**

Run: `uv run alembic upgrade head`

Expected: revision `20260916_0004` applies. If collision detection aborts, report the listed IDs and normalized keys rather than changing existing taxonomy.

Update `scripts/seed_reference_data.py` inserts to include deterministic normalized keys so a clean seed satisfies non-null constraints. Run: `uv run python -m scripts.seed_reference_data`.

- [ ] **Step 6: Run schema and migration tests**

Run: `uv run pytest tests/unit/test_sentiment_migration.py tests/integration/test_database.py -q`

Expected: all tests pass; the seed remains idempotent.

- [ ] **Step 7: Commit the schema slice**

```bash
git add alembic/versions/20260916_0004_precomputed_sentiment_import.py app/models/scoring.py app/models/operations.py app/models/__init__.py scripts/seed_reference_data.py tests/unit/test_sentiment_migration.py tests/integration/test_database.py
git commit -m "feat: add aggregate sentiment persistence schema"
```

---

### Task 3: Dynamic taxonomy and aggregate repository

**Files:**
- Create: `app/repositories/sentiment.py`
- Create: `tests/integration/test_sentiment_repository.py`
- Modify: `app/repositories/catalog.py:13-31,612-817`

**Interfaces:**
- Consumes: `normalize_taxonomy_key`, `deterministic_category_code`, and `deterministic_attribute_slug`.
- Produces: `TaxonomyResolution(attribute_id: UUID, category_key: str, auto_created: bool)`.
- Produces: `AmbiguousAttributeAlias(raw_key: str, attribute_ids: tuple[UUID, ...])`.
- Produces: `AggregateUpsertResult(rows, inserted, updated)`.
- Produces: `SentimentAggregateRepository.resolve_attribute(raw_category_key, raw_attribute_key)` and `.upsert_many(values)`.

- [ ] **Step 1: Write failing real-database repository tests**

Cover these observable behaviors:

```python
@pytest.mark.asyncio
async def test_resolves_active_normalized_alias_only_within_category(session) -> None:
    resolution = await repository.resolve_attribute("ROOM", "cosy_room")
    assert resolution.attribute_id == expected_attribute.id
    assert resolution.auto_created is False

@pytest.mark.asyncio
async def test_ambiguous_alias_does_not_guess(session) -> None:
    with pytest.raises(AmbiguousAttributeAlias) as error:
        await repository.resolve_attribute("room", "compact")
    assert set(error.value.attribute_ids) == {first.id, second.id}

@pytest.mark.asyncio
async def test_auto_created_slug_is_stable_across_categories(session) -> None:
    first = await repository.resolve_attribute("room", "outlook")
    second = await repository.resolve_attribute("location", "outlook")
    assert first.slug == "outlook"
    assert second.slug == "location--outlook"
```

Also test inactive aliases are ignored, concurrent/repeated resolution returns one normalized attribute per category, raw keys do not rewrite existing slugs, aggregate UPSERT identity includes `source_id`, and reimport reports one update rather than another insert.

- [ ] **Step 2: Run the repository tests and verify they fail for missing APIs**

Run: `uv run pytest tests/integration/test_sentiment_repository.py -q`

Expected: collection fails because `app.repositories.sentiment` and its interfaces do not exist.

- [ ] **Step 3: Implement category and attribute resolution**

Define focused result types and ambiguity error:

```python
@dataclass(frozen=True, slots=True)
class TaxonomyResolution:
    attribute_id: uuid.UUID
    category_key: str
    slug: str
    auto_created: bool


class AmbiguousAttributeAlias(Exception):
    def __init__(self, raw_key: str, attribute_ids: tuple[uuid.UUID, ...]) -> None:
        self.raw_key = raw_key
        self.attribute_ids = attribute_ids
        super().__init__(f"AMBIGUOUS_ATTRIBUTE_ALIAS: {raw_key}")
```

Resolve or insert the category by its unique normalized key, using PostgreSQL `ON CONFLICT DO NOTHING` followed by a select. Resolve the direct attribute by `(category_id, normalized_key)`. If absent, select only active aliases joined to active attributes in that category, normalize aliases in Python, deduplicate matching attribute IDs, return exactly one, raise ambiguity for more than one, and auto-create for none. Before insert, select globally used slugs and call the deterministic slug helper; use the `(category_id, normalized_key)` constraint as the concurrency authority.

- [ ] **Step 4: Implement aggregate bulk UPSERTs**

Prefetch existing five-part identity tuples, execute one PostgreSQL insert per hotel record with:

```python
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
        "metadata": statement.excluded.metadata,
        "updated_at": func.now(),
    },
).returning(HotelAttributeSentimentAggregate)
```

Classify inserted versus updated from the prefetched identity set, not PostgreSQL system columns. Return persisted rows for score propagation.

- [ ] **Step 5: Run repository tests and formatting/type checks**

Run: `uv run pytest tests/integration/test_sentiment_repository.py -q`

Run: `uv run ruff check app/repositories/sentiment.py tests/integration/test_sentiment_repository.py && uv run mypy --strict app/repositories/sentiment.py`

Expected: all repository tests and checks pass.

- [ ] **Step 6: Commit the repository slice**

```bash
git add app/repositories/sentiment.py app/repositories/catalog.py tests/integration/test_sentiment_repository.py
git commit -m "feat: resolve dynamic sentiment taxonomy"
```

---

### Task 4: Aggregate score propagation and centralized precedence

**Files:**
- Create: `app/services/aggregate_scoring.py`
- Modify: `app/repositories/catalog.py:684-765`
- Modify: `app/services/scoring.py:91-214`
- Modify: `tests/integration/test_sentiment_repository.py`
- Modify: `tests/unit/test_scoring.py`

**Interfaces:**
- Produces: `ScorePropagationResult(applied: int, skipped_precedence: int)`.
- Produces: `AttributeRepository.upsert_aggregate_hotel_scores(rows) -> int` returning applied row count.
- Produces: `AggregateScoringService.propagate(aggregates) -> ScorePropagationResult`.
- Produces: `ScoreNormalizationService.normalize(scope_type, country_id, region_id, city_id, hotel_type, hotel_ids) -> int`, reused by both scoring paths.
- Preserves: `ScoringService.recalculate_hotel(UUID) -> int` while marking its rows `review_based`.

- [ ] **Step 1: Write failing precedence and provenance tests**

Add integration tests that first insert an aggregate score, then update it from another aggregate, then install a review-based score and retry the aggregate:

```python
first = await service.propagate([aggregate])
assert first == ScorePropagationResult(applied=1, skipped_precedence=0)

score.scoring_source = "review_based"
score.score_5 = Decimal("4.7500")
await session.commit()

blocked = await service.propagate([changed_aggregate])
await session.refresh(score)
assert blocked == ScorePropagationResult(applied=0, skipped_precedence=1)
assert score.score_5 == Decimal("4.7500")
assert score.scoring_source == "review_based"
```

Add a review recalculation test proving it replaces an aggregate score, sets `review_based`, clears `analysis_version`, `review_window`, and `imported_at`, and leaves the aggregate table row intact. Add a unit test proving normalization can be instantiated without a text interpreter.

- [ ] **Step 2: Run the targeted scoring tests and observe precedence failure**

Run: `uv run pytest tests/integration/test_sentiment_repository.py tests/unit/test_scoring.py -q`

Expected: failures show the aggregate scoring service and score provenance behavior are absent.

- [ ] **Step 3: Add atomic aggregate score UPSERT precedence**

Implement the repository operation with PostgreSQL's conditional conflict update:

```python
statement = insert(HotelAttributeScore).values(rows)
statement = statement.on_conflict_do_update(
    index_elements=["hotel_id", "attribute_id", "algorithm_version_id"],
    set_=aggregate_update_values,
    where=HotelAttributeScore.scoring_source == "aggregate_sentiment",
)
result = await self._session.execute(statement)
return result.rowcount
```

The inserted/updated values must include counts, raw/5/100 scores, cleared normalization fields, support confidence, `scoring_source="aggregate_sentiment"`, analysis provenance, identical `imported_at`/`calculated_at` for the propagation event, and the active `algorithm_version_id`. The database `WHERE` clause is mandatory; a read-then-write precedence check is insufficient.

- [ ] **Step 4: Implement aggregate scoring and shared normalization services**

`AggregateScoringService.propagate` loads the active algorithm, builds score rows from persisted aggregates, calls the conditional repository method, and returns `skipped_precedence = len(rows) - applied`. Move the existing normalization body into `ScoreNormalizationService` with the same scope signature, then make `ScoringService.normalize` delegate to it so current callers remain compatible.

The aggregate service must not accept or construct `StructuredTextInterpreter`. It consumes only the async session and persisted aggregate models.

- [ ] **Step 5: Mark review recalculations explicitly**

Extend the values passed by `ScoringService.recalculate_hotel`:

```python
{
    "scoring_source": "review_based",
    "analysis_version": None,
    "review_window": None,
    "imported_at": None,
    "calculated_at": now,
}
```

The existing review score UPSERT remains unconditional, so it replaces an aggregate score. Do not change contribution queries or `ReviewAttributeMention` writes.

- [ ] **Step 6: Run scoring tests**

Run: `uv run pytest tests/unit/test_scoring.py tests/integration/test_sentiment_repository.py -q`

Expected: aggregate precedence, provenance, review replacement, and existing review scoring all pass.

- [ ] **Step 7: Commit scoring behavior**

```bash
git add app/services/aggregate_scoring.py app/services/scoring.py app/repositories/catalog.py tests/unit/test_scoring.py tests/integration/test_sentiment_repository.py
git commit -m "feat: enforce aggregate score precedence"
```

---

### Task 5: Streamed JSONL orchestrator, issues, counters, and normalization

**Files:**
- Create: `app/orchestrators/sentiment_imports.py`
- Create: `tests/integration/test_sentiment_imports.py`
- Modify: `app/enums.py:87-92`
- Modify: `app/dto/admin.py:17-39`
- Modify: `app/services/admin.py:93-115` only if DTO construction needs explicit compatibility fields.

**Interfaces:**
- Consumes: `SentimentAggregateRepository`, `AggregateScoringService`, `ScoreNormalizationService`, `HotelRepository.source_mapping`, `DataQualityRepository`, `ImportJobRepository`, and `ObjectStorage`.
- Produces: `SentimentAnalysisImportOrchestrator(session, storage, batch_size).run(job_id) -> None`.
- Produces: private `ImportCounterDelta` dataclass containing the 13 explicit sentiment counters plus compatibility `inserted`, `updated`, and `skipped` deltas.
- Produces: import type value `SENTIMENT_ANALYSIS` and all explicit counters in `ImportJobRead`.

- [ ] **Step 1: Write failing valid/dynamic/idempotency integration tests**

Create JSONL through an async byte iterator and assert real database results. The first test must include at least two hotels with different category/attribute sets, a known alias, an unknown attribute, and a zero-mention attribute:

```python
assert job.status == "COMPLETED"
assert job.records_read == 2
assert job.hotels_read == 2
assert job.hotels_matched == 2
assert job.attributes_seen == 5
assert job.attributes_skipped_zero_mentions == 1
assert job.attributes_processed == 4
assert job.auto_created_attributes == 1
assert job.failed == job.hotels_failed == 0
assert aggregate_count == 4
assert review_mention_count == 0
```

Reimport the same source/version/window in a second job and assert aggregate count is unchanged, `attributes_inserted == 0`, `attributes_updated == 4`, and compatibility `inserted/updated` mirror aggregate rows.

- [ ] **Step 2: Write failing continuation, duplicate, and counter tests**

Add separate records for blank hotel ID, unknown mapped hotel, malformed JSON, invalid sentiment, negative counts, sum greater than total, malformed category value, malformed attribute value, ambiguous alias, and two raw attribute keys that normalize identically. Assert:

```python
assert job.records_read == job.hotels_read
assert job.failed == job.hotels_failed
assert job.failed <= job.records_read
assert job.attributes_failed == expected_attribute_failures
assert job.status == "PARTIAL"
assert job.score_updates_skipped_precedence == expected_precedence_skips
```

Query `DataQualityIssue` and assert issue details preserve line, upstream hotel ID, raw category key, and both winning/conflicting raw attribute keys for normalized duplicates. Verify later valid lines still persist.

- [ ] **Step 3: Run orchestrator tests and verify the class is missing**

Run: `uv run pytest tests/integration/test_sentiment_imports.py -q`

Expected: collection fails because `SentimentAnalysisImportOrchestrator` does not exist.

- [ ] **Step 4: Implement streamed job execution and hotel-level savepoints**

Implement the run skeleton with a terminal guard and line streaming:

```python
with path.open("r", encoding="utf-8-sig") as handle:
    for line_number, raw_line in enumerate(handle, start=1):
        if not raw_line.strip():
            continue
        job.hotels_read += 1
        job.records_read = job.hotels_read
        try:
            payload = json.loads(raw_line)
            record = HotelSentimentRecord.from_payload(payload)
            async with self._session.begin_nested():
                delta = await self._process_hotel(job, line_number, record)
        except (json.JSONDecodeError, ImportValidationError) as exc:
            delta = ImportCounterDelta(hotels_failed=1)
            await self._record_hotel_issue(job, line_number, raw_line, exc)
        self._apply_delta(job, delta)
        job.failed = job.hotels_failed
        if job.hotels_read % self._batch_size == 0:
            job.checkpoint = {"line": line_number}
            await self._session.commit()
```

Use a bounded raw-line excerpt for malformed JSON issues so an arbitrarily large bad line is not copied unbounded into JSONB. Resume from `checkpoint["line"]`. Set terminal status to `PARTIAL` when either `hotels_failed` or `attributes_failed` is nonzero, otherwise `COMPLETED`.

- [ ] **Step 5: Implement safe attribute processing**

For each category/attribute pair:

1. Convert the validated scalar hotel ID with `str(record.hotel_id)`, resolve it through `HotelRepository.source_mapping(job.source_id, source_hotel_id)`, increment `hotels_matched` only on success, and reject an unknown mapping as a hotel-level failure.
2. Increment `attributes_seen` for each encountered attribute entry.
3. Parse `AttributeSentimentValue`; on failure record an attribute issue and increment only `attributes_failed`.
4. If zero mentions, increment `attributes_skipped_zero_mentions` and compatibility `skipped` before taxonomy resolution.
5. Detect normalized duplicates in an insertion-ordered dictionary; preserve the first and issue the later raw conflict.
6. Resolve direct key, then active category-scoped aliases, then safe creation.
7. Collect aggregate values including non-null `source_id`, `analysis_mode`, `analysis_version`, `review_window`, canonical sentiment, all counts, computed score, exact `raw_attribute_key`, normalized `category_name`, and metadata containing `raw_category_key`, `raw_hotel_id`, `has_recent_reviews`, and `import_job_id`.
8. Bulk UPSERT aggregates and update processed/inserted/updated counters.
9. Propagate scores and update applied/precedence counters.

Use a per-record `ImportCounterDelta` and apply it only after the nested transaction succeeds. Unexpected database exceptions roll back the entire hotel record, increment `hotels_failed`/`failed`, and write a hotel issue outside the savepoint.

- [ ] **Step 6: Normalize after both successful terminal states**

After the final job commit determines `COMPLETED` or `PARTIAL`, run:

```python
if job.status in {JobStatus.COMPLETED.value, JobStatus.PARTIAL.value} and job.score_updates_applied > 0:
    await ScoreNormalizationService(self._session).normalize(ScopeType.GLOBAL)
```

If normalization raises, mark the import job `FAILED` through the existing job-level error path rather than reporting a successful import with stale normalized values. Add one `COMPLETED` fixture and one attribute-error `PARTIAL` fixture, each with applied score updates, and assert both produce `NormalizationRun` rows.

- [ ] **Step 7: Run the complete sentiment orchestrator tests**

Run: `uv run pytest tests/integration/test_sentiment_imports.py -q`

Expected: valid, dynamic, zero-skip, auto-create, alias, ambiguity, duplicate, idempotency, unknown hotel, malformed values, differing hotel attribute sets, scoring, normalization, and all counters pass.

- [ ] **Step 8: Commit the orchestration slice**

```bash
git add app/orchestrators/sentiment_imports.py app/enums.py app/dto/admin.py app/services/admin.py tests/integration/test_sentiment_imports.py
git commit -m "feat: stream sentiment analysis JSONL imports"
```

---

### Task 6: Staging, admin endpoint, and dedicated worker dispatch

**Files:**
- Modify: `app/services/imports.py:25-90`
- Modify: `app/api/v1/admin.py:41-131`
- Modify: `app/integrations/tasks.py:8-22`
- Modify: `app/workers/tasks/jobs.py:64-86`
- Modify: `tests/unit/test_task_dispatcher.py:17-42`
- Create: `tests/integration/test_sentiment_import_api.py`

**Interfaces:**
- Produces: `POST /api/v1/admin/imports/sentiment-analysis`.
- Produces: `TaskDispatcher.import_sentiment_analysis(job_id)`.
- Produces: Celery task `app.workers.tasks.import_sentiment_analysis`.
- Preserves: `TaskDispatcher.import_csv` and `app.workers.tasks.import_csv` for hotel/review CSVs.

- [ ] **Step 1: Write failing staging and dispatch tests**

Extend dispatcher expectations:

```python
dispatcher.import_csv(identifier)
dispatcher.import_sentiment_analysis(identifier)
self.assertEqual(
    celery.calls[1],
    ("app.workers.tasks.import_sentiment_analysis", [str(identifier)]),
)
```

Add integration tests proving `ImportService.create_job` succeeds when passed `ImportType.SENTIMENT_ANALYSIS`, source code `TRIPADVISOR`, file name `input.jsonl`, a non-empty async byte iterator, null country/region IDs, and no actor. Assert `.csv` is rejected for sentiment, `.jsonl` is rejected for hotel/review imports, and empty JSONL cleanup still occurs.

- [ ] **Step 2: Write the authenticated endpoint acceptance test**

Create a data-operator user, sign an access token with `create_access_token`, patch only Celery's external send boundary, and upload one JSONL line through `TestClient`:

```python
response = client.post(
    "/api/v1/admin/imports/sentiment-analysis?source_code=TRIPADVISOR",
    headers={"Authorization": f"Bearer {token}"},
    files={"file": ("sentiment.jsonl", jsonl_bytes, "application/x-ndjson")},
)
assert response.status_code == 200
job_id = response.json()["data"]["job_id"]
assert queued_task == (
    "app.workers.tasks.import_sentiment_analysis",
    [job_id],
)
```

Query the job and assert `import_type == "SENTIMENT_ANALYSIS"`, the original content hash is stored, and the staged object path is under `raw/sentiment_analysis/`.

- [ ] **Step 3: Run the new dispatch/API tests and observe failures**

Run: `uv run pytest tests/unit/test_task_dispatcher.py tests/integration/test_sentiment_import_api.py -q`

Expected: failures identify the missing import type extension, endpoint, and dedicated task method.

- [ ] **Step 4: Make file validation import-type aware**

Replace the unconditional `.csv` check with an exact map:

```python
expected_suffix = {
    ImportType.HOTELS: ".csv",
    ImportType.REVIEWS: ".csv",
    ImportType.SENTIMENT_ANALYSIS: ".jsonl",
}[import_type]
if Path(file_name).suffix.casefold() != expected_suffix:
    raise ImportValidationError(f"Only {expected_suffix} files are accepted")
```

Keep immutable storage, SHA-256 calculation, empty-file deletion, source lookup, audit logging, and current CSV object paths unchanged.

- [ ] **Step 5: Add the route and dedicated dispatch boundary**

Add a route parallel to existing imports:

```python
@router.post(
    "/imports/sentiment-analysis",
    response_model=SuccessResponse[JobAccepted],
    dependencies=[Depends(rate_limit("admin_import"))],
)
async def import_sentiment_analysis(
    request: Request,
    session: SessionDep,
    storage: StorageDep,
    dispatcher: TaskDispatcherDep,
    actor: User = Depends(operator),
    file: UploadFile = File(...),
    source_code: str = "TRIPADVISOR",
) -> SuccessResponse[JobAccepted]:
    job = await ImportService(session, storage).create_job(
        ImportType.SENTIMENT_ANALYSIS,
        source_code,
        file.filename or "sentiment-analysis.jsonl",
        _upload_chunks(file),
        None,
        None,
        actor.id,
    )
    dispatcher.import_sentiment_analysis(job.id)
    return success(request, JobAccepted(job_id=job.id, status=job.status))
```

Implement the dispatcher method and Celery task using `SentimentAnalysisImportOrchestrator`. Do not add a branch inside `CSVImportOrchestrator`, and do not queue `process_reviews` after sentiment imports.

- [ ] **Step 6: Run dispatch, API, and existing import tests**

Run: `uv run pytest tests/unit/test_task_dispatcher.py tests/integration/test_sentiment_import_api.py tests/integration/test_imports.py -q`

Expected: new endpoint tests and all existing CSV import tests pass.

- [ ] **Step 7: Commit the API/worker slice**

```bash
git add app/services/imports.py app/api/v1/admin.py app/integrations/tasks.py app/workers/tasks/jobs.py tests/unit/test_task_dispatcher.py tests/integration/test_sentiment_import_api.py
git commit -m "feat: expose sentiment analysis import endpoint"
```

---

### Task 7: Admin documentation and final contract checks

**Files:**
- Modify: `README.md:149-211`
- Modify: `tests/integration/test_sentiment_imports.py`
- Modify: `tests/integration/test_sentiment_import_api.py`

**Interfaces:**
- Documents: endpoint, source mapping, JSONL structure, upload example, counters, idempotency, validation/continuation behavior, score formula, precedence, and score-method distinction.
- Verifies: no review mentions and no LLM/review task dispatch from aggregate imports.

- [ ] **Step 1: Add final regression assertions before documentation**

Add explicit test assertions:

```python
mention_count = await session.scalar(select(func.count()).select_from(ReviewAttributeMention))
assert mention_count == baseline_mention_count
assert "app.workers.tasks.process_reviews" not in queued_task_names
assert aggregate.scoring_source == "aggregate_sentiment"
assert score.algorithm_version_id == active_algorithm.id
assert score.imported_at is not None
assert score.calculated_at is not None
```

Run: `uv run pytest tests/integration/test_sentiment_imports.py tests/integration/test_sentiment_import_api.py -q`

Expected: tests pass and directly guard the no-fake-evidence/no-LLM requirements.

- [ ] **Step 2: Update README API and import documentation**

Add the endpoint to the operations list and a `JSONL sentiment-analysis import` section containing the approved one-line example, plus this upload command:

```bash
curl -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@sentiment.jsonl;type=application/x-ndjson" \
  "http://localhost:8000/api/v1/admin/imports/sentiment-analysis?source_code=TRIPADVISOR"
```

State that `inserted`/`updated` mean aggregate rows for this import type, `failed` means hotel records only, and `attributes_failed` is separate. Document the five-column idempotency key and exact precedence behavior. Explicitly contrast `review_based` (`rating ± 1`) with `aggregate_sentiment` (mention balance mapped to 0–5).

- [ ] **Step 3: Check documentation diff without disturbing current README edits**

Run: `git diff --check -- README.md`

Expected: no whitespace errors; the existing city-import documentation remains present.

- [ ] **Step 4: Commit documentation and final regression assertions**

```bash
git add README.md tests/integration/test_sentiment_imports.py tests/integration/test_sentiment_import_api.py
git commit -m "docs: document sentiment JSONL imports"
```

---

### Task 8: Migration execution and complete verification

**Files:**
- Verify only; fix failures in the task-owned files above using a new failing regression test before changing behavior.

**Interfaces:**
- Produces: verified migration revision, targeted tests, full suite, and quality-gate evidence for handoff.

- [ ] **Step 1: Confirm migration state and schema drift**

Run:

```bash
uv run alembic current
uv run alembic upgrade head
uv run alembic current
uv run alembic check
```

Expected: current revision is `20260916_0004` and Alembic reports no new upgrade operations.

- [ ] **Step 2: Run targeted sentiment tests**

Run:

```bash
uv run pytest \
  tests/unit/test_sentiment_domain.py \
  tests/unit/test_sentiment_migration.py \
  tests/unit/test_scoring.py \
  tests/unit/test_task_dispatcher.py \
  tests/integration/test_database.py \
  tests/integration/test_sentiment_repository.py \
  tests/integration/test_sentiment_imports.py \
  tests/integration/test_sentiment_import_api.py \
  -q
```

Expected: all targeted tests pass.

- [ ] **Step 3: Run static quality gates**

Run:

```bash
uv run ruff check .
uv run black --check .
uv run isort --check-only .
uv run mypy --strict app scripts
uv run bandit -q -r app
```

Expected: every command exits zero.

- [ ] **Step 4: Run the full test suite with project coverage threshold**

Run:

```bash
uv run pytest tests -q --cov=app --cov-report=term-missing --cov-report=xml
```

Expected: all tests pass and total branch coverage is at least 90%.

- [ ] **Step 5: Review the final diff and migration boundaries**

Run:

```bash
git status --short
git diff --check HEAD
git diff --stat HEAD
git log --oneline -10
```

Verify the diff contains no write to `review_attribute_mentions`, no sentiment branch in `CSVImportOrchestrator`, no existing slug rewrite, and no accidental staging of unrelated user changes.

- [ ] **Step 6: Prepare the completion report**

Report:

- Changed files grouped by schema, import, scoring, tests, and docs.
- Migration revision `20260916_0004` and the successful applied revision output.
- Endpoint `POST /api/v1/admin/imports/sentiment-analysis`.
- Affected tables: `attribute_categories`, `attributes`, `hotel_attribute_sentiment_aggregates`, `hotel_attribute_scores`, and `import_jobs`; note that `data_quality_issues` receives rows but no schema change.
- Targeted and full-suite command results with pass/fail counts.
- Known limitations, including JSONL-only format, source-scoped exact hotel mapping, global normalization cost, and lack of review-level evidence for aggregate scores.
