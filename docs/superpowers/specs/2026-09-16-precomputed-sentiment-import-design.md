# Precomputed Hotel Sentiment Import Design

## Purpose

Add a JSONL import flow for hotel-level precomputed sentiment aggregates. The flow stores aggregate evidence directly, maps dynamic categories and attributes into the existing taxonomy, propagates eligible aggregate scores into `hotel_attribute_scores`, and uses the existing normalization infrastructure. It never creates `review_attribute_mentions`, never fabricates review ratings, and does not alter the existing hotel/review CSV or review-derived scoring behavior.

## API and File Contract

Add an operator/admin endpoint:

`POST /api/v1/admin/imports/sentiment-analysis`

The multipart request accepts:

- `file`: a non-empty `.jsonl` file.
- `source_code`: the existing data-source code used to resolve each upstream hotel identifier; it defaults to `TRIPADVISOR`, matching the existing import endpoints.

The endpoint stages the original bytes through `ImportService`, creates a `SENTIMENT_ANALYSIS` `import_jobs` row, and queues the normal import worker boundary. The worker dispatches the job to a dedicated `SentimentAnalysisImportOrchestrator`; it does not route JSONL through `CSVImportOrchestrator`.

Each nonblank line is one JSON object with this shape:

```json
{
  "hotel_id": 501153,
  "analysis_mode": "all_reviews",
  "analysis_version": "anthropic_message_batches_v1",
  "review_window": "last_6_calendar_months",
  "has_recent_reviews": true,
  "reviews_analyzed": 75,
  "attributes": {
    "location": {
      "good_location": {
        "sentiment": "positive",
        "mentions": 13,
        "positive_mentions": 13,
        "negative_mentions": 0
      }
    },
    "room": {
      "small_rooms": {
        "sentiment": "negative",
        "mentions": 6,
        "positive_mentions": 0,
        "negative_mentions": 6
      }
    }
  }
}
```

Category and attribute keys are arbitrary JSON object keys. The importer does not maintain a fixed allowlist.

## Persistence Model

### Aggregate table

Create `hotel_attribute_sentiment_aggregates` with:

- `id UUID` primary key.
- `hotel_id UUID` foreign key to `hotels`, cascade delete.
- `attribute_id UUID` foreign key to `attributes`, restrict delete.
- `source_id UUID` non-null foreign key to `data_sources` with restricted deletion.
- `category_name` containing the normalized category key used for matching.
- `raw_attribute_key` containing the exact input attribute key.
- `sentiment` containing canonical uppercase `POSITIVE`, `NEGATIVE`, or `NEUTRAL`.
- `positive_mentions`, `negative_mentions`, and `total_mentions` as nonnegative integers.
- `reviews_analyzed` as a nonnegative integer.
- `analysis_mode`, `analysis_version`, and `review_window` as nonblank source-provenance strings.
- `aggregate_score_0_5` as a numeric value constrained to 0 through 5.
- `scoring_source` fixed to `aggregate_sentiment`.
- `metadata JSONB`, including `raw_category_key`, `raw_hotel_id`, `has_recent_reviews`, import job identity, and any non-core provenance retained from the source record.
- `created_at` and `updated_at` timestamps.

The idempotency constraint is:

`UNIQUE (hotel_id, attribute_id, source_id, analysis_version, review_window)`

Indexes cover `hotel_id`, `attribute_id`, `source_id`, and `(analysis_version, review_window)`.

Database checks mirror importer validation for sentiment, nonnegative counts, `positive_mentions + negative_mentions <= total_mentions`, and the score range.

### Taxonomy normalization

Add a persisted `normalized_key` to both `attribute_categories` and `attributes`:

- Category normalized keys are globally unique in `attribute_categories`.
- Attribute normalized keys are unique within `category_id`.
- Existing records are backfilled deterministically from category code/name and attribute slug/name.
- Existing public attribute slugs are never rewritten.

Before adding either normalized-key uniqueness constraint, the migration queries
the backfilled values for collisions. If any collision exists, the migration
aborts without merging, renaming, or deleting taxonomy and raises an actionable
error that lists the conflicting category or attribute identifiers and normalized
key.

Normalization uses Unicode NFKC normalization, trimming, case folding, conversion of runs of non-alphanumeric characters (including underscores and whitespace) to a single hyphen, and removal of leading/trailing hyphens. A key that normalizes to empty is malformed.

For each category, resolution is by normalized category key; an absent category is safely auto-created with a deterministic code and the original raw key as its name. For each attribute within that category:

1. Match the category-scoped normalized attribute key.
2. If absent, normalize and compare active aliases attached to attributes in that category.
3. If exactly one attribute matches, use it.
4. If multiple attributes match, create an `AMBIGUOUS_ATTRIBUTE_ALIAS` data-quality issue and skip the attribute.
5. If no match exists, auto-create an active score attribute.

An auto-created attribute uses the normalized attribute key as its public slug when that slug is globally unused. Otherwise it uses the deterministic slug `<normalized-category-key>--<normalized-attribute-key>`. If that derived slug conflicts with an unrelated existing slug, append a stable short digest of the category and attribute normalized keys. Database constraints and conflict-aware inserts make concurrent creation safe.

Within one hotel/category object, the first raw key for a normalized attribute wins. Later normalized duplicates are skipped and recorded as `DUPLICATE_NORMALIZED_ATTRIBUTE`; issue details include the category key, winning raw attribute key, and conflicting raw attribute key.

Every aggregate row preserves its raw attribute key and raw category key, even when the taxonomy match came from an existing normalized key or alias.

### Import job counters

Add explicit, nonnegative `import_jobs` counters:

- `hotels_read`
- `hotels_matched`
- `hotels_failed`
- `attributes_seen`
- `attributes_processed`
- `attributes_inserted`
- `attributes_updated`
- `attributes_skipped_zero_mentions`
- `attributes_failed`
- `score_updates_applied`
- `score_updates_skipped_precedence`
- `unknown_hotels`
- `auto_created_attributes`

`records_read` remains for compatibility and always equals `hotels_read` for sentiment imports. Existing `inserted` and `updated` also remain; for sentiment imports only, they mirror `attributes_inserted` and `attributes_updated`, meaning aggregate-table rows rather than score rows. `failed` mirrors `hotels_failed` and counts only rejected hotel-level JSONL records, so it never exceeds `records_read`. Attribute validation and mapping failures increment only `attributes_failed`. Score propagation skipped due to precedence is not a failure.

The import-job read DTO exposes the explicit counters.

### Hotel score provenance

Add nullable provenance to `hotel_attribute_scores`:

- `scoring_source`, backfilled to `review_based` for existing rows and non-null thereafter.
- `analysis_version`.
- `review_window`.
- `imported_at`.

Existing `calculated_at` remains the time the score row was calculated or recalculated, and existing `algorithm_version_id` remains the active scoring algorithm identity.

Review-derived recalculation writes `scoring_source = review_based`, clears aggregate-only provenance, and may replace any aggregate-derived score. Aggregate propagation may insert a missing score or update an `aggregate_sentiment` score, but it cannot update a `review_based` score. This precedence predicate lives in the score repository/service layer and is applied atomically by the database upsert, preventing a concurrent aggregate import from blindly replacing review-derived work.

## Validation and Row Handling

Each nonblank JSONL line increments both `hotels_read` and `records_read`. A line must decode to a JSON object and contain:

- A nonblank scalar `hotel_id`.
- Nonblank string `analysis_mode`, `analysis_version`, and `review_window`.
- Boolean `has_recent_reviews`.
- Nonnegative integer `reviews_analyzed` (booleans are rejected as integers).
- An object-valued `attributes` field whose category values are objects.

The upstream hotel identity is `str(hotel_id)` and is resolved through the existing exact `(import_jobs.source_id, hotel_source_mappings.source_hotel_id)` mapping. The importer does not assume the value is an internal UUID. An unknown hotel increments `unknown_hotels`, records an `UNKNOWN_SENTIMENT_HOTEL` issue, increments both `hotels_failed` and compatibility `failed`, and continues.

Each attribute entry increments `attributes_seen`. Its sentiment object must contain:

- `sentiment`, accepted case-insensitively only as positive, negative, or neutral.
- Integer `mentions`, `positive_mentions`, and `negative_mentions`, each at least zero.
- Counts satisfying `positive_mentions + negative_mentions <= mentions`.

`mentions == 0` increments `attributes_skipped_zero_mentions` and `skipped`; it does not resolve or create taxonomy and does not persist an aggregate. A malformed category or attribute records a row-level data-quality issue, increments `attributes_failed`, and continues with other safe attributes without changing `failed`. A valid mapped attribute increments `attributes_processed` after its aggregate upsert succeeds.

Malformed JSON or invalid hotel-level fields reject that hotel line without stopping the import and increment both `hotels_failed` and compatibility `failed`. Blank lines are ignored and do not affect counters.

## Aggregate Scoring

The aggregate path uses only mention counts:

```text
sentiment_balance =
    (positive_mentions - negative_mentions) / total_mentions

aggregate_score_0_5 =
    2.5 + (2.5 * sentiment_balance)
```

The score is stored in the aggregate row and propagated unchanged as `raw_score` and `score_5`; `score_100` is the existing 0-to-100 conversion. `mention_count`, positive and negative counts come from the aggregate, and `neutral_mentions` is `total_mentions - positive_mentions - negative_mentions`. Because aggregate inputs contain no confidence estimate, `confidence_score` uses the existing mention-volume support factor `min(1, log1p(total_mentions) / log(11))`; it does not claim review-level model confidence.

The dedicated aggregate scoring method obtains the active `AlgorithmVersion`, applies centralized precedence, and writes `calculated_at` plus aggregate provenance. It never invokes a text interpreter or LLM.

After either successful terminal state, `COMPLETED` or `PARTIAL`, with at least one applied score update, existing global normalization runs once over the resulting active-algorithm score population. Review-based and aggregate-sentiment scores can therefore share the existing normalized comparison fields, while documentation explicitly states that their raw 0–5 values use different calculation methods.

## Streaming, Transactions, and Idempotency

The staged file is materialized using the existing object-storage boundary and read one line at a time. The importer never loads the full file into memory. It checkpoints by source line and commits job/counter progress every configured import batch size.

Each hotel line runs inside a nested transaction. Expected attribute validation and mapping issues are recorded without aborting safe sibling attributes. Unexpected database failures roll back taxonomy, aggregate, and score mutations for that hotel, then create a hotel-level issue outside the savepoint. One bad hotel cannot roll back earlier chunks or stop later records.

Within a hotel transaction, existing aggregate keys are prefetched, valid aggregate rows are bulk-upserted where practical, and score propagation uses bulk/conditional upserts where practical. Insert/update counters are determined from the prefetch and remain stable on reimport. Reimport updates the unique aggregate row rather than duplicating it. A completed job remains terminal under the existing job-resume rules; uploading the same content again creates a new auditable job whose aggregate operations are updates.

## Quality Issues

Issues reuse `data_quality_issues`, include `source_id` and `import_job_id`, use `entity_type = SENTIMENT_ANALYSIS`, retain the source line number and safe raw context, and identify the upstream hotel when available. Required issue classes include malformed JSON/record, unknown hotel, invalid counts, invalid sentiment, malformed category/attribute, ambiguous alias, duplicate normalized attribute, and unexpected per-hotel persistence failure.

## Test Strategy

Unit tests cover normalization, deterministic slug generation, aggregate score math, validation boundaries, duplicate detection, alias ambiguity, and score precedence. Integration tests use real PostgreSQL persistence and cover:

- Valid JSONL import and endpoint/job creation.
- Dynamic categories and different attribute sets across multiple hotels.
- Zero-mention skip.
- Safe auto-created category/attribute and deterministic collision slug.
- Active normalized alias match and ambiguous alias skip.
- Duplicate normalized attributes with raw conflicting keys in issue details.
- Duplicate reimport idempotency and aggregate updates.
- Unknown hotel continuation.
- Malformed sentiment and invalid mention-count continuation.
- Aggregate score propagation and all-positive/equal/all-negative score values.
- Review-derived score precedence over aggregate reimport.
- Existing aggregate score replacement by the review-derived recalculation path.
- Normalization of eligible imported scores.
- All explicit import-job counters and compatibility counter semantics.
- Taxonomy migration collision detection with actionable category/attribute details.
- No inserted `review_attribute_mentions`.
- Existing hotel/review CSV import behavior remains passing.

Migration tests/checks verify upgrades, constraints, indexes, provenance backfill, and a downgrade that does not silently discard incompatible data.

## Documentation

Update the README/admin API section with the endpoint, multipart upload example, one JSONL line, source mapping semantics, counters, idempotency key, validation behavior, and the explicit distinction:

- `review_based` scores use the existing per-review normalized-rating adjustment formula.
- `aggregate_sentiment` scores use mention balance mapped directly to 0–5.

## Operational Verification

After implementation:

1. Apply the Alembic migration to the configured development database.
2. Run the sentiment-import targeted unit/integration tests.
3. Run the full test suite.
4. Report changed files, migration revision, endpoint, affected tables, test results, and known limitations.
