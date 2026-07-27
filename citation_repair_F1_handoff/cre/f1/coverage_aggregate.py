"""cre/f1/coverage_aggregate.py -- the band's TRI-STATE coverage aggregation.

WHY THIS MODULE EXISTS (ZD 2026-07-27)
--------------------------------------
The F6 correction makes abstract-scoped coverage a TRI-STATE {True, False, None}
decision: an abstract can only STATE a claim (True), CONTRADICT it (False), or
NOT SAY (None). Absence of evidence in an abstract is unknown, NEVER a coverage
gap; only contradiction is a fault at abstract scope. A claim genuinely supported
in the cited paper's Results but silent in its abstract must be HELD, not flagged.

The natural home for this mapping is ``band_prompts.aggregate_coverage``. But
``band_prompts.py`` is a FROZEN substrate: ``mint_v1.derive_source_blob_oid``
pins its committed blob OID against ``semantic_validator_v1.FROZEN_SOURCE_BLOB_OID``
(enforced by SV-002), which is also stamped into both frozen prompt-package
seals. Editing ``band_prompts.py`` at all -- even code unrelated to the prompt
text -- drifts that blob and unfreezes the substrate; re-establishing the freeze
(re-mint packages, regenerate the universe fixtures, update pins) is a deliberate
single-session freeze pass and is ZD's operation, not the builder's.

So until that freeze pass, the tri-state mapping lives HERE, DOWNSTREAM of the
frozen parser. ``band_prompts.parse_coverage`` already returns a
``CoverageVerdict`` carrying the three raw structured fields (``engages_subject``,
``contradicts``, ``unconfirmed_specifics``); only its Boolean
``aggregate_coverage`` / ``as_judge_dict`` collapse the distinction. This module
reads those raw fields off the dataclass and applies the target truth table
itself. ``band_prompts.py`` is never touched, its blob OID stays
``fa01126e...``, both prompt packages stay byte-identical, SV-002 stays
satisfied, and no freeze artifact moves.

KNOWN DEBT (guarded)
--------------------
``band_prompts.aggregate_coverage`` remains BOOLEAN and is WRONG for the band
(it derives ``False`` for a silent or specifics-unconfirmed abstract, which would
route F6 instead of HELD). It is BYPASSED, not used, on the band path: the band
must build its coverage judge from :func:`make_coverage_judge` here, NEVER from
``band_prompts.make_coverage_judge``. ``test_coverage_aggregate`` locks that
divergence so a regression back to the Boolean path is loud. The freeze pass
collapses the two paths back into one by moving this mapping into
``band_prompts`` and re-freezing.
"""
from __future__ import annotations

from typing import Callable, List, Optional

from . import band_prompts as bp


def aggregate_coverage(
    engages_subject: bool, contradicts: bool, unconfirmed_specifics: list
) -> Optional[bool]:
    """Deterministically map strict structured fields to the TRI-STATE coverage
    verdict {True, False, None} at ABSTRACT SCOPE (the target table, ZD 2026-07-27).

        engages, not contradicted, no unconfirmed specifics -> True  (stated)
        contradicts                                         -> False (a gap)
        engages but load-bearing specifics unconfirmed      -> None  (not said)
        off-topic / silent (engages_subject false)          -> None  (not said)

    The type guards and the validation invariant (an off-topic finding requires
    contradicts=false and unconfirmed_specifics=[]) match
    ``band_prompts.aggregate_coverage`` exactly; only the mapping is tri-state.
    """
    if type(engages_subject) is not bool:
        raise ValueError("engages_subject must be an actual JSON boolean")
    if type(contradicts) is not bool:
        raise ValueError("contradicts must be an actual JSON boolean")
    if not isinstance(unconfirmed_specifics, (list, tuple)):
        raise ValueError("unconfirmed_specifics must be a list")
    if not engages_subject and (contradicts or unconfirmed_specifics):
        raise ValueError(
            "engages_subject=false requires contradicts=false and "
            "unconfirmed_specifics=[]"
        )
    if contradicts:
        return False                       # the abstract contradicts -> gap
    if not engages_subject:
        return None                        # off-topic / silent -> unknown
    if unconfirmed_specifics:
        return None                        # engaged but specifics not said
    return True                            # stated and supported


def tristate_judge_dict(verdict: bp.CoverageVerdict) -> dict:
    """Turn a parsed ``CoverageVerdict`` into the band's judge dict.

    Re-derives ``established`` as TRI-STATE from the raw structured fields --
    deliberately IGNORING ``verdict.established``, which ``parse_coverage`` set
    via the frozen Boolean ``band_prompts.aggregate_coverage`` -- and carries the
    raw fields so run_band's coverage_distribution can tally them."""
    established = aggregate_coverage(
        verdict.engages_subject, verdict.contradicts,
        list(verdict.unconfirmed_specifics),
    )
    return {
        "established": established,
        "rationale": verdict.rationale,
        "evidence_span": verdict.evidence_span,
        "engages_subject": verdict.engages_subject,
        "contradicts": verdict.contradicts,
        "unconfirmed_specifics": list(verdict.unconfirmed_specifics),
    }


def _no_usable_abstract_dict() -> dict:
    """The deterministic HELD verdict for the no-usable-abstract path: tri-state
    None, no structured fields (so coverage_bucket leaves it out of the tally)."""
    return {
        "established": None,
        "rationale": "no usable abstract (deterministic evidence-sufficiency gate)",
        "evidence_span": "",
        "engages_subject": None,
        "contradicts": None,
        "unconfirmed_specifics": [],
    }


def make_coverage_judge(
    call_llm: Callable[[str], str]
) -> Callable[[list, dict], list]:
    """Return the band's canonical ``coverage_judge(claims, evidence) -> list[dict]``.

    Same injected-callable contract and the same deterministic no-usable-abstract
    short-circuit (no LLM call) as ``band_prompts.make_coverage_judge``, but the
    per-claim verdict runs through the TRI-STATE :func:`aggregate_coverage` here
    instead of the frozen Boolean one. Judges each claim in its own call against
    the FROZEN ``band_prompts.COVERAGE_PROMPT`` (unchanged; its bytes and the
    package seal do not move)."""
    def coverage_judge(claims: list, evidence: dict) -> list:
        if not bp.evidence_is_usable(evidence):
            return [_no_usable_abstract_dict() for _ in claims]
        evidence_text = bp.render_evidence(evidence)
        out = []
        for claim in claims:
            prompt = (
                bp.COVERAGE_PROMPT
                .replace("<<ATOMIC_CLAIM>>", claim)
                .replace("<<EVIDENCE>>", evidence_text)
            )
            out.append(tristate_judge_dict(bp.parse_coverage(call_llm(prompt))))
        return out
    return coverage_judge


def judge_coverage_tristate(
    call_llm: Callable[[str], str], atomic_claim: str, evidence
) -> dict:
    """Single-claim convenience mirror of :func:`make_coverage_judge` for offline
    verification: returns one tri-state judge dict (HELD, no LLM call, when the
    abstract is unusable)."""
    if not bp.evidence_is_usable(evidence):
        return _no_usable_abstract_dict()
    prompt = (
        bp.COVERAGE_PROMPT
        .replace("<<ATOMIC_CLAIM>>", atomic_claim)
        .replace("<<EVIDENCE>>", bp.render_evidence(evidence))
    )
    return tristate_judge_dict(bp.parse_coverage(call_llm(prompt)))
