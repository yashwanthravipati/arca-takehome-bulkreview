"""Put cells of a review back in the queue, so run_workers.py answers them again.

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
import os
import sys
from pathlib import Path

import psycopg
from dotenv import find_dotenv, load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.models import Template  # noqa: E402

RESET = """
UPDATE cells SET status = 'pending', answer = NULL, reasoning = NULL, attempts = 0,
       claimed_by = NULL, lease_expires_at = NULL, last_error = NULL, model = NULL,
       input_tokens = NULL, output_tokens = NULL, cache_read_tokens = NULL,
       cache_write_tokens = NULL, latency_ms = NULL, updated_at = now()
WHERE id = ANY(%s)
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--review", type=int, required=True)
    ap.add_argument("--column", action="append", default=[], metavar="KEY")
    ap.add_argument("--status", action="append", default=[])
    ap.add_argument("--cell", action="append", default=[], type=int, metavar="ID")
    args = ap.parse_args()
    if not (args.column or args.status or args.cell):
        ap.error("give at least one of --column, --status, --cell")

    load_dotenv(find_dotenv(usecwd=True))
    url = os.environ.get("DATABASE_URL", "postgresql://postgres@127.0.0.1:5433/bulkreview")
    with psycopg.connect(url) as conn:  # one transaction
        review = conn.execute("SELECT template FROM reviews WHERE id = %s",
                              (args.review,)).fetchone()
        if not review:
            sys.exit(f"no review {args.review}")
        template = Template.model_validate_json(
            (ROOT / "templates" / f"{review[0]}.json").read_text())
        columns = {c.key: c for c in template.columns}
        for key in args.column:
            c = columns.get(key)
            updated = c and conn.execute(
                "UPDATE columns SET question = %s, answer_type = %s, instructions = %s,"
                " model_tier = %s WHERE review_id = %s AND key = %s",
                (c.question, c.answer_type, c.instructions, c.model_tier, args.review, key)).rowcount
            if not updated:
                sys.exit(f"column {key!r} is not in both the template and review {args.review}")

        ids = [r[0] for r in conn.execute(
            """SELECT c.id FROM cells c JOIN columns col ON col.id = c.column_id
               WHERE c.review_id = %s AND c.status <> 'running'
                 AND (col.key = ANY(%s) OR c.status = ANY(%s) OR c.id = ANY(%s))
               ORDER BY c.id""", (args.review, args.column, args.status, args.cell))]
        conn.execute("DELETE FROM citations WHERE cell_id = ANY(%s)", (ids,))
        conn.execute(RESET, (ids,))

    print(f"re-queued {len(ids)} cells of review {args.review}: {ids}")
    if args.column:
        print(f"refreshed from template {review[0]}: {', '.join(args.column)}")


if __name__ == "__main__":
    main()
