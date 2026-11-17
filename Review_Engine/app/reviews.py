"""Review operations shared by the CLI scripts and the API: templates, creating reviews and
columns, re-queueing cells, and reading the grid, one cell's evidence and the run totals.

Functions take an open psycopg connection and leave committing to the caller. The SQL for the
grid and the totals is exposed as constants with pure row -> dict helpers, so that async
callers (the API's live event stream) run exactly the same queries.
"""

import re
from pathlib import Path

from psycopg.rows import dict_row, tuple_row
from psycopg.types.json import Jsonb

from app.llm import cost_usd
from app.models import Column, Template

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "templates"
SCHEMA = ROOT / "db" / "schema_review.sql"
STATUSES = ("done", "not_found", "needs_review", "failed", "running", "pending")
FINISHED = ("done", "not_found", "needs_review", "failed")
FILTERABLE = {"id", "product", "doc_type", "is_current", "version"}
COLUMN_KEY = re.compile(r"[a-z][a-z0-9_]{0,62}")


class ReviewError(Exception):
    """A request that can't be carried out. `status` is the HTTP status the API returns."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# Explicit row factories, so these helpers work whatever the caller's connection returns.
def _rows(conn, sql, params=None) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(sql, params).fetchall()


def _tuples(conn, sql, params=None) -> list[tuple]:
    with conn.cursor(row_factory=tuple_row) as cur:
        return cur.execute(sql, params).fetchall()


def ensure_schema(conn) -> None:
    conn.execute(SCHEMA.read_text())


# --- templates ---------------------------------------------------------------------------

def list_templates() -> list[Template]:
    return [Template.model_validate_json(p.read_text()) for p in sorted(TEMPLATES.glob("*.json"))]


def load_template(name: str) -> Template:
    path = TEMPLATES / f"{name}.json"
    if not re.fullmatch(r"[a-z0-9_]+", name) or not path.exists():
        raise ReviewError(f"no template {name!r}", 404)
    return Template.model_validate_json(path.read_text())


# --- creating reviews, columns and cells -------------------------------------------------

def filter_sql(doc_filter: dict) -> tuple[str, dict]:
    """Turn {"column": value | [values]} into a WHERE clause over documents d."""
    clauses, params = [], {}
    for col, value in sorted(doc_filter.items()):
        if col not in FILTERABLE:
            raise ReviewError(f"cannot filter on {col!r}; allowed: {', '.join(sorted(FILTERABLE))}")
        op = "= ANY(%(f_{0})s)" if isinstance(value, list) else "= %(f_{0})s"
        clauses.append(f"d.{col} {op.format(col)}")
        params[f"f_{col}"] = value
    return " AND ".join(clauses) or "TRUE", params


def insert_cells(conn, review_id: int, doc_filter: dict, column_id: int | None = None) -> int:
    """One pending cell per (matching document, column); only `column_id`'s cells if given."""
    where, params = filter_sql(doc_filter)
    # Ids run document by document, in column order within each. Workers claim the lowest id
    # that is its document's first unfinished cell (see worker.py), so documents run in order
    # and each document's questions run back to back while its prompt cache is warm.
    return conn.execute(
        f"""INSERT INTO cells (review_id, column_id, doc_id, version)
            SELECT %(review_id)s, c.id, d.id, d.version
            FROM columns c CROSS JOIN documents d
            WHERE c.review_id = %(review_id)s
              AND (%(column_id)s::int IS NULL OR c.id = %(column_id)s) AND {where}
            ORDER BY d.id, d.version, c.position
            ON CONFLICT DO NOTHING""",
        {"review_id": review_id, "column_id": column_id, **params}).rowcount


def create_review(conn, name: str, template_name: str, doc_filter: dict | None = None) -> dict:
    """Create a review with its columns and pending cells. The default filter is the
    template's doc_type, current versions only; `doc_filter` adds or overrides conditions
    (a list means "any of", None drops one). An existing review with the same name, template
    and filter is reused and only gains missing cells."""
    name = name.strip()
    if not name:
        raise ReviewError("a review needs a name")
    template = load_template(template_name)
    merged = {"doc_type": template.doc_type, "is_current": True, **(doc_filter or {})}
    doc_filter = {k: v for k, v in merged.items() if v is not None}
    filter_sql(doc_filter)  # reject a bad filter before writing anything

    ensure_schema(conn)
    existing = _tuples(conn, "SELECT id, template, doc_filter FROM reviews WHERE name = %s",
                       (name,))
    if existing:
        review_id, old_template, old_filter = existing[0]
        if (old_template, old_filter) != (template.name, doc_filter):
            raise ReviewError(f"review {review_id} {name!r} already exists with template "
                              f"{old_template} and another filter; pick another name", 409)
    else:
        review_id = _tuples(
            conn, "INSERT INTO reviews (name, template, doc_filter) VALUES (%s, %s, %s) RETURNING id",
            (name, template.name, Jsonb(doc_filter)))[0][0]

    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO columns (review_id, key, question, answer_type, instructions,"
            " model_tier, position) VALUES (%s, %s, %s, %s, %s, %s, %s)"
            " ON CONFLICT (review_id, key) DO NOTHING",
            [(review_id, c.key, c.question, c.answer_type, c.instructions, c.model_tier, i)
             for i, c in enumerate(template.columns, 1)])
    new_cells = insert_cells(conn, review_id, doc_filter)
    return {"id": review_id, "created": not existing, "template": template.name,
            "doc_filter": doc_filter, "new_cells": new_cells}


def add_column(conn, review_id: int, column: Column) -> dict:
    """Add a column to a review and create pending cells for that column only."""
    review = _tuples(conn, "SELECT doc_filter FROM reviews WHERE id = %s", (review_id,))
    if not review:
        raise ReviewError(f"no review {review_id}", 404)
    if not COLUMN_KEY.fullmatch(column.key):
        raise ReviewError("key must start with a letter and use only a-z, 0-9 and _")
    if not column.question.strip():
        raise ReviewError("a column needs a question")
    row = _tuples(
        conn,
        """INSERT INTO columns (review_id, key, question, answer_type, instructions, model_tier,
                                position)
           SELECT %s, %s, %s, %s, %s, %s, coalesce(max(position), 0) + 1
           FROM columns WHERE review_id = %s
           ON CONFLICT (review_id, key) DO NOTHING RETURNING id""",
        (review_id, column.key, column.question.strip(), column.answer_type,
         column.instructions or None, column.model_tier, review_id))
    if not row:
        raise ReviewError(f"review {review_id} already has a column {column.key!r}", 409)
    column_id = row[0][0]
    return {"column_id": column_id,
            "new_cells": insert_cells(conn, review_id, review[0][0], column_id)}


RESET = """
UPDATE cells SET status = 'pending', answer = NULL, claims = NULL, reasoning = NULL, attempts = 0,
       claimed_by = NULL, lease_expires_at = NULL, last_error = NULL, model = NULL,
       input_tokens = NULL, output_tokens = NULL, cache_read_tokens = NULL,
       cache_write_tokens = NULL, latency_ms = NULL, updated_at = now()
WHERE id = ANY(%s)
"""


def requeue(conn, review_id: int, columns=(), statuses=(), cell_ids=()) -> list[int]:
    """Put cells back in the queue: every cell of `columns` (whose definitions are first
    refreshed from the review's template), cells with one of `statuses`, and `cell_ids`.
    Re-queued cells lose their answer and citations and restart at attempt 0; running cells
    are left alone. Returns the re-queued ids."""
    review = _tuples(conn, "SELECT template FROM reviews WHERE id = %s", (review_id,))
    if not review:
        raise ReviewError(f"no review {review_id}", 404)
    if columns:
        template = {c.key: c for c in load_template(review[0][0]).columns}
        for key in columns:
            c = template.get(key)
            updated = c and conn.execute(
                "UPDATE columns SET question = %s, answer_type = %s, instructions = %s,"
                " model_tier = %s WHERE review_id = %s AND key = %s",
                (c.question, c.answer_type, c.instructions, c.model_tier, review_id, key)).rowcount
            if not updated:
                raise ReviewError(f"column {key!r} is not in both the template and review "
                                  f"{review_id}", 404)

    ids = [r[0] for r in _tuples(
        conn,
        """SELECT c.id FROM cells c JOIN columns col ON col.id = c.column_id
           WHERE c.review_id = %s AND c.status <> 'running'
             AND (col.key = ANY(%s) OR c.status = ANY(%s) OR c.id = ANY(%s))
           ORDER BY c.id""", (review_id, list(columns), list(statuses), list(cell_ids)))]
    conn.execute("DELETE FROM citations WHERE cell_id = ANY(%s)", (ids,))
    conn.execute(RESET, (ids,))
    return ids


def delete_review(conn, review_id: int) -> None:
    """Delete a review with its columns and cells; citations go with their cells (ON DELETE
    CASCADE). A worker still answering one of its cells finds the cell gone when it writes,
    and drops the result."""
    if not _tuples(conn, "SELECT 1 FROM reviews WHERE id = %s", (review_id,)):
        raise ReviewError(f"no review {review_id}", 404)
    conn.execute("DELETE FROM cells WHERE review_id = %s", (review_id,))
    conn.execute("DELETE FROM columns WHERE review_id = %s", (review_id,))
    conn.execute("DELETE FROM reviews WHERE id = %s", (review_id,))


# --- reading -----------------------------------------------------------------------------

def short_answer(answer) -> str | None:
    """An answer as one line of text: lists joined with "; ", booleans as yes/no."""
    if answer is None:
        return None
    if isinstance(answer, list):
        return "; ".join(answer) if answer else "(none)"
    if isinstance(answer, bool):
        return "yes" if answer else "no"
    return str(answer)


def list_reviews(conn) -> list[dict]:
    rows = _rows(conn, """
        SELECT r.id, r.name, r.template, r.doc_filter, r.created_at,
               coalesce((SELECT jsonb_object_agg(status, n)
                         FROM (SELECT status, count(*) AS n FROM cells
                               WHERE review_id = r.id GROUP BY status) s), '{}') AS counts
        FROM reviews r ORDER BY r.id DESC""")
    for r in rows:
        r["counts"] = {s: r["counts"].get(s, 0) for s in STATUSES}
        r["total"] = sum(r["counts"].values())
        r["finished"] = sum(r["counts"][s] for s in FINISHED)
    return rows


# Cells of a review changed after `since` (pass '-infinity' for all), with the page of the
# first verified citation and every page cited.
GRID_CELLS = """
SELECT c.id, c.doc_id, c.version, c.column_id, c.status, c.answer, c.updated_at,
       (array_agg(ct.page_no ORDER BY ct.id) FILTER (WHERE ct.verified))[1] AS page,
       coalesce(array_agg(DISTINCT ct.page_no ORDER BY ct.page_no)
                FILTER (WHERE ct.verified), '{}') AS pages
FROM cells c LEFT JOIN citations ct ON ct.cell_id = c.id
WHERE c.review_id = %(review_id)s AND c.updated_at > %(since)s::timestamptz
GROUP BY c.id ORDER BY c.id
"""


def cell_summary(row: dict) -> dict:
    """A GRID_CELLS row as the grid shows it."""
    return {"id": row["id"], "doc_id": row["doc_id"], "version": row["version"],
            "column_id": row["column_id"], "status": row["status"],
            "short": short_answer(row["answer"]), "page": row["page"], "pages": row["pages"]}


TOTALS = """
SELECT status, model, count(*) AS n,
       coalesce(sum(input_tokens), 0) AS input_tokens,
       coalesce(sum(output_tokens), 0) AS output_tokens,
       coalesce(sum(cache_read_tokens), 0) AS cache_read_tokens,
       coalesce(sum(cache_write_tokens), 0) AS cache_write_tokens
FROM cells WHERE review_id = %s GROUP BY status, model
"""
TOKEN_FIELDS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")


def summarize_totals(rows: list[dict]) -> dict:
    """TOTALS rows -> status counts, token sums, cache hit rate and estimated cost."""
    counts = dict.fromkeys(STATUSES, 0)
    tokens = dict.fromkeys(TOKEN_FIELDS, 0)
    by_model: dict[str, dict] = {}
    for r in rows:
        counts[r["status"]] += r["n"]
        if r["model"]:
            m = by_model.setdefault(r["model"], {"cells": 0, **dict.fromkeys(TOKEN_FIELDS, 0)})
            m["cells"] += r["n"]
            for f in TOKEN_FIELDS:
                m[f] += r[f]
                tokens[f] += r[f]
    for model, m in by_model.items():
        m["cost_usd"] = cost_usd(model, *(m[f] for f in TOKEN_FIELDS))
    prompt = tokens["input_tokens"] + tokens["cache_read_tokens"] + tokens["cache_write_tokens"]
    return {"counts": counts, "total": sum(counts.values()),
            "finished": sum(counts[s] for s in FINISHED), **tokens,
            "cache_hit_rate": tokens["cache_read_tokens"] / prompt if prompt else None,
            "cost_usd": round(sum(m["cost_usd"] or 0 for m in by_model.values()), 4),
            "by_model": by_model}


def totals(conn, review_id: int) -> dict:
    return summarize_totals(_rows(conn, TOTALS, (review_id,)))


def grid(conn, review_id: int) -> dict:
    """Everything the grid needs: the review, its columns, its documents, every cell, totals."""
    review = _rows(conn, "SELECT id, name, template, doc_filter, created_at FROM reviews"
                         " WHERE id = %s", (review_id,))
    if not review:
        raise ReviewError(f"no review {review_id}", 404)
    columns = _rows(conn, "SELECT id, key, question, answer_type, instructions, model_tier,"
                          " position FROM columns WHERE review_id = %s ORDER BY position",
                    (review_id,))
    rows = _rows(conn, """SELECT DISTINCT d.id AS doc_id, d.version, d.title, d.product
                          FROM cells c JOIN documents d ON d.id = c.doc_id AND d.version = c.version
                          WHERE c.review_id = %s ORDER BY d.id, d.version""", (review_id,))
    cells = _rows(conn, GRID_CELLS, {"review_id": review_id, "since": "-infinity"})
    return {"review": review[0], "columns": columns, "rows": rows,
            "cells": [cell_summary(c) for c in cells], "totals": totals(conn, review_id)}


def cell_detail(conn, cell_id: int) -> dict:
    """One cell in full: answer, reasoning, metrics, and its citations in order."""
    cell = _rows(conn, """
        SELECT c.id, c.review_id, c.doc_id, c.version, c.column_id, col.key, col.question,
               col.answer_type, c.status, c.answer, c.claims, c.reasoning, c.model, c.input_tokens,
               c.output_tokens, c.cache_read_tokens, c.cache_write_tokens, c.latency_ms,
               c.attempts, c.last_error, c.updated_at
        FROM cells c JOIN columns col ON col.id = c.column_id WHERE c.id = %s""", (cell_id,))
    if not cell:
        raise ReviewError(f"no cell {cell_id}", 404)
    cell[0]["citations"] = _rows(conn, """
        SELECT id, claim_no, quote, char_start, char_end, page_no, verified, match_method
        FROM citations WHERE cell_id = %s ORDER BY id""", (cell_id,))
    return cell[0]


def document(conn, doc_id: str, version: str) -> dict:
    """A document version: metadata, the stored text, and its page boundaries."""
    doc = _rows(conn, """SELECT id, version, title, product, doc_type, effective_date, is_current,
                                text FROM documents WHERE id = %s AND version = %s""",
                (doc_id, version))
    if not doc:
        raise ReviewError(f"no document {doc_id} v{version}", 404)
    doc[0]["pages"] = _rows(conn, """SELECT page_no, char_start, char_end FROM pages
                                     WHERE doc_id = %s AND version = %s ORDER BY page_no""",
                            (doc_id, version))
    return doc[0]
