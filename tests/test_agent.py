"""Agent tests with a scripted fake LLM, so no API key or database is needed."""

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import tool

from plangraph.agent import offline
from plangraph.agent.graph import build_agent, collect_uids, summarise_run
from plangraph.embeddings import hash_embed
from plangraph.graph.documents import split_into_chunks


class FakeLLM(FakeMessagesListChatModel):
    """Replays a fixed list of replies. bind_tools is a no-op."""

    def bind_tools(self, tools, **kwargs):
        return self


@tool(response_format="content_and_artifact")
def fake_alerts(territory: str | None = None):
    """List open alerts."""
    rows = [{"uid": "Alert:late_po:1", "title": "PO 1 is late", "usd_at_risk": 1000}]
    return str(rows), {"cypher": "MATCH (a:Alert) RETURN a", "params": {}, "rows": rows}


def test_agent_loop_calls_tool_then_answers():
    llm = FakeLLM(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "fake_alerts", "args": {"territory": "Canada"}, "id": "call-1"}
                ],
            ),
            AIMessage(content="PO 1 is late, with $1,000 at risk."),
        ]
    )
    agent = build_agent(llm, tools=[fake_alerts])
    result = agent.invoke({"messages": [("user", "What is late?")]})
    summary = summarise_run(result["messages"], mode="agent")

    assert summary["answer"] == "PO 1 is late, with $1,000 at risk."
    assert summary["steps"][0]["tool"] == "fake_alerts"
    assert summary["steps"][0]["args"] == {"territory": "Canada"}
    assert summary["steps"][0]["cypher"].startswith("MATCH")
    assert summary["evidence_uids"] == ["Alert:late_po:1"]


def test_collect_uids_finds_nested_uids():
    rows = [{"po_uid": "PurchaseOrder:1", "nested": [{"vendor_uid": "Vendor:9"}], "name": "x"}]
    assert collect_uids(rows) == {"PurchaseOrder:1", "Vendor:9"}


def test_offline_router_picks_tool_from_keywords(monkeypatch):
    names = {"Territory": ("Canada",), "Product": ("Metal Sheet 2", "Lock Ring")}
    monkeypatch.setattr(offline, "known_names", lambda label: names[label])
    assert offline.route("Why is Canada under quota?") == (
        "target_variance",
        {"territory": "Canada"},
    )
    assert offline.route("Is there an alternate vendor for Metal Sheet 2?") == (
        "alternate_vendors",
        {"product": "Metal Sheet 2"},
    )
    assert offline.route("What is our expedite policy?")[0] == "search_documents"
    assert offline.route("What needs my attention today?")[0] == "open_alerts"
    # A product tool without a product falls back to the alert list.
    assert offline.route("how is our stock?")[0] == "open_alerts"


def test_hash_embedding_puts_similar_texts_closer():
    def similarity(a, b):
        return sum(x * y for x, y in zip(hash_embed(a), hash_embed(b), strict=True))

    query = "expedite policy premium freight"
    assert similarity(query, "Expedite policy: premium freight costs 10%") > similarity(
        query, "Scrap review at the paint work centre"
    )


def test_split_into_chunks_respects_size():
    text = "\n\n".join(["word " * 60] * 6)  # six paragraphs of ~300 characters
    chunks = split_into_chunks(text, max_chars=700)
    assert len(chunks) == 3 and all(len(c) <= 700 for c in chunks)
