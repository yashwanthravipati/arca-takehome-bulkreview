"""Run worker loops for one review until no cell is pending or running.

Each of N async worker loops has its own database connection; all of them share an
asyncio.Semaphore that caps concurrent model calls. Safe to run as several processes at
once: cells are claimed with FOR UPDATE SKIP LOCKED. After a crash or kill, just run it
again: finished cells are never redone, and cells the dead process had claimed are picked up
when their lease (LEASE_SECONDS, default 30) expires.

Usage: python scripts/run_workers.py --review 1 [--concurrency 8]
"""

import argparse
import asyncio
import os
import socket
import sys
import time
from pathlib import Path

import psycopg
from dotenv import find_dotenv, load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.llm import cost_usd  # noqa: E402
from app.worker import work  # noqa: E402

STATUSES = ("done", "not_found", "needs_review", "failed", "running", "pending")
REPORT_EVERY_S = 5
COUNTS ="SELECT status, count(*) FROM cells WHERE review_id = %s GROUP BY status"


def with_zeros(rows) -> dict:
    return {**dict.fromkeys(STATUSES, 0), **dict(rows)}


def progress_line(counts, started) -> str:
    return f"[{time.monotonic() - started:5.0f}s] " + " | ".join(
        f"{s} {counts[s]}" for s in STATUSES)


async def report(url, review_id, started, stop: asyncio.Event):
    async with await psycopg.AsyncConnection.connect(url, autocommit=True) as conn:
        while not stop.is_set():
            rows = await (await conn.execute(COUNTS, (review_id,))).fetchall()
            print(progress_line(with_zeros(rows), started), flush=True)
            try:
                await asyncio.wait_for(stop.wait(), REPORT_EVERY_S)
            except TimeoutError:
                pass


async def run(url, review_id, concurrency):
    started = time.monotonic()
    sem = asyncio.Semaphore(concurrency)
    prefix = f"{socket.gethostname()}:{os.getpid()}"
    stop = asyncio.Event()
    reporter = asyncio.create_task(report(url, review_id, started, stop))
    try:
        await asyncio.gather(*(work(url, review_id, f"{prefix}:{i}", sem)
                               for i in range(concurrency)))
    finally:
        stop.set()
        await reporter
    return time.monotonic() - started


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--review", type=int, required=True)
    ap.add_argument("--concurrency", type=int, default=int(os.environ.get("WORKER_CONCURRENCY", 8)))
    args = ap.parse_args()

    load_dotenv(find_dotenv(usecwd=True))
    url = os.environ.get("DATABASE_URL", "postgresql://postgres@127.0.0.1:5433/bulkreview")
    with psycopg.connect(url) as conn:
        if not conn.execute("SELECT 1 FROM reviews WHERE id = %s", (args.review,)).fetchone():
            sys.exit(f"no review {args.review}; create one with scripts/create_review.py")

    print(f"review {args.review}: {args.concurrency} workers")
    elapsed = asyncio.run(run(url, args.review, args.concurrency))

    with psycopg.connect(url) as conn:
        counts = with_zeros(conn.execute(COUNTS, (args.review,)).fetchall())
        print(progress_line(counts, time.monotonic() - elapsed))
        usage = conn.execute(
            "SELECT model, count(*), sum(input_tokens), sum(output_tokens),"
            " sum(cache_read_tokens), sum(cache_write_tokens)"
            " FROM cells WHERE review_id = %s AND model IS NOT NULL GROUP BY model ORDER BY model",
            (args.review,)).fetchall()

    print(f"\nwall-clock (this run): {elapsed:.1f}s")
    print("tokens (whole review):")
    total_cost = 0.0
    for model, n, inp, out, read, write in usage:
        cost = cost_usd(model, inp, out, read, write)
        total_cost += cost or 0
        print(f"  {model}: {n} cells | input {inp:,} uncached + {write:,} cache write + "
              f"{read:,} cache read | output {out:,} | "
              f"{'cost n/a' if cost is None else f'est. ${cost:.4f}'}")
    print(f"estimated cost: ${total_cost:.4f}")


if __name__ == "__main__":
    main()
