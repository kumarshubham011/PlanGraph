"""Offline mode: answer without an LLM.

When no API key is set, the Ask page still works in a limited way: simple keyword
rules pick one of the templated tools, and the rows are shown as the answer.
It cannot reason over several steps or write prose, but it is free, instant and
deterministic, and it is also the "no LLM" baseline in the evaluation.
"""

from functools import lru_cache

from plangraph.agent import tools
from plangraph.db import run_read

# (keywords, tool name). The first rule with a matching keyword wins.
ROUTES = [
    (
        ("policy", "procedure", "sop", "email", "approve", "approval", "allowed", "rule", "said"),
        "search_documents",
    ),
    (
        (
            "alternate",
            "alternative",
            "other vendor",
            "re-source",
            "resource",
            "who else",
            "who supplies",
            "who can supply",
        ),
        "alternate_vendors",
    ),
    (("where used", "where is", "which products use", "uses", "goes into"), "where_used"),
    (("scrap", "quality", "work centre", "work center"), "scrap_by_location"),
    (("stock", "inventory", "cover", "run out", "on hand"), "stock_cover"),
    (("target", "quota", "under", "miss", "tracking"), "target_variance"),
    (("late", "purchase order", " po", "overdue", "vendor"), "late_po_exposure"),
]


@lru_cache
def known_names(label: str) -> tuple[str, ...]:
    rows = run_read(f"MATCH (n:{label}) RETURN n.name AS name")
    return tuple(sorted((r["name"] for r in rows), key=len, reverse=True))  # longest first


def find_name(question: str, label: str) -> str | None:
    """The longest Product/Territory name that appears in the question, if any."""
    lowered = question.lower()
    return next((n for n in known_names(label) if n.lower() in lowered), None)


def route(question: str) -> tuple[str, dict]:
    """Pick a tool and its arguments from the words in the question."""
    lowered = f" {question.lower()} "
    territory = find_name(question, "Territory")
    product = find_name(question, "Product")
    name = next((tool for words, tool in ROUTES if any(w in lowered for w in words)), "open_alerts")
    if name in ("alternate_vendors", "where_used", "stock_cover"):
        if product is None:  # these tools need a product; fall back to the alert list
            return "open_alerts", {"territory": territory}
        return name, {"product": product}
    if name == "search_documents":
        return name, {"query": question}
    if name == "scrap_by_location":
        return name, {}
    return name, {"territory": territory}


def format_rows(rows: list[dict], limit: int = 5) -> str:
    if not rows:
        return "No matching data found."
    lines = []
    for row in rows[:limit]:
        shown = {k: v for k, v in row.items() if not k.endswith("uid") and v not in (None, [])}
        lines.append("- " + "; ".join(f"{k}: {v}" for k, v in shown.items()))
    more = f"\n(+{len(rows) - limit} more rows)" if len(rows) > limit else ""
    return "\n".join(lines) + more


def answer(question: str) -> dict:
    from plangraph.agent.graph import collect_uids  # avoid a circular import

    name, args = route(question)
    tool = getattr(tools, name)
    _, artifact = tool.func(**args)
    rows = artifact["rows"]
    text = (
        f"Offline mode (no LLM key set). I ran the `{name}` query for you.\n\n{format_rows(rows)}"
    )
    step = {
        "tool": name,
        "args": args,
        "cypher": artifact["cypher"],
        "generated": False,
        "error": None,
        "rows": rows,
    }
    return {
        "answer": text,
        "mode": "offline",
        "steps": [step],
        "evidence_uids": sorted(collect_uids(rows)),
        "input_tokens": 0,
        "output_tokens": 0,
    }
