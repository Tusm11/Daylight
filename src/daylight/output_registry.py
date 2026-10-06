"""
Agent Output Registry.

Fingerprints of everything any agent in the organization has written or
published, across all sessions. The origin check queries this to see if a
cited source looks like something an agent produced earlier.

Layered lookup, cheap to expensive, as the README spells out:

1. **Bloom filter** on exact content hashes -- O(k) check, no false
   negatives for exact hashes. Elimates the easy case in a hurry.
2. **MinHash LSH** on word shingles -- near-duplicate retrieval. Catches
   reworded, reflowed, boilerplate-swapped copies.
3. (Not implemented here) semantic vector index -- would come next, for
   heavier paraphrase. Interface is left in place.

Every registered artifact carries `(agent_id, session_id, produced_at)`.
The origin check uses `produced_at` to make similarity *directional*:
similarity only indicates circularity when the agent's artifact came
first.

Lookups never block on each other; a Bloom miss short-circuits the LSH
query. On a hit, the result is the full registry row with timing attached,
so the caller can apply the ordering rule.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from datasketch import MinHash, MinHashLSH

from .fingerprint import Fingerprint, minhash, sha256_hex


_SCHEMA = """
CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id    TEXT PRIMARY KEY,
    agent_id       TEXT NOT NULL,
    session_id     TEXT NOT NULL,
    produced_at    REAL NOT NULL,
    content_hash   TEXT NOT NULL,
    simhash        TEXT NOT NULL,
    minhash_json   TEXT NOT NULL,
    text_preview   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS artifacts_hash ON artifacts(content_hash);
"""


@dataclass(frozen=True)
class Artifact:
    artifact_id: str
    agent_id: str
    session_id: str
    produced_at: float
    content_hash: str
    simhash: int
    minhash_digest: tuple[int, ...]
    text_preview: str


@dataclass(frozen=True)
class RegistryHit:
    artifact: Artifact
    jaccard: float   # 1.0 for exact hash matches
    exact: bool


class BloomFilter:
    """Minimal bit-array Bloom. No external dep, enough for the registry."""

    def __init__(self, size_bits: int = 1 << 18, k: int = 7):
        self.size = size_bits
        self.k = k
        self.bits = bytearray(size_bits // 8)

    def _positions(self, item: str) -> list[int]:
        # Two independent hashes, derive k with double hashing.
        h1 = int.from_bytes(hashlib.sha256(item.encode()).digest()[:8], "big")
        h2 = int.from_bytes(hashlib.sha256(item.encode() + b"salt").digest()[:8], "big")
        return [(h1 + i * h2) % self.size for i in range(self.k)]

    def add(self, item: str) -> None:
        for p in self._positions(item):
            self.bits[p >> 3] |= 1 << (p & 7)

    def __contains__(self, item: str) -> bool:
        return all((self.bits[p >> 3] >> (p & 7)) & 1 for p in self._positions(item))


class OutputRegistry:
    """Registry over everything agents in this org have produced."""

    def __init__(self, path: str | Path, lsh_threshold: float = 0.5,
                 num_perm: int = 128):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
        try:
            self._conn.execute("PRAGMA journal_mode=WAL")
        except Exception:
            pass  # some mounts (fuse etc.) do not support WAL
        self._conn.executescript(_SCHEMA)
        self._bloom = BloomFilter()
        self._lsh = MinHashLSH(threshold=lsh_threshold, num_perm=num_perm)
        self._num_perm = num_perm
        self._load_indexes()

    def _load_indexes(self) -> None:
        for a in self._all():
            self._bloom.add(a.content_hash)
            self._lsh.insert(a.artifact_id, _rehydrate_mh(a.minhash_digest,
                                                         self._num_perm))

    # ---- registration -----------------------------------------------------

    def register(self, text: str, *, agent_id: str, session_id: str,
                 produced_at: Optional[float] = None,
                 artifact_id: Optional[str] = None) -> Artifact:
        produced_at = produced_at if produced_at is not None else time.time()
        fp = Fingerprint.of(text)
        aid = artifact_id or f"a_{fp.sha256[:16]}_{int(produced_at * 1000)}"
        preview = text[:300]
        self._conn.execute(
            "INSERT OR REPLACE INTO artifacts (artifact_id, agent_id, "
            "session_id, produced_at, content_hash, simhash, minhash_json, "
            "text_preview) VALUES (?,?,?,?,?,?,?,?)",
            (aid, agent_id, session_id, produced_at, fp.sha256, str(fp.simhash),
             json.dumps(list(fp.minhash_digest)), preview),
        )
        artifact = Artifact(aid, agent_id, session_id, produced_at, fp.sha256,
                            fp.simhash, fp.minhash_digest, preview)
        self._bloom.add(artifact.content_hash)
        if aid in self._lsh:
            self._lsh.remove(aid)
        self._lsh.insert(aid, _rehydrate_mh(artifact.minhash_digest,
                                            self._num_perm))
        return artifact

    # ---- lookup -----------------------------------------------------------

    def find_matches(self, text: str, *, min_jaccard: float = 0.5
                     ) -> list[RegistryHit]:
        """Return every registry artifact matching this text."""
        content_hash = sha256_hex(text)
        hits: list[RegistryHit] = []

        # Stage 1: exact hash via bloom then DB.
        if content_hash in self._bloom:
            for art in self._by_hash(content_hash):
                hits.append(RegistryHit(art, jaccard=1.0, exact=True))

        # Stage 2: near-dup LSH.
        q = minhash(text, num_perm=self._num_perm)
        for aid in self._lsh.query(q):
            if any(h.artifact.artifact_id == aid for h in hits):
                continue
            art = self._by_id(aid)
            if art is None:
                continue
            # Score the candidate directly, LSH only narrows.
            j = q.jaccard(_rehydrate_mh(art.minhash_digest, self._num_perm))
            if j >= min_jaccard:
                hits.append(RegistryHit(art, jaccard=float(j), exact=False))

        hits.sort(key=lambda h: (-h.jaccard, h.artifact.produced_at))
        return hits

    # ---- helpers ----------------------------------------------------------

    def _by_hash(self, content_hash: str) -> list[Artifact]:
        rows = self._conn.execute(
            "SELECT artifact_id, agent_id, session_id, produced_at, "
            "content_hash, simhash, minhash_json, text_preview "
            "FROM artifacts WHERE content_hash=?",
            (content_hash,),
        ).fetchall()
        return [_row_to_art(r) for r in rows]

    def _by_id(self, aid: str) -> Optional[Artifact]:
        row = self._conn.execute(
            "SELECT artifact_id, agent_id, session_id, produced_at, "
            "content_hash, simhash, minhash_json, text_preview "
            "FROM artifacts WHERE artifact_id=?",
            (aid,),
        ).fetchone()
        return _row_to_art(row) if row else None

    def _all(self) -> Iterable[Artifact]:
        rows = self._conn.execute(
            "SELECT artifact_id, agent_id, session_id, produced_at, "
            "content_hash, simhash, minhash_json, text_preview "
            "FROM artifacts"
        ).fetchall()
        return [_row_to_art(r) for r in rows]


def _row_to_art(row: tuple) -> Artifact:
    aid, agent_id, session_id, produced_at, content_hash, simhash, mh_json, prev = row
    return Artifact(aid, agent_id, session_id, produced_at, content_hash,
                    int(simhash), tuple(json.loads(mh_json)), prev)


def _rehydrate_mh(digest: tuple[int, ...], num_perm: int) -> MinHash:
    mh = MinHash(num_perm=num_perm)
    mh.hashvalues[:] = digest
    return mh
