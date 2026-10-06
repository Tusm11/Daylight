"""
Citation gate.

The gate takes the output of the four checks and emits ONE deterministic
verdict and action. The rules are those listed in the README, applied in
order so every verdict is explainable:

    1. Direct self-origin, or registry match where the agent's artifact
       came first                                     -> CIRCULAR / BLOCK
    2. Cited passage not in the snapshot, OR claim not supported by it
                                                      -> BLOCK
    3. Third-party proof of prior existence, no registry match, claim
       supported                                      -> INDEPENDENT / ALLOW
    4. Dependency signals present but inconclusive    -> SUSPECT / REVIEW
    5. Otherwise                                      -> UNVERIFIED / REVIEW

Fail-closed: a check that times out or errors is treated as missing
evidence, never as independence. A missing check never flips a BLOCK to
ALLOW either.

`decide()` consumes already-computed check results so it is pure and easy
to test. Orchestration (synchronous instant checks + background slow
checks) belongs to a scheduler that lives in `api.py`.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from .checks.evidence_verifier import EvidenceResult
from .checks.origin_check import OriginCheckResult
from .checks.snapshot_check import SnapshotCheckResult
from .checks.temporal_fusion import TemporalResult


class Verdict(str, Enum):
    INDEPENDENT = "INDEPENDENT"
    CIRCULAR = "CIRCULAR"
    SUSPECT = "SUSPECT"
    UNVERIFIED = "UNVERIFIED"


class Action(str, Enum):
    ALLOW = "ALLOW"
    BLOCK = "BLOCK"
    REQUIRE_REVIEW = "REQUIRE_REVIEW"


@dataclass(frozen=True)
class Decision:
    verdict: Verdict
    action: Action
    reasons: tuple[str, ...]


def decide(*, snapshot: SnapshotCheckResult, origin: OriginCheckResult,
           temporal: TemporalResult, evidence: EvidenceResult) -> Decision:
    reasons: list[str] = []

    # Rule 1: self-origin.
    if origin.verdict in ("self_origin", "self_similar"):
        reasons.append(origin.reason)
        return Decision(Verdict.CIRCULAR, Action.BLOCK, tuple(reasons))

    # Rule 2: passage not in snapshot, or claim not supported.
    if not snapshot.present:
        reasons.append(f"snapshot: {snapshot.reason}")
        return Decision(Verdict.UNVERIFIED, Action.BLOCK, tuple(reasons))
    if not evidence.supports:
        reasons.append(f"evidence: {evidence.reason}")
        return Decision(Verdict.UNVERIFIED, Action.BLOCK, tuple(reasons))

    # Rule 3: third-party established prior existence; no self match; supported.
    if temporal.status == "established":
        reasons.append(f"temporal: {temporal.reason}")
        reasons.append(f"snapshot: {snapshot.reason}")
        reasons.append(f"evidence: {evidence.reason}")
        return Decision(Verdict.INDEPENDENT, Action.ALLOW, tuple(reasons))

    # Rule 4: dependency signals (only self-asserted date, or weak origin
    # evidence that didn't cross the self_similar bar).
    if temporal.status == "supported":
        reasons.append("only self-asserted prior-existence evidence")
        return Decision(Verdict.SUSPECT, Action.REQUIRE_REVIEW, tuple(reasons))

    # Rule 5: nothing to say either way. Absence of evidence is never
    # treated as independence -> review, not allow.
    reasons.append("no third-party prior-existence evidence")
    return Decision(Verdict.UNVERIFIED, Action.REQUIRE_REVIEW, tuple(reasons))
