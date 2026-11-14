"""Late-PO risk: the probability that a purchase order arrives after its due date.

One row per purchase order. The features describe the vendor's track record
"as known on the day we score": for training rows that day is the order date,
for open POs it is today. Using only what was known at the time avoids leakage
(training on information from the future).

Reminder: receipt dates are synthetic (see docs/DATA.md), so the AUC measures
how well the model recovers the simulated vendor behaviour, not a real one.
"""

import logging

import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.pipeline import Pipeline, make_pipeline
from sklearn.preprocessing import StandardScaler

from plangraph.db import read_sql
from plangraph.models.store import save_metrics, save_table

log = logging.getLogger(__name__)

FEATURES = ["vendor_late_rate", "vendor_recent_late_rate", "credit_rating", "lead_days"]
RECENT_DAYS = 60
# Smoothing: a vendor with few past orders is pulled towards a typical late rate,
# so one late delivery out of one does not read as "100% late".
PRIOR_LATE_RATE = 0.2
PRIOR_WEIGHT = 5
TEST_SHARE = 0.25

PO_SQL = """
    SELECT o.purchase_order_id, o.vendor_id, o.status, o.order_date, o.due_date,
           o.received_date, o.days_late, o.total_value, o.line_count, o.ship_method_id,
           v.credit_rating, v.is_preferred::int AS is_preferred,
           (SELECT AVG(lead_days) FROM stg.vendor_product vp
            WHERE vp.vendor_id = o.vendor_id) AS lead_days
    FROM stg.purchase_order o
    JOIN stg.vendor v USING (vendor_id)
    WHERE o.status <> 'Rejected'
"""


def add_outcome(pos: pd.DataFrame, as_of: pd.Timestamp) -> pd.DataFrame:
    """Add `late` (1/0, or NA if not known yet) and `known_date` (when we learned it).

    A PO is known to be late the day after its due date passes without delivery,
    and known to be on time the day it arrives.
    """
    pos = pos.copy()
    for column in ["order_date", "due_date", "received_date"]:
        pos[column] = pd.to_datetime(pos[column])
    received = pos["received_date"].notna()
    overdue = ~received & (pos["due_date"] < as_of)
    pos["late"] = pd.NA
    pos.loc[received, "late"] = (pos.loc[received, "days_late"] > 0).astype(int)
    pos.loc[overdue, "late"] = 1
    was_late = pos["late"] == 1
    pos["known_date"] = pos["received_date"]
    pos.loc[was_late, "known_date"] = pos.loc[was_late, "due_date"] + pd.Timedelta(days=1)
    return pos


def vendor_history(pos: pd.DataFrame, vendor_id: int, on_date: pd.Timestamp) -> dict:
    """The vendor's track record using only outcomes known before `on_date`."""
    known = pos[(pos["vendor_id"] == vendor_id) & (pos["known_date"] < on_date)]
    recent = known[known["known_date"] >= on_date - pd.Timedelta(days=RECENT_DAYS)]
    smoothed = (known["late"].sum() + PRIOR_LATE_RATE * PRIOR_WEIGHT) / (len(known) + PRIOR_WEIGHT)
    return {
        "vendor_late_rate": float(smoothed),
        "vendor_recent_late_rate": recent["late"].mean() if len(recent) else float("nan"),
    }


def build_features(pos: pd.DataFrame, as_of: pd.Timestamp) -> pd.DataFrame:
    """One feature row per PO. Finished POs are scored as of their order date, open ones as of today."""
    still_open = pos["late"].isna()
    score_dates = pos["order_date"].where(~still_open, as_of)
    history = [
        vendor_history(pos, vendor_id, on_date)
        for vendor_id, on_date in zip(pos["vendor_id"], score_dates, strict=True)
    ]
    features = pd.concat([pos.reset_index(drop=True), pd.DataFrame(history)], axis=1)
    return features.astype({name: float for name in FEATURES})


def new_model() -> Pipeline:
    """Logistic regression: fill gaps with the median, scale, then fit.

    A simple model on purpose. A boosted tree model was tried and scored lower
    on the test set (it overfitted the small, noisy data).
    """
    return make_pipeline(
        SimpleImputer(strategy="median"), StandardScaler(), LogisticRegression(C=0.3)
    )


def run() -> dict[str, float]:
    as_of = pd.Timestamp(read_sql("SELECT as_of FROM meta.run_info").iloc[0, 0])
    data = build_features(add_outcome(read_sql(PO_SQL), as_of), as_of)

    labelled = data[data["late"].notna()].sort_values("order_date")
    split = int(len(labelled) * (1 - TEST_SHARE))  # oldest 75% train, newest 25% test
    train, test = labelled.iloc[:split], labelled.iloc[split:]

    model = new_model()
    model.fit(train[FEATURES], train["late"].astype(int))
    y_test = test["late"].astype(int)
    metrics = {
        "auc_model": roc_auc_score(y_test, model.predict_proba(test[FEATURES])[:, 1]),
        # Baseline: rank POs by the vendor's past late rate alone.
        "auc_vendor_rate_only": roc_auc_score(y_test, test["vendor_late_rate"]),
        "late_share": float(labelled["late"].astype(int).mean()),
        "train_rows": len(train),
        "test_rows": len(test),
    }
    log.info(
        "Late-PO risk: AUC %.3f (vendor-rate baseline %.3f)",
        metrics["auc_model"],
        metrics["auc_vendor_rate_only"],
    )

    # Refit on all labelled POs, then score the POs that are still open and not yet due.
    model.fit(labelled[FEATURES], labelled["late"].astype(int))
    open_pos = data[data["late"].isna()].copy()
    open_pos["late_risk"] = model.predict_proba(open_pos[FEATURES])[:, 1]
    save_table(open_pos[["purchase_order_id", "late_risk", *FEATURES]], "po_risk")
    save_metrics("late_po", metrics)
    return metrics
