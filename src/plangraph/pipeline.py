"""The nightly job. One command rebuilds everything, in order:

    sources -> staging -> quality checks -> graph -> models -> alerts

Run it with:  python -m plangraph.pipeline
Each step is safe to re-run. Decisions (the audit trail) survive a re-run.
"""

import logging
import time
from datetime import date

from plangraph.alerts import engine
from plangraph.config import get_settings
from plangraph.etl import load_sources, quality, staging
from plangraph.etl.download import download_raw
from plangraph.graph import documents, loader
from plangraph.models import forecast, inventory, late_po, writeback

log = logging.getLogger("plangraph.pipeline")


def run_pipeline() -> None:
    settings = get_settings()
    as_of = settings.as_of or date.today()
    steps = [
        ("download raw files", lambda: download_raw(settings.raw_dir)),
        ("load source systems", lambda: load_sources.run(settings.raw_dir, as_of)),
        ("build staging", staging.build_staging),
        ("data quality checks", quality.run_checks),
        ("load graph", loader.load_graph),
        ("load documents", documents.load_documents),
        ("demand forecast", forecast.run),
        ("late-PO risk", late_po.run),
        ("inventory projection", inventory.run),
        ("write model outputs to graph", writeback.run),
        ("alert engine", engine.run),
    ]
    log.info("PlanGraph pipeline starting, as of %s", as_of)
    for name, step in steps:
        started = time.perf_counter()
        step()
        log.info("== %s done in %.1fs", name, time.perf_counter() - started)
    log.info("Pipeline finished")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("neo4j").setLevel(logging.ERROR)
    run_pipeline()
