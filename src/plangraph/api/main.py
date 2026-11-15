"""FastAPI app. The UI talks only to these endpoints, never to the databases.

Run locally:  uvicorn plangraph.api.main:app --reload
Interactive docs:  http://localhost:8000/docs
"""

import json
import logging

from fastapi import FastAPI, HTTPException

from plangraph.agent import actions
from plangraph.agent.graph import ask
from plangraph.agent.guardrails import clean_rows
from plangraph.api.schemas import ApproveRequest, AskRequest, AskResponse, Subgraph
from plangraph.api.subgraph import build_subgraph
from plangraph.config import get_settings
from plangraph.db import read_sql, run_read
from plangraph.graph import queries

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logging.getLogger("neo4j").setLevel(logging.ERROR)
log = logging.getLogger("plangraph.api")

app = FastAPI(title="PlanGraph API", version="0.1.0")

ALERTS = """
MATCH (a:Alert)
WHERE ($status IS NULL OR a.status = $status) AND ($type IS NULL OR a.type = $type)
  AND ($severity IS NULL OR a.severity = $severity)
OPTIONAL MATCH (d:Decision)-[:RESOLVES]->(a)
WITH a, count(d) AS decisions
RETURN a.uid AS uid, a.type AS type, a.severity AS severity, a.title AS title,
       a.summary AS summary, a.usd_at_risk AS usd_at_risk, a.usd_exposed AS usd_exposed,
       a.early_warning_days AS early_warning_days, toString(a.created) AS created,
       a.status AS status, a.resolution AS resolution, decisions, a.details AS details
ORDER BY a.usd_at_risk DESC, a.title
"""

ALERT_LINKS = """
MATCH (a:Alert {uid: $uid})
OPTIONAL MATCH (a)-[:ABOUT]->(n)
WITH a, collect({uid: n.uid, label: labels(n)[0], name: coalesce(n.name, toString(n.id))}) AS about
OPTIONAL MATCH (a)-[t:THREATENS]->(tg:Target)<-[:HAS_TARGET]-(sp:SalesPerson)-[:COVERS]->(ter)
RETURN about, collect({target_uid: tg.uid, sales_person: sp.name, territory: ter.name,
                       target: tg.amount, usd_at_risk: t.usd_at_risk}) AS threatens
"""

DECISIONS = """
MATCH (d:Decision)-[:RESOLVES]->(a:Alert)
WHERE $alert_uid IS NULL OR a.uid = $alert_uid
RETURN d.uid AS uid, d.label AS label, d.action AS action, d.approved_by AS approved_by,
       d.ts AS ts, d.note AS note, d.est_cost AS est_cost, a.uid AS alert_uid, a.title AS alert
ORDER BY d.ts DESC
"""


def with_details(alert: dict) -> dict:
    """The details are stored as a JSON string in Neo4j. Turn them back into a dict."""
    alert["details"] = json.loads(alert.get("details") or "{}")
    return alert


@app.get("/health")
def health() -> dict:
    as_of = read_sql("SELECT as_of FROM meta.run_info").iloc[0, 0]
    nodes = run_read("MATCH (n) RETURN count(n) AS n")[0]["n"]
    settings = get_settings()
    return {
        "status": "ok",
        "as_of": str(as_of),
        "graph_nodes": nodes,
        "llm_enabled": settings.llm_enabled,
        "model": settings.gemini_model if settings.llm_enabled else None,
    }


@app.get("/alerts")
def list_alerts(
    status: str | None = "open", type: str | None = None, severity: str | None = None
) -> list[dict]:
    rows = run_read(ALERTS, status=status, type=type, severity=severity)
    return [with_details(r) for r in rows]


@app.get("/alerts/{uid}")
def get_alert(uid: str) -> dict:
    rows = run_read(ALERTS + " LIMIT 1000", status=None, type=None, severity=None)
    alert = next((r for r in rows if r["uid"] == uid), None)
    if alert is None:
        raise HTTPException(404, f"Alert {uid} not found")
    links = run_read(ALERT_LINKS, uid=uid)[0]
    return {
        **with_details(alert),
        "about": [n for n in links["about"] if n["uid"]],
        "threatens": [t for t in links["threatens"] if t["target_uid"]],
        "actions": actions.recommend(uid),
        "decisions": run_read(DECISIONS, alert_uid=uid),
    }


@app.post("/ask", response_model=AskResponse)
def ask_question(request: AskRequest) -> dict:
    log.info("ask: %s", request.question)
    try:
        return ask(request.question)
    except Exception as exc:
        log.exception("ask failed")
        raise HTTPException(502, f"The agent could not answer: {exc}") from exc


@app.get("/graph/{uid}", response_model=Subgraph)
def get_graph(uid: str) -> dict:
    subgraph = build_subgraph(uid)
    if not subgraph["nodes"]:
        raise HTTPException(404, f"Node {uid} not found")
    return subgraph


@app.get("/targets")
def targets() -> list[dict]:
    rows = clean_rows(run_read(queries.TARGET_TRACKER, territory=None))
    for row in rows:
        row["projected"] = row["shipped"] + row["open"] - row["at_risk"]
        row["gap"] = row["target"] - row["projected"]
        row["attainment"] = round(row["projected"] / row["target"], 3) if row["target"] else None
    return rows


@app.post("/actions/approve")
def approve_action(request: ApproveRequest) -> dict:
    try:
        decision = actions.approve(
            request.alert_uid, request.action_id, request.approved_by, request.note
        )
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    log.info(
        "decision %s by %s on %s", decision["action"], decision["approved_by"], request.alert_uid
    )
    return decision


@app.get("/decisions")
def decisions() -> list[dict]:
    return run_read(DECISIONS, alert_uid=None)


@app.get("/metrics")
def metrics() -> dict:
    """Model quality metrics from the last pipeline run, and the injected scenarios."""
    rows = read_sql("SELECT model, metric, value FROM ml.metrics")
    by_model: dict[str, dict] = {}
    for row in rows.itertuples():
        by_model.setdefault(row.model, {})[row.metric] = round(row.value, 4)
    scenarios = read_sql(
        "SELECT scenario, description, expected_alert_type FROM meta.scenario_truth"
    )
    return {"models": by_model, "scenarios": scenarios.to_dict("records")}
