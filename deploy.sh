#!/usr/bin/env bash
set -euo pipefail
umask 077

release_sha="${1:?release SHA is required}"
project_dir="/opt/hotel-platform"
cd "$project_dir"

set -a
source "$project_dir/.deploy.env"
set +a

new_image="${GCP_REGION}-docker.pkg.dev/${GCP_PROJECT_ID}/hotel/backend:${release_sha}"
previous_image="$(awk -F= '/^BACKEND_IMAGE=/{print $2}' .release.env 2>/dev/null || true)"

gcloud auth configure-docker "${GCP_REGION}-docker.pkg.dev" --quiet
docker pull "$new_image"
printf 'BACKEND_IMAGE=%s\nREDIS_PASSWORD=%s\nAPI_DOMAIN=%s\n' \
  "$new_image" "$REDIS_PASSWORD" "$API_DOMAIN" > .release.env.next
docker compose --env-file .release.env.next -f docker-compose.prod.yml run --rm backend alembic upgrade head
docker compose --env-file .release.env.next -f docker-compose.prod.yml run --rm backend python -m scripts.seed_reference_data
mv .release.env.next .release.env
docker compose --env-file .release.env -f docker-compose.prod.yml up -d --remove-orphans

for attempt in $(seq 1 30); do
  if curl --fail --silent --show-error "https://${API_DOMAIN}/readyz" >/dev/null; then
    printf '%s\n' "$previous_image" > .previous-image
    docker image prune -f >/dev/null
    exit 0
  fi
  sleep 2
done

if [[ -n "$previous_image" ]]; then
  printf 'BACKEND_IMAGE=%s\nREDIS_PASSWORD=%s\nAPI_DOMAIN=%s\n' \
    "$previous_image" "$REDIS_PASSWORD" "$API_DOMAIN" > .release.env
  docker compose --env-file .release.env -f docker-compose.prod.yml up -d
fi
exit 1
