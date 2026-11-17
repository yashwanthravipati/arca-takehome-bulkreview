# Review engine: questions × documents → cited answers

A review runs a template's questions (columns) over a set of documents. Each (document, column)
pair is a cell, and the `cells` table is both the job queue and the results table. It uses the
Postgres database loaded by `../Ingestion` (start it and run `ingest.py` first), and
`app/text.py` is a symlink to `../Ingestion/app/text.py`. That way quotes are matched with the
same normalization the stored text went through.

1. `uv venv --python 3.13 && uv pip install -r requirements.txt && source .venv/bin/activate`
2. `python scripts/create_review.py --template stability_protocol --name "Stability review"`
   creates 90 pending cells: 15 current protocols × 6 columns.
3. `python scripts/run_workers.py --review 1 --concurrency 8` answers every cell. It is safe to
   kill and re-run, or to run as several processes at once.
4. `python scripts/show_review.py 1` prints the grid. `--cell <id>` shows one cell's answer,
   its reasoning, and each citation in context.
5. `python scripts/eval_review.py 1` scores the review against `corpus/ground_truth.json`:
   accuracy per column, status counts, and the planted cases.

How a worker processes a cell:

1. **Claim.** One `UPDATE … FOR UPDATE SKIP LOCKED` takes the cell with a lease of
   `LEASE_SECONDS` (default 30; a call takes 2–6 s). A cell gets at most 3 attempts.
2. **Answer.** One model call per cell, with the fast tier (Claude Haiku 5.5) by default:
   - The document goes first as a cached block, so the other columns for that document read it
     from the cache.
   - The answer comes back as a forced `record_answer` tool call: `found`, `answer`,
     `citations`, `reasoning`.
3. **Verify.** Each quote is located in the stored text (exact match, else fuzzy ≥ 95) and
   mapped to its page.
4. **Status.**
   - `done`: every citation is verified.
   - `needs_review`: a citation can't be found in the document, or there are none.
   - `not_found`: the model says the document doesn't contain the answer.
   - `failed`: all 3 attempts errored.
5. **Write.** The result and its citations are written in one transaction, and only if this
   worker still holds the lease.

**Why the strong tier doesn't force the tool call.** Claude Haiku 5.5 accepts
`tool_choice: {"type": "tool"}`, so fast-tier calls force `record_answer`. Claude Sonnet 5.5
rejects forced tool choice with a 400 (`tool_choice: type "tool" and "any" are not supported
for this model.`) whether thinking is on or off. Its off switch, `thinking: {"type":
"between_tools"}`, gets the same 400, so thinking is not the cause. Strong-tier calls therefore
use `tool_choice: auto`:
- the system prompt asks for the call;
- `strict: true` keeps the arguments schema-valid;
- a response without the call is an error, which the worker retries.
