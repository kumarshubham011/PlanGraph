"""Turn the raw AdventureWorks sample into a company that is "live today".

Everything here works on a dict of DataFrames keyed by "schema.table", so each
step is a small pure function that is easy to test.

Steps (see docs/DATA.md for the reasoning):
1. cut_future        - pick a planning date inside the sample and drop what happens after it
2. open_sales_orders - orders not shipped by the planning date become "Open"
3. add_po_receipts   - SYNTHETIC receipt dates, because the sample has none
4. open_work_orders  - work orders not finished by the planning date are in progress
5. calibrate_stock   - SYNTHETIC healthy stock for purchased items
6. shift_dates       - move every date so the planning date lands on `as_of` (today)
"""

from datetime import date

import numpy as np
import pandas as pd

from plangraph.bom import component_requirements, explode_bom
from plangraph.etl.tables import TABLES

Frames = dict[str, pd.DataFrame]

# The planning date inside the original sample. It is the day the last big batch
# of reseller orders was placed, so there are open orders and one month left in
# the quota period that started on 2025-02-28.
SAMPLE_CUTOFF = pd.Timestamp("2025-04-30")

WEEKS_PER_MONTH = 52 / 12


def cut_future(frames: Frames, cutoff: pd.Timestamp = SAMPLE_CUTOFF) -> Frames:
    """Drop orders created after the planning date. To the company they do not exist yet."""
    soh = frames["sales.sales_order_header"]
    soh = soh[soh["order_date"] <= cutoff]
    sod = frames["sales.sales_order_detail"]
    frames["sales.sales_order_header"] = soh
    frames["sales.sales_order_detail"] = sod[sod["sales_order_id"].isin(soh["sales_order_id"])]

    poh = frames["purchasing.purchase_order_header"]
    poh = poh[poh["order_date"] <= cutoff]
    pod = frames["purchasing.purchase_order_detail"]
    frames["purchasing.purchase_order_header"] = poh
    frames["purchasing.purchase_order_detail"] = pod[
        pod["purchase_order_id"].isin(poh["purchase_order_id"])]

    wo = frames["production.work_order"]
    wo = wo[wo["start_date"] <= cutoff]
    wor = frames["production.work_order_routing"]
    frames["production.work_order"] = wo
    frames["production.work_order_routing"] = wor[wor["work_order_id"].isin(wo["work_order_id"])]

    quota = frames["finance.sales_person_quota_history"]
    frames["finance.sales_person_quota_history"] = quota[quota["quota_date"] <= cutoff]
    return frames


def open_sales_orders(frames: Frames, cutoff: pd.Timestamp = SAMPLE_CUTOFF) -> Frames:
    """The sample marks every order as shipped. Orders that ship after the planning
    date have not shipped yet from the company's point of view, so they are Open."""
    soh = frames["sales.sales_order_header"].copy()
    is_open = soh["ship_date"] > cutoff
    soh["status"] = np.where(is_open, "Open", "Shipped")
    soh.loc[is_open, "ship_date"] = pd.NaT
    frames["sales.sales_order_header"] = soh
    return frames


def add_po_receipts(frames: Frames, rng: np.random.Generator,
                    cutoff: pd.Timestamp = SAMPLE_CUTOFF) -> Frames:
    """SYNTHETIC: give every purchase order a receipt date.

    AdventureWorks stores when a PO line is due but not when it arrived, so there
    is nothing to learn "late" from. We simulate it: every vendor gets its own
    chance of being late (worse credit rating -> later), and a late PO arrives a
    few days after its due date. POs that arrive after the planning date are open.
    """
    vendor = frames["purchasing.vendor"]
    poh = frames["purchasing.purchase_order_header"].copy()
    pod = frames["purchasing.purchase_order_detail"].copy()

    # Each vendor's own probability of delivering late, between 3% and 60%.
    base = rng.beta(2, 9, size=len(vendor)) + 0.05 * (vendor["credit_rating"].to_numpy() - 1)
    late_prob = pd.Series(np.clip(base, 0.03, 0.60), index=vendor["vendor_id"].to_numpy())

    is_late = rng.random(len(poh)) < poh["vendor_id"].map(late_prob).to_numpy()
    late_days = 1 + rng.geometric(0.2, size=len(poh))  # about 6 days late on average
    early_days = -rng.integers(0, 3, size=len(poh))  # on time = 0 to 2 days early
    poh["delay_days"] = np.where(is_late, late_days, early_days)

    pod = pod.merge(poh[["purchase_order_id", "delay_days", "status"]], on="purchase_order_id")
    pod["received_date"] = pod["due_date"] + pd.to_timedelta(pod["delay_days"], unit="D")

    rejected = pod["status"] == 3  # AdventureWorks status 3 = rejected, never delivered
    not_arrived = (pod["received_date"] > cutoff) | rejected
    pod.loc[not_arrived, ["received_date"]] = pd.NaT
    pod.loc[not_arrived, ["received_qty", "rejected_qty"]] = 0.0
    # Delivered lines: keep the sample's received_qty. A few are short-shipped in the sample.

    open_po_ids = set(pod.loc[pod["received_date"].isna() & ~rejected, "purchase_order_id"])
    poh["status"] = np.select(
        [poh["status"] == 3, poh["purchase_order_id"].isin(open_po_ids)],
        ["Rejected", "Open"], default="Received")

    frames["purchasing.purchase_order_header"] = poh.drop(columns="delay_days")
    frames["purchasing.purchase_order_detail"] = pod.drop(columns=["delay_days", "status"])
    return frames


def open_work_orders(frames: Frames, cutoff: pd.Timestamp = SAMPLE_CUTOFF) -> Frames:
    """Work orders that finish after the planning date are still in progress."""
    wo = frames["production.work_order"].copy()
    in_progress = wo["end_date"] > cutoff
    wo.loc[in_progress, "end_date"] = pd.NaT
    wo.loc[in_progress, "scrapped_qty"] = 0
    wo.loc[in_progress, "scrap_reason_id"] = np.nan
    frames["production.work_order"] = wo
    return frames


def active_bom(frames: Frames) -> pd.DataFrame:
    """Current BOM links only (no end date, and a real parent assembly)."""
    bom = frames["production.bill_of_materials"]
    bom = bom[bom["end_date"].isna() & bom["product_assembly_id"].notna()]
    return pd.DataFrame({
        "assembly_id": bom["product_assembly_id"].astype(int),
        "component_id": bom["component_id"].astype(int),
        "qty": bom["per_assembly_qty"].astype(float),
    })


def monthly_demand(frames: Frames, cutoff: pd.Timestamp, months: int = 3) -> pd.Series:
    """Average units sold per month for each product over the last `months` months."""
    soh = frames["sales.sales_order_header"]
    recent = soh[soh["order_date"] > cutoff - pd.DateOffset(months=months)]
    lines = frames["sales.sales_order_detail"]
    lines = lines[lines["sales_order_id"].isin(recent["sales_order_id"])]
    return lines.groupby("product_id")["order_qty"].sum() / months


def calibrate_stock(frames: Frames, rng: np.random.Generator,
                    cutoff: pd.Timestamp = SAMPLE_CUTOFF) -> Frames:
    """SYNTHETIC: give purchased items a healthy stock level (8 to 12 weeks of demand).

    The sample's stock numbers were not built to match its order history, so
    without this step almost every part looks like it is about to run out and the
    alert engine has no clean baseline. The disruption scenarios then break
    specific items on purpose.
    """
    purchased = frames["purchasing.product_vendor"]["product_id"].unique()
    needs = component_requirements(explode_bom(active_bom(frames)),
                                   monthly_demand(frames, cutoff))
    weekly_need = (needs / WEEKS_PER_MONTH).reindex(purchased).fillna(0)
    cover_weeks = rng.uniform(8, 12, size=len(weekly_need))
    target = np.ceil(weekly_need * cover_weeks)
    target = target[target > 0]

    inv = frames["inventory.product_inventory"]
    inv = set_total_stock(inv, target)
    frames["inventory.product_inventory"] = inv
    return frames


def set_total_stock(inv: pd.DataFrame, target: pd.Series) -> pd.DataFrame:
    """Set the total on-hand quantity of each product in `target` (index = product_id).

    The product keeps its storage locations; quantities are scaled to the new total.
    A product with no stock row gets one in location 6 (Miscellaneous Storage).
    """
    inv = inv.copy()
    current = inv.groupby("product_id")["quantity"].transform("sum")
    rows = inv.groupby("product_id")["quantity"].transform("size")
    new_total = inv["product_id"].map(target)
    share = np.where(current > 0, inv["quantity"] / current.where(current > 0, 1), 1 / rows)
    changed = new_total.notna()
    inv.loc[changed, "quantity"] = np.round(new_total[changed] * share[changed])

    missing = target.index.difference(inv["product_id"])
    if len(missing):
        extra = pd.DataFrame({"product_id": missing, "location_id": 6,
                              "quantity": target.loc[missing].to_numpy()})
        inv = pd.concat([inv, extra], ignore_index=True)
    inv["quantity"] = inv["quantity"].astype(int)
    return inv


def shift_dates(frames: Frames, days: int) -> Frames:
    """Move every date column forward by the same number of days.

    Shifting everything by one constant keeps all gaps (lead times, lateness,
    seasonality by month length) exactly as they were.
    """
    delta = pd.Timedelta(days=days)
    date_columns = {f"{t.schema}.{t.table}": list(t.dates) for t in TABLES}
    date_columns["purchasing.purchase_order_detail"].append("received_date")
    for name, columns in date_columns.items():
        frame = frames[name].copy()
        for column in columns:
            frame[column] = frame[column] + delta
        frames[name] = frame
    return frames


def shift_days_for(as_of: date, cutoff: pd.Timestamp = SAMPLE_CUTOFF) -> int:
    return (pd.Timestamp(as_of) - cutoff).days
