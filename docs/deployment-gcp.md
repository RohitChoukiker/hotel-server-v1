# GCP VM production deployment

This runbook uses one Ubuntu VM for Nginx, FastAPI, Celery and Redis; private-IP Cloud SQL for PostgreSQL/PostGIS; GCS for immutable import files; Secret Manager for secrets; and Artifact Registry for images.

## 1. Sizing

- Initial 3–10 user deployment: `e2-standard-4` (4 vCPU, 16 GB RAM), 50 GB balanced persistent disk.
- Worker-heavy review processing: `e2-standard-8` or a separate worker VM.
- Cloud SQL: PostgreSQL 16, `db-custom-2-7680`, 50 GB SSD, storage auto-growth, HA for production.
- Redis on the VM is acceptable initially. Move to Memorystore when worker/cache availability needs an independent SLA.

Keep the VM, Cloud SQL and GCS bucket in the same region. The commands below use `asia-south1` and zone `asia-south1-a`; change them once and keep them consistent.

## 2. Project, APIs and Artifact Registry

```bash
export GCP_PROJECT_ID="your-project-id"
export GCP_REGION="asia-south1"
export GCP_ZONE="asia-south1-a"
export API_DOMAIN="api.example.com"
gcloud config set project "$GCP_PROJECT_ID"
gcloud services enable compute.googleapis.com sqladmin.googleapis.com \
  artifactregistry.googleapis.com cloudbuild.googleapis.com secretmanager.googleapis.com \
  storage.googleapis.com logging.googleapis.com monitoring.googleapis.com
gcloud artifacts repositories create hotel --repository-format=docker \
  --location="$GCP_REGION" --description="Hotel platform images"
```

## 3. Network, firewall and static IP

```bash
gcloud compute addresses create hotel-api-ip --region="$GCP_REGION"
export HOTEL_STATIC_IP="$(gcloud compute addresses describe hotel-api-ip \
  --region="$GCP_REGION" --format='value(address)')"
gcloud compute firewall-rules create hotel-allow-web \
  --network=default --direction=INGRESS --action=ALLOW --rules=tcp:80,tcp:443 \
  --source-ranges=0.0.0.0/0 --target-tags=hotel-api
```

Do not expose ports 8000, 5432 or 6379. For SSH, prefer IAP instead of a public `tcp:22` rule.

## 4. Service account, VM and disk

```bash
gcloud iam service-accounts create hotel-runtime --display-name="Hotel platform runtime"
export RUNTIME_SA="hotel-runtime@${GCP_PROJECT_ID}.iam.gserviceaccount.com"
for role in roles/artifactregistry.reader roles/secretmanager.secretAccessor \
  roles/storage.objectAdmin roles/logging.logWriter roles/monitoring.metricWriter; do
  gcloud projects add-iam-policy-binding "$GCP_PROJECT_ID" \
    --member="serviceAccount:${RUNTIME_SA}" --role="$role"
done
gcloud compute instances create hotel-api-1 \
  --zone="$GCP_ZONE" --machine-type=e2-standard-4 --boot-disk-size=50GB \
  --boot-disk-type=pd-balanced --image-family=ubuntu-2404-lts-amd64 \
  --image-project=ubuntu-os-cloud --address="$HOTEL_STATIC_IP" --tags=hotel-api \
  --service-account="$RUNTIME_SA" \
  --scopes=https://www.googleapis.com/auth/cloud-platform \
  --metadata=enable-osconfig=TRUE
```

## 5. Cloud SQL, private IP, PostGIS, backups and PITR

Set up private services access in the console or with your existing VPC automation. Then:

```bash
gcloud sql instances create hotel-postgres \
  --database-version=POSTGRES_16 --region="$GCP_REGION" \
  --tier=db-custom-2-7680 --storage-type=SSD --storage-size=50 \
  --storage-auto-increase --network=default --no-assign-ip \
  --availability-type=regional --backup-start-time=20:00 \
  --enable-point-in-time-recovery --retained-transaction-log-days=7 \
  --retained-backups-count=14 --deletion-protection
gcloud sql databases create hotel --instance=hotel-postgres
export HOTEL_DB_PASSWORD="$(openssl rand -hex 32)"
gcloud sql users create hotel_app --instance=hotel-postgres \
  --password="$HOTEL_DB_PASSWORD"
export HOTEL_SQL_PRIVATE_IP="$(gcloud sql instances describe hotel-postgres \
  --format='value(ipAddresses[0].ipAddress)')"
printf 'postgresql+asyncpg://hotel_app:%s@%s:5432/hotel' \
  "$HOTEL_DB_PASSWORD" "$HOTEL_SQL_PRIVATE_IP" \
  | gcloud secrets create hotel-database-url --data-file=-
unset HOTEL_DB_PASSWORD
```

After connecting from the VM or Cloud SQL Studio:

```sql
CREATE EXTENSION IF NOT EXISTS postgis;
SELECT PostGIS_Version();
```

Test restore procedures quarterly. PITR example:

```bash
gcloud sql instances clone hotel-postgres hotel-postgres-restore-test \
  --point-in-time="2026-09-11T10:00:00Z"
```

## 6. GCS and secrets

```bash
gcloud storage buckets create "gs://${GCP_PROJECT_ID}-hotel-imports" \
  --location="$GCP_REGION" --uniform-bucket-level-access
gcloud storage buckets update "gs://${GCP_PROJECT_ID}-hotel-imports" \
  --versioning --lifecycle-file=docs/gcs-lifecycle.json
openssl rand -base64 48 | gcloud secrets create hotel-jwt-secret --data-file=-
openssl rand -base64 32 | gcloud secrets create hotel-redis-password --data-file=-
```

Never put secret values in the image, GitHub secrets used as command-line arguments, or logs. Materialize `.env.production` on the VM with owner-only permissions, preferably from Secret Manager during provisioning.

## 7. Ubuntu and Docker

```bash
gcloud compute ssh hotel-api-1 --zone="$GCP_ZONE" --tunnel-through-iap
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg git google-cloud-ops-agent
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo \"$VERSION_CODENAME\") stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker "$USER"
sudo mkdir -p /opt/hotel-platform
sudo chown "$USER:$USER" /opt/hotel-platform
```

Copy `docker-compose.prod.yml`, `nginx/prod.conf`, `deploy.sh`, `rollback.sh`, and
`scripts/materialize_production_env.sh` into `/opt/hotel-platform`. Point the domain A
record at `$HOTEL_STATIC_IP`.

## 8. HTTPS

Before starting the TLS Nginx config, obtain the certificate with a temporary HTTP Nginx or standalone Certbot:

```bash
sudo apt-get install -y certbot
sudo systemctl stop nginx 2>/dev/null || true
sudo certbot certonly --standalone -d "$API_DOMAIN" \
  --email ops@example.com --agree-tos --no-eff-email
sudo systemctl enable --now certbot.timer
```

The certificate uses Certbot's standalone authenticator. Install renewal hooks after the
Compose stack is started so port 80 is released during renewal:

```bash
sudo install -d -m 0755 /etc/letsencrypt/renewal-hooks/pre \
  /etc/letsencrypt/renewal-hooks/post
printf '%s\n' '#!/usr/bin/env bash' \
  'cd /opt/hotel-platform' \
  'docker compose --env-file .release.env -f docker-compose.prod.yml stop nginx' \
  | sudo tee /etc/letsencrypt/renewal-hooks/pre/hotel-nginx >/dev/null
printf '%s\n' '#!/usr/bin/env bash' \
  'cd /opt/hotel-platform' \
  'docker compose --env-file .release.env -f docker-compose.prod.yml start nginx' \
  | sudo tee /etc/letsencrypt/renewal-hooks/post/hotel-nginx >/dev/null
sudo chmod 0755 /etc/letsencrypt/renewal-hooks/pre/hotel-nginx \
  /etc/letsencrypt/renewal-hooks/post/hotel-nginx
sudo certbot renew --dry-run
```

## 9. Environment

Materialize production configuration directly from Secret Manager. This keeps secret values out
of command arguments and shell history:

```bash
export GCP_PROJECT_ID="your-project-id"
export GCP_REGION="asia-south1"
export API_DOMAIN="api.example.com"
export WEB_APP_ORIGIN="https://app.example.com"
export GCS_BUCKET="${GCP_PROJECT_ID}-hotel-imports"
sudo install -d -m 0700 -o "$USER" -g "$USER" /opt/hotel-platform
chmod 0700 /opt/hotel-platform/scripts/materialize_production_env.sh
/opt/hotel-platform/scripts/materialize_production_env.sh
```

The generated `/opt/hotel-platform/.env.production` has mode `0600` and contains:

```dotenv
APP__ENVIRONMENT=production
APP__PUBLIC_BASE_URL=https://YOUR_API_DOMAIN
APP__TRUSTED_HOSTS=["YOUR_API_DOMAIN"]
DATABASE__URL=postgresql+asyncpg://hotel_app:URL_ENCODED_PASSWORD@PRIVATE_SQL_IP:5432/hotel
REDIS__URL=redis://:REDIS_PASSWORD@redis:6379/0
REDIS__FAIL_OPEN=false
JWT__SECRET_KEY=AT_LEAST_32_RANDOM_CHARACTERS
CORS__ALLOWED_ORIGINS=["https://app.example.com"]
STORAGE__BACKEND=gcs
STORAGE__GCS_BUCKET=PROJECT_ID-hotel-imports
GCP__PROJECT_ID=PROJECT_ID
SENTRY__DSN=OPTIONAL_SENTRY_DSN
LLM__PROVIDER=deterministic
```

Set `/opt/hotel-platform/.deploy.env` to mode `0600` with `GCP_PROJECT_ID`, `GCP_REGION`, `REDIS_PASSWORD`, and `API_DOMAIN`.

## 10. Build, migrate, seed and start

```bash
gcloud builds submit --tag "$GCP_REGION-docker.pkg.dev/$GCP_PROJECT_ID/hotel/backend:v1.0.0"
cd /opt/hotel-platform
./deploy.sh v1.0.0
docker compose --env-file .release.env -f docker-compose.prod.yml run --rm \
  backend python -m scripts.create_admin admin@example.com
docker compose --env-file .release.env -f docker-compose.prod.yml ps
curl -fsS "https://${API_DOMAIN}/healthz"
curl -fsS "https://${API_DOMAIN}/readyz"
curl -fsS "https://${API_DOMAIN}/metrics" | head
```

`deploy.sh` runs `alembic upgrade head`, idempotent reference seeding, starts the API, worker, beat, Redis and Nginx, verifies full readiness (database, Redis and worker heartbeat), and rolls containers back to the prior image on a failed readiness check.

## 11. Logs, rollback and operations

```bash
docker compose --env-file .release.env -f docker-compose.prod.yml logs -f --tail=200 backend
docker compose --env-file .release.env -f docker-compose.prod.yml logs -f --tail=200 worker
./rollback.sh
gcloud sql backups list --instance=hotel-postgres
gcloud sql operations list --instance=hotel-postgres --limit=20
```

Application logs are JSON on stdout and are collected by the Ops Agent/Docker integration. Configure Monitoring alerts for VM CPU/disk, Cloud SQL CPU/connections/storage, `/readyz` failures, HTTP 5xx rate, Celery queue age, scraper failures, and unresolved critical data-quality issues.
