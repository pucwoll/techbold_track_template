#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_NAME="${COMPOSE_PROJECT_NAME:-techbold-smoke-$(date +%s)}"
ENV_FILE="${COMPOSE_SMOKE_ENV_FILE:-${ROOT_DIR}/.env.example}"
SKIP_CLEANUP="${COMPOSE_SMOKE_KEEP_RUNNING:-0}"
BACKEND_PORT="${COMPOSE_SMOKE_BACKEND_PORT:-18000}"
FRONTEND_PORT="${COMPOSE_SMOKE_FRONTEND_PORT:-15173}"

export BACKEND_HOST_PORT="${BACKEND_PORT}"
export FRONTEND_HOST_PORT="${FRONTEND_PORT}"

COMPOSE=(
  docker compose
  --project-directory "${ROOT_DIR}"
  --env-file "${ENV_FILE}"
  --project-name "${PROJECT_NAME}"
)

cleanup() {
  if [[ "${SKIP_CLEANUP}" == "1" ]]; then
    echo "Smoke stack left running with project name ${PROJECT_NAME}"
    return
  fi
  "${COMPOSE[@]}" down --volumes --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "Building and starting smoke stack (${PROJECT_NAME})..."
"${COMPOSE[@]}" up --build --detach --wait

echo "Verifying service health..."
"${COMPOSE[@]}" ps --status running --services | grep -qx "postgres"
"${COMPOSE[@]}" ps --status running --services | grep -qx "backend-api"
"${COMPOSE[@]}" ps --status running --services | grep -qx "worker"
"${COMPOSE[@]}" ps --status running --services | grep -qx "frontend"

HEALTH_JSON="$(curl --fail --silent --show-error "http://localhost:${BACKEND_PORT}/health")"
export HEALTH_JSON

python3 - <<'PY'
import json
import os

health = json.loads(os.environ["HEALTH_JSON"])
assert health["status"] == "ok", health
assert health["database"]["configured"] is True, health
assert health["database"]["reachable"] is True, health
assert health["phoenix"]["configured"] is True, health
print("Backend /health:", json.dumps(health, sort_keys=True))
PY

curl --fail --silent --show-error "http://localhost:${FRONTEND_PORT}" >/dev/null
echo "Compose smoke test passed."
