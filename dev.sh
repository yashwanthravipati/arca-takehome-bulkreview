#!/usr/bin/env bash
# Start everything for local development: Postgres, the API, the worker and the web app.
# Ctrl-C stops all of it. Run `make setup` once first.
set -euo pipefail
cd "$(dirname "$0")"
trap 'kill 0' EXIT

docker compose -f Ingestion/docker-compose.yml up -d
Ingestion/.venv/bin/python Ingestion/scripts/ingest.py   # waits for Postgres; skips loaded documents

(cd api && exec .venv/bin/uvicorn main:api --reload --reload-dir . --reload-dir ../Review_Engine/app \
    --host 127.0.0.1 --port 8010 --timeout-graceful-shutdown 1) &
(cd Review_Engine && exec .venv/bin/python -u scripts/run_workers.py --watch --concurrency 8) &
(cd web && exec npm run dev) &
wait
