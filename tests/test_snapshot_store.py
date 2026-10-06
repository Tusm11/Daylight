import pytest

from daylight.snapshot_store import SnapshotStore, clean


@pytest.fixture()
def store(tmp_path):
    return SnapshotStore(tmp_path / "snaps.sqlite")


def test_clean_strips_tags_and_scripts():
    html = "<html><head><style>x{}</style></head><body>Hello <b>World</b></body></html>"
    assert clean(html) == "hello world"


def test_put_idempotent(store):
    s1 = store.put("<p>hello world</p>")
    s2 = store.put("<p>hello   WORLD</p>")  # normalizes to same text
    assert s1.content_hash == s2.content_hash


def test_get_roundtrip(store):
    s = store.put("<p>something to remember</p>")
    assert store.get(s.content_hash).cleaned_text == "something to remember"


def test_fetch_pinning(store):
    s = store.put("<p>page body</p>")
    f = store.record_fetch(s, "https://example.com/a", "sess1", fetched_at=100.0)
    hits = store.fetches_for_url_in_session("https://example.com/a", "sess1")
    assert len(hits) == 1 and hits[0].id == f.id and hits[0].fetched_at == 100.0


def test_fingerprints_populated(store):
    s = store.put("daylight checks that cited sources are independent of the agent")
    assert s.simhash != 0
    assert len(s.minhash_digest) == 128
