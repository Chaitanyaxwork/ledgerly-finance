"""
Startup schema check: creates missing tables AND adds missing columns.

Why this exists: `Base.metadata.create_all()` only creates tables that don't
exist yet. If a database file was made by an older version of the app (for
example, before `transactions.grace_period_days` was added), the old table is
left untouched, and every query then fails with
"no such column: transactions.grace_period_days" -> HTTP 500 -> the frontend
shows "Could not load data" and looks like it can't connect to the backend.

This adds any missing columns in place (no data loss) so old and new
databases both work. Alembic remains available for managed production deploys.
"""

import logging

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

log = logging.getLogger("ledgerly.schema")


def _default_clause(column) -> str:
    """SQL DEFAULT clause for a column, only when it has a simple constant default."""
    default = getattr(column.default, "arg", None) if column.default is not None else None
    if column.server_default is not None:
        return f" DEFAULT {column.server_default.arg}"
    if isinstance(default, bool):
        return f" DEFAULT {int(default)}"
    if isinstance(default, (int, float)):
        return f" DEFAULT {default}"
    if isinstance(default, str):
        escaped = default.replace("'", "''")
        return f" DEFAULT '{escaped}'"
    return ""


def sync_schema(engine: Engine, metadata) -> None:
    metadata.create_all(bind=engine)

    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    with engine.begin() as conn:
        for table in metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            present = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in present:
                    continue
                col_type = column.type.compile(dialect=engine.dialect)
                default = _default_clause(column)
                # Only enforce NOT NULL when we can backfill existing rows.
                not_null = " NOT NULL" if (default and not column.nullable) else ""
                ddl = f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {col_type}{default}{not_null}'
                log.warning("Upgrading database: %s", ddl)
                conn.execute(text(ddl))
