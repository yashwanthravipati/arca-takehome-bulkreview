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
import os
import sys
from pathlib import Path

import psycopg
from dotenv import find_dotenv, load_dotenv
from psycopg.types.json import Jsonb

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.models import Template  # noqa: E402

SCHEMA = ROOT / "db" / "schema_review.sql"
FILTERABLE = {"id", "product", "doc_type", "is_current", "version"}


def filter_sql(doc_filter: dict) -> tuple[str, dict]:
    """Turn {"column": value | [values]} into a WHERE clause over documents d."""
    clauses, params = [], {}
    for col, value in sorted(doc_filter.items()):
        if col not in FILTERABLE:
            sys.exit(f"cannot filter on {col!r}; allowed: {', '.join(sorted(FILTERABLE))}")
        op = "= ANY(%(f_{0})s)" if isinstance(value, list) else "= %(f_{0})s"
        clauses.append(f"d.{col} {op.format(col)}")
        params[f"f_{col}"] = value
    return " AND ".join(clauses) or "TRUE", params


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--template", required=True, help="name of a file in templates/, without .json")
    ap.add_argument("--name", required=True)
    ap.add_argument("--filter", default="{}", help="JSON object of document conditions")
    args = ap.parse_args()

    load_dotenv(find_dotenv(usecwd=True))
    url = os.environ.get("DATABASE_URL", "postgresql://postgres@127.0.0.1:5433/bulkreview")
    template = Template.model_validate_json((ROOT / "templates" / f"{args.template}.json").read_text())
    merged = {"doc_type": template.doc_type, "is_current": True, **json.loads(args.filter)}
    doc_filter = {k: v for k, v in merged.items() if v is not None}
    where, params = filter_sql(doc_filter)

    with psycopg.connect(url) as conn:  # one transaction: all or nothing
        conn.execute(SCHEMA.read_text())
        existing = conn.execute("SELECT id, template, doc_filter FROM reviews WHERE name = %s",
                                (args.name,)).fetchone()
        if existing:
            review_id, old_template, old_filter = existing
            if (old_template, old_filter) != (template.name, doc_filter):
                sys.exit(f"review {review_id} {args.name!r} already exists with template "
                         f"{old_template} and filter {json.dumps(old_filter)}; pick another name")
        else:
            review_id = conn.execute(
                "INSERT INTO reviews (name, template, doc_filter) VALUES (%s, %s, %s) RETURNING id",
                (args.name, template.name, Jsonb(doc_filter))).fetchone()[0]

        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO columns (review_id, key, question, answer_type, instructions,"
                " model_tier, position) VALUES (%s, %s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (review_id, key) DO NOTHING",
                [(review_id, c.key, c.question, c.answer_type, c.instructions, c.model_tier, i)
                 for i, c in enumerate(template.columns, 1)])

        # Column-major ids: workers claim in id order, so the first column of every document
        # runs before any document's second column. By then the document prefix is cached and
        # the remaining columns read it instead of all writing it at once.
        new_cells = conn.execute(
            f"""INSERT INTO cells (review_id, column_id, doc_id, version)
                SELECT %(review_id)s, c.id, d.id, d.version
                FROM columns c CROSS JOIN documents d
                WHERE c.review_id = %(review_id)s AND {where}
                ORDER BY c.position, d.id, d.version
                ON CONFLICT DO NOTHING""", {"review_id": review_id, **params}).rowcount
        n_docs, n_cols, n_cells = conn.execute(
            "SELECT count(DISTINCT (doc_id, version)), count(DISTINCT column_id), count(*)"
            " FROM cells WHERE review_id = %s", (review_id,)).fetchone()

    print(f"review {review_id} {args.name!r} (template {template.name}, "
          f"filter {json.dumps(doc_filter)})")
    print(f"{n_docs} documents x {n_cols} columns = {n_cells} cells ({new_cells} new)")


if __name__ == "__main__":
    main()
