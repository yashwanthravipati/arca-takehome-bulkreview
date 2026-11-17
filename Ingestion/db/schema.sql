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
  ingested_at    timestamptz DEFAULT now(),
  PRIMARY KEY (id, version)
);

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
