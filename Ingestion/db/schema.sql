-- Applied by scripts/ingest.py on every run, so it must stay idempotent.

CREATE TABLE IF NOT EXISTS documents (
  id             text NOT NULL,
  version        text NOT NULL,
  title          text NOT NULL,
  product        text NOT NULL,
  doc_type       text NOT NULL,
  effective_date date,
  is_current     boolean NOT NULL,
  content_hash   text NOT NULL,
  text           text NOT NULL,
  -- Layout, for display only. text has its whitespace collapsed, so these record where each
  -- source line starts in it, and which of those lines start a block (the first line of a
  -- page, or a line after a blank line). Citation offsets never depend on them.
  line_starts    int[],
  block_starts   int[],
  ingested_at    timestamptz DEFAULT now(),
  PRIMARY KEY (id, version)
);
ALTER TABLE documents ADD COLUMN IF NOT EXISTS line_starts int[];   -- databases created earlier
ALTER TABLE documents ADD COLUMN IF NOT EXISTS block_starts int[];

-- Page p of a document is text[char_start:char_end] (0-based, end-exclusive).
CREATE TABLE IF NOT EXISTS pages (
  doc_id     text NOT NULL,
  version    text NOT NULL,
  page_no    int  NOT NULL,
  char_start int  NOT NULL,
  char_end   int  NOT NULL,
  PRIMARY KEY (doc_id, version, page_no),
  FOREIGN KEY (doc_id, version) REFERENCES documents(id, version)
);
