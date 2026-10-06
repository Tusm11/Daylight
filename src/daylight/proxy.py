"""
Egress proxy.

Sits at the sandbox boundary so EVERY byte the agent sends outbound is
visible, not just its tool calls. This is the first thing the README
mentions in Capture and is where the agent-untouched event log begins.

For this reference implementation the proxy is an HTTP forward proxy
that:

- Logs every request (method, URL, headers digest, bodies summarized or
  hashed) to the `EventLog`.
- For responses to GET, stores the response body as a `Snapshot` and
  records a `fetches` row.
- For outbound WRITES (POST/PUT/PATCH/DELETE and git-style URLs), records
  a `publish` event with the body registered as an artifact so the
  registry can match it later.
- Forwards the request unchanged. The proxy MUST be transparent to the
  agent -- its role is to observe, not to transform.

HTTPS CONNECT tunneling is not implemented here (TLS-terminating proxies
need a per-sandbox trust anchor, which is a deployment concern). The
interface is designed to make that drop-in later.

For testing we expose a plain `ProxyCore` that takes a dict-shaped
request and returns a dict-shaped observation. The HTTP wiring is a thin
adapter around it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .event_log import EventLog
from .output_registry import OutputRegistry
from .provenance import ProvenanceGraph
from .snapshot_store import SnapshotStore, clean


_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


@dataclass(frozen=True)
class ProxyObservation:
    kind: str                       # "fetch" | "publish"
    event_seq: int
    url: str
    snapshot_hash: Optional[str]    # set on fetch
    artifact_id: Optional[str]      # set on publish
    publish_id: Optional[str]       # set on publish


class ProxyCore:
    """Pure-logic core of the egress proxy. HTTP server wraps this."""

    def __init__(self, *, log: EventLog, snapshots: SnapshotStore,
                 registry: OutputRegistry, graph: ProvenanceGraph,
                 agent_id: str, session_id: str):
        self.log = log
        self.snapshots = snapshots
        self.registry = registry
        self.graph = graph
        self.agent_id = agent_id
        self.session_id = session_id

    def observe(self, *, method: str, url: str, body: str, ts: float,
                idempotency_key: str) -> ProxyObservation:
        method = method.upper()
        if method in _WRITE_METHODS:
            return self._observe_publish(method, url, body, ts, idempotency_key)
        return self._observe_fetch(method, url, body, ts, idempotency_key)

    def _observe_fetch(self, method: str, url: str, body: str, ts: float,
                       key: str) -> ProxyObservation:
        snap = self.snapshots.put(body)
        self.snapshots.record_fetch(snap, source_url=url,
                                    session_id=self.session_id,
                                    fetched_at=ts)
        read_id = f"r_{snap.content_hash[:12]}_{int(ts * 1000)}"
        self.graph.add_read(read_id, source_url=url, session_id=self.session_id,
                            fetched_at=ts, content_hash=snap.content_hash)
        ev = self.log.append(
            self.session_id, "fetch",
            {"method": method, "url": url, "content_hash": snap.content_hash,
             "read_id": read_id},
            idempotency_key=key, ts=ts,
        )
        return ProxyObservation("fetch", ev.seq, url, snap.content_hash,
                                None, None)

    def _observe_publish(self, method: str, url: str, body: str, ts: float,
                         key: str) -> ProxyObservation:
        cleaned = clean(body)
        artifact = self.registry.register(
            cleaned, agent_id=self.agent_id, session_id=self.session_id,
            produced_at=ts,
        )
        self.graph.add_artifact(artifact.artifact_id, self.agent_id,
                                produced_at=ts,
                                content_hash=artifact.content_hash)
        publish_id = f"p_{artifact.content_hash[:12]}_{int(ts * 1000)}"
        self.graph.add_publish(publish_id, artifact.artifact_id,
                               target_url=url, published_at=ts,
                               http_method=method)
        ev = self.log.append(
            self.session_id, "publish",
            {"method": method, "url": url,
             "artifact_id": artifact.artifact_id,
             "publish_id": publish_id,
             "content_hash": artifact.content_hash},
            idempotency_key=key, ts=ts,
        )
        return ProxyObservation("publish", ev.seq, url, None,
                                artifact.artifact_id, publish_id)
