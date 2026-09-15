#!/usr/bin/env bash
set -euo pipefail
umask 077

project_dir="/opt/hotel-platform"
cd "$project_dir"
set -a
source "$project_dir/.deploy.env"
set +a

previous_image="$(tr -d '\n' < .previous-image)"
if [[ -z "$previous_image" ]]; then
  printf 'No previous image is recorded.\n' >&2
  exit 1
fi
printf 'BACKEND_IMAGE=%s\nREDIS_PASSWORD=%s\nAPI_DOMAIN=%s\n' \
  "$previous_image" "$REDIS_PASSWORD" "$API_DOMAIN" > .release.env
docker compose --env-file .release.env -f docker-compose.prod.yml up -d
curl --fail --retry 20 --retry-delay 2 "https://${API_DOMAIN}/readyz"
