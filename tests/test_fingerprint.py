from daylight.fingerprint import (
    Fingerprint,
    hamming,
    minhash,
    minhash_similarity,
    sha256_hex,
    shingles,
    simhash64,
)


def test_sha256_normalization():
    assert sha256_hex("Hello   World") == sha256_hex("hello world")


def test_shingles_shorter_than_k():
    assert shingles("one two") == ["one two"]
    assert shingles("") == []


def test_minhash_near_duplicate():
    a = (
        "The agent uploaded a file containing its own answer to a public host "
        "and then cited the resulting link as its source."
    )
    b = (
        "The agent uploaded a file holding its own answer to a public server "
        "and then cited the resulting URL as its source."
    )
    sim = minhash_similarity(minhash(a, k=3), minhash(b, k=3))
    assert sim > 0.3, sim


def test_minhash_unrelated_low():
    sim = minhash_similarity(
        minhash(
            "Daylight checks whether cited sources are independent of the agent "
            "that produced the answer in the first place."
        ),
        minhash(
            "Quarterly revenue at the Nagoya plant grew by eleven percent year "
            "on year according to the latest earnings release."
        ),
    )
    assert sim < 0.1, sim


def test_simhash_near_duplicate_small_hamming():
    a = (
        "Daylight is a provenance aware trust layer for agentic pipelines "
        "that checks whether cited sources are independent of the agent."
    )
    b = (
        "Daylight is a provenance aware trust layer for agentic pipelines "
        "that checks whether cited sources are independent of this agent."
    )
    assert hamming(simhash64(a), simhash64(b)) < 10


def test_fingerprint_bundle_roundtrip():
    text = "some longer passage about citations and independent sources for testing"
    fp = Fingerprint.of(text)
    assert fp.sha256 == sha256_hex(text)
    assert len(fp.minhash_digest) == 128
    mh = fp.to_minhash()
    assert minhash_similarity(mh, minhash(text)) > 0.9
