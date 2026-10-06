"""
Content fingerprinting primitives.

Three levels of granularity, each useful at a different stage of the pipeline:

- `sha256_hex(text)` -- exact-match hash. Cheap, brittle. Used as the storage
  key in the snapshot store and as the first-stage filter in the registry.
- `MinHash` of word shingles -- near-duplicate detector via Jaccard similarity.
  Resilient to small edits (reflow, boilerplate changes, inserted ads).
- `simhash64(text)` -- 64-bit locality-sensitive hash. Compared by Hamming
  distance. Useful when we want a single integer per document rather than
  256 permuted hashes.

All functions are deterministic and side-effect free so verdicts stay
reproducible. The registry layer builds its indexes on top of these.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

import mmh3
from datasketch import MinHash

_WORD = re.compile(r"\w+", re.UNICODE)


def normalize(text: str) -> str:
    """Lowercase + collapse whitespace. Shared by every fingerprint."""
    return " ".join(text.lower().split())


def tokens(text: str) -> list[str]:
    return _WORD.findall(normalize(text))


def shingles(text: str, k: int = 5) -> list[str]:
    """Word k-shingles. `k=5` is standard for near-dup web text."""
    toks = tokens(text)
    if len(toks) < k:
        return [" ".join(toks)] if toks else []
    return [" ".join(toks[i : i + k]) for i in range(len(toks) - k + 1)]


def sha256_hex(text: str) -> str:
    return hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()


def minhash(text: str, num_perm: int = 128, k: int = 5) -> MinHash:
    m = MinHash(num_perm=num_perm)
    for sh in shingles(text, k=k):
        m.update(sh.encode("utf-8"))
    return m


def minhash_similarity(a: MinHash, b: MinHash) -> float:
    """Estimated Jaccard. Range [0, 1]."""
    return float(a.jaccard(b))


def simhash64(text: str, k: int = 5) -> int:
    """64-bit SimHash over word shingles."""
    v = [0] * 64
    for sh in shingles(text, k=k):
        h = mmh3.hash64(sh.encode("utf-8"), signed=False)[0]
        for i in range(64):
            v[i] += 1 if (h >> i) & 1 else -1
    out = 0
    for i, s in enumerate(v):
        if s > 0:
            out |= 1 << i
    return out


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


@dataclass(frozen=True)
class Fingerprint:
    """Bundle of all three fingerprints for one document."""

    sha256: str
    simhash: int
    minhash_digest: tuple[int, ...]  # the 128 permuted hashes, as a tuple

    @classmethod
    def of(cls, text: str) -> "Fingerprint":
        mh = minhash(text)
        return cls(
            sha256=sha256_hex(text),
            simhash=simhash64(text),
            minhash_digest=tuple(int(x) for x in mh.hashvalues),
        )

    def to_minhash(self) -> MinHash:
        mh = MinHash(num_perm=len(self.minhash_digest))
        mh.hashvalues[:] = self.minhash_digest
        return mh
