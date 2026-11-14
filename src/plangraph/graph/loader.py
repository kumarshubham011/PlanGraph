"""Load the staging tables into Neo4j as a knowledge graph.

The pattern is the same for every node and relationship type:
1. read rows from Postgres with a SELECT,
2. send them to Neo4j in batches,
3. `UNWIND $rows AS row MERGE ...` creates or updates one item per row.

MERGE makes the load idempotent: running it twice gives the same graph.
"""

import logging
from pathlib import Path

import pandas as pd

from plangraph.db import neo4j_driver, read_sql, run_read

log = logging.getLogger(__name__)

SCHEMA_FILE = Path(__file__).with_name("schema.cypher")
BATCH_SIZE = 5000
# The graph holds the current operational picture: one year of orders.
# Older history stays in Postgres, where the models read it for training.
HISTORY_DAYS = 365

# label -> SELECT that returns `uid` plus the node's properties
NODE_QUERIES = {
    "Product": """
        SELECT 'Product:' || product_id AS uid, product_id AS id, sku, name, category,
               subcategory, list_price, standard_cost, is_finished_good, is_manufactured,
               is_purchased, on_hand
        FROM stg.product""",
    "Vendor": """
        SELECT 'Vendor:' || vendor_id AS uid, vendor_id AS id, name,
               credit_rating AS rating, is_preferred, is_active
        FROM stg.vendor""",
    "Location": """
        SELECT 'Location:' || location_id AS uid, location_id AS id, name
        FROM stg.location""",
    "Territory": """
        SELECT 'Territory:' || territory_id AS uid, territory_id AS id, name,
               region_group AS region
        FROM stg.territory""",
    "SalesPerson": """
        SELECT 'SalesPerson:' || sales_person_id AS uid, sales_person_id AS id,
               'Sales rep ' || sales_person_id AS name
        FROM stg.sales_person""",
    "Target": """
        SELECT 'Target:' || target_id AS uid, target_id AS id, period_start, period_end,
               amount, is_current
        FROM stg.target""",
    "ScrapReason": """
        SELECT 'ScrapReason:' || scrap_reason_id AS uid, scrap_reason_id AS id, name
        FROM stg.scrap_reason""",
    "PurchaseOrder": f"""
        SELECT 'PurchaseOrder:' || purchase_order_id AS uid, purchase_order_id AS id, status,
               order_date, due_date, received_date, total_value, days_late, is_late
        FROM stg.purchase_order, meta.run_info r
        WHERE order_date > r.as_of - {HISTORY_DAYS}""",
    "SalesOrder": f"""
        SELECT 'SalesOrder:' || sales_order_id AS uid, sales_order_id AS id, status,
               order_date, due_date, ship_date, is_online, total_value
        FROM stg.sales_order, meta.run_info r
        WHERE order_date > r.as_of - {HISTORY_DAYS}""",
    "WorkOrder": f"""
        SELECT 'WorkOrder:' || work_order_id AS uid, work_order_id AS id, status, order_qty,
               scrapped_qty, start_date, end_date, due_date
        FROM stg.work_order, meta.run_info r
        WHERE start_date > r.as_of - {HISTORY_DAYS}""",
}

# (from label, relationship type, to label) -> SELECT returning from_uid, to_uid,
# an optional `key` (when two nodes can be linked more than once) and properties.
REL_QUERIES = {
    ("Product", "HAS_COMPONENT", "Product"): """
        SELECT 'Product:' || assembly_id AS from_uid, 'Product:' || component_id AS to_uid, qty
        FROM stg.bom""",
    ("Vendor", "SUPPLIES", "Product"): """
        SELECT 'Vendor:' || vendor_id AS from_uid, 'Product:' || product_id AS to_uid,
               lead_days, price
        FROM stg.vendor_product""",
    ("Product", "STOCKED_AT", "Location"): """
        SELECT 'Product:' || product_id AS from_uid, 'Location:' || location_id AS to_uid,
               on_hand
        FROM stg.inventory""",
    ("SalesPerson", "COVERS", "Territory"): """
        SELECT 'SalesPerson:' || sales_person_id AS from_uid,
               'Territory:' || home_territory_id AS to_uid
        FROM stg.sales_person""",
    ("SalesPerson", "HAS_TARGET", "Target"): """
        SELECT 'SalesPerson:' || sales_person_id AS from_uid, 'Target:' || target_id AS to_uid
        FROM stg.target""",
    ("PurchaseOrder", "FROM", "Vendor"): f"""
        SELECT 'PurchaseOrder:' || purchase_order_id AS from_uid,
               'Vendor:' || vendor_id AS to_uid
        FROM stg.purchase_order, meta.run_info r
        WHERE order_date > r.as_of - {HISTORY_DAYS}""",
    ("PurchaseOrder", "CONTAINS", "Product"): f"""
        SELECT 'PurchaseOrder:' || l.purchase_order_id AS from_uid,
               'Product:' || l.product_id AS to_uid, l.line_id AS key, l.qty, l.received_qty,
               l.unit_price, l.due_date, l.received_date, l.is_open, l.days_late
        FROM stg.purchase_order_line l
        JOIN stg.purchase_order o USING (purchase_order_id), meta.run_info r
        WHERE o.order_date > r.as_of - {HISTORY_DAYS}""",
    ("SalesOrder", "CONTAINS", "Product"): f"""
        SELECT 'SalesOrder:' || l.sales_order_id AS from_uid,
               'Product:' || l.product_id AS to_uid, l.line_id AS key, l.qty, l.value
        FROM stg.sales_order_line l
        JOIN stg.sales_order o USING (sales_order_id), meta.run_info r
        WHERE o.order_date > r.as_of - {HISTORY_DAYS}""",
    ("SalesOrder", "IN_TERRITORY", "Territory"): f"""
        SELECT 'SalesOrder:' || sales_order_id AS from_uid,
               'Territory:' || territory_id AS to_uid
        FROM stg.sales_order, meta.run_info r
        WHERE order_date > r.as_of - {HISTORY_DAYS}""",
    ("SalesOrder", "SOLD_BY", "SalesPerson"): f"""
        SELECT 'SalesOrder:' || sales_order_id AS from_uid,
               'SalesPerson:' || sales_person_id AS to_uid
        FROM stg.sales_order, meta.run_info r
        WHERE order_date > r.as_of - {HISTORY_DAYS} AND sales_person_id IS NOT NULL""",
    ("WorkOrder", "PRODUCES", "Product"): f"""
        SELECT 'WorkOrder:' || work_order_id AS from_uid, 'Product:' || product_id AS to_uid
        FROM stg.work_order, meta.run_info r
        WHERE start_date > r.as_of - {HISTORY_DAYS}""",
    ("WorkOrder", "SCRAPPED_FOR", "ScrapReason"): f"""
        SELECT 'WorkOrder:' || work_order_id AS from_uid,
               'ScrapReason:' || scrap_reason_id AS to_uid
        FROM stg.work_order, meta.run_info r
        WHERE start_date > r.as_of - {HISTORY_DAYS} AND scrap_reason_id IS NOT NULL""",
    ("WorkOrder", "ROUTED_THROUGH", "Location"): f"""
        SELECT 'WorkOrder:' || l.work_order_id AS from_uid,
               'Location:' || l.location_id AS to_uid
        FROM stg.work_order_location l
        JOIN stg.work_order w USING (work_order_id), meta.run_info r
        WHERE w.start_date > r.as_of - {HISTORY_DAYS}""",
}


def to_rows(frame: pd.DataFrame) -> list[dict]:
    """DataFrame -> list of dicts Neo4j accepts (NaN/NaT become None)."""
    return frame.astype(object).where(frame.notna(), None).to_dict("records")


def write_batches(cypher: str, rows: list[dict]) -> None:
    with neo4j_driver().session() as session:
        for start in range(0, len(rows), BATCH_SIZE):
            session.run(cypher, rows=rows[start : start + BATCH_SIZE]).consume()


def merge_nodes(label: str, rows: list[dict]) -> None:
    write_batches(f"UNWIND $rows AS row MERGE (n:{label} {{uid: row.uid}}) SET n += row", rows)


def merge_relationships(from_label: str, rel_type: str, to_label: str, rows: list[dict]) -> None:
    """Rows need from_uid and to_uid. Everything else becomes relationship properties."""
    has_key = bool(rows) and "key" in rows[0]
    key = " {key: row.key}" if has_key else ""
    cypher = f"""
        UNWIND $rows AS row
        MATCH (a:{from_label} {{uid: row.from_uid}})
        MATCH (b:{to_label} {{uid: row.to_uid}})
        MERGE (a)-[r:{rel_type}{key}]->(b)
        SET r += row.props"""
    packed = [
        {
            "from_uid": r["from_uid"],
            "to_uid": r["to_uid"],
            "key": r.get("key"),
            "props": {k: v for k, v in r.items() if k not in ("from_uid", "to_uid", "key")},
        }
        for r in rows
    ]
    write_batches(cypher, packed)


def apply_schema() -> None:
    statements = [s.strip() for s in SCHEMA_FILE.read_text().split(";")]
    with neo4j_driver().session() as session:
        for statement in statements:
            lines = [ln for ln in statement.splitlines() if not ln.startswith("//")]
            if "".join(lines).strip():
                session.run("\n".join(lines)).consume()


def clear_graph() -> None:
    """Delete everything except Alerts and Decisions (the audit trail must survive reloads)."""
    with neo4j_driver().session() as session:
        session.run("""
            MATCH (n) WHERE NOT n:Alert AND NOT n:Decision
            CALL { WITH n DETACH DELETE n } IN TRANSACTIONS OF 10000 ROWS""").consume()


def verify_counts(expected: dict[str, int]) -> None:
    """Compare what Neo4j holds with what we sent. Raises if anything is missing."""
    problems = []
    for name, count in expected.items():
        if ":" in name:  # relationship, named "From:TYPE:To"
            a, rel, b = name.split(":")
            query = f"MATCH (:{a})-[r:{rel}]->(:{b}) RETURN count(r) AS n"
        else:
            query = f"MATCH (n:{name}) RETURN count(n) AS n"
        actual = run_read(query)[0]["n"]
        log.info("%-40s staging %7d   graph %7d", name, count, actual)
        if actual != count:
            problems.append(name)
    if problems:
        raise ValueError(f"Graph counts do not match staging for: {problems}")


def load_graph() -> None:
    apply_schema()
    clear_graph()
    expected = {}
    for label, query in NODE_QUERIES.items():
        rows = to_rows(read_sql(query))
        merge_nodes(label, rows)
        expected[label] = len(rows)
    for (from_label, rel_type, to_label), query in REL_QUERIES.items():
        rows = to_rows(read_sql(query))
        merge_relationships(from_label, rel_type, to_label, rows)
        expected[f"{from_label}:{rel_type}:{to_label}"] = len(rows)
    verify_counts(expected)
