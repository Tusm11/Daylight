import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DAYLIGHT_DATA", str(tmp_path))
    # Force a fresh module instance pointing at the tmp dir.
    import daylight.api as api_mod
    importlib.reload(api_mod)
    api_mod._app_state = api_mod.Daylight(data_dir=str(tmp_path))
    return TestClient(api_mod.app)


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["ok"]


def test_independent_citation_flow(client):
    # Agent fetches a legitimate source.
    client.post("/observe", json={
        "agent_id": "A", "session_id": "s1", "method": "GET",
        "url": "https://wiki/x", "body": "<p>Paris is the capital of France</p>",
        "ts": 1000.0, "idempotency_key": "k1",
    })
    # Third-party evidence says the page existed long before.
    client.post("/evidence", json={
        "source_url": "https://wiki/x", "at": 0.0,
        "weight": "third_party", "source": "wayback",
    })
    r = client.post("/cite", json={
        "agent_id": "A", "session_id": "s1",
        "source_url": "https://wiki/x",
        "claim": "Paris is the capital of France",
        "cited_passage": "Paris is the capital of France",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["verdict"] == "INDEPENDENT" and body["action"] == "ALLOW"


def test_circular_citation_blocked(client):
    # Agent uploads a page then cites it.
    client.post("/observe", json={
        "agent_id": "A", "session_id": "s2", "method": "POST",
        "url": "https://host/self", "body": "An answer the agent wrote itself.",
        "ts": 2000.0, "idempotency_key": "p1",
    })
    client.post("/observe", json={
        "agent_id": "A", "session_id": "s2", "method": "GET",
        "url": "https://host/self", "body": "An answer the agent wrote itself.",
        "ts": 2001.0, "idempotency_key": "k2",
    })
    r = client.post("/cite", json={
        "agent_id": "A", "session_id": "s2",
        "source_url": "https://host/self",
        "claim": "The agent wrote this",
        "cited_passage": "An answer the agent wrote itself",
    })
    body = r.json()
    assert body["verdict"] == "CIRCULAR" and body["action"] == "BLOCK"


def test_review_queue_and_resolution(client):
    client.post("/observe", json={
        "agent_id": "A", "session_id": "s3", "method": "GET",
        "url": "https://news/x", "body": "<p>Some news body</p>",
        "ts": 3000.0, "idempotency_key": "k3",
    })
    # No evidence posted -> UNVERIFIED -> REQUIRE_REVIEW.
    r = client.post("/cite", json={
        "agent_id": "A", "session_id": "s3",
        "source_url": "https://news/x",
        "claim": "Some news body",
        "cited_passage": "Some news body",
    })
    cid = r.json()["citation_id"]
    reviews = client.get("/reviews").json()
    assert reviews["count"] >= 1
    resolved = client.post(f"/reviews/{cid}", json={
        "approve": True, "reviewer": "abhiram", "note": "known-good source",
    })
    assert resolved.json()["resolved"] is True
