import pytest

from daylight.event_log import EventLog
from daylight.output_registry import OutputRegistry
from daylight.provenance import ProvenanceGraph
from daylight.proxy import ProxyCore
from daylight.snapshot_store import SnapshotStore


@pytest.fixture()
def core(tmp_path):
    return ProxyCore(
        log=EventLog(tmp_path / "log.sqlite"),
        snapshots=SnapshotStore(tmp_path / "snap.sqlite"),
        registry=OutputRegistry(tmp_path / "reg.sqlite", lsh_threshold=0.2),
        graph=ProvenanceGraph(),
        agent_id="agent-A",
        session_id="s1",
    )


def test_fetch_records_snapshot_and_read(core):
    obs = core.observe(method="GET", url="https://e.com/x",
                       body="<p>page body</p>", ts=10.0,
                       idempotency_key="k1")
    assert obs.kind == "fetch"
    assert obs.snapshot_hash is not None
    assert core.snapshots.get(obs.snapshot_hash).cleaned_text == "page body"


def test_publish_registers_artifact(core):
    obs = core.observe(method="POST", url="https://host.com/upload",
                       body="the agent's own long answer about something",
                       ts=5.0, idempotency_key="p1")
    assert obs.kind == "publish"
    hits = core.registry.find_matches(
        "the agent's own long answer about something", min_jaccard=0.5)
    assert hits and hits[0].exact


def test_log_chain_integrity_across_proxy_events(core):
    core.observe(method="GET", url="https://a", body="a", ts=1.0,
                 idempotency_key="k1")
    core.observe(method="POST", url="https://b", body="b", ts=2.0,
                 idempotency_key="k2")
    assert core.log.verify_chain("s1")
