"""Named, parameterised Cypher templates.

These are the queries the alert engine, the API and the agent's tools run.
Keeping them here, written and tested by a person, is the main reliability
decision in this project: the LLM chooses WHICH template to run and with what
parameters, it does not write these queries itself.
"""

# The spec's core query: revenue exposed to late purchase orders.
# Late PO -> its parts -> every finished good that uses them -> open orders -> territory.
LATE_PO_EXPOSURE = """
MATCH (po:PurchaseOrder {is_late: true})-[l:CONTAINS]->(part:Product)
WHERE l.is_open
MATCH (po)-[:FROM]->(v:Vendor)
MATCH (fg:Product)-[:HAS_COMPONENT*0..4]->(part)
WITH DISTINCT po, v, fg
MATCH (so:SalesOrder {status: 'Open'})-[s:CONTAINS]->(fg)
MATCH (so)-[:IN_TERRITORY]->(t:Territory)
WHERE $territory IS NULL OR toLower(t.name) = toLower($territory)
RETURN po.uid AS po_uid, po.id AS late_po, v.name AS vendor, po.days_late AS days_late,
       t.name AS territory, count(DISTINCT fg) AS products_hit,
       round(sum(s.value)) AS revenue_exposed
ORDER BY revenue_exposed DESC LIMIT $limit
"""

OPEN_ALERTS = """
MATCH (a:Alert {status: 'open'})
WHERE ($type IS NULL OR a.type = $type)
  AND ($territory IS NULL OR toLower(a.details) CONTAINS toLower($territory))
RETURN a.uid AS uid, a.type AS type, a.severity AS severity, a.title AS title,
       a.summary AS summary, a.usd_at_risk AS usd_at_risk, a.usd_exposed AS usd_exposed,
       a.early_warning_days AS early_warning_days
ORDER BY a.usd_at_risk DESC, a.severity LIMIT $limit
"""

# Where is a part used? Finished goods at any BOM depth, with their open order value.
WHERE_USED = """
MATCH (part:Product)
WHERE toLower(part.name) CONTAINS toLower($product) OR part.sku = $product
   OR part.uid = $product
MATCH path = (fg:Product)-[:HAS_COMPONENT*1..4]->(part)
WHERE fg.is_finished_good
WITH part, fg, sum(reduce(q = 1.0, r IN relationships(path) | q * r.qty)) AS qty_per_unit
OPTIONAL MATCH (so:SalesOrder {status: 'Open'})-[l:CONTAINS]->(fg)
RETURN part.uid AS part_uid, part.name AS part, fg.uid AS product_uid, fg.name AS product,
       qty_per_unit, fg.on_hand AS product_on_hand,
       round(coalesce(sum(l.value), 0)) AS open_order_value
ORDER BY open_order_value DESC LIMIT $limit
"""

# Who else can supply a part? Cheapest reliable source first.
ALTERNATE_VENDORS = """
MATCH (v:Vendor)-[s:SUPPLIES]->(p:Product)
WHERE toLower(p.name) CONTAINS toLower($product) OR p.sku = $product OR p.uid = $product
OPTIONAL MATCH (v)<-[:FROM]-(po:PurchaseOrder {status: 'Received'})
WITH p, v, s, count(po) AS past_orders,
     sum(CASE WHEN po.days_late > 0 THEN 1 ELSE 0 END) AS late_orders
RETURN p.uid AS product_uid, p.name AS product, v.uid AS vendor_uid, v.name AS vendor,
       s.lead_days AS lead_days, s.price AS price, v.rating AS credit_rating,
       v.is_active AS is_active, past_orders,
       CASE WHEN past_orders > 0 THEN round(100.0 * late_orders / past_orders) END AS late_pct
ORDER BY p.name, s.lead_days, s.price LIMIT $limit
"""

# Target vs shipped vs open vs at risk, per territory, for the current quota period.
TARGET_TRACKER = """
MATCH (t:Territory)<-[:COVERS]-(sp:SalesPerson)-[:HAS_TARGET]->(tg:Target {is_current: true})
OPTIONAL MATCH (so:SalesOrder)-[:SOLD_BY]->(sp)
WHERE so.order_date >= tg.period_start AND so.order_date <= tg.period_end
WITH t, tg,
     sum(CASE WHEN so.status = 'Shipped' THEN so.total_value ELSE 0 END) AS shipped,
     sum(CASE WHEN so.status = 'Open' THEN so.total_value ELSE 0 END) AS open
WITH t, collect(tg.uid) AS target_uids, sum(tg.amount) AS target, sum(shipped) AS shipped,
     sum(open) AS open, sum(coalesce(tg.usd_at_risk, 0)) AS at_risk,
     min(tg.period_start) AS period_start, max(tg.period_end) AS period_end
WHERE $territory IS NULL OR toLower(t.name) = toLower($territory)
RETURN t.id AS territory_id, t.name AS territory, target_uids, round(target) AS target,
       round(shipped) AS shipped, round(open) AS open, round(at_risk) AS at_risk,
       period_start, period_end
ORDER BY territory
"""

# Scrap by work centre and reason over the last N days.
SCRAP_BY_LOCATION = """
MATCH (w:WorkOrder)-[:ROUTED_THROUGH]->(loc:Location)
WHERE w.end_date > date($as_of) - duration({days: $days}) AND w.scrapped_qty > 0
MATCH (w)-[:SCRAPPED_FOR]->(reason:ScrapReason)
MATCH (w)-[:PRODUCES]->(p:Product)
RETURN loc.uid AS location_uid, loc.name AS location, reason.name AS reason,
       count(w) AS work_orders, sum(w.scrapped_qty) AS scrapped_units,
       count(DISTINCT p) AS products, collect(DISTINCT p.name)[..5] AS example_products
ORDER BY scrapped_units DESC LIMIT $limit
"""

# Stock position of one product: where it is, what is on order, what demand is expected.
STOCK_COVER = """
MATCH (p:Product)
WHERE toLower(p.name) CONTAINS toLower($product) OR p.sku = $product OR p.uid = $product
OPTIONAL MATCH (p)-[s:STOCKED_AT]->(loc:Location)
WITH p, collect({location: loc.name, on_hand: s.on_hand}) AS stock
OPTIONAL MATCH (po:PurchaseOrder)-[l:CONTAINS]->(p) WHERE l.is_open
WITH p, stock, collect({po: po.id, qty: l.qty, due_date: toString(l.due_date),
                        days_late: l.days_late}) AS on_order
RETURN p.uid AS product_uid, p.name AS product, p.on_hand AS on_hand, stock, on_order,
       p.weekly_demand AS weekly_demand, p.weeks_of_cover AS weeks_of_cover,
       p.stockout_week AS stockout_week
LIMIT $limit
"""

# ---- Helpers used by the alert engine ----

OPEN_PO_PARTS = """
MATCH (:PurchaseOrder {uid: $po_uid})-[l:CONTAINS]->(p:Product)
WHERE l.is_open
RETURN DISTINCT p.uid AS uid
"""

TOP_TERRITORY_FOR_PRODUCTS = """
MATCH (so:SalesOrder {status: 'Open'})-[l:CONTAINS]->(p:Product) WHERE p.uid IN $product_uids
MATCH (so)-[:IN_TERRITORY]->(t:Territory)
RETURN t.name AS territory, sum(l.qty) AS units
ORDER BY units DESC LIMIT 1
"""
