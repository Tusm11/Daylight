import pytest

from daylight.checks.evidence_verifier import verify
from daylight.checks.origin_check import check_origin
from daylight.checks.snapshot_check import contains_passage
from daylight.checks.temporal_fusion import Evidence, fuse
from daylight.output_registry import OutputRegistry
from daylight.provenance import ProvenanceGraph
from daylight.snapshot_store import SnapshotStore


# ---- snapshot_check -------------------------------------------------------

@pytest.fixture()
def store(tmp_path):
    return SnapshotStore(tmp_path / "snaps.sqlite")


def test_snapshot_check_exact(store):
    snap = store.put("<p>the capital of France is Paris and it is nice</p>")
    r = contains_passage(snap, "The capital of France is Paris")
    assert r.present and r.reason == "exact_substring"


def test_snapshot_check_overlap_passes(store):
    snap = store.put("<p>Daylight checks whether cited sources are independent</p>")
    r = contains_passage(snap, "Daylight checks that cited sources are independent",
                         min_overlap=0.7)
    assert r.present


def test_snapshot_check_missing(store):
    snap = store.put("<p>unrelated page about pastries</p>")
    r = contains_passage(snap, "the capital of France is Paris")
    assert not r.present


# ---- temporal_fusion ------------------------------------------------------

def test_temporal_third_party_establishes():
    ev = [Evidence(at=100.0, weight="third_party", source="wayback"),
          Evidence(at=50.0,  weight="self_asserted", source="page_meta")]
    r = fuse(ev, task_start=200.0)
    assert r.status == "established" and r.earliest_third_party == 100.0


def test_temporal_only_self_asserted_is_supported():
    ev = [Evidence(at=50.0, weight="self_asserted", source="page_meta")]
    r = fuse(ev, task_start=200.0)
    assert r.status == "supported"


def test_temporal_agent_touched_discarded():
    ev = [Evidence(at=50.0, weight="agent_touched", source="agent_fetch")]
    r = fuse(ev, task_start=200.0)
    assert r.status == "unknown"


def test_temporal_evidence_after_task_start_ignored():
    ev = [Evidence(at=300.0, weight="third_party", source="wayback")]
    r = fuse(ev, task_start=200.0)
    assert r.status == "unknown"


# ---- evidence_verifier ---------------------------------------------------

def test_evidence_missing_number_fails():
    r = verify("Revenue grew 11% in Q1", "Revenue grew substantially in the quarter")
    assert not r.supports and "11%" in r.missing_tokens


def test_evidence_present_passes():
    r = verify("Revenue grew 11% in 2024",
               "The company said revenue grew 11% during the 2024 fiscal year")
    assert r.supports


# ---- origin_check --------------------------------------------------------

def test_origin_detects_self_publish_via_graph(tmp_path):
    reg = OutputRegistry(tmp_path / "reg.sqlite")
    g = ProvenanceGraph()
    text = "the agent's own answer about something"
    g.add_artifact("art1", "agent-A", produced_at=10.0, content_hash="h")
    g.add_publish("pub1", "art1", "https://host/a", published_at=11.0,
                  http_method="POST")
    g.add_read("r1", "https://host/a", session_id="s1", fetched_at=12.0,
              content_hash="h")
    g.add_citation("c1", "r1", "X", "x exists", agent_id="agent-A", made_at=13.0)
    r = check_origin("c1", text, "agent-A", 13.0, g, reg)
    assert r.verdict == "self_origin"


def test_origin_clean_when_no_match(tmp_path):
    reg = OutputRegistry(tmp_path / "reg.sqlite")
    g = ProvenanceGraph()
    g.add_source("https://wiki/x")
    g.add_read("r1", "https://wiki/x", session_id="s1", fetched_at=1.0,
              content_hash="h")
    g.add_citation("c1", "r1", "X", "x exists", agent_id="agent-A", made_at=2.0)
    r = check_origin("c1", "some unrelated snapshot content", "agent-A", 2.0, g, reg)
    assert r.verdict == "clean"


def test_origin_registry_match_requires_prior_timing(tmp_path):
    reg = OutputRegistry(tmp_path / "reg.sqlite", lsh_threshold=0.2)
    g = ProvenanceGraph()
    text = (
        "Daylight checks whether cited sources are independent of the agent, "
        "backed by a provenance graph, snapshots and a tamper-evident log."
    )
    # Agent produced this AFTER the citation -> not circular even though similar.
    reg.register(text, agent_id="agent-A", session_id="s1", produced_at=100.0)
    g.add_source("https://wiki/x")
    g.add_read("r1", "https://wiki/x", session_id="s1", fetched_at=1.0,
              content_hash="h")
    g.add_citation("c1", "r1", "X", "x exists", agent_id="agent-A", made_at=10.0)
    r = check_origin("c1", text, "agent-A", 10.0, g, reg, min_jaccard=0.3)
    assert r.verdict == "clean"
