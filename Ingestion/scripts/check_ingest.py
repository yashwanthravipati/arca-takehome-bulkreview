"""Prove citations will work: every ground-truth evidence sentence is found in the stored
text of the right document version and maps to the right page.

"page" is derived from the database (char offset -> pages table). "expected" is the page
whose <!-- page N --> section of the source Markdown contains the sentence; for the planted
appendix deviation it must also be the document's last page. Exits 1 on any miss or mismatch.

Usage: python scripts/check_ingest.py
"""

import json
import os
import sys
from pathlib import Path

import psycopg
from dotenv import find_dotenv, load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.text import locate, normalize  # noqa: E402
from ingest import DOCS_DIR, read_doc  # noqa: E402  (scripts/ is on sys.path)

GT_PATH = ROOT / "corpus" / "ground_truth.json"


def evidence(doc):
    """Yield (field, evidence sentence) for every non-null evidence in a ground-truth doc."""
    for field, v in doc["fields"].items():
        items = v if isinstance(v, list) else [v]
        for i, item in enumerate(items):
            if item["evidence"]:
                yield (f"{field}[{i}]" if isinstance(v, list) else field), item["evidence"]


def main():
    load_dotenv(find_dotenv(usecwd=True))
    url = os.environ.get("DATABASE_URL", "postgresql://postgres@127.0.0.1:5433/bulkreview")
    gt = json.loads(GT_PATH.read_text(encoding="utf-8"))

    rows, failures = [], 0
    with psycopg.connect(url) as conn:
        for doc in gt["documents"]:
            key = {"id": doc["id"], "version": doc["version"]}
            stored = conn.execute("SELECT text FROM documents WHERE id = %(id)s"
                                  " AND version = %(version)s", key).fetchone()
            _, src_pages = read_doc(DOCS_DIR / f"{doc['id']}_v{doc['version']}.md")
            last_page = src_pages[-1][0]

            for field, sentence in evidence(doc):
                hit = locate(sentence, stored[0]) if stored else None
                page = None
                if hit:
                    page = conn.execute(
                        "SELECT page_no FROM pages WHERE doc_id = %(id)s AND version = %(version)s"
                        " AND char_start <= %(pos)s AND %(pos)s < char_end",
                        {**key, "pos": hit[0]}).fetchone()
                    page = page[0] if page else None
                expected = next((no for no, t, _ in src_pages if normalize(sentence) in t), None)
                ok = hit is not None and page is not None and page == expected
                if doc["planted"] == "deviation_in_appendix" and field.startswith("deviations"):
                    ok = ok and page == last_page
                    field += f" (appendix, last page = {last_page})"
                failures += not ok
                rows.append((f"{doc['id']} v{doc['version']}", field, "Y" if hit else "N",
                             page or "-", expected or "-", hit[2] if hit else "-",
                             "" if ok else "  <-- FAIL"))

    widths = [max(len(str(r[i])) for r in rows + [("doc", "field", "found", "page", "expected",
                                                     "method", "")]) for i in range(6)]
    header = ("doc", "field", "found", "page", "expected", "method")
    print("  ".join(h.ljust(w) for h, w in zip(header, widths)))
    print("  ".join("-" * w for w in widths))
    for r in rows:
        print("  ".join(str(c).ljust(w) for c, w in zip(r, widths)) + r[6])
    print(f"\n{len(rows) - failures}/{len(rows)} evidence sentences found on the expected page "
          f"across {len(gt['documents'])} document versions")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
