"""Phase 1f -- LLM formatting-discrepancy filter (Claude Opus).

Runs ONLY on flagged candidates. Separates genuine fabrication signals from
benign formatting discrepancies (abbreviated/informal titles that resolve to a
real indexed paper -- the Topaz example).

Output verdict in {fabrication, formatting_discrepancy, reference_error, uncertain}.
This is NOT semantic judgment; it is metadata-mismatch triage.

The model pin is set in config (your stronger-than-Haiku lever). This module is
transport-agnostic: pass any callable `complete(prompt:str)->str`, so it works
with the Anthropic SDK, a batch wrapper, or a mock in tests.
"""
from __future__ import annotations
import json
from typing import Callable

from .schema import (Reference, V_FABRICATION, V_FORMATTING,
                     V_REFERENCE_ERROR, V_UNCERTAIN)

_VALID = {V_FABRICATION, V_FORMATTING, V_REFERENCE_ERROR, V_UNCERTAIN}

PROMPT = """You triage a citation whose claimed metadata does not match the \
record its claimed PMID resolves to. Decide which of four cases applies. Do \
NOT judge whether any scientific claim is true; only compare the bibliographic \
metadata.

CLAIMED (from the citing paper's reference list):
  title:   {c_title}
  authors: {c_authors}
  year:    {c_year}
  journal: {c_journal}
  claimed PMID: {c_pmid}

RECORD the claimed PMID resolves to (empty = the PMID returned nothing):
  title:   {r_title}
  authors: {r_authors}
  year:    {r_year}
  journal: {r_journal}

Cases:
- "formatting_discrepancy": same work, just abbreviated/informal/translated \
title or minor author/journal formatting. NOT a problem.
- "reference_error": the claimed work is real but the PMID was pasted wrong \
(claimed metadata describes a different real paper than the resolved record).
- "fabrication": the claimed title/author combination does not appear to \
describe any real paper (plausible-sounding but likely invented).
- "uncertain": insufficient evidence to choose.

Respond with ONLY a JSON object, no prose:
{{"verdict": "<one of the four>", "reason": "<one sentence>"}}"""


def build_prompt(ref: Reference) -> str:
    c, r = ref.claimed, ref.retrieved
    return PROMPT.format(
        c_title=c.title or "(none)", c_authors=", ".join(c.authors) or "(none)",
        c_year=c.year or "(none)", c_journal=c.journal or "(none)",
        c_pmid=c.claimed_pmid or "(none)",
        r_title=r.title or "(none)", r_authors=", ".join(r.authors) or "(none)",
        r_year=r.year or "(none)", r_journal=r.journal or "(none)",
    )


def parse_verdict(raw: str) -> tuple[str, str]:
    raw = raw.strip().replace("```json", "").replace("```", "").strip()
    try:
        obj = json.loads(raw)
        v = obj.get("verdict", V_UNCERTAIN)
        return (v if v in _VALID else V_UNCERTAIN), obj.get("reason", "")
    except (json.JSONDecodeError, AttributeError):
        return V_UNCERTAIN, "unparseable LLM output"


def llm_filter(ref: Reference, complete: Callable[[str], str]) -> str:
    """Run the filter; record verdict on the log; return the verdict."""
    verdict, reason = parse_verdict(complete(build_prompt(ref)))
    ref.log.llm_verdict = verdict
    if reason:
        ref.log.notes = (ref.log.notes + " | " if ref.log.notes else "") + reason
    return verdict
