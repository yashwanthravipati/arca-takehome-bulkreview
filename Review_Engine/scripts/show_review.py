"""Print a review as a grid, or one cell in full with its evidence.

Grid: one row per document, one column per question. A cell shows its id, a truncated answer
and the pages of its verified citations, or a status marker (NOT FOUND / REVIEW / FAILED).
--cell shows the full answer, the reasoning and each citation with its page, match method and
~100 characters of surrounding text from the stored document.

Usage: python scripts/show_review.py 1
       python scripts/show_review.py 1 --cell 17
"""

import argparse
import json
import os
import sys
import textwrap

import psycopg
from dotenv import find_dotenv, load_dotenv
from psycopg.rows import dict_row

COL_WIDTH = 30
CONTEXT_CHARS = 100
MARKERS = {"not_found": "NOT FOUND", "needs_review": "REVIEW", "failed": "FAILED",
           "pending": "pending", "running": "running"}


def short(answer) -> str:
    if isinstance(answer, list):
        return "; ".join(answer) if answer else "(none)"
    if isinstance(answer, bool):
        return "yes" if answer else "no"
    return str(answer)


def fit(s: str, width: int) -> str:
    return s if len(s) <= width else s[:width - 1] + "…"


def grid_cell(cell) -> str:
    pages = f" p.{','.join(map(str, cell['pages']))}" if cell["pages"] else ""
    tag = f"#{cell['id']} "
    if cell["status"] == "done":
        body = fit(short(cell["answer"]), COL_WIDTH - len(tag) - len(pages)) + pages
    elif cell["status"] == "needs_review":
        body = fit(f"REVIEW {short(cell['answer'])}", COL_WIDTH - len(tag))
    else:
        body = MARKERS[cell["status"]]
    return tag + body


def show_grid(conn, review_id):
    review = conn.execute("SELECT * FROM reviews WHERE id = %s", (review_id,)).fetchone()
    if not review:
        sys.exit(f"no review {review_id}")
    cells = conn.execute(
        """SELECT c.id, c.doc_id, c.version, c.status, c.answer, col.key, col.position,
                  array_agg(DISTINCT ct.page_no ORDER BY ct.page_no)
                    FILTER (WHERE ct.verified) AS pages
           FROM cells c JOIN columns col ON col.id = c.column_id
           LEFT JOIN citations ct ON ct.cell_id = c.id
           WHERE c.review_id = %s
           GROUP BY c.id, col.key, col.position""", (review_id,)).fetchall()
    keys = [k for _, k in sorted({(c["position"], c["key"]) for c in cells})]
    docs = sorted({(c["doc_id"], c["version"]) for c in cells})
    by_pos = {(c["doc_id"], c["version"], c["key"]): c for c in cells}

    print(f"review {review['id']} {review['name']!r}: template {review['template']}, "
          f"filter {json.dumps(review['doc_filter'])}, {len(docs)} documents x {len(keys)} "
          f"columns\n")
    doc_w = max(len(f"{d} v{v}") for d, v in docs)
    print("  ".join(["document".ljust(doc_w)] + [fit(k, COL_WIDTH).ljust(COL_WIDTH) for k in keys]))
    print("  ".join(["-" * doc_w] + ["-" * COL_WIDTH] * len(keys)))
    for d, v in docs:
        row = [grid_cell(by_pos[d, v, k]) if (d, v, k) in by_pos else "" for k in keys]
        print("  ".join([f"{d} v{v}".ljust(doc_w)] + [r.ljust(COL_WIDTH) for r in row]))

    counts = {}
    for c in cells:
        counts[c["status"]] = counts.get(c["status"], 0) + 1
    print("\n" + ", ".join(f"{s} {n}" for s, n in sorted(counts.items())) +
          f"  ({len(cells)} cells)")
    print(f"details: python scripts/show_review.py {review_id} --cell <id>")


def show_cell(conn, review_id, cell_id):
    cell = conn.execute(
        """SELECT c.*, col.key, col.question, d.text FROM cells c
           JOIN columns col ON col.id = c.column_id
           JOIN documents d ON d.id = c.doc_id AND d.version = c.version
           WHERE c.id = %s AND c.review_id = %s""", (cell_id, review_id)).fetchone()
    if not cell:
        sys.exit(f"no cell {cell_id} in review {review_id}")
    citations = conn.execute("SELECT * FROM citations WHERE cell_id = %s ORDER BY id",
                             (cell_id,)).fetchall()
    wrap = textwrap.TextWrapper(width=100, initial_indent="    ", subsequent_indent="    ")

    print(f"cell {cell['id']}: {cell['doc_id']} v{cell['version']} / {cell['key']}")
    print(f"status {cell['status']}, attempts {cell['attempts']}, model {cell['model']}")
    print(f"question: {cell['question']}\n")
    print("answer:")
    answer = cell["answer"]
    if isinstance(answer, list):
        for item in answer or ["(empty list)"]:
            print(wrap.fill(f"- {item}"))
    else:
        print(wrap.fill("(no answer)" if answer is None else short(answer)))
    print(f"\nreasoning:\n{wrap.fill(cell['reasoning'] or '-')}\n")

    print(f"citations ({len(citations)}):")
    text = cell["text"]
    for i, ct in enumerate(citations, 1):
        if ct["verified"]:
            s, e = ct["char_start"], ct["char_end"]
            before = text[max(0, s - CONTEXT_CHARS):s]
            after = text[e:e + CONTEXT_CHARS]
            print(f"  [{i}] page {ct['page_no']}, {ct['match_method']} match, chars {s}-{e}")
            print(wrap.fill(f"…{before}«{text[s:e]}»{after}…"))
        else:
            print(f"  [{i}] NOT FOUND in the document text; the model's quote:")
            print(wrap.fill(f'"{ct["quote"]}"'))

    if cell["model"]:
        print(f"\ntokens: {cell['input_tokens']:,} input, {cell['cache_read_tokens']:,} cache read, "
              f"{cell['cache_write_tokens']:,} cache write, {cell['output_tokens']:,} output; "
              f"latency {cell['latency_ms']:,} ms")
    if cell["last_error"]:
        print(f"last error: {cell['last_error']}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("review", type=int)
    ap.add_argument("--cell", type=int, help="show one cell in full")
    args = ap.parse_args()

    load_dotenv(find_dotenv(usecwd=True))
    url = os.environ.get("DATABASE_URL", "postgresql://postgres@127.0.0.1:5433/bulkreview")
    with psycopg.connect(url, row_factory=dict_row) as conn:
        if args.cell:
            show_cell(conn, args.review, args.cell)
        else:
            show_grid(conn, args.review)


if __name__ == "__main__":
    main()
