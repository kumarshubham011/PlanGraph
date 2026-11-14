"""Write model outputs back into the graph, so questions can use them like any other fact."""

import logging

from plangraph.db import read_sql, run_write
from plangraph.graph.loader import merge_nodes, merge_relationships, to_rows

log = logging.getLogger(__name__)

FORECAST_SQL = """
    SELECT 'Forecast:' || product_id || ':' || period_start AS uid, product_id,
           period_start, period_end, horizon, ROUND(qty::numeric, 1)::float AS qty,
           ROUND(p10::numeric, 1)::float AS p10, ROUND(p90::numeric, 1)::float AS p90
    FROM ml.demand_forecast"""


def write_forecasts() -> None:
    run_write("MATCH (f:Forecast) DETACH DELETE f")
    forecasts = read_sql(FORECAST_SQL)
    merge_nodes("Forecast", to_rows(forecasts.drop(columns="product_id")))
    links = forecasts.assign(
        from_uid="Product:" + forecasts["product_id"].astype(str), to_uid=forecasts["uid"]
    )[["from_uid", "to_uid"]]
    merge_relationships("Product", "HAS_FORECAST", "Forecast", to_rows(links))
    log.info("Wrote %d Forecast nodes", len(forecasts))


def write_po_risk() -> None:
    rows = to_rows(
        read_sql(
            "SELECT 'PurchaseOrder:' || purchase_order_id AS uid, "
            "ROUND(late_risk::numeric, 3)::float AS late_risk FROM ml.po_risk"
        )
    )
    run_write(
        "UNWIND $rows AS row MATCH (po:PurchaseOrder {uid: row.uid}) "
        "SET po.late_risk = row.late_risk",
        rows=rows,
    )
    log.info("Wrote late_risk on %d open purchase orders", len(rows))


def write_inventory_projection() -> None:
    rows = to_rows(
        read_sql(
            "SELECT 'Product:' || product_id AS uid, weekly_demand, weeks_of_cover, stockout_week "
            "FROM ml.inventory_projection"
        )
    )
    run_write(
        "UNWIND $rows AS row MATCH (p:Product {uid: row.uid}) "
        "SET p.weekly_demand = row.weekly_demand, p.weeks_of_cover = row.weeks_of_cover, "
        "p.stockout_week = row.stockout_week",
        rows=rows,
    )
    log.info("Wrote stock projection on %d products", len(rows))


def run() -> None:
    write_forecasts()
    write_po_risk()
    write_inventory_projection()
