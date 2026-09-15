# Hotel Platform Backend

Production-oriented FastAPI backend for an India-first, internationally extensible hotel discovery and personalized recommendation product. PostgreSQL/PostGIS is the source of truth; Redis provides cache/rate-limit/queue state; Celery performs heavy work. Hotel ranking is deterministic and never uses embeddings, vector search, RAG, or an LLM.

## Architecture

```text
HTTPS / Nginx
      |
FastAPI API (JWT, RBAC, ownership, DTOs, rate limits)
      |
Services + repositories ---- Redis cache
      |
PostgreSQL 16 + PostGIS

Celery + Redis broker
      |-- CSV hotel/review imports
      |-- structured review attribute extraction
      |-- deterministic score aggregation
      |-- bell-curve normalization
      `-- source scraping/retries/checkpoints
```

The canonical `hotels.id` is a UUID. TripAdvisor `locationId` and every provider review ID remain exact text in source-mapping tables. Factual amenities and review-derived attribute scores are separate domains.

## Recommendation formula

Background processing:

1. A constrained structured-text adapter extracts explicit attribute/sentiment/evidence tuples.
2. Positive mention: `clamp(normalized_review_rating + 1, 0, 5)`.
3. Negative mention: `clamp(normalized_review_rating - 1, 0, 5)`.
4. Neutral mention: normalized review rating.
5. Contributions aggregate per hotel/attribute into `score_5` and `score_100`.
6. Comparable populations produce mean, population standard deviation and z-score.
7. The standard-normal CDF maps z-score to relative 1–5; zero-variance populations center at 3.

Live ranking:

1. Resolve preferences in `TRIP_SPECIFIC > USER_MANUAL > ONBOARDING_AI` order.
2. Fetch destination candidates and stored hotel-attribute scores.
3. Enforce mandatory minimum scores.
4. Calculate the weighted mean from current preferences.
5. Sort descending and persist an explanation snapshot.

No raw reviews are scanned and no LLM is called in the live recommendation request.

## Local start

Requirements: Docker Engine with Compose v2.

```bash
cp .env.example .env
# Replace JWT__SECRET_KEY in .env.
docker compose up -d postgres redis
docker compose build backend worker beat
docker compose run --rm backend alembic upgrade head
docker compose run --rm backend python -m scripts.seed_reference_data
docker compose up -d
curl -fsS http://localhost:8080/healthz
curl -fsS http://localhost:8080/readyz
open http://localhost:8080/docs
```

Bootstrap the first administrator after migrations and seeding; the password is read from a hidden prompt and is never accepted as a command-line argument:

```bash
docker compose run --rm backend python -m scripts.create_admin admin@example.com
```

Local API traffic goes through Nginx at `http://localhost:8080`. PostgreSQL and Redis have no host ports, which avoids accidental external exposure.

## Native development

```bash
uv sync --group dev
cp .env.example .env
uv run alembic upgrade head
uv run python -m scripts.seed_reference_data
uv run uvicorn app.main:app --reload
uv run celery -A app.workers.celery_app:celery_app worker --loglevel=INFO
uv run celery -A app.workers.celery_app:celery_app beat --loglevel=INFO
```

## API inventory

All application responses use the stable success/list/error envelopes in the specification.

Authentication and profile:

- `POST /api/v1/auth/register`
- `POST /api/v1/auth/login`
- `POST /api/v1/auth/refresh`
- `POST /api/v1/auth/logout`
- `GET /api/v1/auth/me`
- `GET /api/v1/users/me`
- `PUT /api/v1/users/me`

Onboarding and preferences:

- `POST /api/v1/onboarding/start`
- `GET /api/v1/onboarding/{session_id}/questions`
- `POST /api/v1/onboarding/{session_id}/answers`
- `POST /api/v1/onboarding/{session_id}/complete`
- `GET /api/v1/onboarding/status`
- `GET /api/v1/preferences/attributes`
- `GET /api/v1/users/me/preferences`
- `PUT /api/v1/users/me/preferences`
- `PATCH /api/v1/users/me/preferences/{attribute_id}`
- `DELETE /api/v1/users/me/preferences/{attribute_id}`

Trips:

- `POST /api/v1/trips`
- `GET /api/v1/trips`
- `GET /api/v1/trips/{trip_id}`
- `PUT /api/v1/trips/{trip_id}`
- `DELETE /api/v1/trips/{trip_id}`
- `GET /api/v1/trips/{trip_id}/preferences`
- `PUT /api/v1/trips/{trip_id}/preferences`

Locations, hotels and reviews:

- `GET /api/v1/locations/countries`
- `GET /api/v1/locations/countries/{country_id}/regions`
- `GET /api/v1/locations/regions/{region_id}/cities`
- `GET /api/v1/locations/search?q=`
- `GET /api/v1/hotels`
- `GET /api/v1/hotels/search`
- `GET /api/v1/hotels/nearby`
- `GET /api/v1/hotels/{hotel_id}`
- `GET /api/v1/hotels/{hotel_id}/images`
- `GET /api/v1/hotels/{hotel_id}/reviews`
- `GET /api/v1/hotels/{hotel_id}/attributes`
- `GET /api/v1/hotels/{hotel_id}/attributes/{attribute_id}`
- `GET /api/v1/hotels/{hotel_id}/attributes/{attribute_id}/reviews`
- `GET /api/v1/reviews/{review_id}`
- `GET /api/v1/reviews/{review_id}/images`

Recommendations and chat:

- `POST /api/v1/recommendations`
- `GET /api/v1/recommendations/{run_id}`
- `GET /api/v1/recommendations/{run_id}/hotels/{hotel_id}`
- `POST /api/v1/chat`
- `GET /api/v1/chat/conversations`
- `GET /api/v1/chat/conversations/{conversation_id}`

Operations (`DATA_OPERATOR` or `ADMIN`, except user/role operations which require `ADMIN`):

- `POST /api/v1/admin/imports/hotels`
- `POST /api/v1/admin/imports/reviews`
- `GET /api/v1/admin/imports`
- `GET /api/v1/admin/imports/{job_id}`
- `GET /api/v1/admin/scrape-runs`
- `POST /api/v1/admin/scrape-runs` (requires an explicit source `geo_id`)
- `GET /api/v1/admin/scrape-runs/{run_id}`
- `GET /api/v1/admin/scrape-runs/{run_id}/failures`
- `POST /api/v1/admin/scrape-runs/{run_id}/retry-failures`
- `POST /api/v1/admin/scoring/process-reviews`
- `POST /api/v1/admin/scoring/recalculate-hotel/{hotel_id}`
- `POST /api/v1/admin/scoring/normalize`
- `GET /api/v1/admin/data-quality/issues`
- `POST /api/v1/admin/data-quality/scan`
- `GET /api/v1/admin/dashboard`
- `GET /api/v1/admin/system-health`
- `GET /api/v1/admin/users`
- `GET /api/v1/admin/users/{user_id}`
- `PATCH /api/v1/admin/users/{user_id}/role`
- `PATCH /api/v1/admin/users/{user_id}/status`
- `GET /api/v1/admin/algorithm-versions`
- `GET /api/v1/admin/settings` (`ADMIN` only; non-secret values)
- `PUT /api/v1/admin/settings/{key}` (`ADMIN` only; non-secret values)
- `POST /api/v1/admin/locations/cities` (canonical city upsert)

Operations endpoints:

- `GET /healthz`
- `GET /readyz` (database, Redis and worker heartbeat)
- `GET /metrics` (Prometheus)
- `GET /docs`

## CSV import

Hotel imports require explicit `country_id` and `region_id`; review joins always use `(source_id, hotel_location_id)`. Raw files are copied unchanged into the configured object store. Jobs checkpoint by row and use provider unique constraints for repeat-safe upserts. Invalid rows are recorded in `data_quality_issues` with their raw row and reason.

Before importing a region, create canonical cities with `POST /api/v1/admin/locations/cities`. The import deliberately reports `MISSING_CITY` instead of silently creating ambiguous city spellings. Validate legacy folders without changing them:

```bash
uv run python scripts/validate_data.py /path/to/india-data
```

The mapping explicitly retains both `dadar-and-nagar` and `daman-diu` inputs while mapping them to the current combined Union Territory.

## Quality gates

```bash
uv run ruff check .
uv run black --check .
uv run isort --check-only .
uv run mypy --strict app scripts
uv run pytest tests -q --cov=app --cov-report=term-missing --cov-report=xml
uv run bandit -q -r app
uv run pip-audit
docker build -t hotel-platform:local .
BACKEND_IMAGE=hotel-platform:local REDIS_PASSWORD=validation-only \
  API_DOMAIN=api.example.com \
  docker compose -f docker-compose.prod.yml config
```

The GitHub Actions workflow executes the same checks, migrates an empty PostGIS database, verifies Alembic drift, builds the image, and gates the protected production environment.

## Configuration and credentials

Copy `.env.example`; every setting uses Pydantic Settings with `__` nested environment keys. Production startup rejects debug mode and weak/default JWT secrets.

External values still required for a real deployment:

- PostgreSQL/Cloud SQL URL and password.
- A random JWT secret (at least 32 characters).
- Redis password and URL.
- GCP project and GCS bucket.
- Sentry DSN (optional but recommended).
- OpenAI-compatible API key only if `LLM__PROVIDER=openai`; `deterministic` is the safe offline fallback.
- Authorized TripAdvisor session cookie and explicit source `geoId` values only if scraping is legally/contractually authorized. The code never invents geoIds and defaults scraping off.
- Domain/DNS, TLS certificate email, Artifact Registry and deployment identity.

## Production deployment

Follow [docs/deployment-gcp.md](docs/deployment-gcp.md). It contains exact project/API, VM, firewall, static IP, Cloud SQL/PostGIS, GCS, Secret Manager, Docker, HTTPS, migration, seed, health, logging, rollback, backup and PITR commands.
