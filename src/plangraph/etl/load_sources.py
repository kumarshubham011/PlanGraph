"""Read the raw CSVs, prepare them, and write the five "source systems" to Postgres."""

import logging
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import text

from plangraph.db import pg_engine
from plangraph.etl import prepare, scenarios
from plangraph.etl.tables import SCHEMAS, TABLES

log = logging.getLogger(__name__)

SEED = 42  # one fixed seed so every run produces the same company


def read_raw(raw_dir: Path) -> prepare.Frames:
    """Load each CSV, keep the columns we use and rename them to snake_case."""
    frames = {}
    for t in TABLES:
        frame = pd.read_csv(raw_dir / f"{t.file}.csv", sep="\t", header=None,
                            names=t.columns, low_memory=False)
        frame = frame[list(t.keep)].rename(columns=t.keep)
        for column in t.dates:
            frame[column] = pd.to_datetime(frame[column])
        frames[f"{t.schema}.{t.table}"] = frame
    return frames


def build_company(frames: prepare.Frames, as_of: date) -> tuple[prepare.Frames, pd.DataFrame]:
    """Apply every preparation step and scenario. Pure function: no database needed."""
    rng = np.random.default_rng(SEED)
    frames = prepare.cut_future(frames)
    frames = prepare.open_sales_orders(frames)
    frames = prepare.add_po_receipts(frames, rng)
    frames = prepare.open_work_orders(frames)
    frames = prepare.calibrate_stock(frames, rng)
    frames, truth = scenarios.inject_scenarios(frames, rng)
    frames = prepare.shift_dates(frames, prepare.shift_days_for(as_of))
    return frames, truth


def write_sources(frames: prepare.Frames, truth: pd.DataFrame, as_of: date) -> None:
    """Replace the source schemas in Postgres with the prepared frames."""
    engine = pg_engine()
    with engine.begin() as conn:
        for schema in [*SCHEMAS, "meta"]:
            conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
            conn.execute(text(f"CREATE SCHEMA {schema}"))
    for name, frame in frames.items():
        schema, table = name.split(".")
        frame.to_sql(table, engine, schema=schema, index=False, chunksize=5000, method="multi")
        log.info("Loaded %-45s %7d rows", name, len(frame))
    truth.to_sql("scenario_truth", engine, schema="meta", index=False)
    run_info = pd.DataFrame([{
        "as_of": as_of, "shift_days": prepare.shift_days_for(as_of), "seed": SEED}])
    run_info.to_sql("run_info", engine, schema="meta", index=False)


def run(raw_dir: Path, as_of: date) -> None:
    frames, truth = build_company(read_raw(raw_dir), as_of)
    write_sources(frames, truth, as_of)
