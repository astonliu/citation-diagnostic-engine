"""Deterministic F8 timing gate over source-bound PubMed metadata."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .f5_notice import resolve_formal_notice
from .ncbi_meta import is_retracted


F8_TIMING_VERSION = "f8_pubmed_timing_v1"
F8_MIN_GAP_DAYS = 31

#: How many times a MISSING BOUNDARY is re-fetched before the assessment is held.
#: Every F8_UNRESOLVED reason below names a boundary that did not arrive -- an
#: EFetch that did not answer, a pub_date that did not parse, a linked notice
#: whose date could not be resolved. None of them is a judgment; they are
#: transport outcomes, and a transport outcome deserves the same bounded retry
#: the model stages get before it costs a reference its verdict.
F8_BOUNDARY_ATTEMPTS = 2

#: The F8_UNRESOLVED reasons a retry can plausibly clear: something did not
#: arrive. Enumerated rather than "everything", so a reason whose cause is
#: structural is not re-fetched to reproduce the same answer at cost.
F8_RETRYABLE_REASONS = frozenset({
    "cited_metadata_failure",
    "citing_metadata_failure",
    "citing_metadata_unavailable",
    "citing_date_unavailable",
    "retraction_notice_or_date_unresolved",
    "retraction_notice_date_unparseable",
})
F8_QUALIFIED = "qualified"
F8_CLEAR = "clear"
F8_TIMING_INDETERMINATE = "timing_indeterminate"
F8_UNRESOLVED = "unresolved"
F8_NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True)
class F8TimingAssessment:
    status: str
    notice_date: str = ""
    citing_date_earliest: str = ""
    timing_gap_days: int | None = None
    reason: str = ""


@dataclass(frozen=True)
class F8TimingAttempt:
    """One boundary-resolution attempt, for the paid-call ledger."""
    attempt: int
    status: str
    reason: str


def assess_f8_timing_with_retry(cited_pmid: str, citing_pmid: str, *, fetch_meta,
                                attempts: int = F8_BOUNDARY_ATTEMPTS
                                ) -> "tuple[F8TimingAssessment, list]":
    """:func:`assess_f8_timing`, retried while the BOUNDARY is what is missing.

    Returns ``(assessment, attempt_log)``. The log carries one row per attempt --
    including the ones that failed -- so the boundary fetches show up in the run's
    call accounting instead of being invisible work.

    Only a reason in :data:`F8_RETRYABLE_REASONS` is retried. A resolved
    assessment of ANY kind stops immediately: ``F8_QUALIFIED``, ``F8_CLEAR``,
    ``F8_NOT_APPLICABLE`` and ``F8_TIMING_INDETERMINATE`` are all real answers,
    and re-fetching to second-guess a real answer is how a deterministic gate
    becomes nondeterministic.
    """
    attempt_log: list = []
    assessment = None
    for attempt in range(1, max(1, int(attempts)) + 1):
        assessment = assess_f8_timing(cited_pmid, citing_pmid,
                                      fetch_meta=fetch_meta)
        attempt_log.append(F8TimingAttempt(
            attempt, assessment.status, assessment.reason))
        if assessment.status != F8_UNRESOLVED:
            break
        if assessment.reason not in F8_RETRYABLE_REASONS:
            break
    return assessment, attempt_log


def assess_f8_timing(cited_pmid: str, citing_pmid: str, *, fetch_meta
                     ) -> F8TimingAssessment:
    """Apply the registered >=31-day rule, holding every missing boundary."""
    cited = str(cited_pmid or "").strip()
    citing = str(citing_pmid or "").strip()
    if not cited.isdigit():
        return F8TimingAssessment(
            F8_NOT_APPLICABLE, reason="no_resolved_cited_pubmed_work")
    if not citing.isdigit() or not callable(fetch_meta):
        return F8TimingAssessment(F8_UNRESOLVED, reason="work_identity_unavailable")
    try:
        cited_meta = fetch_meta(cited)
    except Exception:
        return F8TimingAssessment(F8_UNRESOLVED, reason="cited_metadata_failure")
    if cited_meta is None:
        return F8TimingAssessment(
            F8_NOT_APPLICABLE, reason="cited_pubmed_record_absent")
    if (not isinstance(cited_meta, dict) or str(
            cited_meta.get("id") or cited_meta.get("pmid") or "") != cited):
        return F8TimingAssessment(F8_UNRESOLVED, reason="cited_metadata_conflict")
    try:
        citing_meta = fetch_meta(citing)
    except Exception:
        return F8TimingAssessment(F8_UNRESOLVED, reason="citing_metadata_failure")
    if not isinstance(citing_meta, dict) or str(
            citing_meta.get("id") or citing_meta.get("pmid") or "") != citing:
        return F8TimingAssessment(F8_UNRESOLVED, reason="citing_metadata_unavailable")
    citing_day_raw = str(citing_meta.get("pub_date") or "").strip()
    try:
        citing_day = date.fromisoformat(citing_day_raw)
    except ValueError:
        return F8TimingAssessment(F8_UNRESOLVED, reason="citing_date_unavailable")

    notice = resolve_formal_notice(
        cited, as_of_date=citing_day_raw, fetch_meta=fetch_meta)
    if notice.notice_resolution == "resolved_clear":
        return F8TimingAssessment(
            F8_CLEAR, citing_date_earliest=citing_day_raw,
            reason="no_retraction_in_force_at_citation")
    # A RESOLVED NOTICE THAT IS NOT A RETRACTION IS AN ANSWER, NOT AN OUTAGE.
    #
    # ``resolve_formal_notice`` returns exactly ONE chosen notice, selected by
    # ``_SEVERITY`` -- retraction 0, expression-of-concern 1, correction 2 --
    # with a flagged row preferred over an unresolved one at equal severity. A
    # retraction link therefore always outranks a correction or an EoC, so a
    # chosen notice of any other kind means no retraction relationship was
    # actionable at all. That is a determination, and F8's question ("was this
    # work retracted before it was cited") has been answered: no.
    #
    # Falling through to the ``retraction_notice_or_date_unresolved`` return
    # below reported a MISSING BOUNDARY instead, which is a different and false
    # claim -- nothing was missing. Because ``decide`` routes F8_UNRESOLVED to
    # UNSCOREABLE with ``f8_timing_boundary_unresolved``, every cited work
    # carrying a pre-citation erratum and no retraction was dropped from the
    # scoreable set and terminated UNJUDGEABLE in Band 2. It also produced the
    # inverted behaviour that a work with NO linked notices resolved CLEAR while
    # the same work with one resolved erratum did not: more metadata made the
    # citation less judgeable.
    #
    # THE PUBTYPE GUARD IS LOAD-BEARING. The severity argument holds only for
    # the RELATIONSHIP list. A work whose PubMed record carries the
    # ``Retracted Publication`` publication type but links no ``RetractionIn``
    # relationship never reaches ``resolve_formal_notice``'s direct-pubtype
    # fallback, because that fallback runs only when no subject relationship was
    # relevant at all -- one erratum link is enough to pre-empt it. Clearing on
    # the erratum would then be a false negative on a work PubMed marks
    # retracted. Such a work stays UNRESOLVED: retracted, with no datable
    # notice, which is a genuine missing boundary. ``is not True`` keeps an
    # unknown pubtype list on the clearing side, matching the ``resolved_clear``
    # branch above, which attests no pubtype either.
    if (notice.notice_resolution == "flagged"
            and notice.notice_kind != "retraction"
            and is_retracted(cited_meta.get("publication_types")) is not True):
        return F8TimingAssessment(
            F8_CLEAR, citing_date_earliest=citing_day_raw,
            reason="only_non_retraction_notice_in_force")
    if (notice.notice_kind != "retraction"
            or notice.notice_resolution != "flagged" or not notice.date):
        return F8TimingAssessment(
            F8_UNRESOLVED, citing_date_earliest=citing_day_raw,
            reason="retraction_notice_or_date_unresolved")
    try:
        notice_day = date.fromisoformat(notice.date)
    except ValueError:
        return F8TimingAssessment(
            F8_UNRESOLVED, citing_date_earliest=citing_day_raw,
            reason="retraction_notice_date_unparseable")
    gap = (citing_day - notice_day).days
    status = F8_QUALIFIED if gap >= F8_MIN_GAP_DAYS else F8_TIMING_INDETERMINATE
    return F8TimingAssessment(
        status, notice_date=notice.date,
        citing_date_earliest=citing_day_raw, timing_gap_days=gap,
        reason=("retracted_before_citation_31_day_floor_met"
                if status == F8_QUALIFIED else
                "retraction_to_citation_gap_below_31_days"))
