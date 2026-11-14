"""Demand forecast: units sold per product per planning month.

One LightGBM model is trained on all products together ("global model"). Each
row is one product in one month, and the features are that product's own recent
history (lags). Two more models predict the 10th and 90th percentile, which
gives a range instead of a single number.

Planning months follow the sample's own calendar (before the date shift),
because reseller orders arrive in one batch at the end of each sample month.
"""

import logging

import lightgbm as lgb
import numpy as np
import pandas as pd

from plangraph.db import read_sql
from plangraph.models.store import save_metrics, save_table

log = logging.getLogger(__name__)

LAGS = [1, 2, 3, 6, 12]
FEATURES = [f"lag_{n}" for n in LAGS] + ["mean_3", "mean_6", "month", "age", "list_price"]
HORIZON = 3  # months to forecast ahead
BACKTEST_MONTHS = 6
QUANTILES = {"p10": 0.1, "p90": 0.9}

DEMAND_SQL = """
    SELECT l.product_id, (o.order_date - r.shift_days::int) AS sample_date, l.qty
    FROM stg.sales_order_line l
    JOIN stg.sales_order o USING (sales_order_id), meta.run_info r
"""


def monthly_history(lines: pd.DataFrame) -> pd.DataFrame:
    """Order lines -> one row per product per month, with zero rows for silent months.

    Returns columns product_id, period (a monthly pd.Period), qty.
    """
    lines = lines.assign(period=pd.to_datetime(lines["sample_date"]).dt.to_period("M"))
    sold = lines.groupby(["product_id", "period"])["qty"].sum()
    last = lines["period"].max()
    frames = []
    for product_id, series in sold.groupby(level=0):
        series = series.droplevel(0)
        months = pd.period_range(series.index.min(), last, freq="M")
        full = series.reindex(months, fill_value=0.0)
        frames.append(
            pd.DataFrame({"product_id": product_id, "period": months, "qty": full.to_numpy()})
        )
    return pd.concat(frames, ignore_index=True)


def add_features(history: pd.DataFrame, prices: pd.Series) -> pd.DataFrame:
    """Add lag features. Every feature only looks at months BEFORE the row's month."""
    data = history.sort_values(["product_id", "period"]).copy()
    by_product = data.groupby("product_id")["qty"]
    for n in LAGS:
        data[f"lag_{n}"] = by_product.shift(n)
    data["mean_3"] = data[["lag_1", "lag_2", "lag_3"]].mean(axis=1)
    data["mean_6"] = by_product.transform(lambda s: s.shift(1).rolling(6, min_periods=1).mean())
    data["month"] = data["period"].dt.month
    data["age"] = data.groupby("product_id").cumcount()  # months since first sale
    data["list_price"] = data["product_id"].map(prices)
    return data


def fit_models(train: pd.DataFrame) -> dict[str, lgb.LGBMRegressor]:
    """Fit the point model and the two quantile models on the same features."""
    common = dict(
        n_estimators=200,
        learning_rate=0.05,
        num_leaves=15,
        min_child_samples=10,
        subsample=0.8,
        subsample_freq=1,
        colsample_bytree=0.8,
        random_state=42,
        verbose=-1,
    )
    models = {"qty": lgb.LGBMRegressor(objective="regression_l1", **common)}
    for name, alpha in QUANTILES.items():
        models[name] = lgb.LGBMRegressor(objective="quantile", alpha=alpha, **common)
    train = train.dropna(subset=["lag_1"])  # a product's first month has no history
    for model in models.values():
        model.fit(train[FEATURES], train["qty"])
    return models


def predict(models: dict, rows: pd.DataFrame) -> pd.DataFrame:
    """Predict qty, p10 and p90. Demand cannot be negative and p10 <= qty <= p90."""
    out = rows[["product_id", "period"]].copy()
    for name, model in models.items():
        out[name] = np.clip(model.predict(rows[FEATURES]), 0, None)
    out["p10"] = np.minimum(out["p10"], out["qty"])
    out["p90"] = np.maximum(out["p90"], out["qty"])
    return out


def wape(actual: pd.Series, forecast: pd.Series) -> float:
    """Weighted absolute percentage error: total absolute error / total actual."""
    return float((actual - forecast).abs().sum() / actual.sum())


def backtest(data: pd.DataFrame) -> pd.DataFrame:
    """Walk forward over the last months: train on the past, predict the next month."""
    periods = sorted(data["period"].unique())[-BACKTEST_MONTHS:]
    folds = []
    for period in periods:
        test = data[(data["period"] == period) & data["lag_1"].notna()]
        fold = predict(fit_models(data[data["period"] < period]), test)
        fold["actual"] = test["qty"].to_numpy()
        # Baselines: "same as last month" and "same month last year".
        fold["last_month"] = test["lag_1"].to_numpy()
        fold["last_year"] = test["lag_12"].fillna(test["lag_1"]).to_numpy()
        folds.append(fold)
    return pd.concat(folds, ignore_index=True)


def forecast_ahead(history: pd.DataFrame, prices: pd.Series) -> pd.DataFrame:
    """Forecast HORIZON months ahead, one month at a time.

    Each step appends its own prediction to the history, so the next step can
    use it as "last month" (a recursive forecast).
    """
    models = fit_models(add_features(history, prices))
    last = history["period"].max()
    recent = history[history["period"] > last - 6].groupby("product_id")["qty"].sum()
    active = recent[recent > 0].index  # skip products that stopped selling
    history = history[history["product_id"].isin(active)]
    results = []
    for step in range(1, HORIZON + 1):
        next_rows = pd.DataFrame({"product_id": active, "period": last + step, "qty": np.nan})
        data = add_features(pd.concat([history, next_rows], ignore_index=True), prices)
        step_forecast = predict(models, data[data["period"] == last + step])
        step_forecast["horizon"] = step
        results.append(step_forecast)
        history = pd.concat(
            [history, step_forecast[["product_id", "period", "qty"]]], ignore_index=True
        )
    return pd.concat(results, ignore_index=True)


def add_calendar_dates(frame: pd.DataFrame, shift_days: int) -> pd.DataFrame:
    """Replace the sample-calendar `period` with real start and end dates."""
    shift = pd.Timedelta(days=shift_days)
    frame = frame.copy()
    frame["period_start"] = (frame["period"].dt.start_time + shift).dt.date
    frame["period_end"] = (frame["period"].dt.end_time.dt.normalize() + shift).dt.date
    return frame.drop(columns="period")


def run() -> dict[str, float]:
    shift_days = int(read_sql("SELECT shift_days FROM meta.run_info").iloc[0, 0])
    prices = read_sql("SELECT product_id, list_price FROM stg.product").set_index("product_id")[
        "list_price"
    ]
    history = monthly_history(read_sql(DEMAND_SQL))

    folds = backtest(add_features(history, prices))
    metrics = {
        "wape_model": wape(folds["actual"], folds["qty"]),
        "wape_last_month": wape(folds["actual"], folds["last_month"]),
        "wape_last_year": wape(folds["actual"], folds["last_year"]),
        "p10_p90_coverage": float(
            ((folds["actual"] >= folds["p10"]) & (folds["actual"] <= folds["p90"])).mean()
        ),
        "backtest_rows": len(folds),
    }
    log.info(
        "Demand forecast: WAPE %.3f (last-month baseline %.3f, last-year baseline %.3f)",
        metrics["wape_model"],
        metrics["wape_last_month"],
        metrics["wape_last_year"],
    )

    save_table(add_calendar_dates(folds, shift_days), "demand_backtest")
    save_table(add_calendar_dates(forecast_ahead(history, prices), shift_days), "demand_forecast")
    save_metrics("demand_forecast", metrics)
    return metrics
