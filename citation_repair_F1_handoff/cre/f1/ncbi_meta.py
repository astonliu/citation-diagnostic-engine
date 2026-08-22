"""Shared NCBI metadata helpers -- publication type, review classification, and
PMC full-text / reference-list resolution.

These four helpers were extracted from ``f3_candidate_collect.py`` (which is
documented CALIBRATION-ONLY) so the production F3-F7 judgment band can depend on
them WITHOUT importing a calibration-only module -- a cold session must never
mistake the band for calibration tooling. ``f3_candidate_collect`` now re-imports
(re-exports) these names, so its public API and tests are unchanged.

All network calls go through the shared NCBI limiter (``ratelimit.NCBI``), so a
scaled run that mixes the collector and the band still respects one per-IP
budget. Every helper returns a safe can't-judge value (``None`` / ``""``) on any
failure rather than raising, so callers can treat the network as best-effort.

ONE EXCEPTION TO THAT, AND IT IS DELIBERATE (ZD 2026-08-11).
:func:`ncbi_pmids_to_pmcids` RAISES :class:`ResolverError` instead of swallowing.
Swallowing collapses "the resolver did not answer" into "this article has no PMC
full text", and those two are not the same fact about the world. Measured live on
2026-08-11: ELink ``pubmed_pmc`` answered ``HTTP 200`` with
``{"header":{...},"linksets":[],"ERROR":"NCBI C++ Exception:\\n Error:
TXCLIENT(CException::eUnknown) ..."}``. The ``ERROR`` value carries a RAW
NEWLINE, so the body is not valid JSON, ``r.json()`` raised, the ``except
ValueError`` below returned ``""``, and ``fulltext_reader`` reported
``no_pmcid``. ALL 25 distinct cited PMIDs in calibration run 1 came back
``no_pmcid`` that way -- including three whose PMCIDs were confirmed
independently through the PMC ID Converter (``30140736`` -> ``PMC6105232``,
``32382079`` -> ``PMC7206102``, ``26372954`` -> ``PMC4586821``). The run produced
zero coverage judgments and it READ AS AN OA-SUBSET CEILING. An outage must never
again be readable as an absence of full text, so the failure propagates and the
caller names it (``fulltext_reader.REASON_RESOLVER_ERROR``).

Some sandboxes cannot reach NCBI: build/unit-test offline with these helpers
monkeypatched on the *consumer* module's namespace (the consumer imports them as
module globals), run live in Colab with ``NCBI_API_KEY`` in Secrets.
"""
from __future__ import annotations

import os
import re
import tempfile

import requests

from .parser import parse_pmc_xml
from .ratelimit import NCBI, request_with_retry
from .lookup import EFETCH   # reuse the EFetch endpoint constant

# ELink lives on the same eutils host, so it shares the NCBI per-IP budget and
# the shared limiter. RETAINED for reference and for any caller that still wants
# the link graph; it is NO LONGER the PMID -> PMCID resolver (see IDCONV).
ELINK = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/elink.fcgi"

#: The PMC ID Converter -- the PMID -> PMCID resolver as of 2026-08-11. A second
#: host to throttle, which is why ELink was chosen first; the shared limiter is
#: still applied to it, so the per-IP budget is if anything over-respected.
IDCONV = "https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/"

#: Ids per converter request. The API's documented ceiling. Batching is why COST
#: MUST BE ESTIMATED ON REQUEST COUNT, NOT PMID COUNT: 200 PMIDs is one request,
#: and 201 is two. :func:`idconv_request_count` is that estimate.
IDCONV_BATCH_MAX = 200

TOOL = "cre-ncbi-meta"
DEFAULT_EMAIL = "aston.hliu@gmail.com"


class RetrievalUnavailable(RuntimeError):
    """A lookup DID NOT ANSWER: transport failure, non-200, unparseable payload.

    THE ONE DISTINCTION THAT CORRUPTED A NUMBER, given a name every taxonomy can
    raise. Every NCBI helper here fails closed to a falsy value, which reads
    identically whether the resolver answered "this article has no PMC full
    text" or never answered at all. The first is a fact about the article and is
    a legitimate hold; the second is a fact about the network, and counting it as
    the first turns an outage into evidence about the corpus.

    ``fulltext_reader`` already drew this line for F6 with its ``no_pmcid`` vs
    ``resolver_error`` reasons. This is the same line, raised rather than
    returned, so F3/F5/F7 -- whose seams have no reason vocabulary of their own
    -- get it without each inventing one. Callers that want the old
    swallow-everything contract simply do not pass ``strict``.
    """


class ResolverError(RetrievalUnavailable):
    """The PMID -> PMCID resolver did not answer.

    Transport failure, non-200, an unparseable body, or a payload whose top-level
    ``status`` is neither absent nor ``"ok"``. Raised rather than swallowed so a
    caller can distinguish it from the resolver ANSWERING that an article has no
    PMC full text -- see the module docstring for what conflating the two cost."""

# Review-family PubMed publication types (lowercased for compare).
REVIEW_PUBTYPES = {
    "review", "systematic review", "meta-analysis", "scoping review",
    "narrative review", "review literature as topic",
}

# The ONE publication type that means "this article was retracted" (F8).
#
# PubMed carries two retraction publication types and they mean OPPOSITE things:
#   "Retracted Publication" -- THIS article was retracted          -> F8
#   "Retraction Notice"     -- this article IS the notice that      -> NOT F8
#                              retracts some OTHER article
# Matching the wrong one would label every retraction notice a faulty citation
# and miss every actually-retracted paper -- a silent inversion that still looks
# like a working detector. So the compare is EXACT on the lowercased string; a
# substring test on "retract" matches both and is never correct here.
#
# Live counts, checked 2026-08-15 via esearch ``<term>[pt]``:
#   "Retracted Publication"     33923
#   "Retraction Notice"         32967
#   "Retraction of Publication"     0  <- the OLD MeSH name for the notice type;
#                                         PubMed no longer emits it. Kept below so
#                                         a legacy/cached record still reads
#                                         correctly, and so the exact-match
#                                         contract is pinned against BOTH spellings.
RETRACTED_PUBTYPE = "retracted publication"
RETRACTION_NOTICE_PUBTYPES = frozenset({
    "retraction notice",            # what PubMed emits today
    "retraction of publication",    # historical name, zero live records
})

# ELink's canonical "this PMID's own PMC full text" linkname. NOT pubmed_pmc_refs
# (PMC articles that CITE this PMID) nor pubmed_pmc_citedin -- those resolve to
# unrelated papers and would pre-stage a wrong reference list for the human.
#
# NO LONGER USED: resolution moved to the ID Converter, which is keyed on the
# requested id and has no citing-articles group, so there is no linkname to
# check. Kept as the record of WHY the old code looked the way it did -- see
# ncbi_pmids_to_pmcids -- so the guard is not reintroduced as a mystery, and so
# anyone who reaches for ELINK again knows what it costs.
_PMC_SELF_LINKNAME = "pubmed_pmc"


def _ncbi_params(base: dict, api_key: str, email: str) -> dict:
    """Always send tool + email; include api_key only when present."""
    params = dict(base)
    params["tool"] = TOOL
    params["email"] = email
    if api_key:
        params["api_key"] = api_key
    return params


def ncbi_pubtypes(pmid: str, api_key: str = "", email: str = "",
                  session=None) -> "list[str] | None":
    """PubMed publication types for a PMID via EFetch (medline/text).

    Parses ``^PT - <type>`` lines. Returns None on any failure (empty PMID,
    non-200, empty body, request exception)."""
    if not pmid:
        return None
    params = _ncbi_params({"db": "pubmed", "id": str(pmid),
                           "rettype": "medline", "retmode": "text"},
                          api_key, email)
    try:
        r = request_with_retry(session, EFETCH, params, limiter=NCBI, timeout=20)
    except requests.RequestException:
        return None
    if r is None or r.status_code != 200 or not r.text.strip():
        return None
    return [m.strip() for m in re.findall(r"(?m)^PT\s*-\s*(.+)$", r.text)]


def is_retracted(pubtypes: "list[str] | None") -> "bool | None":
    """None when pubtypes is None (couldn't judge); else True iff the types
    include ``Retracted Publication`` EXACTLY (see ``RETRACTED_PUBTYPE``).

    A retraction NOTICE type (``RETRACTION_NOTICE_PUBTYPES``) deliberately returns
    False: the notice retracts some other article and is not itself a retracted
    work."""
    if pubtypes is None:
        return None
    return any(pt.strip().lower() == RETRACTED_PUBTYPE for pt in pubtypes)


def is_review(pubtypes: "list[str] | None") -> "bool | None":
    """None when pubtypes is None (couldn't judge); else True if any pubtype is
    in the review family."""
    if pubtypes is None:
        return None
    return any(pt.lower() in REVIEW_PUBTYPES for pt in pubtypes)


def idconv_request_count(n_ids: int) -> int:
    """HTTP requests :func:`ncbi_pmids_to_pmcids` will spend on ``n_ids`` PMIDs.

    The cost estimate for a scaled run. NCBI meters REQUESTS, so estimating on
    PMID count over-states a batched resolve by up to 200x and would size a run's
    budget against a number that is not what gets metered."""
    n = max(0, int(n_ids))
    return -(-n // IDCONV_BATCH_MAX)          # ceil, without importing math


def ncbi_pmids_to_pmcids(pmids, api_key: str = "", email: str = "",
                         session=None) -> "dict[str, str]":
    """Resolve many PMIDs to their OWN PMCIDs via the PMC ID Converter.

    Returns ``{pmid: "PMC"+digits}``, with ``""`` for a PMID the converter
    ANSWERED has no PMC full text. Batches at :data:`IDCONV_BATCH_MAX` ids per
    request; :func:`idconv_request_count` is the cost.

    RAISES :class:`ResolverError` -- it does not swallow -- on a transport
    failure, a non-200 (what ``raise_for_status`` would have raised), a body that
    is not JSON, or a payload whose TOP-LEVEL ``status`` is neither absent nor
    ``"ok"``. ``fulltext_reader.fetch_fulltext`` turns that into
    ``resolver_error``, which is a different fact from ``no_pmcid``.

    TOP-LEVEL vs PER-RECORD ``status``, and the distinction is load-bearing. A
    PMID with no PMC full text comes back inside a perfectly healthy ``status:
    "ok"`` payload as a per-record ``{"status": "error", "errmsg": "Identifier not
    found in PMC"}``. That is the resolver ANSWERING, so it maps to ``""``.
    Treating a per-record error as a ResolverError would turn every non-OA article
    into a resolver outage and nothing would ever route.

    THE ``pubmed_pmc_refs`` HAZARD DOES NOT EXIST HERE, and that is worth stating
    so the guard is not reintroduced as a mystery. ELink returns the citing
    articles (``pubmed_pmc_refs``) alongside the article's own full text
    (``pubmed_pmc``) in one response, so taking the "first link" without checking
    the linkname yields a completely unrelated paper -- which is what
    :data:`_PMC_SELF_LINKNAME` existed to prevent. The converter is keyed on the
    REQUESTED ID and returns that article's own PMCID; there is no citing-articles
    group to confuse it with, so there is no linkname to check."""
    wanted = [str(p).strip() for p in (pmids or []) if str(p).strip()]
    out: dict = {p: "" for p in wanted}
    for start in range(0, len(wanted), IDCONV_BATCH_MAX):
        batch = wanted[start:start + IDCONV_BATCH_MAX]
        params = _ncbi_params({"ids": ",".join(batch), "format": "json"},
                              api_key, email)
        try:
            r = request_with_retry(session, IDCONV, params, limiter=NCBI,
                                   timeout=30)
        except requests.RequestException as exc:
            raise ResolverError(f"id converter transport failure: {exc}") from exc
        if r is None or r.status_code != 200:
            code = "no response" if r is None else r.status_code
            raise ResolverError(f"id converter returned {code}")
        try:
            data = r.json()
        except ValueError as exc:
            # The exact shape of the 2026-08-11 ELink outage: HTTP 200, body not
            # JSON. Silence here is what made an outage look like an absence.
            raise ResolverError(f"id converter body is not JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise ResolverError(
                f"id converter payload is not an object: {type(data).__name__}")
        status = data.get("status")
        if status is not None and status != "ok":
            raise ResolverError(f"id converter reported status {status!r}")
        for record in data.get("records") or []:
            if not isinstance(record, dict):
                continue
            requested = str(record.get("requested-id")
                            or record.get("pmid") or "").strip()
            if requested not in out:
                continue
            # A per-record error is the resolver ANSWERING "not in PMC".
            if record.get("status") == "error":
                continue
            digits = re.sub(r"\D", "", str(record.get("pmcid") or ""))
            if digits:
                out[requested] = "PMC" + digits
    return out


def ncbi_pmid_to_pmcid(pmid: str, api_key: str = "", email: str = "",
                       session=None, strict: bool = False) -> str:
    """Resolve ONE PMID to the PMCID of its own PMC full text. ``""`` when the
    article has no PMC full text, AND ``""`` on any failure.

    Routes through :func:`ncbi_pmids_to_pmcids` but keeps its own
    swallow-everything contract, because its existing caller
    (``f3_candidate_collect``) documents "returns a safe can't-judge value on any
    failure" and has no exception handling of its own -- a raise here would abort
    a collector run. Only ``fulltext_reader``'s resolver seam propagates, because
    only ``fetch_fulltext`` has a reason vocabulary to record the difference in.

    So this function CANNOT distinguish an outage from an absence, and a caller
    that needs to must use the batch helper directly."""
    if not pmid:
        return ""
    try:
        return ncbi_pmids_to_pmcids([pmid], api_key, email,
                                    session=session).get(str(pmid).strip(), "")
    except (ResolverError, requests.RequestException) as exc:
        # strict: the resolver DID NOT ANSWER, and "" would be indistinguishable
        # from it answering that this article has no PMC full text.
        if strict:
            raise ResolverError(
                f"PMID->PMCID resolver failed for {pmid}: "
                f"{type(exc).__name__}: {exc}") from exc
        return ""


def ncbi_pmc_reflist(pmcid: str, api_key: str = "", email: str = "",
                     session=None, strict: bool = False):
    """Fetch + parse a PMC review's own reference list (EFetch db=pmc, xml).

    Returns ``(provenance_candidates, review_fulltext_available)`` where
    ``provenance_candidates`` is ``[{title, claimed_pmid, year} ...]`` for refs
    that carry a title (the F3-V3 rightful-primary candidates), and
    ``review_fulltext_available`` is True when the review's full text was
    reachable and parseable into references. ``(None, None)`` on any failure.

    NOTE: this is a full-text/OA REACHABILITY signal only -- the human still
    confirms PMC-OA status per the F3 requirements. Do not over-claim OA."""
    digits = re.sub(r"\D", "", pmcid or "")
    if not digits:
        # ABSENCE, and the only one this function can be sure of: the caller
        # named no PMCID, so there is nothing to fetch. Never an outage.
        return None, None
    params = _ncbi_params({"db": "pmc", "id": digits, "retmode": "xml"},
                          api_key, email)
    try:
        r = request_with_retry(session, EFETCH, params, limiter=NCBI, timeout=30)
    except requests.RequestException as exc:
        if strict:
            raise RetrievalUnavailable(
                f"PMC reflist transport failure for {digits}: {exc}") from exc
        return None, None
    if r is None or r.status_code != 200 or not r.text.strip():
        if strict:
            raise RetrievalUnavailable(
                f"PMC reflist did not answer for {digits}: "
                f"status={getattr(r, 'status_code', None)}")
        return None, None
    tmp = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False,
                                         encoding="utf-8") as tf:
            tf.write(r.text)
            tmp = tf.name
        refs = parse_pmc_xml(tmp, source_pmcid=pmcid)
    except Exception as exc:                          # noqa: BLE001 - best-effort
        if strict:
            raise RetrievalUnavailable(
                f"PMC reflist unparseable for {digits}: "
                f"{type(exc).__name__}: {exc}") from exc
        return None, None
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    provenance = [
        {"title": ref.claimed.title,
         "claimed_pmid": ref.claimed.claimed_pmid,
         "year": ref.claimed.year}
        for ref in refs if ref.claimed.title
    ]
    return provenance, bool(refs)
