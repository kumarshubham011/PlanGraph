"""Thin client for the PlanGraph API. The UI gets all its data through these functions."""

import os

import httpx
import streamlit as st

API_URL = os.environ.get("API_URL", "http://localhost:8000")
SEVERITY_ICON = {"high": "🔴", "medium": "🟠", "low": "⚪"}
TYPE_LABEL = {
    "late_po": "Late PO",
    "po_at_risk": "PO at risk",
    "stockout": "Stockout",
    "demand_surge": "Demand surge",
    "scrap_spike": "Scrap spike",
    "target_gap": "Target gap",
}


def get(path: str, **params):
    response = httpx.get(f"{API_URL}{path}", params=params, timeout=60)
    response.raise_for_status()
    return response.json()


def post(path: str, payload: dict):
    response = httpx.post(f"{API_URL}{path}", json=payload, timeout=180)
    response.raise_for_status()
    return response.json()


def require_api() -> dict:
    """Check the API is up. Show a clear message and stop the page if it is not."""
    try:
        return get("/health")
    except httpx.HTTPError as exc:
        st.error(f"Cannot reach the PlanGraph API at {API_URL}. Is it running? ({exc})")
        st.stop()


def money(value: float | None) -> str:
    return f"${value:,.0f}" if value is not None else "-"
