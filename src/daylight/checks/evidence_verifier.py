"""
Evidence verifier.

Does the cited passage SUPPORT the claim?

Two layers:

1. **Deterministic.** Numbers, dates, and named entities in the claim must
   also appear in the cited passage. These are the easy fails -- a claim
   saying "revenue grew 11%" cited to a passage with no number at all.
2. **Entailment (stub).** An NLI model would judge the exact span. Models
   contribute a signal but never decide alone; this hook is left in place.

If the deterministic layer rejects, no model call is made.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..fingerprint import normalize


_NUM = re.compile(r"\d+(?:\.\d+)?%?")
_YEAR = re.compile(r"\b(19|20|21)\d{2}\b")


@dataclass(frozen=True)
class EvidenceResult:
    supports: bool
    missing_tokens: tuple[str, ...]
    reason: str


def _extract_facts(text: str) -> set[str]:
    t = normalize(text)
    nums = set(_NUM.findall(t))
    years = set(_YEAR.findall(t))
    # Crude "named entities": capitalized words that aren't sentence-initial.
    # Dropping sentence-initial caps avoids false "missing" matches on common
    # nouns that happen to open a claim ("Revenue grew..." vs "revenue grew...").
    caps = set()
    # Match a capitalized word only when it has a non-period, non-start char
    # immediately before it. First word of the whole text is always skipped.
    for m in re.finditer(r"(?<=[^\s.!?\n])\s+([A-Z][A-Za-z0-9_-]{2,})", text):
        caps.add(m.group(1).lower())
    return nums | years | caps


def verify(claim: str, cited_passage: str) -> EvidenceResult:
    claim_facts = _extract_facts(claim)
    if not claim_facts:
        return EvidenceResult(True, (), "no checkable facts in claim")
    passage_facts = _extract_facts(cited_passage)
    missing = tuple(sorted(claim_facts - passage_facts))
    if missing:
        return EvidenceResult(False, missing,
                              f"missing in passage: {', '.join(missing)}")
    return EvidenceResult(True, (), "all claim facts appear in passage")
