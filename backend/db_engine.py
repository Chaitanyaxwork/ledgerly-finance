"""SQLAlchemy engine/session setup. Works against SQLite (local dev) or
PostgreSQL (production) purely based on DATABASE_URL — nothing else in the
codebase needs to know which one it's talking to."""

from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from . import config

connect_args = {}
if config.DATABASE_URL.startswith("sqlite"):
    # Needed for SQLite + multiple threads (FastAPI's default worker model).
    connect_args = {"check_same_thread": False}
    # Make sure the parent directory for the SQLite file exists.
    if ":///" in config.DATABASE_URL:
        db_path = config.DATABASE_URL.split(":///", 1)[1]
        if db_path and db_path != ":memory:":
            Path(db_path).resolve().parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(config.DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)

if config.DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection, _record):
        # SQLite ignores ON DELETE CASCADE unless this is switched on per connection.
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    """FastAPI dependency: yields a session and always closes it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
