"""
Provenance graph.

The spine of a Daylight investigation. The README diagrams it as:

    agent -> artifact -> publish -> source -> read -> citation

Each node is one of:

- **Agent.** The session that is being audited.
- **Artifact.** Something the agent produced inside the sandbox (a draft
  answer, a file written to disk, a snippet it emitted to a tool).
- **Publish.** A moment at which an artifact escaped the sandbox: an HTTP
  POST/PUT, a `git push`, an upload, an edit of an existing page.
- **Source.** A URL + revision that the agent then fetched.
- **Read.** A specific fetch of a source during the task.
- **Citation.** The attribution the agent attached to its final answer.

The whole point of the graph is to make one specific pattern cheaply
detectable: a citation whose source is downstream of the same agent that
is making the claim. The `is_circular(citation_id)` method does exactly
that -- walks back from the citation and reports whether the trail returns
to the same agent through a publish event.

Backed by networkx in-memory; the DB is the event log (events replay into
a graph), so the graph itself is derived, cacheable state.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import networkx as nx


class NodeKind(str, Enum):
    AGENT = "agent"
    ARTIFACT = "artifact"
    PUBLISH = "publish"
    SOURCE = "source"
    READ = "read"
    CITATION = "citation"


@dataclass
class ProvenanceGraph:
    g: nx.DiGraph = field(default_factory=nx.DiGraph)

    # ---- node builders ----------------------------------------------------

    def add_agent(self, agent_id: str) -> str:
        node = f"agent:{agent_id}"
        self.g.add_node(node, kind=NodeKind.AGENT, agent_id=agent_id)
        return node

    def add_artifact(self, artifact_id: str, agent_id: str,
                     produced_at: float, content_hash: str) -> str:
        agent_node = self.add_agent(agent_id)
        node = f"artifact:{artifact_id}"
        self.g.add_node(node, kind=NodeKind.ARTIFACT,
                        produced_at=produced_at, content_hash=content_hash)
        self.g.add_edge(agent_node, node, label="produces")
        return node

    def add_publish(self, publish_id: str, artifact_id: str,
                    target_url: str, published_at: float,
                    http_method: str) -> str:
        node = f"publish:{publish_id}"
        self.g.add_node(node, kind=NodeKind.PUBLISH, target_url=target_url,
                        published_at=published_at, http_method=http_method)
        self.g.add_edge(f"artifact:{artifact_id}", node, label="published_as")
        source = self.add_source(target_url)
        # A publish produces/edits a source revision.
        self.g.add_edge(node, source, label="writes_to")
        return node

    def add_source(self, url: str) -> str:
        node = f"source:{url}"
        if node not in self.g:
            self.g.add_node(node, kind=NodeKind.SOURCE, url=url)
        return node

    def add_read(self, read_id: str, source_url: str, session_id: str,
                 fetched_at: float, content_hash: str) -> str:
        source = self.add_source(source_url)
        node = f"read:{read_id}"
        self.g.add_node(node, kind=NodeKind.READ, session_id=session_id,
                        fetched_at=fetched_at, content_hash=content_hash)
        self.g.add_edge(source, node, label="observed_as")
        return node

    def add_citation(self, citation_id: str, read_id: str, claim: str,
                     cited_passage: str, agent_id: str,
                     made_at: Optional[float] = None) -> str:
        made_at = made_at if made_at is not None else time.time()
        node = f"citation:{citation_id}"
        self.g.add_node(node, kind=NodeKind.CITATION, claim=claim,
                        cited_passage=cited_passage, agent_id=agent_id,
                        made_at=made_at)
        self.g.add_edge(f"read:{read_id}", node, label="supports")
        return node

    # ---- queries ----------------------------------------------------------

    def citation_trail(self, citation_id: str) -> list[str]:
        """Nodes between the citation and any upstream agent, closest first."""
        node = f"citation:{citation_id}"
        if node not in self.g:
            return []
        order = [node]
        # Walk predecessors breadth-first.
        frontier = [node]
        seen = {node}
        while frontier:
            nxt = []
            for n in frontier:
                for p in self.g.predecessors(n):
                    if p in seen:
                        continue
                    seen.add(p)
                    order.append(p)
                    nxt.append(p)
            frontier = nxt
        return order

    def is_circular(self, citation_id: str) -> Optional[dict]:
        """
        If the citation traces back through a publish by the same agent that
        made the claim, return a dict describing the loop. Otherwise None.
        """
        c_node = f"citation:{citation_id}"
        if c_node not in self.g:
            return None
        claim_agent = self.g.nodes[c_node]["agent_id"]
        claim_time = self.g.nodes[c_node]["made_at"]
        trail = self.citation_trail(citation_id)
        for n in trail:
            if self.g.nodes[n]["kind"] != NodeKind.PUBLISH:
                continue
            # Walk back from the publish to its artifact and the agent.
            for p in self.g.predecessors(n):
                if self.g.nodes[p]["kind"] != NodeKind.ARTIFACT:
                    continue
                for pp in self.g.predecessors(p):
                    if (self.g.nodes[pp]["kind"] == NodeKind.AGENT
                            and self.g.nodes[pp]["agent_id"] == claim_agent):
                        # Directional ordering: artifact must come before claim.
                        if self.g.nodes[p]["produced_at"] < claim_time:
                            return {
                                "agent": claim_agent,
                                "artifact": p.split(":", 1)[1],
                                "publish": n.split(":", 1)[1],
                                "published_at": self.g.nodes[n]["published_at"],
                                "produced_at": self.g.nodes[p]["produced_at"],
                            }
        return None

    def nodes_of(self, kind: NodeKind) -> list[str]:
        return [n for n, d in self.g.nodes(data=True) if d.get("kind") == kind]
