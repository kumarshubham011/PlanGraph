import pandas as pd

from plangraph.alerts import rules
from plangraph.alerts.impact import blocked_share


def test_late_and_at_risk_pos_are_separate_groups():
    pos = pd.DataFrame(
        {
            "purchase_order_id": [1, 2, 3, 4],
            "is_late": [True, True, False, False],
            "days_late": [9, 1, 0, 0],
            "late_risk": [0.0, 0.0, 0.55, 0.10],
        }
    )
    assert rules.late_pos(pos)["purchase_order_id"].tolist() == [1]  # 1 day late is ignored
    assert rules.pos_at_risk(pos)["purchase_order_id"].tolist() == [3]


def test_demand_surge_needs_a_big_gap_and_groups_by_subcategory():
    latest = pd.DataFrame(
        {
            "product_id": [1, 2, 3],
            "subcategory": ["Touring Bikes", "Touring Bikes", "Helmets"],
            "actual": [120.0, 90.0, 12.0],
            "qty": [50.0, 40.0, 10.0],
            "p90": [70.0, 55.0, 14.0],
        }
    )
    surges = rules.demand_surges(latest)
    assert surges["subcategory"].tolist() == ["Touring Bikes"]
    assert surges["product_ids"].iloc[0] == [1, 2]


def test_target_gap_uses_projection_after_supply_risk():
    tracker = pd.DataFrame(
        {
            "territory": ["A", "B"],
            "target": [100.0, 100.0],
            "shipped": [50.0, 50.0],
            "open": [45.0, 45.0],
            "at_risk": [0.0, 20.0],
        }
    )
    gaps = rules.target_gaps(tracker)
    assert gaps["territory"].tolist() == ["B"]  # A projects 95, B projects 75
    assert gaps["gap"].iloc[0] == 25


def test_scrap_spike_and_likely_source():
    scrap = pd.DataFrame(
        {
            "location": ["Paint", "Frame Welding", "Subassembly"],
            "recent_qty": [100, 400, 5000],
            "recent_scrapped": [35, 35, 40],
            "normal_qty": [10000, 10000, 10000],
            "normal_scrapped": [20, 20, 30],
        }
    )
    spikes = rules.scrap_spikes(scrap)
    assert set(spikes["location"]) == {"Paint", "Frame Welding"}  # Subassembly is under 5%
    assert rules.likely_scrap_source(spikes)["location"] == "Paint"  # highest rate


def test_severity_levels():
    assert rules.severity(60_000) == "high"
    assert rules.severity(0, urgent=True) == "high"
    assert rules.severity(500) == "medium"
    assert rules.severity(0, notable=True) == "medium"
    assert rules.severity(0) == "low"


def test_blocked_share_is_proportional_to_missing_parts():
    # 10 bikes on open orders, 4 in stock -> 6 short. Each needs 2 tyres = 12 tyres.
    # Only 3 tyres in stock -> 25% of the short bikes can be built, 75% are blocked.
    usage = pd.DataFrame(
        {
            "part_uid": ["tyre"],
            "part_on_hand": [3],
            "fg_uid": ["bike"],
            "fg_on_hand": [4],
            "qty_per_unit": [2.0],
            "open_qty": [10.0],
            "open_value": [1000.0],
        }
    )
    assert blocked_share(usage)["bike"] == 0.6 * 0.75


def test_nothing_is_blocked_when_finished_stock_covers_orders():
    usage = pd.DataFrame(
        {
            "part_uid": ["tyre"],
            "part_on_hand": [0],
            "fg_uid": ["bike"],
            "fg_on_hand": [50],
            "qty_per_unit": [2.0],
            "open_qty": [10.0],
            "open_value": [1000.0],
        }
    )
    assert blocked_share(usage)["bike"] == 0
