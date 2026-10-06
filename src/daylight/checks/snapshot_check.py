"""
Snapshot check.

Does the cited passage actually appear in the snapshot the agent fetched,
*before* the citation was made?

The verification runs against the stored snapshot, never the live URL. If
the live page has since changed (or was edited by the agent after it
cited it), that is exactly what Daylight exists to notice.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..fingerprint import normalize
from ..snapshot_store import Snapshot


@dataclass(frozen=True)
class SnapshotCheckResult:
    present: bool
    overlap_ratio: float
    reason: str


def contains_passage(snapshot: Snapshot, cited_passage: str,
                     min_overlap: float = 0.9) -> SnapshotCheckResult:
    """Return whether `cited_passage` is in the snapshot.

    Exact-ish: normalized substring match first; otherwise a token-overlap
    ratio to tolerate small quoting differences.
    """
    passage = normalize(cited_passage)
    text = snapshot.cleaned_text
    if not passage:
        return SnapshotCheckResult(False, 0.0, "empty_passage")
    if passage in text:
        return SnapshotCheckResult(True, 1.0, "exact_substring")

    # Token-overlap fallback for small quoting differences.
    p_toks = passage.split()
    t_toks = set(text.split())
    if not p_toks:
        return SnapshotCheckResult(False, 0.0, "empty_tokens")
    overlap = sum(1 for t in p_toks if t in t_toks) / len(p_toks)
    present = overlap >= min_overlap
    return SnapshotCheckResult(
        present,
        overlap,
        f"token_overlap={overlap:.2f}" + ("" if present else "_below_threshold"),
    )
