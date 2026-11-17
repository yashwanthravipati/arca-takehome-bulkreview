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

## Using the grid

- **New review.** Pick a template, then choose the documents: by product, current versions
  only, or one by one. Before anything runs, you see the number of cells, an estimated cost
  and duration, based on the cells answered so far.
- **Find the problems.**
  - Click a column title to sort.
  - Search matches document ids, titles and answers.
  - "Filter by column…" keeps rows by answer: yes or no, a date before today (overdue
    commitments), a list that has items, or not found.
  - The status chips show needs review, not found, failed and not run.
- **Check an answer.** Click a cell for a quick look: the drawer lists the answer's claims,
  each with its check result and quotes. **Open evidence**, or click a claim or a quote, to
  check it against the document:
  - The answer and its claims sit beside the document's pages, shown with their headings,
    paragraphs, lists and tables.
  - The focused claim's quotes are highlighted and the other quotes are dimmed. Each
    highlight carries its claim's number.
  - Pages with nothing cited are folded away; untick **Cited pages only** to see them.
  - ↑ ↓ move between claims. ← → move to the previous or next document in the same column,
    in the grid's current order, so a whole column can be reviewed without closing. Esc
    closes.
  - Links go straight to an answer (`#/reviews/<id>/cells/<cellId>`) or to its evidence
    (`…/evidence`); **Copy link** copies the one you're on.
- **Tune a question.** **Add column** or a column's **Edit** can run on the first 3 documents
  first. The rest wait as "not run" until you click **Run on remaining N** in the column
  header. Editing a column clears its answers and answers it again.
- **Export CSV** writes one row per document, with each quote prefixed by its claim.
- **Delete** in the review list removes a review with its cells and citations.

## How live updates work

The API only creates pending cells. The worker is a separate long-lived process that claims
cells from every review: it polls once a second and holds a lease on each cell it claims.

Each open grid page has a Server-Sent Events stream:
- The stream starts with every cell in the review.
- After that, it polls Postgres every 500 ms for cells whose `updated_at` changed.
- It re-reads a 5-second window so that a transaction committing late isn't missed.

The upgrade path is Postgres `LISTEN/NOTIFY`: workers `NOTIFY` when they write a cell, and the
API `LISTEN`s and pushes the change instead of polling.
