import numpy as np
import pandas as pd

from plangraph.etl import prepare
from plangraph.etl.scenarios import open_order_shortage

CUTOFF = pd.Timestamp("2025-04-30")


def sales_frames():
    return {
        "sales.sales_order_header": pd.DataFrame(
            {
                "sales_order_id": [1, 2],
                "order_date": pd.to_datetime(["2025-04-01", "2025-04-29"]),
                "due_date": pd.to_datetime(["2025-04-13", "2025-05-11"]),
                "ship_date": pd.to_datetime(["2025-04-08", "2025-05-06"]),
                "status": [5, 5],
            }
        ),
        "sales.sales_order_detail": pd.DataFrame(
            {
                "sales_order_id": [1, 2],
                "product_id": [10, 10],
                "order_qty": [3, 8],
                "line_total": [300.0, 800.0],
            }
        ),
        "inventory.product_inventory": pd.DataFrame(
            {"product_id": [10], "location_id": [7], "quantity": [5]}
        ),
    }


def test_orders_shipping_after_the_planning_date_become_open():
    frames = prepare.open_sales_orders(sales_frames(), CUTOFF)
    orders = frames["sales.sales_order_header"].set_index("sales_order_id")
    assert orders.loc[1, "status"] == "Shipped"
    assert orders.loc[2, "status"] == "Open"
    assert pd.isna(orders.loc[2, "ship_date"])


def test_open_order_shortage_nets_off_stock():
    frames = prepare.open_sales_orders(sales_frames(), CUTOFF)
    shortage = open_order_shortage(frames).loc[10]
    assert shortage["open_qty"] == 8  # only the open order counts
    assert shortage["short_qty"] == 3  # 8 ordered - 5 in stock
    assert shortage["short_value"] == 300.0  # 3/8 of $800


def test_set_total_stock_keeps_location_split_and_adds_missing_products():
    inv = pd.DataFrame({"product_id": [1, 1], "location_id": [6, 7], "quantity": [30, 10]})
    result = prepare.set_total_stock(inv, pd.Series({1: 80.0, 2: 15.0}))
    by_location = result[result["product_id"] == 1].set_index("location_id")["quantity"]
    assert by_location[6] == 60 and by_location[7] == 20  # still a 3:1 split
    assert result.loc[result["product_id"] == 2, "quantity"].item() == 15


def test_shift_days_moves_planning_date_to_as_of():
    days = prepare.shift_days_for(pd.Timestamp("2026-11-14").date(), CUTOFF)
    assert CUTOFF + pd.Timedelta(days=days) == pd.Timestamp("2026-11-14")


def test_po_receipts_are_reproducible_and_open_after_cutoff():
    def frames():
        return {
            "purchasing.vendor": pd.DataFrame({"vendor_id": [1], "credit_rating": [1]}),
            "purchasing.purchase_order_header": pd.DataFrame(
                {"purchase_order_id": [1, 2], "vendor_id": [1, 1], "status": [4, 4]}
            ),
            "purchasing.purchase_order_detail": pd.DataFrame(
                {
                    "purchase_order_id": [1, 2],
                    "due_date": pd.to_datetime(["2025-03-01", "2025-06-01"]),
                    "received_qty": [5.0, 5.0],
                    "rejected_qty": [0.0, 0.0],
                }
            ),
        }

    first = prepare.add_po_receipts(frames(), np.random.default_rng(1), CUTOFF)
    second = prepare.add_po_receipts(frames(), np.random.default_rng(1), CUTOFF)
    lines = first["purchasing.purchase_order_detail"]
    assert lines.equals(second["purchasing.purchase_order_detail"])  # same seed, same data
    future = lines[lines["purchase_order_id"] == 2].iloc[0]
    assert pd.isna(future["received_date"]) and future["received_qty"] == 0
    statuses = first["purchasing.purchase_order_header"].set_index("purchase_order_id")["status"]
    assert statuses[2] == "Open"
