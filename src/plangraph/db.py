"""Database connections. Everything that talks to Postgres or Neo4j goes through here."""

from functools import lru_cache

import pandas as pd
from neo4j import READ_ACCESS, Driver, GraphDatabase, Query
from sqlalchemy import Engine, create_engine

from plangraph.config import get_settings


@lru_cache
def pg_engine() -> Engine:
    return create_engine(get_settings().postgres_url)


def read_sql(query: str, **params) -> pd.DataFrame:
    """Run a SELECT and return a DataFrame. Use %(name)s placeholders for params."""
    return pd.read_sql(query, pg_engine(), params=params or None)


@lru_cache
def neo4j_driver() -> Driver:
    s = get_settings()
    return GraphDatabase.driver(s.neo4j_uri, auth=(s.neo4j_user, s.neo4j_password))


def run_read(cypher: str, timeout: float = 30, **params) -> list[dict]:
    """Run a Cypher query in a READ-ONLY session. Neo4j itself rejects any write."""
    with neo4j_driver().session(default_access_mode=READ_ACCESS) as session:
        return [r.data() for r in session.run(Query(cypher, timeout=timeout), params)]


def run_write(cypher: str, **params) -> list[dict]:
    with neo4j_driver().session() as session:
        return [r.data() for r in session.run(cypher, params)]
