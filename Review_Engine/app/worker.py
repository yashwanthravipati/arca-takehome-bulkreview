"""Cell worker: claim a cell with a lease, answer it, verify its citations, write the result.

The cells table is the queue. A claim is one atomic UPDATE ... FOR UPDATE SKIP LOCKED, so any
number of worker loops and processes can share a review without taking the same cell. A claim
is a lease of LEASE_SECONDS (default 30; a call takes 2-6 s): if a worker dies, its cell becomes
claimable again once the lease expires. A cell gets at most MAX_ATTEMPTS attempts before it is
marked failed. With review_id None a worker serves every review (run_workers.py --watch).

Only the first unfinished cell of each document is claimable, so a document's questions run one
after another: the first writes the document to the prompt cache and the rest read it, at any
review size. Workers spread across documents instead. Throttling (429, 529) puts a cell back in
the queue without using an attempt.
"""

import asyncio
import os
import time

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.llm import answer_cell, backoff_s, check_claims, is_throttled
from app.models import Column
from app.text import locate, normalize

MAX_ATTEMPTS = 3
MIN_QUOTE_CHARS = 20  # shorter quotes must be exact and unique in the document (see verify)
IDLE_POLL_S = 2   # waiting for other workers' leases at the end of a review
WATCH_POLL_S = 1  # --watch: how often an idle worker looks for new cells
APP_NAME = "bulkreview worker"  # each worker connection's application_name starts with this

# The status IN (...) conditions match the partial indexes on unfinished cells, so a claim
# reads only open work, however many finished cells the table holds.
CLAIM = """
UPDATE cells SET status = 'running', attempts = attempts + 1, claimed_by = %(worker)s,
       lease_expires_at = now() + make_interval(secs => %(lease_s)s), updated_at = now()
WHERE id = (
  SELECT c.id FROM cells c
  WHERE c.status IN ('pending', 'running')
    AND (c.status = 'pending' OR c.lease_expires_at < now())
    AND c.attempts < %(max_attempts)s
    AND (%(review_id)s::int IS NULL OR c.review_id = %(review_id)s)
    -- the first unfinished cell of its document in this review (see the module docstring)
    AND c.id = (SELECT min(s.id) FROM cells s
                WHERE s.review_id = c.review_id AND s.doc_id = c.doc_id
                  AND s.version = c.version AND s.status IN ('pending', 'running'))
  ORDER BY c.id
  FOR UPDATE SKIP LOCKED
  LIMIT 1)
RETURNING id, review_id, column_id, doc_id, version, attempts
"""
# Named columns, not RETURNING *: workers keep this statement prepared, and Postgres refuses a
# cached plan whose result columns change, so adding a column to cells would crash every
# running worker ("cached plan must not change result type").

# Guarded by status and claimed_by: if this worker's lease expired and another worker
# re-claimed the cell, the stale result is dropped instead of overwriting the new attempt.
WRITE_RESULT = """
UPDATE cells SET status = %(status)s, answer = %(answer)s, claims = %(claims)s,
       checks = %(checks)s, reasoning = %(reasoning)s, model = %(model)s, input_tokens = %(input_tokens)s, output_tokens = %(output_tokens)s,
       cache_read_tokens = %(cache_read_tokens)s, cache_write_tokens = %(cache_write_tokens)s,
       latency_ms = %(latency_ms)s, lease_expires_at = NULL, updated_at = now()
WHERE id = %(id)s AND status = 'running' AND claimed_by = %(worker)s
"""

INSERT_CITATION = """
INSERT INTO citations (cell_id, claim_no, doc_id, version, quote, char_start, char_end,
                       page_no, verified, match_method)
VALUES (%(cell_id)s, %(claim_no)s, %(doc_id)s, %(version)s, %(quote)s, %(char_start)s,
        %(char_end)s, %(page_no)s, %(verified)s, %(match_method)s)
"""

RECORD_FAILURE = """
UPDATE cells SET status = CASE WHEN attempts >= %(max_attempts)s THEN 'failed' ELSE 'pending' END,
       last_error = %(error)s, lease_expires_at = NULL, updated_at = now()
WHERE id = %(id)s AND status = 'running' AND claimed_by = %(worker)s
"""

# Throttling is not the cell's fault: back to the queue, and the claim's attempt is returned.
REQUEUE_THROTTLED = """
UPDATE cells SET status = 'pending', attempts = attempts - 1, last_error = %(error)s,
       lease_expires_at = NULL, updated_at = now()
WHERE id = %(id)s AND status = 'running' AND claimed_by = %(worker)s
"""

# A cell whose final attempt died with the lease held: no worker will finish it, and as an
# unfinished cell it would hold up the rest of its document.
SWEEP_DEAD = """
UPDATE cells SET status = 'failed', last_error = 'lease expired during the final attempt',
       updated_at = now()
WHERE (%(review_id)s::int IS NULL OR review_id = %(review_id)s)
  AND status = 'running' AND attempts >= %(max_attempts)s AND lease_expires_at < now()
"""


def page_of(pos: int, pages: list[dict]) -> int | None:
    """Page number containing char offset `pos`; `pages` are rows ordered by char_start."""
    page = None
    for p in pages:
        if p["char_start"] > pos:
            break
        page = p["page_no"]
    return page


def verify(quote: str, text: str, pages: list[dict]) -> dict:
    """Locate a quote in the stored document text and map it to a page.

    A short quote counts only if it occurs exactly once: "| pH | 4.5-5.5 |" from a table pins
    one place in the document, while "25°C" appears all over it and proves nothing.
    """
    hit = locate(quote, text)
    q = normalize(quote)
    if hit and len(q) < MIN_QUOTE_CHARS and text.count(q) != 1:
        hit = None
    if hit is None:
        return {"quote": quote, "char_start": None, "char_end": None, "page_no": None,
                "verified": False, "match_method": "none"}
    start, end, method = hit
    return {"quote": quote, "char_start": start, "char_end": end,
            "page_no": page_of(start, pages), "verified": True, "match_method": method}


def quoted(claim: dict) -> bool:
    """The claim has quotes and every one was found in the document."""
    return bool(claim["citations"]) and all(q["verified"] for q in claim["citations"])


def decide_status(found: bool, answer, claims: list[dict]) -> str:
    """`claims`: [{"claim": str, "citations": [verify() results], "check": {"ok", "reason"} or
    None}]. An answer is done only when every claim's quotes were found in the document and the
    check says they support its key fact, and a list answer has a claim for each item. Anything
    else goes to a human."""
    if not found:
        return "not_found"
    supported = bool(claims) and all(
        quoted(c) and (c.get("check") or {}).get("ok") is True for c in claims)
    covered = not isinstance(answer, list) or len(claims) >= len(answer)
    return "done" if supported and covered else "needs_review"


async def fetchone(conn, sql, params):
    return await (await conn.execute(sql, params)).fetchone()


async def fetchall(conn, sql, params):
    return await (await conn.execute(sql, params)).fetchall()


async def process(conn, cell: dict, worker: str, sem: asyncio.Semaphore) -> str:
    """Answer, verify and store one claimed cell. Returns the cell's new status."""
    doc = {"id": cell["doc_id"], "version": cell["version"]}
    try:
        column = Column(**await fetchone(
            conn, "SELECT key, question, answer_type, instructions, model_tier"
                  " FROM columns WHERE id = %s", (cell["column_id"],)))
        text = (await fetchone(conn, "SELECT text FROM documents"
                                     " WHERE id = %(id)s AND version = %(version)s", doc))["text"]
        pages = await fetchall(conn, "SELECT page_no, char_start FROM pages WHERE doc_id = %(id)s"
                                     " AND version = %(version)s ORDER BY char_start", doc)

        async with sem:  # caps concurrent model calls across all worker loops in this process
            result = await answer_cell(text, column)

        claims = [{"claim": c.claim, "citations": [verify(q, text, pages) for q in c.quotes],
                   "check": None} for c in result.claims]
        # Check the claims whose quotes were all found: do those quotes support the key fact?
        # (A claim with a missing quote goes to review anyway.) The call's tokens and time are
        # added to the cell's, so the cost totals include it.
        checkable = [c for c in claims if quoted(c)]
        if checkable:
            async with sem:
                verdicts, usage = await check_claims(
                    [(c["claim"], [q["quote"] for q in c["citations"]]) for c in checkable])
            for c, v in zip(checkable, verdicts):
                c["check"] = v and {"ok": v.ok, "reason": v.reason}
            result.input_tokens += usage["input_tokens"]
            result.output_tokens += usage["output_tokens"]
            result.latency_ms += usage["latency_ms"]
        status = decide_status(result.found, result.answer, claims)

        async with conn.transaction():
            written = await conn.execute(WRITE_RESULT, {
                **result.model_dump(exclude={"found", "claims"}),
                "answer": None if result.answer is None else Jsonb(result.answer),
                "claims": Jsonb([c["claim"] for c in claims]),
                "checks": Jsonb([c["check"] for c in claims]),
                "status": status,
                "id": cell["id"], "worker": worker})
            if written.rowcount == 0:
                # Another worker owns the cell now, or its review was deleted: drop this result.
                return "lease lost"
            await conn.execute("DELETE FROM citations WHERE cell_id = %s", (cell["id"],))
            async with conn.cursor() as cur:
                await cur.executemany(INSERT_CITATION, [
                    {**q, "claim_no": n, "cell_id": cell["id"], "doc_id": doc["id"],
                     "version": doc["version"]}
                    for n, c in enumerate(claims, 1) for q in c["citations"]])
        return status

    except Exception as e:  # noqa: BLE001 - any failure is recorded on the cell and retried
        error = f"{type(e).__name__}: {e}"[:1000]
        if is_throttled(e):  # still throttled after create()'s own retries
            await conn.execute(REQUEUE_THROTTLED, {"id": cell["id"], "worker": worker,
                                                   "error": error})
            await asyncio.sleep(backoff_s(e, attempt=4))  # pause this loop before it claims again
            return "throttled"
        print(f"cell {cell['id']} attempt {cell['attempts']} failed: {error}", flush=True)
        await conn.execute(RECORD_FAILURE, {"id": cell["id"], "worker": worker,
                                            "error": error, "max_attempts": MAX_ATTEMPTS})
        return "error"


async def remaining(conn, review_id: int | None) -> int:
    """Cells still to finish (in one review, or all with None)."""
    row = await fetchone(conn, "SELECT count(*) AS n FROM cells"
                               " WHERE (%(review_id)s::int IS NULL OR review_id = %(review_id)s)"
                               " AND status IN ('pending', 'running')", {"review_id": review_id})
    return row["n"]


async def work(url: str, review_id: int | None, worker: str, sem: asyncio.Semaphore,
               watch: bool = False) -> None:
    """Claim and process cells of one review (or of all reviews with None) until none are
    pending or running. With watch=True, keep polling for new cells and never return."""
    claim = {"review_id": review_id, "worker": worker, "max_attempts": MAX_ATTEMPTS,
             "lease_s": int(os.environ.get("LEASE_SECONDS", 30))}
    # Named in pg_stat_activity, so a script can see whether workers are connected.
    async with await psycopg.AsyncConnection.connect(
            url, autocommit=True, row_factory=dict_row,
            application_name=f"{APP_NAME} {worker}"[:63]) as conn:
        next_sweep = 0.0
        while True:
            if time.monotonic() >= next_sweep:
                await conn.execute(SWEEP_DEAD, claim)
                next_sweep = time.monotonic() + claim["lease_s"]
            cell = await fetchone(conn, CLAIM, claim)
            if cell is not None:
                await process(conn, cell, worker, sem)
            elif await remaining(conn, review_id) == 0 and not watch:
                return
            else:  # nothing claimable yet: new cells, or other workers' leases may expire
                await asyncio.sleep(WATCH_POLL_S if watch else IDLE_POLL_S)
