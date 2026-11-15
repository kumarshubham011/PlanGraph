"""Safety checks for Cypher written by an LLM (the Text2Cypher fallback).

Defence in depth. A generated query must pass ALL of these layers:
1. validate_cypher: static checks on the text (this file).
2. Read-only session: Neo4j itself refuses writes (db.run_read).
3. Timeout: a slow query is cancelled (db.run_read).
4. Row limit: a LIMIT is always present, and results are trimmed before they
   go back to the LLM (clean_rows).
"""

import re

MAX_ROWS = 50
MAX_TEXT_CHARS = 300

# Anything that can change data, change the schema, or call arbitrary procedures.
FORBIDDEN = re.compile(
    r"\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|FOREACH|CALL|LOAD\s+CSV|GRANT|REVOKE|"
    r"ALTER|DENY|TERMINATE|USE)\b",
    re.IGNORECASE,
)
ALLOWED_START = re.compile(r"^\s*(MATCH|OPTIONAL\s+MATCH|WITH|UNWIND|RETURN)\b", re.IGNORECASE)
HAS_LIMIT = re.compile(r"\bLIMIT\s+(\d+)\s*$", re.IGNORECASE)


class UnsafeCypherError(ValueError):
    """Raised when a generated query fails a guardrail."""


def strip_code_fence(text: str) -> str:
    """LLMs often wrap queries in ```cypher ... ```. Remove the wrapper."""
    text = text.strip()
    match = re.search(r"```(?:cypher)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    return (match.group(1) if match else text).strip()


def validate_cypher(query: str) -> str:
    """Return a safe version of the query, or raise UnsafeCypherError."""
    query = strip_code_fence(query).rstrip(";").strip()
    without_strings = re.sub(r"'[^']*'|\"[^\"]*\"", "''", query)
    if ";" in without_strings:
        raise UnsafeCypherError("Only one statement is allowed")
    if not ALLOWED_START.match(query):
        raise UnsafeCypherError("Query must start with MATCH, WITH, UNWIND or RETURN")
    forbidden = FORBIDDEN.search(without_strings)
    if forbidden:
        raise UnsafeCypherError(f"Keyword not allowed: {forbidden.group(1).upper()}")
    if not re.search(r"\bRETURN\b", without_strings, re.IGNORECASE):
        raise UnsafeCypherError("Query must RETURN something")
    if "embedding" in without_strings.lower():
        raise UnsafeCypherError("The embedding property cannot be queried")
    limit = HAS_LIMIT.search(query)
    if not limit:
        return f"{query}\nLIMIT {MAX_ROWS}"
    if int(limit.group(1)) > MAX_ROWS:
        return HAS_LIMIT.sub(f"LIMIT {MAX_ROWS}", query)
    return query


def clean_value(value):
    """Make one value safe and small enough to show to an LLM or a user."""
    if isinstance(value, str):
        return value[:MAX_TEXT_CHARS]
    if isinstance(value, list):
        return [clean_value(v) for v in value[:10]]
    if isinstance(value, dict):
        return {k: clean_value(v) for k, v in value.items() if k != "embedding"}
    if isinstance(value, int | float | bool) or value is None:
        return value
    return str(value)  # dates and other Neo4j types


def clean_rows(rows: list[dict]) -> list[dict]:
    return [clean_value(row) for row in rows[:MAX_ROWS]]
