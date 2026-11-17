"""Cell worker: claim a cell with a lease, answer it, verify its citations, write the result.

The cells table is the queue. A claim is one atomic UPDATE ... FOR UPDATE SKIP LOCKED, so any
number of worker loops and processes can share a review without taking the same cell. A claim
is a lease of LEASE_SECONDS (default 30; a call takes 2-6 s): if a worker dies, its cell becomes
claimable again once the lease expires. A cell gets at most MAX_ATTEMPTS attempts before it is
marked failed.
"""

import asyncio
import os

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.llm import answer_cell
from app.models import Column
from app.text import locate, normalize

MAX_ATTEMPTS = 3
MIN_QUOTE_CHARS = 20  # shorter quotes must be exact and unique in the document (see verify)
IDLE_POLL_S = 2

CLAIM = """
UPDATE cells SET status = 'running', attempts = attempts + 1, claimed_by = %(worker)s,
       lease_expires_at = now() + make_interval(secs => %(lease_s)s), updated_at = now()
WHERE id = (
  SELECT id FROM cells
  WHERE review_id = %(review_id)s
    AND attempts < %(max_attempts)s
    AND (status = 'pending'
         OR (status = 'running' AND lease_expires_at < now()))
  ORDER BY id
  FOR UPDATE SKIP LOCKED
  LIMIT 1)
RETURNING *
"""

# Guarded by status and claimed_by: if this worker's lease expired and another worker
# re-claimed the cell, the stale result is dropped instead of overwriting the new attempt.
WRITE_RESULT = """
UPDATE cells SET status = %(status)s, answer = %(answer)s, reasoning = %(reasoning)s,
       model = %(model)s, input_tokens = %(input_tokens)s, output_tokens = %(output_tokens)s,
       cache_read_tokens = %(cache_read_tokens)s, cache_write_tokens = %(cache_write_tokens)s,
       latency_ms = %(latency_ms)s, lease_expires_at = NULL, updated_at = now()
WHERE id = %(id)s AND status = 'running' AND claimed_by = %(worker)s
"""

INSERT_CITATION = """
INSERT INTO citations (cell_id, doc_id, version, quote, char_start, char_end, page_no,
                       verified, match_method)
VALUES (%(cell_id)s, %(doc_id)s, %(version)s, %(quote)s, %(char_start)s, %(char_end)s,
        %(page_no)s, %(verified)s, %(match_method)s)
"""

RECORD_FAILURE = """
UPDATE cells SET status = CASE WHEN attempts >= %(max_attempts)s THEN 'failed' ELSE 'pending' END,
       last_error = %(error)s, lease_expires_at = NULL, updated_at = now()
WHERE id = %(id)s AND status = 'running' AND claimed_by = %(worker)s
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


def decide_status(found: bool, citations: list[dict]) -> str:
    if not found:
        return "not_found"
    if citations and all(c["verified"] for c in citations):
        return "done"
    return "needs_review"  # an answer without checkable evidence goes to a human


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

        citations = [verify(q, text, pages) for q in result.citations]
        status = decide_status(result.found, citations)

        async with conn.transaction():
            written = await conn.execute(WRITE_RESULT, {
                **result.model_dump(exclude={"found", "citations"}),
                "answer": None if result.answer is None else Jsonb(result.answer),
                "status": status,
                "id": cell["id"], "worker": worker})
            if written.rowcount == 0:
                return "lease lost"  # another worker owns the cell now; drop this result
            await conn.execute("DELETE FROM citations WHERE cell_id = %s", (cell["id"],))
            async with conn.cursor() as cur:
                await cur.executemany(INSERT_CITATION, [
                    {**c, "cell_id": cell["id"], "doc_id": doc["id"], "version": doc["version"]}
                    for c in citations])
        return status

    except Exception as e:  # noqa: BLE001 - any failure is recorded on the cell and retried
        error = f"{type(e).__name__}: {e}"[:1000]
        print(f"cell {cell['id']} attempt {cell['attempts']} failed: {error}", flush=True)
        await conn.execute(RECORD_FAILURE, {"id": cell["id"], "worker": worker,
                                            "error": error, "max_attempts": MAX_ATTEMPTS})
        return "error"


async def remaining(conn, review_id: int) -> int:
    """Cells still to finish. Fails cells whose final attempt died with the lease held."""
    await conn.execute(
        "UPDATE cells SET status = 'failed', last_error = 'lease expired during the final attempt',"
        " updated_at = now() WHERE review_id = %s AND status = 'running' AND attempts >= %s"
        " AND lease_expires_at < now()", (review_id, MAX_ATTEMPTS))
    row = await fetchone(conn, "SELECT count(*) AS n FROM cells WHERE review_id = %s"
                               " AND status IN ('pending', 'running')", (review_id,))
    return row["n"]


async def work(url: str, review_id: int, worker: str, sem: asyncio.Semaphore) -> None:
    """Claim and process cells until none are pending or running."""
    claim = {"review_id": review_id, "worker": worker, "max_attempts": MAX_ATTEMPTS,
             "lease_s": int(os.environ.get("LEASE_SECONDS", 30))}
    async with await psycopg.AsyncConnection.connect(
            url, autocommit=True, row_factory=dict_row) as conn:
        while True:
            cell = await fetchone(conn, CLAIM, claim)
            if cell is not None:
                await process(conn, cell, worker, sem)
            elif await remaining(conn, review_id) == 0:
                return
            else:  # other workers hold live leases; one may expire and become claimable
                await asyncio.sleep(IDLE_POLL_S)
