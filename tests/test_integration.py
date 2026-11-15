"""Integration tests: need Postgres and Neo4j running, and the pipeline to have run.

Run with:  pytest -m integration
"""

import pytest

pytestmark = pytest.mark.integration


def test_every_injected_scenario_is_caught():
    from plangraph.eval.scenario_recall import evaluate

    result = evaluate()
    assert result["recall"] == 1.0, result["scenarios"]


def test_read_session_rejects_writes():
    from neo4j.exceptions import ClientError

    from plangraph.db import run_read

    with pytest.raises(ClientError):
        run_read("CREATE (n:ShouldNotExist) RETURN n")


def test_core_query_finds_the_slipping_vendor():
    from plangraph.db import run_read
    from plangraph.graph import queries

    rows = run_read(queries.LATE_PO_EXPOSURE, territory=None, limit=50)
    assert any(r["vendor"] == "Custom Frames, Inc." for r in rows)


def test_offline_ask_returns_evidence():
    from plangraph.agent.graph import ask

    result = ask("Which late purchase orders hurt revenue the most?")
    assert result["mode"] in ("offline", "agent")
    assert result["steps"] and result["steps"][0]["rows"]
