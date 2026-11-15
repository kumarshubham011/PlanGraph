"""API tests. Database calls are replaced with fakes, so no services are needed."""

import pytest
from fastapi.testclient import TestClient

from plangraph.api import main


@pytest.fixture
def client():
    return TestClient(main.app)


def test_ask_returns_the_agent_answer(client, monkeypatch):
    fake = {"answer": "Two POs are late.", "mode": "offline", "steps": [], "evidence_uids": []}
    monkeypatch.setattr(main, "ask", lambda question: fake)
    response = client.post("/ask", json={"question": "What is late?"})
    assert response.status_code == 200
    assert response.json()["answer"] == "Two POs are late."


def test_ask_rejects_empty_and_oversized_questions(client):
    assert client.post("/ask", json={"question": ""}).status_code == 422
    assert client.post("/ask", json={"question": "x" * 501}).status_code == 422


def test_ask_reports_agent_failures_as_502(client, monkeypatch):
    def broken(question):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(main, "ask", broken)
    assert client.post("/ask", json={"question": "What is late?"}).status_code == 502


def test_approving_an_unknown_action_is_a_404(client, monkeypatch):
    monkeypatch.setattr(main.actions, "recommend", lambda uid: [])
    response = client.post(
        "/actions/approve",
        json={"alert_uid": "Alert:x", "action_id": "expedite:1", "approved_by": "Sam"},
    )
    assert response.status_code == 404


def test_targets_adds_projection_and_gap(client, monkeypatch):
    row = {"territory": "Canada", "target": 100.0, "shipped": 40.0, "open": 50.0, "at_risk": 10.0}
    monkeypatch.setattr(main, "run_read", lambda *args, **kwargs: [row])
    body = client.get("/targets").json()[0]
    assert body["projected"] == 80 and body["gap"] == 20 and body["attainment"] == 0.8
