"""Load corpus/docs/*.md into Postgres: one row per document version plus page offsets.

Each page's content is normalize()d and the pages are joined with a single
space; pages.char_start/char_end index into that stored text. Where each source
line starts is kept too (documents.line_starts / block_starts), so the UI can
show headings, lists and tables. A version whose text, metadata and layout are
unchanged is skipped, so re-running is a no-op.

Usage: python scripts/ingest.py
"""

import hashlib
import os
import re
import sys
import time
from pathlib import Path

import psycopg
import yaml
from dotenv import find_dotenv, load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.text import normalize  # noqa: E402

DOCS_DIR = ROOT / "corpus" / "docs"
SCHEMA = ROOT / "db" / "schema.sql"
FRONT_MATTER = re.compile(r"\A---\n(.*?)\n---\n(.*)\Z", re.S)
PAGE_MARKER = re.compile(r"<!-- page (\d+) -->")


def page_lines(raw):
    """Normalize one page and find where each source line starts in the result.

    Returns (normalized text, [(offset, starts_block), ...]): one entry per non-empty source
    line, where starts_block is true for the page's first line and for a line after a blank
    line. normalize() collapses whitespace, so the page is its normalized lines joined by
    single spaces; the assert keeps that true."""
    text_parts, lines, pos, blank = [], [], 0, True
    for line in raw.split("\n"):
        part = normalize(line)
        if not part:
            blank = True
            continue
        lines.append((pos, blank))
        text_parts.append(part)
        pos += len(part) + 1
        blank = False
    text = " ".join(text_parts)
    assert text == normalize(raw), "line-by-line normalization differs from the page's"
    return text, lines


def read_doc(path):
    """Return (metadata dict, [(page_no, normalized page text, lines), ...]), with lines as
    page_lines() returns them."""
    m = FRONT_MATTER.match(path.read_text(encoding="utf-8"))
    if not m:
        raise ValueError(f"{path.name}: missing YAML front matter")
    meta = yaml.safe_load(m.group(1))
    parts = PAGE_MARKER.split(m.group(2))  # [before first marker, no, text, no, text, ...]
    if parts[0].strip():
        raise ValueError(f"{path.name}: content before the first page marker")
    pages = [(int(no), *page_lines(raw)) for no, raw in zip(parts[1::2], parts[2::2])]
    return meta, pages


def join_pages(pages):
    """Join page texts with one space. Returns (full text, [(page_no, start, end), ...],
    line_starts, block_starts), all offsets into the full text."""
    spans, line_starts, block_starts, pos = [], [], [], 0
    for no, text, lines in pages:
        spans.append((no, pos, pos + len(text)))
        line_starts += [pos + offset for offset, _ in lines]
        block_starts += [pos + offset for offset, starts_block in lines if starts_block]
        pos += len(text) + 1  # the joining space
    return " ".join(text for _, text, _ in pages), spans, line_starts, block_starts


def connect(url, wait_s=30):
    """Connect, retrying briefly so `docker compose up -d && python ...` works on a cold start."""
    for attempt in range(wait_s):
        try:
            return psycopg.connect(url, autocommit=True)
        except psycopg.OperationalError:
            if attempt == wait_s - 1:
                raise
            time.sleep(1)


def main():
    load_dotenv(find_dotenv(usecwd=True))
    url = os.environ.get("DATABASE_URL", "postgresql://postgres@127.0.0.1:5433/bulkreview")
    counts = {"inserted": 0, "updated": 0, "skipped": 0}

    with connect(url) as conn:
        conn.execute(SCHEMA.read_text())
        for path in sorted(DOCS_DIR.glob("*.md")):
            meta, pages = read_doc(path)
            text, spans, line_starts, block_starts = join_pages(pages)
            doc = {
                "id": meta["id"], "version": str(meta["version"]), "title": meta["title"],
                "product": meta["product"], "doc_type": meta["doc_type"],
                "effective_date": meta.get("effective_date"), "is_current": meta["is_current"],
                "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(), "text": text,
                "line_starts": line_starts, "block_starts": block_starts,
            }
            existing = conn.execute(
                "SELECT content_hash, title, product, doc_type, effective_date, is_current,"
                " line_starts, block_starts"
                " FROM documents WHERE id = %(id)s AND version = %(version)s", doc).fetchone()
            # Compare metadata and layout too: flipping is_current on a superseded version
            # leaves its text unchanged, and versions ingested before the layout lack it.
            if existing == (doc["content_hash"], doc["title"], doc["product"], doc["doc_type"],
                            doc["effective_date"], doc["is_current"], line_starts, block_starts):
                counts["skipped"] += 1
                continue

            with conn.transaction():
                conn.execute(
                    """INSERT INTO documents (id, version, title, product, doc_type,
                                              effective_date, is_current, content_hash, text,
                                              line_starts, block_starts)
                       VALUES (%(id)s, %(version)s, %(title)s, %(product)s, %(doc_type)s,
                               %(effective_date)s, %(is_current)s, %(content_hash)s, %(text)s,
                               %(line_starts)s, %(block_starts)s)
                       ON CONFLICT (id, version) DO UPDATE SET
                         title = EXCLUDED.title, product = EXCLUDED.product,
                         doc_type = EXCLUDED.doc_type, effective_date = EXCLUDED.effective_date,
                         is_current = EXCLUDED.is_current, content_hash = EXCLUDED.content_hash,
                         text = EXCLUDED.text, line_starts = EXCLUDED.line_starts,
                         block_starts = EXCLUDED.block_starts, ingested_at = now()""", doc)
                conn.execute("DELETE FROM pages WHERE doc_id = %(id)s AND version = %(version)s",
                             doc)
                with conn.cursor() as cur:
                    cur.executemany(
                        "INSERT INTO pages (doc_id, version, page_no, char_start, char_end)"
                        " VALUES (%s, %s, %s, %s, %s)",
                        [(doc["id"], doc["version"], no, s, e) for no, s, e in spans])
            counts["updated" if existing else "inserted"] += 1
            print(f"{'updated' if existing else 'inserted'} {doc['id']} v{doc['version']} "
                  f"({len(spans)} pages, {len(text):,} chars)")

        total = conn.execute("SELECT count(*) FROM documents").fetchone()[0]
    print(f"inserted {counts['inserted']} / updated {counts['updated']} / "
          f"skipped {counts['skipped']}  ({total} document versions in database)")


if __name__ == "__main__":
    main()
