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
# the shared limiter. (The PMC idconv API would be an acceptable alternative but
# adds a second host to throttle.)
ELINK = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/elink.fcgi"

TOOL = "cre-ncbi-meta"
DEFAULT_EMAIL = "aston.hliu@gmail.com"

# Review-family PubMed publication types (lowercased for compare).
REVIEW_PUBTYPES = {
    "review", "systematic review", "meta-analysis", "scoping review",
    "narrative review", "review literature as topic",
}

# The canonical "this PMID's own PMC full text" link. NOT pubmed_pmc_refs
# (PMC articles that CITE this PMID) nor pubmed_pmc_citedin -- those resolve to
# unrelated papers and would pre-stage a wrong reference list for the human.
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


def is_review(pubtypes: "list[str] | None") -> "bool | None":
    """None when pubtypes is None (couldn't judge); else True if any pubtype is
    in the review family."""
    if pubtypes is None:
        return None
    return any(pt.lower() in REVIEW_PUBTYPES for pt in pubtypes)


def ncbi_pmid_to_pmcid(pmid: str, api_key: str = "", email: str = "",
                       session=None) -> str:
    """Resolve a PMID to the PMCID of its OWN PMC full text via ELink
    (pubmed -> pmc). Returns ``"PMC"+id`` of the ``pubmed_pmc`` self-link, else
    ``""`` (no PMC full text for this article, or any failure).

    Only the ``pubmed_pmc`` linkname is honored: ELink also returns
    ``pubmed_pmc_refs`` (articles that cite this PMID), and grabbing the "first
    link" from that group -- as e.g. PMID 111, which has no self-link -- yields a
    completely unrelated citing paper. That would mislead the human adjudicator,
    so a PMID with no self-link resolves to ``""`` (honestly: not OA-reachable)."""
    if not pmid:
        return ""
    params = _ncbi_params({"dbfrom": "pubmed", "db": "pmc", "id": str(pmid),
                           "retmode": "json"}, api_key, email)
    try:
        r = request_with_retry(session, ELINK, params, limiter=NCBI, timeout=20)
    except requests.RequestException:
        return ""
    if r is None or r.status_code != 200:
        return ""
    try:
        data = r.json()
    except ValueError:
        return ""
    for linkset in data.get("linksets", []) or []:
        for ldb in linkset.get("linksetdbs", []) or []:
            if ldb.get("linkname") != _PMC_SELF_LINKNAME:
                continue
            links = ldb.get("links") or []
            if links:
                return "PMC" + str(links[0])
    return ""


def ncbi_pmc_reflist(pmcid: str, api_key: str = "", email: str = "",
                     session=None):
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
        return None, None
    params = _ncbi_params({"db": "pmc", "id": digits, "retmode": "xml"},
                          api_key, email)
    try:
        r = request_with_retry(session, EFETCH, params, limiter=NCBI, timeout=30)
    except requests.RequestException:
        return None, None
    if r is None or r.status_code != 200 or not r.text.strip():
        return None, None
    tmp = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False,
                                         encoding="utf-8") as tf:
            tf.write(r.text)
            tmp = tf.name
        refs = parse_pmc_xml(tmp, source_pmcid=pmcid)
    except Exception:                                 # noqa: BLE001 - best-effort
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
