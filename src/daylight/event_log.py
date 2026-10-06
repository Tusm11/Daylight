"""
Tamper-evident, append-only event log.

Design points, drawn from the README:

- *The proxy, never the agent, writes it.* Callers of this module are the
  proxy and the Daylight core services; the agent has no credentials for it.
  This module cannot enforce that -- it has to be enforced at deploy time by
  running the log on a separate principal. What it CAN enforce is that
  tampering is detectable after the fact.

- *Hash chain.* Each record stores `prev_hash` (the hash of the previous
  record's canonical form) and `hash` (its own). An attacker who edits any
  record must rewrite every record after it.

- *Periodic Merkle roots.* `merkle_root(session_id, upto)` folds the chain
  into a single root, suitable for publishing outside the system a la
  Certificate Transparency. In production the roots go to a separate store.

- *Idempotency keys.* An event carries a client-supplied `idempotency_key`;
  re-appending the same key is a no-op and returns the existing record.
  Required for a crash-safe proxy that may replay events on recovery.

The storage backend is SQLite. Trading database power for simplicity: for a
real deployment this is swapped for Postgres with the same table shape.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional


_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    seq            INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id     TEXT    NOT NULL,
    ts             REAL    NOT NULL,
    kind           TEXT    NOT NULL,
    payload_json   TEXT    NOT NULL,
    idempotency_key TEXT   NOT NULL,
    prev_hash      TEXT    NOT NULL,
    hash           TEXT    NOT NULL,
    UNIQUE(session_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS events_session_seq ON events(session_id, seq);
"""


@dataclass(frozen=True)
class Event:
    seq: int
    session_id: str
    ts: float
    kind: str
    payload: dict[str, Any]
    idempotency_key: str
    prev_hash: str
    hash: str


def _canonical(session_id: str, ts: float, kind: str, payload: dict[str, Any],
               idempotency_key: str, prev_hash: str) -> bytes:
    """Deterministic byte form used for hashing. JSON with sorted keys."""
    body = {
        "session_id": session_id,
        "ts": ts,
        "kind": kind,
        "payload": payload,
        "idempotency_key": idempotency_key,
        "prev_hash": prev_hash,
    }
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _row_to_event(row: tuple) -> Event:
    seq, session_id, ts, kind, payload_json, idempotency_key, prev_hash, h = row
    return Event(seq, session_id, ts, kind, json.loads(payload_json),
                 idempotency_key, prev_hash, h)


class EventLog:
    GENESIS = "0" * 64

    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
        try:
            self._conn.execute("PRAGMA journal_mode=WAL")
        except Exception:
            pass  # some mounts (fuse etc.) do not support WAL
        self._conn.executescript(_SCHEMA)

    # ---- write side -------------------------------------------------------

    def append(self, session_id: str, kind: str, payload: dict[str, Any],
               idempotency_key: str, ts: Optional[float] = None) -> Event:
        """Append one event. Idempotent on `(session_id, idempotency_key)`."""
        ts = ts if ts is not None else time.time()
        existing = self._get_by_key(session_id, idempotency_key)
        if existing is not None:
            return existing

        prev_hash = self._tip_hash(session_id)
        canon = _canonical(session_id, ts, kind, payload, idempotency_key, prev_hash)
        h = hashlib.sha256(canon).hexdigest()
        cur = self._conn.execute(
            "INSERT INTO events (session_id, ts, kind, payload_json, "
            "idempotency_key, prev_hash, hash) VALUES (?,?,?,?,?,?,?)",
            (session_id, ts, kind, json.dumps(payload, sort_keys=True),
             idempotency_key, prev_hash, h),
        )
        seq = cur.lastrowid
        return Event(seq, session_id, ts, kind, payload, idempotency_key,
                     prev_hash, h)

    # ---- read side --------------------------------------------------------

    def iter_session(self, session_id: str) -> Iterable[Event]:
        cur = self._conn.execute(
            "SELECT seq, session_id, ts, kind, payload_json, idempotency_key, "
            "prev_hash, hash FROM events WHERE session_id=? ORDER BY seq",
            (session_id,),
        )
        for row in cur:
            yield _row_to_event(row)

    def _get_by_key(self, session_id: str, key: str) -> Optional[Event]:
        row = self._conn.execute(
            "SELECT seq, session_id, ts, kind, payload_json, idempotency_key, "
            "prev_hash, hash FROM events WHERE session_id=? AND idempotency_key=?",
            (session_id, key),
        ).fetchone()
        return _row_to_event(row) if row else None

    def _tip_hash(self, session_id: str) -> str:
        row = self._conn.execute(
            "SELECT hash FROM events WHERE session_id=? ORDER BY seq DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        return row[0] if row else self.GENESIS

    # ---- integrity --------------------------------------------------------

    def verify_chain(self, session_id: str) -> bool:
        """Recompute hashes end to end; returns False if any link is broken."""
        prev = self.GENESIS
        for ev in self.iter_session(session_id):
            if ev.prev_hash != prev:
                return False
            canon = _canonical(ev.session_id, ev.ts, ev.kind, ev.payload,
                               ev.idempotency_key, ev.prev_hash)
            if hashlib.sha256(canon).hexdigest() != ev.hash:
                return False
            prev = ev.hash
        return True

    def merkle_root(self, session_id: str, upto_seq: Optional[int] = None) -> str:
        """Merkle root over event hashes in order. Publish externally."""
        leaves: list[str] = []
        for ev in self.iter_session(session_id):
            if upto_seq is not None and ev.seq > upto_seq:
                break
            leaves.append(ev.hash)
        if not leaves:
            return self.GENESIS
        layer = [bytes.fromhex(h) for h in leaves]
        while len(layer) > 1:
            if len(layer) % 2 == 1:
                layer.append(layer[-1])  # duplicate last leaf for odd layers
            layer = [hashlib.sha256(layer[i] + layer[i + 1]).digest()
                     for i in range(0, len(layer), 2)]
        return layer[0].hex()
