"""Run worker loops for one review until no cell is pending or running, or with --watch serve
every review forever (the long-lived worker behind the API).

Each of N async worker loops has its own database connection; all of them share an
asyncio.Semaphore that caps concurrent model calls. Safe to run as several processes at
once: cells are claimed with FOR UPDATE SKIP LOCKED. After a crash or kill, just run it
again: finished cells are never redone, and cells the dead process had claimed are picked up
when their lease (LEASE_SECONDS, default 30) expires.

Usage: python scripts/run_workers.py --review 1 [--concurrency 8]
       python scripts/run_workers.py --watch [--concurrency 8]
"""

import argparse
import asyncio
import os
import socket
import sys
import time
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.db import database_url  # noqa: E402
from app.reviews import STATUSES, totals  # noqa: E402
from app.worker import work  # noqa: E402

REPORT_EVERY_S = 5
COUNTS = """SELECT status, count(*) FROM cells
            WHERE (%(review_id)s::int IS NULL OR review_id = %(review_id)s) GROUP BY status"""


def progress_line(rows, started) -> str:
    counts = {**dict.fromkeys(STATUSES, 0), **dict(rows)}
    return f"[{time.monotonic() - started:5.0f}s] " + " | ".join(
        f"{s} {counts[s]}" for s in STATUSES)


async def report(url, review_id, started, stop: asyncio.Event):
    """Print status counts every few seconds, when they changed."""
    last = None
    async with await psycopg.AsyncConnection.connect(url, autocommit=True) as conn:
        while not stop.is_set():
            rows = sorted(await (await conn.execute(COUNTS, {"review_id": review_id})).fetchall())
            if rows != last:
                print(progress_line(rows, started), flush=True)
                last = rows
            try:
                await asyncio.wait_for(stop.wait(), REPORT_EVERY_S)
            except TimeoutError:
                pass


async def run(url, review_id, concurrency, watch):
    started = time.monotonic()
    sem = asyncio.Semaphore(concurrency)
    prefix = f"{socket.gethostname()}:{os.getpid()}"
    stop = asyncio.Event()
    reporter = asyncio.create_task(report(url, review_id, started, stop))
    try:
        await asyncio.gather(*(work(url, review_id, f"{prefix}:{i}", sem, watch)
                               for i in range(concurrency)))
    finally:
        stop.set()
        await reporter
    return time.monotonic() - started


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--review", type=int, help="work on this review and exit when it is done")
    ap.add_argument("--watch", action="store_true", help="serve every review; never exit")
    ap.add_argument("--concurrency", type=int, default=int(os.environ.get("WORKER_CONCURRENCY", 8)))
    args = ap.parse_args()
    if bool(args.review) == args.watch:
        ap.error("give exactly one of --review N and --watch")

    url = database_url()
    if args.watch:
        print(f"watching all reviews with {args.concurrency} workers (Ctrl-C to stop)", flush=True)
        try:
            asyncio.run(run(url, None, args.concurrency, watch=True))
        except KeyboardInterrupt:
            print("stopped; cells in flight are picked up again when their leases expire")
        return

    with psycopg.connect(url) as conn:
        if not conn.execute("SELECT 1 FROM reviews WHERE id = %s", (args.review,)).fetchone():
            sys.exit(f"no review {args.review}; create one with scripts/create_review.py")
    print(f"review {args.review}: {args.concurrency} workers")
    elapsed = asyncio.run(run(url, args.review, args.concurrency, watch=False))

    with psycopg.connect(url) as conn:
        t = totals(conn, args.review)
    print(f"\nwall-clock (this run): {elapsed:.1f}s")
    print("tokens (whole review):")
    for model, m in t["by_model"].items():
        cost = "cost n/a" if m["cost_usd"] is None else f"est. ${m['cost_usd']:.4f}"
        print(f"  {model}: {m['cells']} cells | input {m['input_tokens']:,} uncached + "
              f"{m['cache_write_tokens']:,} cache write + {m['cache_read_tokens']:,} cache read"
              f" | output {m['output_tokens']:,} | {cost}")
    print(f"estimated cost: ${t['cost_usd']:.4f}")


if __name__ == "__main__":
    main()
