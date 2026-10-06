from daylight.provenance import NodeKind, ProvenanceGraph


def _circular_graph():
    g = ProvenanceGraph()
    g.add_artifact("art1", "agent-A", produced_at=10.0, content_hash="h1")
    g.add_publish("pub1", "art1", "https://host/a", published_at=11.0,
                  http_method="POST")
    g.add_read("r1", "https://host/a", session_id="s1", fetched_at=12.0,
              content_hash="h1")
    g.add_citation("c1", "r1", claim="X", cited_passage="X exists",
                   agent_id="agent-A", made_at=13.0)
    return g


def test_detects_self_publish_cycle():
    g = _circular_graph()
    loop = g.is_circular("c1")
    assert loop is not None
    assert loop["agent"] == "agent-A"
    assert loop["artifact"] == "art1"


def test_independent_citation_not_flagged():
    g = ProvenanceGraph()
    g.add_source("https://wiki/x")  # no agent publish touched this
    g.add_read("r1", "https://wiki/x", session_id="s1", fetched_at=5.0,
              content_hash="h")
    g.add_citation("c1", "r1", "X", "X exists", agent_id="agent-A", made_at=6.0)
    assert g.is_circular("c1") is None


def test_different_agent_publishes_source_not_circular():
    g = ProvenanceGraph()
    g.add_artifact("art1", "agent-B", produced_at=1.0, content_hash="h")
    g.add_publish("pub1", "art1", "https://host/a", published_at=2.0,
                  http_method="POST")
    g.add_read("r1", "https://host/a", session_id="s1", fetched_at=3.0,
              content_hash="h")
    g.add_citation("c1", "r1", "X", "X exists", agent_id="agent-A", made_at=4.0)
    # agent-A is the claim-maker; agent-B owns the source. Not circular.
    assert g.is_circular("c1") is None


def test_citation_before_artifact_not_circular():
    g = ProvenanceGraph()
    g.add_artifact("art1", "agent-A", produced_at=100.0, content_hash="h")
    g.add_publish("pub1", "art1", "https://host/a", published_at=101.0,
                  http_method="POST")
    g.add_read("r1", "https://host/a", session_id="s1", fetched_at=5.0,
              content_hash="h")
    g.add_citation("c1", "r1", "X", "X exists", agent_id="agent-A", made_at=10.0)
    assert g.is_circular("c1") is None


def test_nodes_of():
    g = _circular_graph()
    assert len(g.nodes_of(NodeKind.AGENT)) == 1
    assert len(g.nodes_of(NodeKind.CITATION)) == 1
    assert len(g.nodes_of(NodeKind.PUBLISH)) == 1
