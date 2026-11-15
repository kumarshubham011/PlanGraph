"""The agent's tools.

Each tool is a small function that runs ONE fixed, parameterised Cypher template
in a read-only session. The LLM picks a tool and fills in the parameters; it
never writes these queries. Every tool returns:
- content: the rows as JSON text, for the LLM to read
- artifact: the Cypher that ran, its parameters and the rows, for the user to audit
"""

import json

from langchain_core.tools import tool

from plangraph.agent import text2cypher
from plangraph.agent.guardrails import clean_rows
from plangraph.agent.llm import get_llm
from plangraph.db import as_of_date, run_read
from plangraph.graph import queries
from plangraph.graph.documents import search_documents as vector_search

ROW_LIMIT = 15


def run_template(cypher: str, **params) -> tuple[str, dict]:
    """Run a template and package the result for the LLM (content) and the UI (artifact)."""
    rows = clean_rows(run_read(cypher, **params))
    artifact = {"cypher": cypher.strip(), "params": params, "rows": rows}
    return json.dumps(rows, default=str), artifact


@tool(response_format="content_and_artifact")
def open_alerts(alert_type: str | None = None, territory: str | None = None):
    """List open alerts, biggest dollars at risk first. Use this first for questions about
    what is going wrong or what needs attention.
    alert_type: late_po | po_at_risk | stockout | demand_surge | scrap_spike | target_gap.
    territory: optional territory name, e.g. 'Southwest'."""
    return run_template(queries.OPEN_ALERTS, type=alert_type, territory=territory, limit=ROW_LIMIT)


@tool(response_format="content_and_artifact")
def late_po_exposure(territory: str | None = None):
    """Late purchase orders and the open sales order revenue EXPOSED to each, by territory.
    Exposed means every open order for a product that uses the late parts. It is an upper
    bound; the alert's usd_at_risk is the part stock cannot cover."""
    return run_template(queries.LATE_PO_EXPOSURE, territory=territory, limit=ROW_LIMIT)


@tool(response_format="content_and_artifact")
def where_used(product: str):
    """Which finished goods use a given part (at any bill-of-materials depth), with the
    open order value of each. product: a part name, SKU or uid, e.g. 'Metal Sheet 2'."""
    return run_template(queries.WHERE_USED, product=product, limit=ROW_LIMIT)


@tool(response_format="content_and_artifact")
def alternate_vendors(product: str):
    """Vendors that can supply a part, with lead time, price, credit rating and late history.
    Use for re-sourcing questions. product: a part name, SKU or uid."""
    return run_template(queries.ALTERNATE_VENDORS, product=product, limit=ROW_LIMIT)


@tool(response_format="content_and_artifact")
def target_variance(territory: str | None = None):
    """Sales target vs shipped revenue, open orders and supply-blocked revenue (at_risk) per
    territory for the current quota period. Projected = shipped + open - at_risk. If a
    territory is under target and at_risk is small, the gap is a demand problem, not supply."""
    return run_template(queries.TARGET_TRACKER, territory=territory)


@tool(response_format="content_and_artifact")
def scrap_by_location(days: int = 21):
    """Scrapped units by work centre (location) and scrap reason over the last `days` days,
    with example products. Use for production quality questions."""
    return run_template(queries.SCRAP_BY_LOCATION, days=days, as_of=as_of_date(), limit=ROW_LIMIT)


@tool(response_format="content_and_artifact")
def stock_cover(product: str):
    """Stock position of a product: on hand by location, open purchase orders, forecast weekly
    demand, weeks of cover and projected stockout week. product: a name, SKU or uid."""
    return run_template(queries.STOCK_COVER, product=product, limit=5)


@tool(response_format="content_and_artifact")
def search_documents(query: str):
    """Search company documents: policies, procedures (SOPs) and vendor emails. Use for
    questions about rules, approvals, or what a vendor or colleague said."""
    rows = clean_rows(vector_search(query))
    artifact = {
        "cypher": "vector search on DocChunk.embedding",
        "params": {"query": query},
        "rows": rows,
    }
    return json.dumps(rows, default=str), artifact


@tool(response_format="content_and_artifact")
def graph_query(question: str):
    """LAST RESORT. Answers a question by writing a new read-only Cypher query. Use only when
    no other tool fits. question: a precise, self-contained question in plain English."""
    result = text2cypher.generate_and_run(question, get_llm())
    artifact = {
        "cypher": result["cypher"],
        "params": {},
        "rows": result.get("rows", []),
        "generated": True,
        "error": result.get("error"),
    }
    return json.dumps(result.get("rows", {"error": result.get("error")}), default=str), artifact


TEMPLATE_TOOLS = [
    open_alerts,
    late_po_exposure,
    where_used,
    alternate_vendors,
    target_variance,
    scrap_by_location,
    stock_cover,
    search_documents,
]
ALL_TOOLS = [*TEMPLATE_TOOLS, graph_query]
