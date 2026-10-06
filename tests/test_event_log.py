import tempfile
from pathlib import Path

import pytest

from daylight.event_log import EventLog


@pytest.fixture()
def log(tmp_path):
    return EventLog(tmp_path / "log.sqlite")


def test_append_and_iter(log):
    log.append("s1", "fetch", {"url": "https://a"}, idempotency_key="k1")
    log.append("s1", "publish", {"url": "https://b"}, idempotency_key="k2")
    kinds = [e.kind for e in log.iter_session("s1")]
    assert kinds == ["fetch", "publish"]


def test_idempotent_append(log):
    a = log.append("s1", "fetch", {"url": "https://a"}, idempotency_key="k1")
    b = log.append("s1", "fetch", {"url": "https://a"}, idempotency_key="k1")
    assert a.seq == b.seq and a.hash == b.hash
    assert len(list(log.iter_session("s1"))) == 1


def test_chain_links(log):
    log.append("s1", "fetch", {"url": "https://a"}, idempotency_key="k1")
    log.append("s1", "fetch", {"url": "https://b"}, idempotency_key="k2")
    log.append("s1", "fetch", {"url": "https://c"}, idempotency_key="k3")
    assert log.verify_chain("s1") is True


def test_detects_tamper(tmp_path):
    log = EventLog(tmp_path / "log.sqlite")
    log.append("s1", "fetch", {"url": "https://a"}, idempotency_key="k1")
    log.append("s1", "fetch", {"url": "https://b"}, idempotency_key="k2")
    # Tamper: rewrite payload of event 1 directly in SQLite.
    log._conn.execute(
        "UPDATE events SET payload_json=? WHERE seq=1",
        ('{"url":"https://evil"}',),
    )
    assert log.verify_chain("s1") is False


def test_merkle_root_stable(log):
    for i in range(5):
        log.append("s1", "fetch", {"url": f"https://a/{i}"}, idempotency_key=f"k{i}")
    r1 = log.merkle_root("s1")
    r2 = log.merkle_root("s1")
    assert r1 == r2 and len(r1) == 64


def test_sessions_isolated(log):
    log.append("s1", "fetch", {}, idempotency_key="k")
    log.append("s2", "fetch", {}, idempotency_key="k")  # same key, different session
    assert len(list(log.iter_session("s1"))) == 1
    assert len(list(log.iter_session("s2"))) == 1
