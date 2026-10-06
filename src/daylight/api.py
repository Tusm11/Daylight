"""
HTTP API.

Thin FastAPI layer that wires the components together:

- `POST /observe`  -- proxy feeds in a request it just saw.
- `POST /cite`     -- the agent (or its runner) asks the gate whether a
                      proposed citation is OK. Returns Decision + queues
                      for human review if applicable.
- `GET  /reviews`  -- review queue; everything BLOCK / REQUIRE_REVIEW.
- `POST /reviews/{id}` -- human resolves a review.
- `GET  /citations/{id}` -- run-explorer payload.
- `GET  /citations/{id}/trail` -- provenance view payload.
- `GET  /dashboard` -- counts + system health.

The slow checks (temporal fusion, entailment) run inline here for
simplicity. Moving them to a background queue is a one-function change:
set `REQUIRE_REVIEW` on the citation and resolve it later.
"""
from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .checks.evidence_verifier import verify
from .checks.origin_check import check_origin
from .checks.snapshot_check import contains_passage
from .checks.temporal_fusion import Evidence, fuse
from .event_log import EventLog
from .gate import Action, Decision, Verdict, decide
from .output_registry import OutputRegistry
from .provenance import ProvenanceGraph
from .proxy import ProxyCore
from .snapshot_store import SnapshotStore


# ---- state -----------------------------------------------------------------

class Daylight:
    """Everything the HTTP layer needs. One per agent session for now."""

    def __init__(self, data_dir: str = "~/.daylight/data"):
        base = Path(data_dir).expanduser()
        base.mkdir(parents=True, exist_ok=True)
        self.log = EventLog(base / "events.sqlite")
        self.snapshots = SnapshotStore(base / "snapshots.sqlite")
        self.registry = OutputRegistry(base / "registry.sqlite",
                                       lsh_threshold=0.3)
        self.graph = ProvenanceGraph()
        self.task_start_by_session: dict[str, float] = {}
        self.evidence_by_source: dict[str, list[Evidence]] = {}
        self.decisions: dict[str, dict] = {}   # citation_id -> decision record
        self.reviews: list[dict] = []

    # --- helpers --------------------------------------------------------

    def proxy_for(self, agent_id: str, session_id: str) -> ProxyCore:
        return ProxyCore(log=self.log, snapshots=self.snapshots,
                         registry=self.registry, graph=self.graph,
                         agent_id=agent_id, session_id=session_id)


_app_state = Daylight()
app = FastAPI(title="Daylight", version="0.1.0")


@app.get("/health")
def health() -> dict:
    return {"ok": True, "ts": time.time()}


# ---- observe (proxy pipe) -------------------------------------------------

class ObserveIn(BaseModel):
    agent_id: str
    session_id: str
    method: str
    url: str
    body: str = ""
    ts: Optional[float] = None
    idempotency_key: Optional[str] = None


@app.post("/observe")
def observe(req: ObserveIn) -> dict:
    ts = req.ts if req.ts is not None else time.time()
    _app_state.task_start_by_session.setdefault(req.session_id, ts)
    core = _app_state.proxy_for(req.agent_id, req.session_id)
    key = req.idempotency_key or f"auto_{uuid.uuid4().hex}"
    obs = core.observe(method=req.method, url=req.url, body=req.body,
                       ts=ts, idempotency_key=key)
    return obs.__dict__


# ---- external evidence feed ----------------------------------------------

class EvidenceIn(BaseModel):
    source_url: str
    at: float
    weight: str = Field(pattern="^(third_party|self_asserted|agent_touched)$")
    source: str   # label, e.g. "wayback"


@app.post("/evidence")
def post_evidence(req: EvidenceIn) -> dict:
    bucket = _app_state.evidence_by_source.setdefault(req.source_url, [])
    bucket.append(Evidence(at=req.at, weight=req.weight, source=req.source))
    return {"ok": True, "count": len(bucket)}


# ---- cite (the gate) ------------------------------------------------------

class CiteIn(BaseModel):
    agent_id: str
    session_id: str
    source_url: str
    claim: str
    cited_passage: str
    citation_id: Optional[str] = None


@app.post("/cite")
def cite(req: CiteIn) -> dict:
    now = time.time()
    task_start = _app_state.task_start_by_session.get(req.session_id, now)

    # Pin the citation to the most recent fetch of this URL in this session.
    fetches = _app_state.snapshots.fetches_for_url_in_session(
        req.source_url, req.session_id)
    if not fetches:
        raise HTTPException(400, "no snapshot: agent must fetch the source first")
    fetch = fetches[-1]
    snap = _app_state.snapshots.get(fetch.content_hash)
    assert snap is not None

    # Register the citation in the provenance graph.
    cid = req.citation_id or f"c_{uuid.uuid4().hex[:12]}"
    read_id = f"r_{snap.content_hash[:12]}_{int(fetch.fetched_at * 1000)}"
    _app_state.graph.add_citation(cid, read_id=read_id, claim=req.claim,
                                  cited_passage=req.cited_passage,
                                  agent_id=req.agent_id, made_at=now)

    # Run the four checks.
    snap_res = contains_passage(snap, req.cited_passage)
    origin_res = check_origin(cid, snap.cleaned_text, req.agent_id, now,
                              _app_state.graph, _app_state.registry)
    temp_res = fuse(_app_state.evidence_by_source.get(req.source_url, []),
                    task_start)
    ev_res = verify(req.claim, req.cited_passage)

    decision = decide(snapshot=snap_res, origin=origin_res,
                      temporal=temp_res, evidence=ev_res)

    record = {
        "citation_id": cid,
        "agent_id": req.agent_id,
        "session_id": req.session_id,
        "source_url": req.source_url,
        "claim": req.claim,
        "cited_passage": req.cited_passage,
        "snapshot_hash": snap.content_hash,
        "verdict": decision.verdict,
        "action": decision.action,
        "reasons": list(decision.reasons),
        "checks": {
            "snapshot": snap_res.__dict__,
            "origin": {"verdict": origin_res.verdict,
                       "reason": origin_res.reason,
                       "graph_loop": origin_res.graph_loop},
            "temporal": temp_res.__dict__,
            "evidence": ev_res.__dict__,
        },
        "created_at": now,
        "resolved": decision.action == Action.ALLOW,
    }
    _app_state.decisions[cid] = record
    if decision.action != Action.ALLOW:
        _app_state.reviews.append(record)
    return record


# ---- review queue --------------------------------------------------------

@app.get("/reviews")
def list_reviews() -> dict:
    pending = [r for r in _app_state.reviews if not r["resolved"]]
    return {"count": len(pending), "items": pending}


class ReviewResolve(BaseModel):
    approve: bool
    reviewer: str
    note: str = ""


@app.post("/reviews/{citation_id}")
def resolve_review(citation_id: str, req: ReviewResolve) -> dict:
    rec = _app_state.decisions.get(citation_id)
    if rec is None:
        raise HTTPException(404, "unknown citation")
    rec["resolved"] = True
    rec["human_resolution"] = {
        "approve": req.approve, "reviewer": req.reviewer, "note": req.note,
        "resolved_at": time.time(),
    }
    return rec


# ---- run explorer / provenance view --------------------------------------

@app.get("/citations/{citation_id}")
def get_citation(citation_id: str) -> dict:
    rec = _app_state.decisions.get(citation_id)
    if rec is None:
        raise HTTPException(404, "unknown citation")
    return rec


@app.get("/citations/{citation_id}/trail")
def citation_trail(citation_id: str) -> dict:
    g = _app_state.graph
    trail = g.citation_trail(citation_id)
    return {
        "trail": [{"id": n, **{k: v for k, v in g.g.nodes[n].items()
                                if k != "kind"}, "kind": g.g.nodes[n]["kind"].value}
                   for n in trail],
        "loop": g.is_circular(citation_id),
    }


# ---- dashboard -----------------------------------------------------------

@app.get("/dashboard")
def dashboard() -> dict:
    counts: dict[str, int] = {v.value: 0 for v in Verdict}
    for r in _app_state.decisions.values():
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    return {
        "verdicts": counts,
        "pending_reviews": sum(1 for r in _app_state.reviews if not r["resolved"]),
        "sessions": len(_app_state.task_start_by_session),
        "snapshots": len(list(_app_state.graph.g.nodes)),
    }
