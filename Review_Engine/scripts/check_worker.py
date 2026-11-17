"""Check the worker's queue rules with a fake model: no API calls, no cost.

1. Throttling: a 429 with retry-after puts the cell back in the queue without using an attempt.
2. Spend cap: its 429 cannot be retried away, so cells fail normally after their attempts.
3. Document order: one cell per document at a time, in column order, while several documents
   run in parallel.

Each check runs real worker loops against a temporary review in the local database, which is
deleted afterwards. Stop other workers first: a --watch worker would answer these cells with
the real model.
Usage: python scripts/check_worker.py
"""

import asyncio
import sys
from collections import defaultdict
from pathlib import Path

import anthropic
import httpx2
import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import reviews, worker  # noqa: E402
from app.db import database_url  # noqa: E402
from app.llm import SPEND_CAP  # noqa: E402
from app.models import CellResult  # noqa: E402

URL = database_url()
DOCS = ["STAB-001", "STAB-002", "STAB-003"]  # 3 documents x 6 questions = 18 cells
LOOPS = 4
COLUMNS = [c.key for c in reviews.load_template("stability_protocol").columns]


def rate_limit_error(retry_after: str | None = None, error_code: str | None = None):
    error = {"type": "rate_limit_error", "message": "fake"}
    if error_code:
        error["details"] = {"error_code": error_code}
    response = httpx2.Response(
        429, headers={"retry-after": retry_after} if retry_after else {},
        request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages"))
    return anthropic.RateLimitError("fake", response=response, body={"type": "error", "error": error})


def fake_result(doc_text: str) -> CellResult:
    return CellResult(found=True, answer="fake answer", citations=[doc_text[200:300]],
                      reasoning="fake", model="fake", input_tokens=0, output_tokens=0,
                      cache_read_tokens=0, cache_write_tokens=0, latency_ms=0)


async def run(name: str, docs: list[str], fake) -> list[dict]:
    """Answer a temporary review's cells with `fake` as the model; return its cells."""
    with psycopg.connect(URL) as conn:
        review_id = reviews.create_review(conn, f"check_worker: {name}", "stability_protocol",
                                          {"id": docs})["id"]
    worker.answer_cell = fake
    try:
        sem = asyncio.Semaphore(LOOPS)
        await asyncio.gather(*(worker.work(URL, review_id, f"check:{i}", sem)
                               for i in range(LOOPS)))
        with psycopg.connect(URL, row_factory=psycopg.rows.dict_row) as conn:
            return conn.execute("SELECT status, attempts, last_error FROM cells"
                                " WHERE review_id = %s", (review_id,)).fetchall()
    finally:
        with psycopg.connect(URL) as conn:
            reviews.delete_review(conn, review_id)


def check(label: str, ok: bool) -> bool:
    print(f"{'PASS' if ok else 'FAIL'}  {label}")
    return ok


async def main() -> int:
    results = []

    # 1. The first 6 calls are throttled; each cell must still finish on one counted attempt.
    throttles = {"left": 6}

    async def throttled_then_ok(doc_text, column):
        if throttles["left"]:
            throttles["left"] -= 1
            raise rate_limit_error(retry_after="0")
        return fake_result(doc_text)

    cells = await run("throttling", DOCS, throttled_then_ok)
    results.append(check(
        "throttling: 6 throttled calls, all 18 cells done, every cell on attempt 1",
        throttles["left"] == 0 and all(c["status"] == "done" and c["attempts"] == 1
                                       for c in cells) and len(cells) == 18))

    # 2. The spend cap never clears this month: cells fail after their attempts, with the error.
    async def spend_cap(doc_text, column):
        raise rate_limit_error(error_code=SPEND_CAP)

    cells = await run("spend cap", DOCS[:1], spend_cap)
    results.append(check(
        f"spend cap: all 6 cells failed after {worker.MAX_ATTEMPTS} attempts with the error kept",
        all(c["status"] == "failed" and c["attempts"] == worker.MAX_ATTEMPTS
            and "RateLimitError" in (c["last_error"] or "") for c in cells) and len(cells) == 6))

    # 3. Record calls per document (keyed by its text) and how many run at once.
    in_flight, peak, order = defaultdict(int), defaultdict(int), defaultdict(list)
    overall = {"now": 0, "peak": 0}

    async def recording(doc_text, column):
        in_flight[doc_text] += 1
        peak[doc_text] = max(peak[doc_text], in_flight[doc_text])
        overall["now"] += 1
        overall["peak"] = max(overall["peak"], overall["now"])
        order[doc_text].append(column.key)
        await asyncio.sleep(0.05)
        in_flight[doc_text] -= 1
        overall["now"] -= 1
        return fake_result(doc_text)

    await run("document order", DOCS, recording)
    results.append(check("order: never two calls in flight for one document",
                         len(peak) == 3 and all(p == 1 for p in peak.values())))
    results.append(check("order: each document's questions ran in column order",
                         all(keys == COLUMNS for keys in order.values())))
    results.append(check(f"order: documents ran in parallel (peak {overall['peak']} calls at once)",
                         overall["peak"] >= 2))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
