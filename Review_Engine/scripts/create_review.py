"""Create a review from a template: the review row, its columns, one pending cell per
(document, column).

The default document filter is the template's doc_type, current versions only (so a
superseded version such as STAB-007 v1.0 is left out). --filter adds or overrides equality
conditions on documents (a list value means "any of"; null drops a condition). Re-running with
the same name reuses the review and only adds missing cells, e.g. for newly ingested documents.

Usage: python scripts/create_review.py --template stability_protocol --name "Stability review"
       [--filter '{"product": "Product B"}']
"""

import argparse
import json
import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.db import database_url  # noqa: E402
from app.reviews import ReviewError, create_review  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--template", required=True, help="name of a file in templates/, without .json")
    ap.add_argument("--name", required=True)
    ap.add_argument("--filter", default="{}", help="JSON object of document conditions")
    args = ap.parse_args()

    try:
        with psycopg.connect(database_url()) as conn:  # one transaction: all or nothing
            r = create_review(conn, args.name, args.template, json.loads(args.filter))
            n_docs, n_cols, n_cells = conn.execute(
                "SELECT count(DISTINCT (doc_id, version)), count(DISTINCT column_id), count(*)"
                " FROM cells WHERE review_id = %s", (r["id"],)).fetchone()
    except ReviewError as e:
        sys.exit(str(e))

    print(f"review {r['id']} {args.name!r} (template {r['template']}, "
          f"filter {json.dumps(r['doc_filter'])})")
    print(f"{n_docs} documents x {n_cols} columns = {n_cells} cells ({r['new_cells']} new)")


if __name__ == "__main__":
    main()
