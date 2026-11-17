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
import sys
import textwrap
from collections import Counter
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.db import database_url  # noqa: E402
from app.reviews import ReviewError, cell_detail, document, grid, short_answer  # noqa: E402

COL_WIDTH = 30
CONTEXT_CHARS = 100
MARKERS = {"not_found": "NOT FOUND", "needs_review": "REVIEW", "failed": "FAILED",
           "pending": "pending", "running": "running"}


def fit(s: str, width: int) -> str:
    return s if len(s) <= width else s[:width - 1] + "…"


def grid_cell(cell) -> str:
    pages = f" p.{','.join(map(str, cell['pages']))}" if cell["pages"] else ""
    tag = f"#{cell['id']} "
    if cell["status"] == "done":
        body = fit(cell["short"], COL_WIDTH - len(tag) - len(pages)) + pages
    elif cell["status"] == "needs_review":
        body = fit(f"REVIEW {cell['short']}", COL_WIDTH - len(tag))
    else:
        body = MARKERS[cell["status"]]
    return tag + body


def show_grid(conn, review_id):
    g = grid(conn, review_id)
    review, keys = g["review"], {c["id"]: c["key"] for c in g["columns"]}
    by_pos = {(c["doc_id"], c["version"], c["column_id"]): c for c in g["cells"]}
    docs = [(r["doc_id"], r["version"]) for r in g["rows"]]

    print(f"review {review['id']} {review['name']!r}: template {review['template']}, "
          f"filter {json.dumps(review['doc_filter'])}, {len(docs)} documents x {len(keys)} "
          f"columns\n")
    doc_w = max(len(f"{d} v{v}") for d, v in docs)
    print("  ".join(["document".ljust(doc_w)] + [fit(k, COL_WIDTH).ljust(COL_WIDTH)
                                                 for k in keys.values()]))
    print("  ".join(["-" * doc_w] + ["-" * COL_WIDTH] * len(keys)))
    for d, v in docs:
        row = [grid_cell(by_pos[d, v, k]) if (d, v, k) in by_pos else "" for k in keys]
        print("  ".join([f"{d} v{v}".ljust(doc_w)] + [r.ljust(COL_WIDTH) for r in row]))

    counts = Counter(c["status"] for c in g["cells"])
    print("\n" + ", ".join(f"{s} {n}" for s, n in sorted(counts.items())) +
          f"  ({len(g['cells'])} cells)")
    print(f"details: python scripts/show_review.py {review_id} --cell <id>")


def show_cell(conn, review_id, cell_id):
    cell = cell_detail(conn, cell_id)
    if cell["review_id"] != review_id:
        raise ReviewError(f"no cell {cell_id} in review {review_id}")
    text = document(conn, cell["doc_id"], cell["version"])["text"]
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
        print(wrap.fill(short_answer(answer) or "(no answer)"))
    print(f"\nreasoning:\n{wrap.fill(cell['reasoning'] or '-')}\n")

    print(f"citations ({len(cell['citations'])}):")
    for i, ct in enumerate(cell["citations"], 1):
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

    try:
        with psycopg.connect(database_url()) as conn:
            if args.cell:
                show_cell(conn, args.review, args.cell)
            else:
                show_grid(conn, args.review)
    except ReviewError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
