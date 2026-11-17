# Bulk review

Runs a set of questions across regulatory documents. Every answer cites the exact passage and
page it came from.

- `Ingestion/` loads the synthetic corpus into Postgres: documents plus page offsets.
- `Review_Engine/` holds the cell queue, model calls, citation checks, a CLI, and the eval.
- `api/` is a FastAPI service that reads and writes Postgres. It never calls the model.
- `web/` is the review grid UI (Vite + React + TanStack Table).

## Run it locally

1. Install Docker, [uv](https://docs.astral.sh/uv/) and Node 20 or later.
2. `cp .env.example .env`, then set `ANTHROPIC_API_KEY`.
3. `make setup` creates the Python virtualenvs and installs the web dependencies.
4. `make dev` starts Postgres, loads the corpus, and runs the API (127.0.0.1:8010), the worker
   (`run_workers.py --watch`) and the web app. Ctrl-C stops everything.
5. Open http://127.0.0.1:5173, click **New review**, and watch the cells fill in.
6. Optional: while `make dev` is running, `make screenshots` saves screenshots to
   `web/screenshots/`.

## How live updates work

The API only creates pending cells. The worker is a separate long-lived process that claims
cells from every review: it polls once a second and holds a lease on each cell it claims.

Each open grid page has a Server-Sent Events stream:
- The stream starts with every cell in the review.
- After that, it polls Postgres every 500 ms for cells whose `updated_at` changed.
- It re-reads a 5-second window so that a transaction committing late isn't missed.

The upgrade path is Postgres `LISTEN/NOTIFY`: workers `NOTIFY` when they write a cell, and the
API `LISTEN`s and pushes the change instead of polling.
