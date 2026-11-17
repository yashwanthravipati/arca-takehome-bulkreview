.PHONY: setup dev screenshots

# Python virtualenvs for each part, plus the web app's node modules.
setup:
	cd Ingestion && ([ -d .venv ] || uv venv --python 3.13) && uv pip install -r requirements.txt
	cd Review_Engine && ([ -d .venv ] || uv venv --python 3.13) && uv pip install -r requirements.txt
	cd api && ([ -d .venv ] || uv venv --python 3.13) && uv pip install -r requirements.txt
	cd web && npm install

# Postgres, API (127.0.0.1:8010), worker (--watch) and web app (127.0.0.1:5173).
dev:
	./dev.sh

# With `make dev` running: creates a review and saves screenshots to web/screenshots/.
screenshots:
	cd web && npm run screenshots
