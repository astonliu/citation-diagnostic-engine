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
from .recording_adapter import PaidCallMeter


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


def aggregate_fulltext_coverage(
    verdict: bp.CoverageVerdict, retrieval_complete
) -> Optional[bool]:
    """The DEC-032 truth table, at FULL-TEXT scope. Leaves the abstract-scoped
    :func:`aggregate_coverage` above untouched -- that stays the default path.

    What changes at full-text scope is what SILENCE means. In an abstract,
    absence is unknown: a finding can be in the Results and simply not summarized.
    In a COMPLETE full text, absence is absence, and that is the whole point of
    retrieving the body.

    So the table splits on whether a conclusion rests on evidence PRESENT or on
    evidence MISSING:

      * a contradiction is present evidence -> ``False`` whether or not retrieval
        was complete. A contradiction in a partial text is still a contradiction.
      * engaged, uncontradicted, nothing left unconfirmed -> ``True``, likewise
        regardless of completeness. What we retrieved established the claim; the
        pages we did not retrieve cannot un-establish it.
      * everything else is an argument from silence -- the text is off-topic, or a
        load-bearing specific never appears. That argument is only valid against a
        COMPLETE retrieval: complete -> ``False``, incomplete -> ``None`` (hold).

    ``retrieval_complete`` is tri-state-safe: anything that is not exactly True is
    treated as not-complete, so an absent or unknown signal holds rather than
    flags. Fail-closed, matching DEC-032's direction.
    """
    engages_subject = verdict.engages_subject
    contradicts = verdict.contradicts
    unconfirmed_specifics = list(verdict.unconfirmed_specifics)
    if type(engages_subject) is not bool:
        raise ValueError("engages_subject must be an actual JSON boolean")
    if type(contradicts) is not bool:
        raise ValueError("contradicts must be an actual JSON boolean")
    if not engages_subject and (contradicts or unconfirmed_specifics):
        raise ValueError(
            "engages_subject=false requires contradicts=false and "
            "unconfirmed_specifics=[]"
        )
    if contradicts:
        return False                       # present evidence, incompatible
    if engages_subject and not unconfirmed_specifics:
        return True                        # present evidence, established
    # From here the conclusion rests on something NOT being there.
    if retrieval_complete is True:
        return False                       # absence in a complete text is absence
    return None                            # incomplete: unknown, hold


def fulltext_judge_dict(verdict: bp.CoverageVerdict, retrieval_complete) -> dict:
    """:func:`tristate_judge_dict`'s full-text twin for a FIVE-KEY
    ``bp.CoverageVerdict``: same dict shape, same raw fields for the
    coverage_distribution tally, ``established`` re-derived through
    :func:`aggregate_fulltext_coverage` instead of the abstract-scoped table.

    SUPERSEDED ON THE LIVE v3 PATH by :func:`fulltext_judge_dict_v3`, which
    carries the split span fields (ZD 2026-08-11 item 6). Kept because the
    aggregation it wraps is version-agnostic and this remains the correct adapter
    for a five-key verdict."""
    return {
        "established": aggregate_fulltext_coverage(verdict, retrieval_complete),
        "rationale": verdict.rationale,
        "evidence_span": verdict.evidence_span,
        "engages_subject": verdict.engages_subject,
        "contradicts": verdict.contradicts,
        "unconfirmed_specifics": list(verdict.unconfirmed_specifics),
    }


def fulltext_judge_dict_v3(verdict, retrieval_complete, *,
                           evidence_spans=(), span_status="") -> dict:
    """:func:`fulltext_judge_dict` for a SENTENCE-SELECTION ``CoverageVerdictV3``.

    ``verdict`` is duck-typed rather than annotated, to avoid importing
    ``coverage_prompts_v3`` -- which imports this module -- back into it.

    ``evidence_spans`` and ``span_status`` are passed in RESOLVED rather than read off
    the verdict, because resolving an id to its text needs the sections and only the
    judge has them. Each resolved span carries ``label`` / ``sentence_ids`` / ``text``
    / ``span_source``: the ids are machine-checkable provenance, the text keeps the
    artifact readable, and the source records whether the model pointed at the
    sentence or merely quoted something we aligned to it.

    The span field has now been superseded four times -- one string, a label/text
    pair, a list of ``{label, text}``, and now selections -- and every step removed
    one way for the model to misreport source text. This one removes the last, by not
    asking it to report text at all.

    ``span_status`` IS RECORDED AND REPORTED AND DOES NOT GATE ``established``
    (DEC-047). ``aggregate_fulltext_coverage`` is reused unchanged, as it has been
    across every span reshape: the DEC-032 truth table reads only ``engages_subject``
    / ``contradicts`` / ``unconfirmed_specifics``. A verdict with
    ``span_status="not_found"`` is decided exactly as one with a full span list --
    incompleteness is measured as recall, not punished as error.

    Entries are COPIED, not aliased: the record is durable JSONL and must not share
    mutable state with the caller's list."""
    return {
        "established": aggregate_fulltext_coverage(verdict, retrieval_complete),
        "rationale": verdict.rationale,
        "evidence_spans": [dict(entry) for entry in evidence_spans or ()],
        "span_status": span_status,
        "engages_subject": verdict.engages_subject,
        "contradicts": verdict.contradicts,
        "unconfirmed_specifics": list(verdict.unconfirmed_specifics),
    }


def no_usable_fulltext_dict() -> dict:
    """The deterministic HELD verdict when the body was not retrievable.

    Mirrors :func:`_no_usable_abstract_dict` exactly, including carrying no
    structured fields so ``coverage_bucket`` leaves it out of the tally: an
    unretrieved body is not a coverage judgment and must not be counted as one.

    Carries an EMPTY ``evidence_spans`` list, because this dict only ever appears on
    the v3 path and one record shape per path is what makes the path readable. Empty
    is the honest value: nothing was retrieved, so nothing can be cited.

    ``span_status`` is ``"not_applicable"``, NOT ``"not_found"``: no model call was
    made and no selection was attempted, so counting it as a selection miss would
    inflate the recall denominator with rows that never entered the task."""
    return {
        "established": None,
        "rationale": "no usable full text (retrieval incomplete; deterministic gate)",
        "evidence_spans": [],
        "span_status": "not_applicable",
        "engages_subject": None,
        "contradicts": None,
        "unconfirmed_specifics": [],
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
    package seal do not move).

    THE PER-CLAIM CALLS ARE COUNTED HERE, on ``coverage_judge.paid_call_meter``,
    because this is the innermost place they are visible: one
    ``coverage_judge(claims, evidence)`` invocation bills ONE CALL PER CLAIM, so
    a receipt entry per seam call undercounts a five-claim reference by four, and
    ``judgment_run`` cannot see inside this closure to do better. The meter lives
    on this factory rather than on the frozen ``band_prompts`` one for the same
    reason the tri-state mapping does -- ``band_prompts.py`` cannot be edited at
    all without drifting its blob OID and unfreezing both prompt-package seals.
    The no-usable-abstract short-circuit above is the zero-call path and is left
    unmetered on purpose: it never reaches the model."""
    meter = PaidCallMeter()

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
            meter.bump()
            out.append(tristate_judge_dict(bp.parse_coverage(call_llm(prompt))))
        return out

    coverage_judge.paid_call_meter = meter
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
