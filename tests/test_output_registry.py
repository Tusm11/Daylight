import pytest

from daylight.output_registry import BloomFilter, OutputRegistry


def test_bloom_no_false_negative():
    b = BloomFilter(size_bits=1024, k=5)
    b.add("abc")
    b.add("xyz")
    assert "abc" in b
    assert "xyz" in b


@pytest.fixture()
def reg(tmp_path):
    return OutputRegistry(tmp_path / "reg.sqlite", lsh_threshold=0.2)


def test_registry_exact_match(reg):
    txt = "the agent uploaded its own answer to a public host and cited the link"
    reg.register(txt, agent_id="agent-A", session_id="s1")
    hits = reg.find_matches(txt)
    assert len(hits) == 1 and hits[0].exact and hits[0].jaccard == 1.0


def test_registry_near_duplicate(reg):
    original = (
        "Daylight checks whether cited sources are independent of the agent, "
        "using a provenance graph, snapshots, and a tamper-evident log."
    )
    paraphrase = (
        "Daylight checks whether cited sources are independent of the agent "
        "by relying on a provenance graph, snapshots and a tamper-evident log."
    )
    reg.register(original, agent_id="agent-A", session_id="s1", produced_at=1.0)
    hits = reg.find_matches(paraphrase, min_jaccard=0.3)
    assert hits, "expected near-dup match"
    assert hits[0].exact is False
    assert hits[0].jaccard >= 0.3


def test_registry_no_match_for_unrelated(reg):
    reg.register("something about citations and independence", agent_id="A",
                 session_id="s1")
    hits = reg.find_matches(
        "the Nagoya plant reported strong quarterly earnings in the latest release"
    )
    assert hits == []


def test_registry_persists_across_instances(tmp_path):
    r1 = OutputRegistry(tmp_path / "reg.sqlite", lsh_threshold=0.2)
    txt = "artifact text produced by the agent during an earlier session"
    r1.register(txt, agent_id="A", session_id="s1")
    r2 = OutputRegistry(tmp_path / "reg.sqlite", lsh_threshold=0.2)  # reopen
    hits = r2.find_matches(txt)
    assert hits and hits[0].exact
