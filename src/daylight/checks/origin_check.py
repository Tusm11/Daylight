"""
Origin check.

Did the agent write, upload, or edit this source? Does the source match
anything in the agent output registry?

Similarity is **directional**. The README is explicit: an honest answer
naturally resembles the sources it summarized, so similarity alone proves
nothing. It only counts as circular when the agent's artifact came
FIRST.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..output_registry import OutputRegistry, RegistryHit
from ..provenance import ProvenanceGraph


@dataclass(frozen=True)
class OriginCheckResult:
    verdict: str              # "self_origin" | "self_similar" | "clean"
    registry_hit: Optional[RegistryHit]
    graph_loop: Optional[dict]
    reason: str


def check_origin(citation_id: str, snapshot_text: str,
                 claim_agent_id: str, claim_time: float,
                 graph: ProvenanceGraph, registry: OutputRegistry,
                 min_jaccard: float = 0.5) -> OriginCheckResult:
    # 1. Graph tells us if the agent published this exact source.
    loop = graph.is_circular(citation_id)
    if loop is not None:
        return OriginCheckResult(
            verdict="self_origin",
            registry_hit=None,
            graph_loop=loop,
            reason=f"agent {loop['agent']} published this source "
                   f"via artifact {loop['artifact']}",
        )

    # 2. Registry lookup catches rehosting / laundering.
    hits = registry.find_matches(snapshot_text, min_jaccard=min_jaccard)
    for h in hits:
        if h.artifact.agent_id != claim_agent_id:
            continue  # same org, different agent -- interesting but not a loop
        if h.artifact.produced_at < claim_time:
            return OriginCheckResult(
                verdict="self_similar",
                registry_hit=h,
                graph_loop=None,
                reason=(
                    f"snapshot matches earlier artifact {h.artifact.artifact_id} "
                    f"by same agent (jaccard={h.jaccard:.2f})"
                ),
            )

    return OriginCheckResult("clean", None, None, "no self-origin evidence")
