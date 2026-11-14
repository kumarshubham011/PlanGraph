"""Build the staging schema by running staging.sql inside Postgres."""

import logging
from pathlib import Path

from sqlalchemy import text

from plangraph.db import pg_engine

log = logging.getLogger(__name__)

SQL_FILE = Path(__file__).with_name("staging.sql")


def split_statements(sql: str) -> list[str]:
    """Split a SQL file on ';' and drop comment-only or empty pieces."""
    statements = []
    for piece in sql.split(";"):
        lines = [ln for ln in piece.splitlines() if ln.strip() and not ln.strip().startswith("--")]
        if lines:
            statements.append("\n".join(lines))
    return statements


def build_staging() -> None:
    with pg_engine().begin() as conn:
        for statement in split_statements(SQL_FILE.read_text()):
            conn.execute(text(statement))
    log.info("Staging schema rebuilt")
