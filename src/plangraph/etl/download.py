"""Download the AdventureWorks CSV files from Microsoft's public GitHub repo."""

import logging
from pathlib import Path

import requests

from plangraph.etl.tables import TABLES

log = logging.getLogger(__name__)

BASE_URL = (
    "https://raw.githubusercontent.com/microsoft/sql-server-samples/master/"
    "samples/databases/adventure-works/oltp-install-script"
)


def download_raw(raw_dir: Path) -> None:
    """Fetch each CSV once. Files that already exist are skipped."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    for table in TABLES:
        target = raw_dir / f"{table.file}.csv"
        if target.exists():
            continue
        log.info("Downloading %s.csv", table.file)
        response = requests.get(f"{BASE_URL}/{table.file}.csv", timeout=120)
        response.raise_for_status()
        target.write_bytes(response.content)
