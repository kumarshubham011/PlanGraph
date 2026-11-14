"""Data quality checks on the staging schema.

Each check is a SQL query that counts BAD rows, so every check should return 0.
The pipeline stops if any check fails: a wrong graph is worse than no graph.
"""

import logging

import pandas as pd

from plangraph.db import read_sql

log = logging.getLogger(__name__)

CHECKS = {
    # Row counts: staging must not lose or duplicate rows from the sources.
    "sales orders match source":
        "SELECT ABS((SELECT COUNT(*) FROM stg.sales_order) - "
        "(SELECT COUNT(*) FROM sales.sales_order_header))",
    "sales order lines match source":
        "SELECT ABS((SELECT COUNT(*) FROM stg.sales_order_line) - "
        "(SELECT COUNT(*) FROM sales.sales_order_detail))",
    "po lines match source":
        "SELECT ABS((SELECT COUNT(*) FROM stg.purchase_order_line) - "
        "(SELECT COUNT(*) FROM purchasing.purchase_order_detail))",
    "work orders match source":
        "SELECT ABS((SELECT COUNT(*) FROM stg.work_order) - "
        "(SELECT COUNT(*) FROM production.work_order))",
    # Keys: unique and never null.
    "product ids unique":
        "SELECT COUNT(*) - COUNT(DISTINCT product_id) FROM stg.product",
    "sales order ids unique":
        "SELECT COUNT(*) - COUNT(DISTINCT sales_order_id) FROM stg.sales_order",
    "target ids unique":
        "SELECT COUNT(*) - COUNT(DISTINCT target_id) FROM stg.target",
    # Orphans: every foreign key must point at a row that exists.
    "order lines have a product":
        "SELECT COUNT(*) FROM stg.sales_order_line l "
        "LEFT JOIN stg.product p USING (product_id) WHERE p.product_id IS NULL",
    "po lines have a product":
        "SELECT COUNT(*) FROM stg.purchase_order_line l "
        "LEFT JOIN stg.product p USING (product_id) WHERE p.product_id IS NULL",
    "purchase orders have a vendor":
        "SELECT COUNT(*) FROM stg.purchase_order o "
        "LEFT JOIN stg.vendor v USING (vendor_id) WHERE v.vendor_id IS NULL",
    "bom components exist":
        "SELECT COUNT(*) FROM stg.bom b "
        "LEFT JOIN stg.product p ON p.product_id = b.component_id WHERE p.product_id IS NULL",
    "sales people have a home territory":
        "SELECT COUNT(*) FROM stg.sales_person WHERE home_territory_id IS NULL",
    # Business rules.
    "no negative stock":
        "SELECT COUNT(*) FROM stg.inventory WHERE on_hand < 0",
    "no order dated after today":
        "SELECT COUNT(*) FROM stg.sales_order, meta.run_info r WHERE order_date > r.as_of",
    "open orders have no ship date":
        "SELECT COUNT(*) FROM stg.sales_order WHERE status = 'Open' AND ship_date IS NOT NULL",
    "exactly one current target period":
        "SELECT ABS(COUNT(DISTINCT period_start) - 1) FROM stg.target WHERE is_current",
    "bom has no product that contains itself":
        "SELECT COUNT(*) FROM stg.bom WHERE assembly_id = component_id",
}


def run_checks() -> pd.DataFrame:
    """Run every check. Raises ValueError if any check finds bad rows."""
    results = [
        {"check": name, "bad_rows": int(read_sql(sql).iloc[0, 0])} for name, sql in CHECKS.items()
    ]
    report = pd.DataFrame(results)
    failed = report[report["bad_rows"] > 0]
    for row in report.itertuples():
        log.info("%-45s %s", row.check, "ok" if row.bad_rows == 0 else f"FAILED ({row.bad_rows})")
    if not failed.empty:
        raise ValueError(f"Data quality checks failed: {failed['check'].tolist()}")
    return report
