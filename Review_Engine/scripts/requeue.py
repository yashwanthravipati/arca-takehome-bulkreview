"""Put cells of a review back in the queue, so the workers answer them again.

  --column KEY  after editing a column in the template: refreshes the column's question,
                instructions, answer type and tier from templates/<template>.json, then
                re-queues all of its cells
  --status S    re-queue cells with this status, e.g. needs_review after changing how
                citations are verified
  --cell ID     re-queue specific cells
Re-queued cells lose their answer and citations and start again at attempt 0. Cells that
are running are left alone. Then run: python scripts/run_workers.py --review N

Usage: python scripts/requeue.py --review 1 --column deviations --status needs_review
"""

import argparse
import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.db import database_url  # noqa: E402
from app.reviews import ReviewError, requeue  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--review", type=int, required=True)
    ap.add_argument("--column", action="append", default=[], metavar="KEY")
    ap.add_argument("--status", action="append", default=[])
    ap.add_argument("--cell", action="append", default=[], type=int, metavar="ID")
    args = ap.parse_args()
    if not (args.column or args.status or args.cell):
        ap.error("give at least one of --column, --status, --cell")

    try:
        with psycopg.connect(database_url()) as conn:  # one transaction
            ids = requeue(conn, args.review, args.column, args.status, args.cell)
    except ReviewError as e:
        sys.exit(str(e))
    print(f"re-queued {len(ids)} cells of review {args.review}: {ids}")
    if args.column:
        print(f"refreshed from the template: {', '.join(args.column)}")


if __name__ == "__main__":
    main()
