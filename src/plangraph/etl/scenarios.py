"""Inject known disruptions into the data. These are the "answer key" for the alerts.

Each function changes the data the way a real disruption would, and returns one
row for the `meta.scenario_truth` table saying what was changed and which alert
should catch it. The evaluation later checks alerts against this table.

All scenarios run BEFORE the date shift, so "today" here is SAMPLE_CUTOFF.
"""

import numpy as np
import pandas as pd

from plangraph.bom import component_requirements, explode_bom
from plangraph.etl.prepare import (
    SAMPLE_CUTOFF,
    WEEKS_PER_MONTH,
    Frames,
    active_bom,
    monthly_demand,
    set_total_stock,
)

SURGE_TERRITORY_ID = 4  # Southwest
SURGE_SUBCATEGORY_ID = 3  # Touring Bikes
SURGE_FACTOR = 3
SCRAP_LOCATION_ID = 20  # Frame Welding
SCRAP_REASON_ID = 11  # Stress test failed


def _truth(scenario: str, description: str, alert_type: str, entity_type: str, ids) -> dict:
    return {
        "scenario": scenario,
        "description": description,
        "expected_alert_type": alert_type,
        "entity_type": entity_type,
        "entity_ids": ",".join(str(int(i)) for i in sorted(set(ids))),
    }


def open_order_shortage(frames: Frames) -> pd.DataFrame:
    """For each product on an open order: units ordered, units in stock, units short."""
    soh = frames["sales.sales_order_header"]
    open_ids = soh.loc[soh["status"] == "Open", "sales_order_id"]
    lines = frames["sales.sales_order_detail"]
    lines = lines[lines["sales_order_id"].isin(open_ids)]
    by_product = lines.groupby("product_id").agg(
        open_qty=("order_qty", "sum"), open_value=("line_total", "sum"))
    stock = frames["inventory.product_inventory"].groupby("product_id")["quantity"].sum()
    by_product["on_hand"] = stock.reindex(by_product.index).fillna(0)
    by_product["short_qty"] = (by_product["open_qty"] - by_product["on_hand"]).clip(lower=0)
    by_product["short_value"] = (
        by_product["open_value"] * by_product["short_qty"] / by_product["open_qty"])
    return by_product


def demand_surge(frames: Frames) -> dict:
    """Scenario: resellers in one territory suddenly triple their touring bike orders."""
    soh = frames["sales.sales_order_header"]
    sod = frames["sales.sales_order_detail"].copy()
    product = frames["production.product"]

    surge_orders = soh.loc[
        (soh["status"] == "Open") & (soh["territory_id"] == SURGE_TERRITORY_ID)
        & (soh["online_order_flag"] == 0), "sales_order_id"]
    surge_products = product.loc[
        product["product_subcategory_id"] == SURGE_SUBCATEGORY_ID, "product_id"]
    hit = sod["sales_order_id"].isin(surge_orders) & sod["product_id"].isin(surge_products)
    sod.loc[hit, "order_qty"] *= SURGE_FACTOR
    sod.loc[hit, "line_total"] *= SURGE_FACTOR
    frames["sales.sales_order_detail"] = sod

    # Keep order totals consistent with their lines.
    soh = soh.copy()
    totals = sod.groupby("sales_order_id")["line_total"].sum()
    soh["sub_total"] = soh["sales_order_id"].map(totals).fillna(soh["sub_total"])
    frames["sales.sales_order_header"] = soh
    return _truth(
        "demand_surge",
        f"Open reseller orders for touring bikes in territory {SURGE_TERRITORY_ID} "
        f"multiplied by {SURGE_FACTOR}",
        "demand_surge", "Product", sod.loc[hit, "product_id"])


def vendor_slip(frames: Frames, cutoff: pd.Timestamp = SAMPLE_CUTOFF) -> dict:
    """Scenario: one vendor starts delivering two weeks late.

    We pick the vendor whose parts matter most right now: the one whose parts go
    into the most open-order revenue that cannot be served from finished stock.
    """
    poh = frames["purchasing.purchase_order_header"].copy()
    pod = frames["purchasing.purchase_order_detail"].copy()
    flat_bom = explode_bom(active_bom(frames))
    shortage = open_order_shortage(frames)
    part_need = component_requirements(flat_bom, shortage["short_qty"])

    # Vendor lines that are due around today, for parts that open orders need.
    lines = pod.merge(poh[["purchase_order_id", "vendor_id"]], on="purchase_order_id")
    due_now = lines[
        (lines["due_date"] > cutoff - pd.Timedelta(days=14))
        & (lines["due_date"] <= cutoff + pd.Timedelta(days=14))
        & lines["product_id"].isin(part_need.index)]

    def exposed_value(parts) -> float:
        users = flat_bom.loc[flat_bom["component_id"].isin(parts), "top_id"]
        return shortage.loc[shortage.index.intersection(users), "short_value"].sum()

    exposure = due_now.groupby("vendor_id")["product_id"].agg(lambda p: exposed_value(set(p)))
    vendor_id = int(exposure.idxmax())

    # 1) The last 6 weeks of this vendor's deliveries arrive 14 days late.
    vendor_pos = poh.loc[poh["vendor_id"] == vendor_id, "purchase_order_id"]
    slipping = pod["purchase_order_id"].isin(vendor_pos) & (
        pod["due_date"] > cutoff - pd.Timedelta(days=45))
    pod.loc[slipping, "received_date"] = pod.loc[slipping, "due_date"] + pd.Timedelta(days=14)
    not_arrived = slipping & (pod["received_date"] > cutoff)
    pod.loc[not_arrived, "received_date"] = pd.NaT
    pod.loc[not_arrived, ["received_qty", "rejected_qty"]] = 0.0
    open_pos = pod.loc[not_arrived, "purchase_order_id"].unique()
    poh.loc[poh["purchase_order_id"].isin(open_pos), "status"] = "Open"

    # 2) Stock of the affected parts has run down to 25% of what open orders need.
    late_parts = pod.loc[not_arrived & (pod["due_date"] <= cutoff), "product_id"].unique()
    low_stock = np.floor(part_need.reindex(late_parts).dropna() * 0.25)
    frames["inventory.product_inventory"] = set_total_stock(
        frames["inventory.product_inventory"], low_stock)

    frames["purchasing.purchase_order_header"] = poh
    frames["purchasing.purchase_order_detail"] = pod
    late_pos = pod.loc[not_arrived & (pod["due_date"] <= cutoff), "purchase_order_id"]
    return _truth(
        "vendor_slip",
        f"Vendor {vendor_id} delivers 14 days late; its open POs are overdue and stock of "
        f"{len(low_stock)} of its parts has run low",
        "late_po", "PurchaseOrder", late_pos) | {"vendor_id": vendor_id}


def stock_shortfall(frames: Frames, exclude_vendor: int,
                    cutoff: pd.Timestamp = SAMPLE_CUTOFF) -> dict:
    """Scenario: a stock count finds a key purchased part is mostly missing.

    We take the busiest purchased part that the slipping vendor does not supply
    and has nothing on order, and leave one week of stock.
    """
    pv = frames["purchasing.product_vendor"]
    excluded = set(pv.loc[pv["vendor_id"] == exclude_vendor, "product_id"])
    pod = frames["purchasing.purchase_order_detail"]
    on_order = set(pod.loc[pod["received_date"].isna(), "product_id"])

    needs = component_requirements(explode_bom(active_bom(frames)),
                                   monthly_demand(frames, cutoff))
    weekly_need = needs / WEEKS_PER_MONTH
    candidates = [p for p in pv["product_id"].unique() if p not in excluded | on_order]
    part_id = int(weekly_need.reindex(candidates).idxmax())

    one_week = pd.Series({part_id: np.ceil(weekly_need[part_id])})
    frames["inventory.product_inventory"] = set_total_stock(
        frames["inventory.product_inventory"], one_week)
    return _truth(
        "stock_shortfall",
        f"Stock of part {part_id} cut to one week of demand, with nothing on order",
        "stockout", "Product", [part_id])


def scrap_spike(frames: Frames, rng: np.random.Generator,
                cutoff: pd.Timestamp = SAMPLE_CUTOFF) -> dict:
    """Scenario: the frame welding work centre scraps 25-40% of its output for 3 weeks."""
    wo = frames["production.work_order"].copy()
    routing = frames["production.work_order_routing"]
    welded = routing.loc[routing["location_id"] == SCRAP_LOCATION_ID, "work_order_id"]
    hit = wo["work_order_id"].isin(welded) & (wo["end_date"] > cutoff - pd.Timedelta(days=21))
    rate = rng.uniform(0.25, 0.40, size=hit.sum())
    wo.loc[hit, "scrapped_qty"] = np.ceil(wo.loc[hit, "order_qty"] * rate).astype(int)
    wo.loc[hit, "scrap_reason_id"] = SCRAP_REASON_ID
    frames["production.work_order"] = wo
    return _truth(
        "scrap_spike",
        f"Work orders through location {SCRAP_LOCATION_ID} (Frame Welding) scrap 25-40% "
        "of units in the last 3 weeks",
        "scrap_spike", "Location", [SCRAP_LOCATION_ID])


def inject_scenarios(frames: Frames, rng: np.random.Generator) -> tuple[Frames, pd.DataFrame]:
    """Run all four scenarios. Returns the changed frames and the truth table."""
    surge = demand_surge(frames)  # first: it changes open demand
    slip = vendor_slip(frames)
    shortfall = stock_shortfall(frames, exclude_vendor=slip.pop("vendor_id"))
    scrap = scrap_spike(frames, rng)
    return frames, pd.DataFrame([slip, surge, shortfall, scrap])
