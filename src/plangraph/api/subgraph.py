"""Build the small subgraph the UI draws for one node (the "impact graph" view)."""

from plangraph.agent.guardrails import clean_value
from plangraph.db import run_read

# The node and its direct neighbours.
NEIGHBOURS = """
MATCH (n {uid: $uid})
OPTIONAL MATCH (n)-[]-(m)
WITH n, collect(DISTINCT m)[..$limit] AS around
UNWIND [n] + around AS node
RETURN DISTINCT node.uid AS uid, labels(node)[0] AS label, properties(node) AS props
"""

# For an alert: the BOM path from each part it is about up to each finished good it hits.
# These in-between assemblies are what make the picture an evidence path.
ALERT_BOM_PATHS = """
MATCH (a:Alert {uid: $uid})-[:ABOUT]->(part:Product), (a)-[:ABOUT]->(fg:Product)
WHERE part <> fg AND NOT part.is_finished_good AND fg.is_finished_good
MATCH path = shortestPath((fg)-[:HAS_COMPONENT*1..4]->(part))
UNWIND nodes(path) AS node
RETURN DISTINCT node.uid AS uid, labels(node)[0] AS label, properties(node) AS props
LIMIT 40
"""

# For an alert about a purchase order: the vendor and the open parts on that order.
ALERT_PO_CONTEXT = """
MATCH (:Alert {uid: $uid})-[:ABOUT]->(po:PurchaseOrder)-[l:CONTAINS]->(part:Product)
WHERE l.is_open
RETURN DISTINCT part.uid AS uid, labels(part)[0] AS label, properties(part) AS props
"""

EDGES = """
MATCH (a)-[r]->(b) WHERE a.uid IN $uids AND b.uid IN $uids
RETURN DISTINCT a.uid AS source, b.uid AS target, type(r) AS type
"""


def node_title(label: str, props: dict) -> str:
    name = props.get("name") or props.get("title")
    if name:
        return str(name)
    if label == "Target":
        return f"Target ${props.get('amount', 0):,.0f}"
    return f"{label} {props.get('id', '')}".strip()


def build_subgraph(uid: str, limit: int = 40) -> dict:
    rows = run_read(NEIGHBOURS, uid=uid, limit=limit)
    if uid.startswith("Alert:"):
        rows += run_read(ALERT_PO_CONTEXT, uid=uid) + run_read(ALERT_BOM_PATHS, uid=uid)
    nodes = {}
    for row in rows:
        if row["uid"] is None:
            continue
        props = clean_value({k: v for k, v in row["props"].items() if k != "embedding"})
        nodes[row["uid"]] = {
            "id": row["uid"],
            "label": row["label"],
            "title": node_title(row["label"], props),
            "props": props,
        }
    edges = run_read(EDGES, uids=list(nodes))
    return {"nodes": list(nodes.values()), "edges": edges}
