"""Alert rules: plain functions that decide WHAT is a problem.

Each rule takes a DataFrame and returns the rows that should become alerts.
No database access here, so every rule is easy to unit test. The thresholds are
constants at the top so they are easy to find and tune.
"""

import pandas as pd

LATE_DAYS_MIN = 3  # ignore POs that are only a day or two late
LATE_RISK_MIN = 0.40  # twice the typical late rate of about 20%
SURGE_RATIO_MIN = 1.5  # actual demand at least 1.5x the forecast
SURGE_UNITS_MIN = 20  # and at least 20 units above it
SURGE_GROUP_UNITS_MIN = 50  # a product group needs 50 extra units to raise an alert
# Alert when projected revenue is more than 15% under target. In this dataset
# sales reps historically reach about 84% of quota, so a tighter tolerance would
# flag every territory every quarter and the alert would be ignored.
TARGET_TOLERANCE = 0.15
SCRAP_RATE_MIN = 0.05  # at least 5% of units scrapped recently
SCRAP_RATIO_MIN = 3.0  # and at least 3x the location's normal rate
HIGH_USD = 50_000


def late_pos(pos: pd.DataFrame) -> pd.DataFrame:
    """Open POs that are already past their due date by a meaningful margin."""
    return pos[pos["is_late"] & (pos["days_late"] >= LATE_DAYS_MIN)]


def pos_at_risk(pos: pd.DataFrame) -> pd.DataFrame:
    """Open POs that are not late yet, but the model expects them to be."""
    return pos[~pos["is_late"] & (pos["late_risk"] >= LATE_RISK_MIN)]


def stockouts(projection: pd.DataFrame) -> pd.DataFrame:
    """Purchased items projected to run out within the horizon."""
    return projection[projection["stockout_week"].notna()]


def demand_surges(latest: pd.DataFrame) -> pd.DataFrame:
    """Products whose latest month came in far above forecast, summed per subcategory.

    A product counts when actual > P90 (above the forecast range), and the gap is
    large in both relative and absolute terms. Grouping by subcategory turns 20
    alerts for 20 touring bikes into one alert a planner can act on.
    """
    surged = latest[
        (latest["actual"] > latest["p90"])
        & (latest["actual"] >= SURGE_RATIO_MIN * latest["qty"])
        & (latest["actual"] - latest["qty"] >= SURGE_UNITS_MIN)
    ]
    groups = (
        surged.groupby("subcategory")
        .agg(actual=("actual", "sum"), forecast=("qty", "sum"), product_ids=("product_id", list))
        .reset_index()
    )
    groups["ratio"] = groups["actual"] / groups["forecast"].clip(lower=1)
    return groups[groups["actual"] - groups["forecast"] >= SURGE_GROUP_UNITS_MIN]


def target_gaps(tracker: pd.DataFrame) -> pd.DataFrame:
    """Territories whose projected revenue for the current period is under target.

    projected = shipped + open orders - open orders at risk
    """
    tracker = tracker.copy()
    tracker["projected"] = tracker["shipped"] + tracker["open"] - tracker["at_risk"]
    tracker["gap"] = tracker["target"] - tracker["projected"]
    return tracker[tracker["gap"] > TARGET_TOLERANCE * tracker["target"]]


def scrap_spikes(scrap: pd.DataFrame) -> pd.DataFrame:
    """Locations whose recent scrap rate is far above their own normal rate."""
    scrap = scrap.copy()
    scrap["recent_rate"] = scrap["recent_scrapped"] / scrap["recent_qty"].clip(lower=1)
    scrap["normal_rate"] = scrap["normal_scrapped"] / scrap["normal_qty"].clip(lower=1)
    return scrap[
        (scrap["recent_rate"] >= SCRAP_RATE_MIN)
        & (scrap["recent_rate"] >= SCRAP_RATIO_MIN * scrap["normal_rate"])
    ]


def likely_scrap_source(spikes: pd.DataFrame) -> pd.Series | None:
    """Pick the work centre that best explains a scrap spike.

    A work order passes through several work centres, so one bad work centre
    makes every centre on the same route look bad. The most likely source is the
    one with a high scrap rate that also covers most of the scrapped units:
    score = recent scrap rate x share of all scrapped units.
    """
    if spikes.empty:
        return None
    share = spikes["recent_scrapped"] / spikes["recent_scrapped"].max()
    return spikes.loc[(spikes["recent_rate"] * share).idxmax()]


def severity(usd_at_risk: float, urgent: bool = False, notable: bool = False) -> str:
    """high: big money or urgent. medium: some money or notable. low: worth knowing."""
    if usd_at_risk >= HIGH_USD or urgent:
        return "high"
    return "medium" if usd_at_risk > 0 or notable else "low"
