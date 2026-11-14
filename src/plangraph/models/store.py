"""Save model outputs and metrics to the `ml` schema in Postgres."""

import pandas as pd
from sqlalchemy import text

from plangraph.db import pg_engine


def save_table(frame: pd.DataFrame, table: str) -> None:
    with pg_engine().begin() as conn:
        conn.execute(text("CREATE SCHEMA IF NOT EXISTS ml"))
    frame.to_sql(table, pg_engine(), schema="ml", index=False, if_exists="replace")


def save_metrics(model: str, metrics: dict[str, float]) -> None:
    """Replace the metrics of one model, keep the others."""
    with pg_engine().begin() as conn:
        conn.execute(text("CREATE SCHEMA IF NOT EXISTS ml"))
        conn.execute(
            text("CREATE TABLE IF NOT EXISTS ml.metrics (model text, metric text, value float)")
        )
        conn.execute(text("DELETE FROM ml.metrics WHERE model = :m"), {"m": model})
    rows = pd.DataFrame(
        [{"model": model, "metric": k, "value": float(v)} for k, v in metrics.items()]
    )
    rows.to_sql("metrics", pg_engine(), schema="ml", index=False, if_exists="append")
