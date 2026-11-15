import pandas as pd

from plangraph.models import forecast, inventory, late_po


def test_project_stock_and_first_stockout_week():
    projection = inventory.project_stock(on_hand=100, weekly_demand=40, inbound={3: 50})
    assert projection[:3] == [60, 20, 30]
    assert inventory.first_stockout_week(projection) == 4  # 30 - 40 < 0
    assert inventory.first_stockout_week([5, 1, 0]) is None


def test_overdue_po_lines_are_assumed_to_arrive_next_week():
    as_of = pd.Timestamp("2026-11-14")
    weeks = inventory.arrival_week(pd.Series(["2026-11-01", "2026-11-16", "2026-11-30"]), as_of)
    assert weeks.tolist() == [1, 1, 3]


def test_monthly_history_fills_silent_months_with_zero():
    lines = pd.DataFrame(
        {
            "product_id": [1, 1, 2],
            "sample_date": ["2025-01-10", "2025-03-05", "2025-03-20"],
            "qty": [5.0, 7.0, 2.0],
        }
    )
    history = forecast.monthly_history(lines)
    product_1 = history[history["product_id"] == 1]["qty"].tolist()
    assert product_1 == [5.0, 0.0, 7.0]  # February is a zero, not a gap


def test_lag_features_only_use_the_past():
    history = pd.DataFrame(
        {
            "product_id": 1,
            "period": pd.period_range("2025-01", periods=4, freq="M"),
            "qty": [10.0, 20.0, 30.0, 40.0],
        }
    )
    data = forecast.add_features(history, pd.Series({1: 99.0}))
    last = data.iloc[-1]
    assert last["lag_1"] == 30 and last["lag_2"] == 20
    assert last["mean_3"] == 20  # mean of 10, 20, 30: the current 40 is not included


def test_wape():
    assert forecast.wape(pd.Series([100.0, 100.0]), pd.Series([90.0, 120.0])) == 0.15


def test_vendor_history_ignores_outcomes_not_known_yet():
    as_of = pd.Timestamp("2026-11-14")
    pos = pd.DataFrame(
        {
            "vendor_id": [1, 1, 1],
            "order_date": ["2026-09-01", "2026-10-01", "2026-11-01"],
            "due_date": ["2026-09-15", "2026-10-15", "2026-11-15"],
            "received_date": ["2026-09-20", "2026-10-15", None],
            "days_late": [5, 0, 0],
        }
    )
    pos = late_po.add_outcome(pos, as_of)
    assert pos["late"].tolist()[:2] == [1, 0] and pd.isna(pos["late"].iloc[2])
    # On 1 October only the first PO's outcome (late, known 16 Sept) was known.
    on_oct_1 = late_po.vendor_history(pos, 1, pd.Timestamp("2026-10-01"))
    expected = (1 + late_po.PRIOR_LATE_RATE * late_po.PRIOR_WEIGHT) / (1 + late_po.PRIOR_WEIGHT)
    assert on_oct_1["vendor_late_rate"] == expected
