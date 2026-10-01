"""
Centralized environment configuration.

Reads everything from environment variables so the app never hardcodes
localhost URLs. A local .env file (see .env.example) is loaded automatically
for local development; in production (Render, etc.) these are set as real
environment variables in the platform's dashboard.

Multi-user: people sign up with an email + password and each account only
ever sees its own data. DATABASE_URL is environment-driven so it can point at
SQLite locally and PostgreSQL in production without any code changes.
"""

import os
import secrets
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()  # no-op in production if there's no .env file present

# Falls back to a local SQLite file so `git clone && run` still works with
# zero setup. In production, set DATABASE_URL to a hosted Postgres URL,
# e.g. postgresql://user:pass@host:5432/dbname
_DEFAULT_DB_PATH = Path(__file__).resolve().with_name("ledgerly.db")
DATABASE_URL = os.environ.get(
    "DATABASE_URL", f"sqlite:///{_DEFAULT_DB_PATH.as_posix()}"
)
# Render/Neon/Supabase sometimes hand out "postgres://" which SQLAlchemy's
# psycopg2 dialect no longer accepts directly — normalize it.
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# Comma-separated list of allowed frontend origins, e.g.
#   CORS_ORIGINS=https://your-app.vercel.app,http://localhost:8000
# Falls back to "*" for local development convenience.
_raw_origins = os.environ.get("CORS_ORIGINS", "")
CORS_ORIGINS = [o.strip() for o in _raw_origins.split(",") if o.strip()] or ["*"]

# --- Authentication ------------------------------------------------------
# SECRET_KEY signs login tokens. Anyone who knows it can forge a login for any
# account, so it must be long, random, and never committed or sent to the
# browser. Generate one with:  python -c "import secrets; print(secrets.token_hex(32))"
JWT_ALGORITHM = "HS256"
# How long a login lasts before the user must sign in again (default 7 days).
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.environ.get("ACCESS_TOKEN_EXPIRE_MINUTES", str(7 * 24 * 60)))


def _load_secret_key() -> str:
    key = os.environ.get("SECRET_KEY", "").strip()
    if key:
        if len(key) < 32:
            raise RuntimeError("SECRET_KEY is too short. Use at least 32 random characters.")
        return key
    if DATABASE_URL.startswith("sqlite"):
        # Local development convenience: create a key once and keep it in a
        # git-ignored file so logins survive restarts. Never used with Postgres.
        key_file = Path(__file__).resolve().with_name(".secret_key")
        if key_file.exists():
            existing = key_file.read_text().strip()
            if len(existing) >= 32:
                return existing
        generated = secrets.token_hex(32)
        key_file.write_text(generated)
        try:
            key_file.chmod(0o600)
        except OSError:
            pass
        return generated
    raise RuntimeError(
        "SECRET_KEY is not set. It is required whenever DATABASE_URL points at a real database. "
        'Generate one with: python -c "import secrets; print(secrets.token_hex(32))"'
    )


SECRET_KEY = _load_secret_key()
