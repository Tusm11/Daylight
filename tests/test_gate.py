from daylight.checks.evidence_verifier import EvidenceResult
from daylight.checks.origin_check import OriginCheckResult
from daylight.checks.snapshot_check import SnapshotCheckResult
from daylight.checks.temporal_fusion import TemporalResult
from daylight.gate import Action, Verdict, decide


def _snap(ok=True): return SnapshotCheckResult(ok, 1.0 if ok else 0.0, "ok" if ok else "miss")
def _origin(v="clean"): return OriginCheckResult(v, None, None, v)
def _temp(status="established"):
    return TemporalResult(status, 1.0 if status != "unknown" else None,
                          None, status)
def _ev(ok=True): return EvidenceResult(ok, (), "ok" if ok else "missing fact")


def test_independent_path():
    d = decide(snapshot=_snap(), origin=_origin(), temporal=_temp("established"),
               evidence=_ev())
    assert d.verdict == Verdict.INDEPENDENT and d.action == Action.ALLOW


def test_self_origin_blocks_even_with_support():
    d = decide(snapshot=_snap(), origin=_origin("self_origin"),
               temporal=_temp("established"), evidence=_ev())
    assert d.verdict == Verdict.CIRCULAR and d.action == Action.BLOCK


def test_self_similar_blocks_as_circular():
    d = decide(snapshot=_snap(), origin=_origin("self_similar"),
               temporal=_temp("established"), evidence=_ev())
    assert d.verdict == Verdict.CIRCULAR and d.action == Action.BLOCK


def test_missing_from_snapshot_blocks():
    d = decide(snapshot=_snap(False), origin=_origin(),
               temporal=_temp("established"), evidence=_ev())
    assert d.action == Action.BLOCK


def test_unsupported_claim_blocks():
    d = decide(snapshot=_snap(), origin=_origin(), temporal=_temp("established"),
               evidence=_ev(False))
    assert d.action == Action.BLOCK


def test_only_self_asserted_is_suspect_review():
    d = decide(snapshot=_snap(), origin=_origin(), temporal=_temp("supported"),
               evidence=_ev())
    assert d.verdict == Verdict.SUSPECT and d.action == Action.REQUIRE_REVIEW


def test_unknown_temporal_is_review_never_allow():
    d = decide(snapshot=_snap(), origin=_origin(), temporal=_temp("unknown"),
               evidence=_ev())
    assert d.verdict == Verdict.UNVERIFIED and d.action == Action.REQUIRE_REVIEW
