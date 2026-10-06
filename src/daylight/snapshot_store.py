"""
Content-addressed snapshot store.

For every page an agent fetches, Daylight keeps the cleaned main text the
agent actually saw, keyed by sha256 of its normalized form. The important
property is that *the snapshot is what we verify against*, never the live
URL. A live page can be edited after the fact, including by the agent
itself.

Each snapshot carries:

- `content_hash` -- storage key (sha256 of normalized text).
- `simhash` and `minhash` -- for near-dup and semantic-ish lookups later.
- `source_url` + `fetched_at` + `agent_session_id` -- bookkeeping needed by
  the temporal and origin checks.

Why content-addressed: the README calls this out directly ("a page fetched
by thousands of agents is stored once"). It also means every reference to a
snapshot is to a specific immutable revision, which is what the temporal
check needs ("checks are made at the revision level").

Cleaning is intentionally minimal here (strip HTML tags, collapse
whitespace). Richer boilerplate removal is a separate component we can slot
in later behind the same `clean()` call.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from .fingerprint import Fingerprint, normalize, sha256_hex, simhash64


_SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    content_hash  TEXT PRIMARY KEY,
    cleaned_text  TEXT NOT NULL,
    simhash       TEXT NOT NULL,
    minhash_json  TEXT NOT NULL,
    created_at    REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS fetches (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    content_hash  TEXT NOT NULL,
    source_url    TEXT NOT NULL,
    fetched_at    REAL NOT NULL,
    session_id    TEXT NOT NULL,
    FOREIGN KEY(content_hash) REFERENCES snapshots(content_hash)
);
CREATE INDEX IF NOT EXISTS fetches_url ON fetches(source_url);
CREATE INDEX IF NOT EXISTS fetches_session ON fetches(session_id);
"""


_TAG = re.compile(r"<[^>]+>")
_SCRIPT = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)


def clean(html_or_text: str) -> str:
    """Strip scripts/styles and tags, normalize whitespace. Deliberately simple."""
    stripped = _SCRIPT.sub(" ", html_or_text)
    stripped = _TAG.sub(" ", stripped)
    return normalize(stripped)


@dataclass(frozen=True)
class Snapshot:
    content_hash: str
    cleaned_text: str
    simhash: int
    minhash_digest: tuple[int, ...]
    created_at: float


@dataclass(frozen=True)
class Fetch:
    id: int
    content_hash: str
    source_url: str
    fetched_at: float
    session_id: str


class SnapshotStore:
    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
        try:
            self._conn.execute("PRAGMA journal_mode=WAL")
        except Exception:
            pass  # some mounts (fuse etc.) do not support WAL
        self._conn.executescript(_SCHEMA)

    # ---- snapshots --------------------------------------------------------

    def put(self, raw_text: str) -> Snapshot:
        """Store the cleaned main text. Returns the Snapshot. Idempotent."""
        cleaned = clean(raw_text)
        h = sha256_hex(cleaned)
        existing = self.get(h)
        if existing is not None:
            return existing
        fp = Fingerprint.of(cleaned)
        now = time.time()
        self._conn.execute(
            "INSERT INTO snapshots (content_hash, cleaned_text, simhash, "
            "minhash_json, created_at) VALUES (?,?,?,?,?)",
            (h, cleaned, str(fp.simhash), json.dumps(list(fp.minhash_digest)), now),
        )
        return Snapshot(h, cleaned, fp.simhash, fp.minhash_digest, now)

    def get(self, content_hash: str) -> Optional[Snapshot]:
        row = self._conn.execute(
            "SELECT content_hash, cleaned_text, simhash, minhash_json, created_at "
            "FROM snapshots WHERE content_hash=?",
            (content_hash,),
        ).fetchone()
        if row is None:
            return None
        return Snapshot(row[0], row[1], int(row[2]), tuple(json.loads(row[3])), row[4])

    # ---- fetches ----------------------------------------------------------

    def record_fetch(self, snapshot: Snapshot, source_url: str, session_id: str,
                     fetched_at: Optional[float] = None) -> Fetch:
        """Pin a snapshot to a URL+session+time -- what the agent *saw*."""
        fetched_at = fetched_at if fetched_at is not None else time.time()
        cur = self._conn.execute(
            "INSERT INTO fetches (content_hash, source_url, fetched_at, session_id) "
            "VALUES (?,?,?,?)",
            (snapshot.content_hash, source_url, fetched_at, session_id),
        )
        return Fetch(cur.lastrowid, snapshot.content_hash, source_url,
                     fetched_at, session_id)

    def fetches_for_url_in_session(self, source_url: str, session_id: str) -> list[Fetch]:
        rows = self._conn.execute(
            "SELECT id, content_hash, source_url, fetched_at, session_id "
            "FROM fetches WHERE source_url=? AND session_id=? ORDER BY fetched_at",
            (source_url, session_id),
        ).fetchall()
        return [Fetch(*r) for r in rows]
