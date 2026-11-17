-- Applied by scripts/create_review.py on every run, so it must stay idempotent.
-- The cells table is both the job queue (status, attempts, lease) and the results table.

CREATE TABLE IF NOT EXISTS reviews (
  id          serial PRIMARY KEY,
  name        text NOT NULL UNIQUE,            -- re-running create_review.py reuses the review
  template    text NOT NULL,
  doc_filter  jsonb NOT NULL,
  created_at  timestamptz DEFAULT now()
);

CREATE TABLE IF NOT EXISTS columns (
  id           serial PRIMARY KEY,
  review_id    int REFERENCES reviews(id),
  key          text NOT NULL,                  -- e.g. "storage_condition"
  question     text NOT NULL,
  answer_type  text NOT NULL,                  -- text | list | date | boolean
  instructions text,
  model_tier   text NOT NULL DEFAULT 'fast',   -- fast | strong
  position     int NOT NULL,
  UNIQUE (review_id, key)
);

CREATE TABLE IF NOT EXISTS cells (
  id                 bigserial PRIMARY KEY,
  review_id          int REFERENCES reviews(id),
  column_id          int REFERENCES columns(id),
  doc_id             text NOT NULL,
  version            text NOT NULL,
  status             text NOT NULL DEFAULT 'pending',
     -- pending | running | done | not_found | needs_review | failed
  answer             jsonb,
  reasoning          text,
  attempts           int NOT NULL DEFAULT 0,
  claimed_by         text,
  lease_expires_at   timestamptz,
  last_error         text,
  model              text,
  input_tokens       int,                      -- uncached input only
  output_tokens      int,
  cache_read_tokens  int,
  cache_write_tokens int,                      -- billed at 1.25x input, so tracked for the cost estimate
  latency_ms         int,
  updated_at         timestamptz DEFAULT now(),
  UNIQUE (review_id, column_id, doc_id, version),
  FOREIGN KEY (doc_id, version) REFERENCES documents(id, version)
);
CREATE INDEX IF NOT EXISTS cells_claim_idx ON cells (review_id, status, lease_expires_at);

CREATE TABLE IF NOT EXISTS citations (
  id           bigserial PRIMARY KEY,
  cell_id      bigint REFERENCES cells(id) ON DELETE CASCADE,
  doc_id       text NOT NULL,
  version      text NOT NULL,
  quote        text NOT NULL,                  -- as the model wrote it
  char_start   int,                            -- span in documents.text; null when not found
  char_end     int,
  page_no      int,
  verified     boolean NOT NULL,
  match_method text                            -- exact | fuzzy | none
);
CREATE INDEX IF NOT EXISTS citations_cell_idx ON citations (cell_id);
