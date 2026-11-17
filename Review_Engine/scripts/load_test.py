"""Load test the queue: 100,000 cells answered by a fake model. No API calls, no cost.

Inserts 25,000 short synthetic documents and a review over them with the ha_letter template's
4 questions (100,000 cells), then runs worker processes on it with the model and the support
check faked, so what is measured is the queue itself: claims, writes, retries and ordering.
Along the way 1% of cells fail their first attempt, 1% are throttled, and halfway through one
worker process is killed and replaced, leaving its claimed cells to expire and be picked up.

Checks at the end: every cell done exactly once with its verified citation; every attempt
accounted for (failures retried, throttling never uses one); never two cells of one document
running at once; each document's questions answered in column order. Prints throughput and
claim time for each tenth of the run. The review and documents are deleted afterwards.

Stop other workers first: a --watch worker would answer these cells with the real model. The
test refuses to start while one is connected.
Usage: python scripts/load_test.py [--docs 25000] [--processes 4] [--loops 16] [--json out.json]
"""

import argparse
import asyncio
import contextvars
import json
import multiprocessing
import os
import statistics
import sys
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import reviews, worker  # noqa: E402
from app.db import database_url  # noqa: E402
from check_worker import fake_check, fake_result, rate_limit_error  # noqa: E402

URL = database_url()
NAME = "load test"
TEMPLATE = "ha_letter"  # 4 questions per document
PREFIX = "loadtest"     # worker names: loadtest:<process>:<loop>
LEASE_S = 5             # short leases, so the killed process's cells come back quickly
FAIL_EVERY = 100        # cells with id % 100 == 0 fail their first attempt
THROTTLE_AT = 50        # cells with id % 100 == 50 are throttled (once in each process)
REPORT_EVERY_S = 5

DOCUMENTS = """
INSERT INTO documents (id, version, title, product, doc_type, is_current, content_hash, text)
SELECT 'LOAD-' || lpad(i::text, 6, '0'), '1.0', 'Load test document ' || i, 'Load test',
       'load_test', true, md5(i::text),
       (SELECT string_agg('Section ' || j || ' of load test document ' || i
                          || ' records value ' || md5(i || ':' || j) || '.', ' ')
        FROM generate_series(1, 20) j)
FROM generate_series(1, %(n)s) i
"""
PAGES = """
INSERT INTO pages (doc_id, version, page_no, char_start, char_end)
SELECT id, version, p, CASE p WHEN 1 THEN 0 ELSE length(text) / 2 END,
       CASE p WHEN 1 THEN length(text) / 2 ELSE length(text) END
FROM documents CROSS JOIN generate_series(1, 2) p WHERE doc_type = 'load_test'
"""
OTHER_WORKERS = """
SELECT count(*) FROM pg_stat_activity
WHERE application_name LIKE %(app)s AND application_name NOT LIKE %(ours)s
"""
STATUS_COUNTS = "SELECT status, count(*) FROM cells WHERE review_id = %s GROUP BY status"
# Documents with two cells running at once: the claim rule says this never happens.
DOUBLE_RUNNING = """
SELECT count(*) FROM (SELECT doc_id FROM cells WHERE review_id = %s AND status = 'running'
                      GROUP BY doc_id HAVING count(*) > 1) d
"""
FINAL = """
SELECT c.id, c.status, c.attempts, c.model,
       (SELECT count(*) FROM citations t WHERE t.cell_id = c.id AND t.verified) AS cited
FROM cells c WHERE c.review_id = %s
"""

# The cell a worker loop is answering and how long its claim took (each loop is its own task,
# so each sees its own values).
in_hand = contextvars.ContextVar("in_hand")
claim_s = contextvars.ContextVar("claim_s", default=0.0)


def child(n: int, review_id: int, loops: int, log_path: str) -> None:
    """One worker process: `loops` worker loops with the model faked. Appends a line per
    attempt to `log_path`: cell, document, worker, claim time, start, end, outcome."""
    sys.stdout = open(os.devnull, "w")  # the worker prints every failed attempt
    os.environ["LEASE_SECONDS"] = str(LEASE_S)
    log = open(log_path, "a", buffering=1)  # line-buffered: a kill loses at most one line
    throttled = set()
    real_fetchone, real_process = worker.fetchone, worker.process

    async def timed_fetchone(conn, sql, params):
        if sql is not worker.CLAIM:
            return await real_fetchone(conn, sql, params)
        start = time.time()
        row = await real_fetchone(conn, sql, params)
        claim_s.set(time.time() - start)
        return row

    async def logged_process(conn, cell, name, sem):
        in_hand.set(cell)
        start = time.time()
        outcome = await real_process(conn, cell, name, sem)
        log.write(f"{cell['id']} {cell['doc_id']} {name} {claim_s.get():.6f} {start:.6f} "
                  f"{time.time():.6f} {outcome.replace(' ', '_')}\n")
        return outcome

    async def fake_answer(text, column):
        cell = in_hand.get()
        if cell["id"] % FAIL_EVERY == 0 and cell["attempts"] == 1:
            raise RuntimeError("injected failure")
        if cell["id"] % FAIL_EVERY == THROTTLE_AT and cell["id"] not in throttled:
            throttled.add(cell["id"])
            raise rate_limit_error(retry_after="0")
        return fake_result(text)

    worker.fetchone, worker.process = timed_fetchone, logged_process
    worker.answer_cell, worker.check_claims = fake_answer, fake_check

    async def run():
        sem = asyncio.Semaphore(loops)
        await asyncio.gather(*(worker.work(URL, review_id, f"{PREFIX}:{n}:{i}", sem)
                               for i in range(loops)))
    asyncio.run(run())


def cleanup(conn) -> None:
    for (review_id,) in conn.execute("SELECT id FROM reviews WHERE name = %s", (NAME,)):
        reviews.delete_review(conn, review_id)
    conn.execute("DELETE FROM pages p USING documents d WHERE p.doc_id = d.id"
                 " AND p.version = d.version AND d.doc_type = 'load_test'")
    conn.execute("DELETE FROM documents WHERE doc_type = 'load_test'")


def setup(conn, n_docs: int) -> tuple[int, int]:
    """Insert the documents and the review; returns (review id, cells)."""
    conn.execute(DOCUMENTS, {"n": n_docs})
    conn.execute(PAGES)
    review = reviews.create_review(conn, NAME, TEMPLATE, {"doc_type": "load_test"})
    conn.execute("ANALYZE documents; ANALYZE pages; ANALYZE cells")
    return review["id"], review["new_cells"]


def start(n: int, review_id: int, loops: int, logs: Path):
    p = multiprocessing.get_context("spawn").Process(
        target=child, args=(n, review_id, loops, str(logs / f"{n}.log")))
    p.start()
    return p


def drive(conn, review_id: int, total: int, procs: list, loops: int, logs: Path) -> dict:
    """Watch the run until the workers exit. Halfway, kill process 0 and start a replacement.
    Returns what was observed: progress samples, the kill, documents ever seen double-running."""
    began = time.time()
    seen = {"samples": [], "orphans": [], "killed_at": None, "double": 0, "snapshots": 0}
    last_report = 0.0
    while any(p.is_alive() for p in procs):
        counts = dict(conn.execute(STATUS_COUNTS, (review_id,)).fetchall())
        done = counts.get("done", 0)
        seen["double"] += conn.execute(DOUBLE_RUNNING, (review_id,)).fetchone()[0]
        seen["snapshots"] += 1
        now = time.time() - began
        seen["samples"].append((now, done))
        if seen["killed_at"] is None and done >= total // 2:
            procs[0].kill()
            procs[0].join()
            seen["killed_at"] = now
            seen["orphans"] = [r[0] for r in conn.execute(
                "SELECT id FROM cells WHERE review_id = %s AND status = 'running'"
                " AND claimed_by LIKE %s", (review_id, f"{PREFIX}:0:%"))]
            procs.append(start(len(procs), review_id, loops, logs))
            print(f"[{now:5.1f}s] killed process 0 with {len(seen['orphans'])} cells claimed;"
                  f" started process {len(procs) - 1}", flush=True)
        if now - last_report >= REPORT_EVERY_S:
            print(f"[{now:5.1f}s] {done:,} of {total:,} done", flush=True)
            last_report = now
        time.sleep(0.5)
    seen["elapsed"] = time.time() - began
    return seen


def read_logs(logs: Path) -> list[dict]:
    rows = []
    for path in sorted(logs.glob("*.log")):
        for line in path.read_text().splitlines():
            parts = line.split()
            if len(parts) == 7:  # a killed process may leave half a line
                rows.append({"cell": int(parts[0]), "doc": parts[1], "worker": parts[2],
                             "claim_s": float(parts[3]), "start": float(parts[4]),
                             "end": float(parts[5]), "outcome": parts[6]})
    return rows


def tenths(done: list[dict]) -> list[dict]:
    """Throughput and claim time for each tenth of the finished cells, in finishing order,
    timed from the first answer (process start-up is left out)."""
    out, prev_end = [], min(r["start"] for r in done)
    for i in range(10):
        part = done[len(done) * i // 10: len(done) * (i + 1) // 10]
        claims = sorted(r["claim_s"] * 1000 for r in part)
        out.append({"cells_per_s": len(part) / (part[-1]["end"] - prev_end),
                    "claim_ms_p50": statistics.median(claims),
                    "claim_ms_p99": claims[int(len(claims) * 0.99)]})
        prev_end = part[-1]["end"]
    return out


def check(label: str, ok: bool) -> bool:
    print(f"{'PASS' if ok else 'FAIL'}  {label}")
    return ok


def evaluate(conn, review_id: int, total: int, rows: list[dict], seen: dict) -> list[bool]:
    final = conn.execute(FINAL, (review_id,)).fetchall()
    orphans = set(seen["orphans"])
    by_cell = defaultdict(Counter)
    for r in rows:
        by_cell[r["cell"]][r["outcome"]] += 1
    throttles = sum(c["throttled"] for c in by_cell.values())
    failures = sum(c["error"] for c in by_cell.values())

    # Each document's successful attempts, in the order they finished, are in column order.
    finished = defaultdict(list)
    for r in sorted((r for r in rows if r["outcome"] == "done"), key=lambda r: r["end"]):
        finished[r["doc"]].append(r["cell"])

    return [
        check(f"all {total:,} cells done, each with one verified citation, none by a real model",
              len(final) == total and all(
                  s == "done" and cited == 1 and model == "fake"
                  for _, s, _, model, cited in final)),
        check("no cell written twice, no stale write",
              all(c["done"] <= 1 for c in by_cell.values())
              and not any(c["lease_lost"] for c in by_cell.values())),
        check(f"every attempt accounted for: {failures:,} failed attempts retried,"
              f" {throttles:,} throttled calls used none,"
              f" {len(orphans)} cells of the killed process retried",
              all(attempts == 1 + by_cell[cid]["error"] + (cid in orphans)
                  for cid, _, attempts, _, _ in final)),
        check(f"never two cells of one document running at once ({seen['snapshots']:,} snapshots)",
              seen["double"] == 0),
        check("each document's questions finished in column order",
              all(cells == sorted(cells) for cells in finished.values())),
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--docs", type=int, default=25_000, help="documents (4 cells each)")
    ap.add_argument("--processes", type=int, default=4, help="worker processes")
    ap.add_argument("--loops", type=int, default=16, help="worker loops per process")
    ap.add_argument("--json", type=Path, help="also write the results here")
    args = ap.parse_args()

    with psycopg.connect(URL, autocommit=True) as conn:
        if conn.execute(OTHER_WORKERS, {"app": f"{worker.APP_NAME} %",
                                        "ours": f"{worker.APP_NAME} {PREFIX}:%"}).fetchone()[0]:
            sys.exit("a worker is connected to this database; stop it first"
                     " (it would answer the test's cells with the real model)")
        cleanup(conn)
        t = time.time()
        review_id, total = setup(conn, args.docs)
        print(f"review {review_id}: {args.docs:,} documents x 4 questions = {total:,} cells"
              f" (set up in {time.time() - t:.1f}s); {args.processes} processes"
              f" x {args.loops} worker loops, {LEASE_S}s leases", flush=True)

        logs = Path(tempfile.mkdtemp(prefix="load_test_"))
        procs = []
        try:
            procs = [start(n, review_id, args.loops, logs) for n in range(args.processes)]
            seen = drive(conn, review_id, total, procs, args.loops, logs)
            rows = read_logs(logs)
            done = sorted((r for r in rows if r["outcome"] == "done"), key=lambda r: r["end"])
            parts = tenths(done)
            print(f"\n{total:,} cells in {seen['elapsed']:.1f}s:"
                  f" {total / seen['elapsed']:,.0f} cells/s overall\n")
            print("tenth   cells/s   claim p50   claim p99")
            for i, p in enumerate(parts, 1):
                print(f"{i:>5}   {p['cells_per_s']:7,.0f}   {p['claim_ms_p50']:6.1f} ms"
                      f"   {p['claim_ms_p99']:6.1f} ms")
            print()
            results = evaluate(conn, review_id, total, rows, seen)
            if args.json:
                args.json.write_text(json.dumps({
                    "docs": args.docs, "cells": total, "processes": args.processes,
                    "loops": args.loops, "elapsed_s": seen["elapsed"],
                    "killed_at_s": seen["killed_at"], "orphans": len(seen["orphans"]),
                    "tenths": parts, "samples": seen["samples"], "passed": all(results)},
                    indent=1))
            return 0 if all(results) else 1
        finally:
            for p in procs:
                if p.is_alive():
                    p.kill()
            cleanup(conn)


if __name__ == "__main__":
    sys.exit(main())
