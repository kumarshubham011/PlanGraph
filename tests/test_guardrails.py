import pytest

from plangraph.agent.guardrails import (
    MAX_ROWS,
    UnsafeCypherError,
    clean_rows,
    validate_cypher,
)


def test_safe_query_gets_a_limit():
    query = validate_cypher("MATCH (p:Product) RETURN p.name AS name")
    assert query.endswith(f"LIMIT {MAX_ROWS}")


def test_existing_small_limit_is_kept_and_code_fence_removed():
    query = validate_cypher("```cypher\nMATCH (p:Product) RETURN p.name LIMIT 5;\n```")
    assert query == "MATCH (p:Product) RETURN p.name LIMIT 5"


def test_huge_limit_is_reduced():
    assert validate_cypher("MATCH (n) RETURN n.uid LIMIT 100000").endswith(f"LIMIT {MAX_ROWS}")


@pytest.mark.parametrize(
    "query",
    [
        "MATCH (n) DETACH DELETE n",
        "CREATE (n:Hack) RETURN n",
        "MATCH (p:Product) SET p.on_hand = 0 RETURN p",
        "MATCH (n) RETURN n; MATCH (m) DELETE m",
        "CALL dbms.components() YIELD name RETURN name",
        "DROP INDEX product_name",
        "MATCH (c:DocChunk) RETURN c.embedding",
        "LOAD CSV FROM 'file:///etc/passwd' AS row RETURN row",
        "MATCH (p:Product)",  # nothing returned
    ],
)
def test_unsafe_queries_are_rejected(query):
    with pytest.raises(UnsafeCypherError):
        validate_cypher(query)


def test_keywords_inside_strings_are_allowed():
    validate_cypher("MATCH (p:Product) WHERE p.name CONTAINS 'Seat Set' RETURN p.name")


def test_clean_rows_trims_and_drops_embeddings():
    rows = [{"text": "x" * 1000, "node": {"embedding": [0.1] * 768, "name": "a"}}] * 100
    cleaned = clean_rows(rows)
    assert len(cleaned) == MAX_ROWS
    assert len(cleaned[0]["text"]) == 300
    assert "embedding" not in cleaned[0]["node"]
