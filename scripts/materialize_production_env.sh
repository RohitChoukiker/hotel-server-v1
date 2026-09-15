#!/usr/bin/env bash
set -euo pipefail
umask 077

project_dir="/opt/hotel-platform"
: "${GCP_PROJECT_ID:?GCP_PROJECT_ID is required}"
: "${GCP_REGION:?GCP_REGION is required}"
: "${API_DOMAIN:?API_DOMAIN is required}"
: "${WEB_APP_ORIGIN:?WEB_APP_ORIGIN is required}"
: "${GCS_BUCKET:?GCS_BUCKET is required}"

safe_host='^[A-Za-z0-9.-]+$'
if [[ ! "$API_DOMAIN" =~ $safe_host ]]; then
  printf 'API_DOMAIN contains unsupported characters.\n' >&2
  exit 1
fi

database_url="$(gcloud secrets versions access latest --secret=hotel-database-url)"
jwt_secret="$(gcloud secrets versions access latest --secret=hotel-jwt-secret)"
redis_password="$(gcloud secrets versions access latest --secret=hotel-redis-password)"
for secret_value in "$database_url" "$jwt_secret" "$redis_password"; do
  if [[ -z "$secret_value" || "$secret_value" == *$'\n'* || "$secret_value" == *$'\r'* ]]; then
    printf 'A required secret is empty or contains a newline.\n' >&2
    exit 1
  fi
done

install -d -m 0700 "$project_dir"
printf '%s\n' \
  'APP__ENVIRONMENT=production' \
  'APP__DEBUG=false' \
  "APP__PUBLIC_BASE_URL=https://${API_DOMAIN}" \
  "APP__TRUSTED_HOSTS=[\"${API_DOMAIN}\"]" \
  "DATABASE__URL=${database_url}" \
  "REDIS__URL=redis://:${redis_password}@redis:6379/0" \
  'REDIS__FAIL_OPEN=false' \
  "JWT__SECRET_KEY=${jwt_secret}" \
  "CORS__ALLOWED_ORIGINS=[\"${WEB_APP_ORIGIN}\"]" \
  'CORS__ALLOW_CREDENTIALS=true' \
  'LOGGING__LEVEL=INFO' \
  'LOGGING__JSON_LOGS=true' \
  'LLM__PROVIDER=deterministic' \
  'STORAGE__BACKEND=gcs' \
  "STORAGE__GCS_BUCKET=${GCS_BUCKET}" \
  "GCP__PROJECT_ID=${GCP_PROJECT_ID}" \
  'SCRAPER__TRIPADVISOR_ENABLED=false' \
  > "$project_dir/.env.production"

printf 'GCP_PROJECT_ID=%s\nGCP_REGION=%s\nREDIS_PASSWORD=%s\nAPI_DOMAIN=%s\n' \
  "$GCP_PROJECT_ID" "$GCP_REGION" "$redis_password" "$API_DOMAIN" \
  > "$project_dir/.deploy.env"
chmod 0600 "$project_dir/.env.production" "$project_dir/.deploy.env"
