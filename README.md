# Bulk Review

Ask the same questions of many regulatory documents at once and get a grid of answers in which
every fact links to the exact passage and page it came from. An answer that the document
doesn't back up goes to a person instead of being marked done.

The demo corpus is synthetic: 15 stability protocols for five fictional products, one
superseded protocol version, and 5 specifications. The stability template asks each protocol
six questions: storage condition, timepoints, acceptance criteria, deviations, bracketing
justification and post-approval commitment.

| On the 90-cell stability review | |
|---|---|
| Answers that match the ground truth | 87–89 of 90, across runs |
| Citations on the expected page | 74–76 of 78 |
| Claims with a quote found in the document | 396 of 396 |
| Time and cost, 8 workers | about a minute, about $0.08 |

![System overview: the web UI talks to the API, which only reads and writes Postgres. Workers claim cells from Postgres with a lease, call Claude, and write results back. ingest.py loads the corpus.](docs/diagrams/1-system.svg)

## Run it

1. Install Docker, [uv](https://docs.astral.sh/uv/) and Node 20 or later.
2. `cp .env.example .env`, then set `ANTHROPIC_API_KEY`.
3. `make setup` creates the Python virtualenvs and installs the web dependencies.
4. `make dev` starts Postgres (127.0.0.1:5433), loads the corpus, and runs the API
   (127.0.0.1:8010), the worker and the web app. Ctrl-C stops everything.
5. Open http://127.0.0.1:5173 and click **New review**.

## Using the app

- **Start a review.** Pick a template and the documents (by product, current versions only,
  or one by one). The number of cells, an estimated cost and a duration show before anything
  runs.
- **Watch it fill in.** Cells update live. Sort by a column, search answers and document
  names, or filter by an answer: yes or no, a date before today (overdue commitments), a list
  with items, or not found.
- **Check an answer.** Click a cell for its answer, claims and check results. **Open
  evidence** shows the document's pages beside the answer, with the focused claim's quotes
  highlighted and uncited pages folded away. Use ↑ ↓ to move between claims, ← → to move to
  the next document in the column, and Esc to close. Every answer and its evidence view has a
  link (**Copy link**).
- **Tune a question.** **Add column** or **Edit** can run a question on the first 3 documents.
  Check those answers, then click **Run on remaining** in the column header.
- **Rerun** a cell, **Export CSV** (each quote is prefixed by its claim), or **Delete** a
  review.

## How it works

### One cell, end to end

A cell is one question about one document. Workers process cells in six steps.

![The six steps a worker takes for one cell: claim, answer, verify, check, status, write, with a real example from STAB-015 and the failure paths.](docs/diagrams/2-one-cell.svg)

1. **Claim.** One `UPDATE … FOR UPDATE SKIP LOCKED` takes the cell with a 30-second lease, so
   any number of worker loops and processes can share the queue.
2. **Answer.** Claude Haiku 5.5 (or Sonnet 5.5, per column) gets the whole document as a cached
   prefix, then the question. It replies through a forced tool call: `found`, `answer`,
   `claims` (each with its own verbatim quotes) and `reasoning`.
3. **Verify.** Each quote is located in the stored text (exact, else fuzzy ≥ 95) and mapped to
   its page. A quote under 20 characters counts only if it appears once in the document.
4. **Check.** A second, cheap call sees only the claims and their found quotes. It decides
   whether each quote holds its claim's key fact (a value, limit, timepoint, condition or
   yes/no conclusion).
5. **Status.** `done` only if every claim passes and a list answer has a claim per item.
   Anything else is `needs_review`. `not_found` means the document doesn't contain the answer.
6. **Write.** The result, claims, checks and citations are written in one transaction, and
   only if this worker still holds the lease.

### Cell states

![Cell states: held, pending, running, then done, needs review, not found or failed, with the transitions between them.](docs/diagrams/3-cell-states.svg)

- **Throttling** (429 or 529) puts a cell back in the queue without using one of its 3
  attempts, so rate limits slow a run down but never fail a cell. The monthly spend-cap 429
  is the exception: it fails the cell normally.
- **Crashes lose nothing.** If a worker dies, its lease runs out and another worker takes
  the cell.

### Scheduling and caching

![Left: four workers on four different documents, each document's questions in order. Right: before, a document's second question ran after every other document's first; now all six run back to back inside the cache's lifetime.](docs/diagrams/4-scheduling.svg)

- **One cell per document at a time.** A worker only claims a cell that is its document's
  first unfinished one, so each document's questions run back to back while workers spread
  across documents.
- **One cache write per document.** The first question writes the document to the prompt
  cache and the rest read it, at any review size. Review 15 wrote the cache exactly 15 times
  for 15 documents.
- **Fast claims at any size.** Partial indexes cover only unfinished cells. With 262k finished
  cells in the table, a claim took 0.009 ms; the old query took 15 ms and grew with the table.

### The evidence behind an answer

![The evidence chain: answer, claim, quote, text span, page, highlight. Three checks guard it, and a failed check sends the cell to needs review.](docs/diagrams/5-evidence.svg)

- **Highlights use the stored offsets.** Ingestion stores each document's normalized text,
  with page offsets and the position of every source line. The page number and the highlight
  in the evidence view come from those same offsets, so nothing is matched twice.
- **What the support check can't catch.** It doesn't flag a claim that is true to its quote
  but doesn't belong in the answer, such as a one-off supplementary test listed as a scheduled
  timepoint. The eval measures those.

### Live updates

Each open grid has a Server-Sent Events stream. It starts with every cell, then polls Postgres
every 500 ms for cells whose `updated_at` changed. It re-reads a 5-second window, so a
transaction that commits late isn't missed. The upgrade path is `LISTEN/NOTIFY`.

## Scale and cost

The ceiling is the model's rate limit, not the database.

- **Per cell, measured on review 27:** about 1,050 output tokens and 5.3 s for the answer and
  the check, and $0.0009.
- **Start tier:** Haiku 5.5 allows 400,000 output tokens a minute. That's about 380 cells a
  minute with about 35 calls in flight, so 100,000 cells take about 4.5 hours and cost about
  $90.
- **Scale tier:** 2,000,000 output tokens a minute, about 1,900 cells a minute, so 100,000
  cells take about an hour.
- **Next:**
  - the Message Batches API for portfolio-wide runs: half price, its own queue of up to
    200k–500k requests, results within 24 hours;
  - fair scheduling across reviews;
  - adaptive pacing of concurrency.

## Checks and the eval

```sh
Ingestion/.venv/bin/python Ingestion/scripts/check_ingest.py          # every evidence sentence on its expected page (89/89)
Review_Engine/.venv/bin/python Review_Engine/scripts/eval_review.py 1  # answers, citations, claims, support check, planted cases
Review_Engine/.venv/bin/python Review_Engine/scripts/check_worker.py   # queue rules with a fake model (no API calls; stop other workers first)
```

The corpus has five planted cases, and the eval checks each one. The pipeline handles four:
- excluding a superseded version;
- reporting a missing bracketing rationale as not found;
- finding a deviation that is only listed in an appendix;
- reporting an absent post-approval commitment as not found.

The fifth, acceptance criteria that contradict the product specification, needs a question
that reads two documents. That isn't built yet.

## Repository

| Folder | Contents |
|---|---|
| [`Ingestion/`](Ingestion/README.md) | Synthetic corpus and ground truth, the loader (text, page and line offsets), the citation check |
| [`Review_Engine/`](Review_Engine/README.md) | Review templates, the worker (queue, model calls, verification, support check), CLI scripts, the eval |
| `api/` | FastAPI service: reviews, columns, cells, documents, CSV export, live updates. It never calls the model. |
| `web/` | React, Vite and TanStack Table: the grid, the cell drawer, the evidence view |
| `docs/diagrams/` | The diagrams in this README |

## Decisions and tradeoffs

- **Postgres is the queue.**
  - `SKIP LOCKED` plus leases gives crash-safe, at-least-once processing with one system to
    run.
  - It would move to a workflow engine (such as Temporal) when a cell becomes several steps
    (retrieve, answer, compare with the specification, sign off), not for throughput.
- **The whole document goes into each call, with no chunking.**
  - Every question sees the full context, and the cache makes the second to sixth questions
    cheap.
  - Documents over 100k tokens, where Haiku's price is five times higher, should be split by
    section at ingestion.
- **One call per document and question.** A rerun or a new column touches only its own cells,
  and answer quality doesn't depend on how many questions share a call.
- **Quotes are verified in code, not trusted.** A citation counts only once it is found in the
  stored text, and the support check sits on top of that.
- **Haiku by default.** It costs about a twentieth of Sonnet per token, and any column can use
  the stronger model.

## What's next

- **A review workflow:**
  - accept, correct or sign off on an answer;
  - keep an audit trail of who decided, when, and with which model and prompt version (a
    rerun overwrites the old answer today).
- **Questions that span documents:** "does this contradict the current specification?" and
  "has this commitment been met?"
- **More document types:** templates and corpus for CMC sections and health-authority letters.
- **Long documents:** split them by section.
- **Scale and access:** a batch lane for big runs, and authentication.
