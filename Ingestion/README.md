# Ingestion: synthetic regulatory corpus → Postgres

1. At the repo root, `cp .env.example .env` (`ANTHROPIC_API_KEY` is only needed to regenerate the corpus).
2. `cd Ingestion && docker compose up -d` starts Postgres 16 on 127.0.0.1:5433.
3. `uv venv && uv pip install -r requirements.txt && source .venv/bin/activate`
4. `python scripts/ingest.py` loads the 21 document versions in `corpus/docs/`; a re-run skips them all.
5. `python scripts/check_ingest.py` finds every evidence sentence from `corpus/ground_truth.json` and its page.
6. Optional: `python scripts/generate_corpus.py [--force] [DOC_ID ...]` rewrites `corpus/docs/` with Claude.
