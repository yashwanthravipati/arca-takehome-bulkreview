"""Database URL from the environment; the repo-root .env is loaded on first use."""

import os

from dotenv import find_dotenv, load_dotenv

DEFAULT_URL = "postgresql://postgres@127.0.0.1:5433/bulkreview"


def database_url() -> str:
    load_dotenv(find_dotenv(usecwd=True))
    return os.environ.get("DATABASE_URL", DEFAULT_URL)
