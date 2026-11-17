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
6. `python scripts/requeue.py --review 1 --column deviations` re-queues a column after you
   edit it in the template. `--status needs_review` or `--cell <id>` re-queue other cells.
   Then run step 3 again.
7. `python scripts/check_worker.py` checks the queue rules with a fake model, so it makes no
   API calls: throttling, the spend cap, and one cell per document at a time. Stop other
   workers first.

How a worker processes a cell:

1. **Claim.** One `UPDATE … FOR UPDATE SKIP LOCKED` takes the cell with a lease of
   `LEASE_SECONDS` (default 30; a call takes 2–6 s). A cell gets at most 3 attempts.
   - Only the first unfinished cell of each document can be claimed. A document's questions
     therefore run back to back, while workers spread across documents.
   - The first question writes the document to the prompt cache and the rest read it: one
     cache write per document, at any review size. Before this, a document's questions were
     spread across the whole run. Past a few thousand documents that is longer than the
     5-minute cache lifetime, so every question would pay to write the cache again.
   - Partial indexes cover only unfinished cells, so a claim doesn't slow down as finished
     cells pile up. With 262k finished cells in the table, a claim took 0.009 ms; the old
     query took 15 ms and grew with the table.
2. **Answer.** One model call answers the cell, with the fast tier (Claude Haiku 5.5) by default:
   - The document goes first as a cached block, so the other columns for that document read it
     from the cache.
   - Throttling (a 429 rate or ramp limit, or a 529 overloaded) is retried within the call,
     waiting as long as the `retry-after` header asks. If it persists, the cell goes back in
     the queue without using an attempt and the worker loop pauses. The monthly spend-cap
     429 can't be retried away, so it fails the cell normally.
   - The answer comes back as a forced `record_answer` tool call: `found`, `answer`,
     `claims`, `reasoning`. The claims split the answer into its facts: one per list item,
     or one per fact of a text answer (each storage condition, say). Each claim carries its
     own verbatim quotes, so every fact can be traced to its source.
3. **Verify.** Each quote is located in the stored text (exact match, else fuzzy ≥ 95) and
   mapped to its page. A quote under 20 characters counts only if it occurs exactly once.
   `| pH | 4.5-5.5 |` pins one table row, but `25°C` proves nothing.
4. **Check.** Verification proves a quote exists, not that it supports the claim. So a second
   fast-tier call sees only the claims and their verified quotes, not the document, and decides
   for each whether the quotes contain its key fact (a value, limit, timepoint, condition or
   yes/no conclusion).
   - **On review 27:** it flagged 5 of 86 answered cells. One was a real error the eval had
     missed: "no numeric limit", against a table row that says "NMT 0.20% each". One was the
     checker's own mistake, and the rest were defensible nitpicks.
   - **What it doesn't catch:** a claim that is true to its quote but doesn't belong in the
     answer, like a one-off supplementary test listed as a scheduled timepoint.
   - **Prompt design:** writing the reason before the verdict mattered. With the verdict
     first, the same rules flagged 37 cells, and several verdicts contradicted their own
     reasons.
   - **Cost:** about $0.0003 per cell, which is included in the cell's token counts.
5. **Status.**
   - `done`: every claim has at least one quote, every quote is verified, the check passes
     every claim, and a list answer has a claim for each item.
   - `needs_review`: anything less. For example, a quote can't be found in the document, a
     claim or list item has no quote, or the check finds a claim unsupported.
   - `not_found`: the model says the document doesn't contain the answer.
   - `failed`: all 3 attempts errored.
   - `held`: not run yet. A new or edited column can be tried on its first 3 documents; the
     other cells wait as `held` until the user runs them. Workers never claim held cells.
6. **Write.** The result, its claims, their checks and its citations (each tagged with its
   claim's number) are written in one transaction, and only if this worker still holds the
   lease.

**Why the strong tier doesn't force the tool call.** Claude Haiku 5.5 accepts
`tool_choice: {"type": "tool"}`, so fast-tier calls force `record_answer`. Claude Sonnet 5.5
rejects forced tool choice with a 400 (`tool_choice: type "tool" and "any" are not supported
for this model.`) whether thinking is on or off. Its off switch, `thinking: {"type":
"between_tools"}`, gets the same 400, so thinking is not the cause. Strong-tier calls therefore
use `tool_choice: auto`:
- the system prompt asks for the call;
- `strict: true` keeps the arguments schema-valid;
- a response without the call is an error, which the worker retries.
