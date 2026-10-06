"""
Temporal fusion.

Did this content exist BEFORE the task started? Evidence weighted by who
controls it:

- **Third-party observed** (archive capture, DOI registration, inbound
  links from unrelated domains) can ESTABLISH prior existence.
- **Self-asserted** (dates printed on the page, HTTP headers, commit
  dates) can only SUPPORT it, because the publisher can backdate them.
- Evidence the agent itself touched is DISCARDED.

Checks are at the revision level, not the URL level.

Here we accept pre-collected evidence objects. Real adapters (Wayback,
Crossref, OpenAlex, etc.) sit outside this module behind a common
interface so they can be mocked in tests and cached indefinitely in prod.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal

Weight = Literal["third_party", "self_asserted", "agent_touched"]


@dataclass(frozen=True)
class Evidence:
    at: float
    weight: Weight
    source: str   # label like "wayback", "crossref", "page_meta"


@dataclass(frozen=True)
class TemporalResult:
    status: str   # "established" | "supported" | "unknown"
    earliest_third_party: float | None
    earliest_self: float | None
    reason: str


def fuse(evidence: Iterable[Evidence], task_start: float) -> TemporalResult:
    third_party_before = [e for e in evidence
                          if e.weight == "third_party" and e.at < task_start]
    self_before = [e for e in evidence
                   if e.weight == "self_asserted" and e.at < task_start]

    if third_party_before:
        earliest = min(e.at for e in third_party_before)
        return TemporalResult(
            "established",
            earliest_third_party=earliest,
            earliest_self=(min(e.at for e in self_before) if self_before else None),
            reason=f"third-party evidence {earliest:.0f} < task_start {task_start:.0f}",
        )
    if self_before:
        earliest = min(e.at for e in self_before)
        return TemporalResult(
            "supported",
            earliest_third_party=None,
            earliest_self=earliest,
            reason="only self-asserted evidence of prior existence",
        )
    return TemporalResult(
        "unknown",
        earliest_third_party=None,
        earliest_self=None,
        reason="no evidence of prior existence",
    )
