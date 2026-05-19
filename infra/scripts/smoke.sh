#!/usr/bin/env bash
# Phase 0 smoke — bring up the v2 stack and verify each service is healthy.
#
# Prereqs: cp infra/.env.example infra/.env  →  edit passwords.
# Usage:   bash infra/scripts/smoke.sh
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ ! -f .env ]]; then
  echo "ERROR: infra/.env missing. Copy from .env.example and fill in passwords." >&2
  exit 1
fi

# Source .env so we can reference GSFLUENT_V2_PORT below.
set -a; . ./.env; set +a

PORT="${GSFLUENT_V2_PORT:-8869}"

echo "==> bringing up v2 stack (this may take a minute on first pull)..."
docker compose --profile v2 up -d --wait

echo
echo "==> postgres"
docker compose --profile v2 exec -T postgres pg_isready -U gsfluent -d gsfluent_v2

echo
echo "==> redis"
docker compose --profile v2 exec -T redis redis-cli ping

echo
echo "==> minio"
docker compose --profile v2 exec -T minio curl -fsS http://localhost:9000/minio/health/live
echo "minio: live"

echo
echo "==> loki"
docker compose --profile v2 exec -T loki wget -qO- http://localhost:3100/ready
echo "loki: ready"

echo
echo "==> caddy root"
curl -fsS "http://localhost:${PORT}/" || { echo "caddy root failed"; exit 1; }
echo

echo
echo "==> grafana via caddy"
curl -fsS "http://localhost:${PORT}/grafana/api/health"
echo

echo
echo "==> minio bucket list (init sidecar should have created 3 buckets)"
docker compose --profile v2 exec -T minio sh -c \
  'mc alias set local http://localhost:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null 2>&1 && mc ls local'

echo
echo "phase 0 smoke: OK"
