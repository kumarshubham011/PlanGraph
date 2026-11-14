"""Inventory projection: will a purchased item run out in the next weeks?

No machine learning here, just the planner's classic sum, week by week:

    stock at end of week = stock at start + deliveries that week - demand that week

Demand for a part comes from the demand forecast for finished goods, pushed
down through the bill of materials (a bike forecast becomes a tyre forecast).
"""

import logging

import numpy as np
import pandas as pd

from plangraph.bom import component_requirements, explode_bom
from plangraph.db import read_sql
from plangraph.models.store import save_table

log = logging.getLogger(__name__)

HORIZON_WEEKS = 6
WEEKS_PER_MONTH = 52 / 12
OVERDUE_ARRIVAL_DAYS = 7  # assume an overdue PO finally arrives a week from today


def project_stock(
    on_hand: float, weekly_demand: float, inbound: dict[int, float], weeks: int = HORIZON_WEEKS
) -> list[float]:
    """Projected stock at the end of each week. `inbound` maps week number -> quantity."""
    projection = []
    stock = on_hand
    for week in range(1, weeks + 1):
        stock = stock + inbound.get(week, 0.0) - weekly_demand
        projection.append(stock)
    return projection


def first_stockout_week(projection: list[float]) -> int | None:
    """First week where projected stock is below zero, or None if it never is."""
    for week, stock in enumerate(projection, start=1):
        if stock < 0:
            return week
    return None


def arrival_week(due_date: pd.Series, as_of: pd.Timestamp) -> pd.Series:
    """Week number (1 = this coming week) in which each open PO line should arrive."""
    expected = pd.to_datetime(due_date)
    overdue = expected <= as_of
    expected = expected.where(~overdue, as_of + pd.Timedelta(days=OVERDUE_ARRIVAL_DAYS))
    return np.ceil((expected - as_of).dt.days / 7).clip(lower=1).astype(int)


def run() -> pd.DataFrame:
    as_of = pd.Timestamp(read_sql("SELECT as_of FROM meta.run_info").iloc[0, 0])
    products = read_sql("SELECT product_id, on_hand FROM stg.product WHERE is_purchased")
    bom = read_sql("SELECT assembly_id, component_id, qty FROM stg.bom")
    next_month = read_sql("SELECT product_id, qty FROM ml.demand_forecast WHERE horizon = 1")
    open_lines = read_sql(
        "SELECT product_id, qty, due_date FROM stg.purchase_order_line WHERE is_open"
    )

    needs = component_requirements(explode_bom(bom), next_month.set_index("product_id")["qty"])
    weekly_demand = needs / WEEKS_PER_MONTH
    open_lines["week"] = arrival_week(open_lines["due_date"], as_of)
    inbound = open_lines.groupby(["product_id", "week"])["qty"].sum()

    rows = []
    for product in products.itertuples():
        demand = float(weekly_demand.get(product.product_id, 0.0))
        if demand <= 0:
            continue
        arriving = (
            inbound.loc[product.product_id].to_dict()
            if product.product_id in inbound.index.get_level_values(0)
            else {}
        )
        projection = project_stock(product.on_hand, demand, arriving)
        rows.append(
            {
                "product_id": product.product_id,
                "on_hand": product.on_hand,
                "weekly_demand": round(demand, 1),
                "weeks_of_cover": round(product.on_hand / demand, 1),
                "inbound_qty": float(sum(v for w, v in arriving.items() if w <= HORIZON_WEEKS)),
                "stockout_week": first_stockout_week(projection),
                "end_stock": round(projection[-1], 1),
            }
        )
    result = pd.DataFrame(rows)
    save_table(result, "inventory_projection")
    log.info(
        "Inventory projection: %d purchased items, %d projected to run out within %d weeks",
        len(result),
        result["stockout_week"].notna().sum(),
        HORIZON_WEEKS,
    )
    return result
