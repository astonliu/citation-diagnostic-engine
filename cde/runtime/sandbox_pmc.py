"""Pull a REAL PMC paper into packet shape, using the parsers a run already uses.

WHY THE BENCH NEEDED THIS. Every field of a hand-authored packet is typed by a
person, which is the point when the question is "what does the band do with THIS
text" -- and a hard ceiling when the question is anything else. A synthetic
citing sentence is one sentence somebody wrote in a form; a real one arrives with
its markers, its sentence boundaries, its section of origin, and seven other
references cited alongside it. A synthetic abstract is a paraphrase of what its
author already believes; a real one is what the cited paper actually says. The
band's verdict is only ever as interesting as the evidence handed to it.

So this module supplies the real thing, and supplies it through the SAME code a
production run uses -- ``parser.parse_pmc_xml`` for the citing document,
``evidence_reader.fetch_abstract`` for the cited abstract,
``fulltext_reader.fetch_fulltext`` for the cited body. Not a convenience
re-implementation: if the parser mis-splits a sentence or the reader drops a
section, the bench must show that defect rather than paper over it with tidier
code, because the defect is the engine's and the bench exists to expose it.

WHAT IT DOES NOT DO. It never invents a field. A reference whose bibliography
entry prints no PMID comes back with an empty PMID, and a cited work with no PMC
full text comes back with ``resolved: false`` and a named reason. An empty value
here means the document did not carry one, never that the fetch was skipped.

IT ALSO DOES NOT MAKE THE PAIR REAL. Loading a real paper fills the packet with
real text; the ``taxonomies`` you then select and the claim you then test are
still yours, and one pair is still a population of one. Nothing produced through
this module is reportable, for the same reason nothing from ``sandbox_judge`` is.

NETWORK, NO MODEL. Every function here is an NCBI retrieval and costs nothing but
rate limit. No model is called and no verdict is formed.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile

import requests

from ..claims.abstracts import fetch_abstract as _fetch_abstract
from ..claims.fulltext import fetch_fulltext as _fetch_fulltext
from ..refs.lookup import EFETCH
from ..refs.ncbi_meta import DEFAULT_EMAIL, _ncbi_params
from ..refs.parser import parse_pmc_xml
from .ratelimit import NCBI, request_with_retry
from .sandbox_wiring import F7_SECTION_LABELS

_PMCID_RE = re.compile(r"(?i)^\s*(?:PMC)?(\d+)\s*$")


class PmcError(ValueError):
    """The requested article could not be fetched or parsed."""


def normalize_pmcid(value: str) -> str:
    """``PMC4661126`` from ``4661126``, ``pmc4661126`` or ``PMC4661126``."""
    match = _PMCID_RE.match(str(value or ""))
    if not match:
        raise PmcError(
            f"{value!r} is not a PMCID; expected PMC followed by digits, e.g. "
            "PMC4661126")
    return "PMC" + match.group(1)


def fetch_article_xml(pmcid: str, *, api_key: str = "", email: str = DEFAULT_EMAIL,
                      session=None, raw_input: str = "") -> str:
    """The article's JATS XML from EFetch ``db=pmc``, or a named failure.

    Every non-answer raises with what actually happened. An empty return would
    reach ``parse_pmc_xml`` as an unparseable document and surface as "0
    references", which reads as an article with no bibliography rather than as a
    fetch that did not land.
    """
    digits = normalize_pmcid(pmcid)[3:]
    params = _ncbi_params({"db": "pmc", "id": digits, "retmode": "xml"},
                          api_key, email)
    try:
        response = request_with_retry(session or requests, EFETCH, params,
                                      limiter=NCBI, timeout=30)
    except requests.RequestException as exc:
        raise PmcError(f"EFetch db=pmc failed for PMC{digits}: {exc}") from exc
    if response is None:
        raise PmcError(
            f"EFetch db=pmc did not answer for PMC{digits} after its retries")
    if response.status_code != 200:
        raise PmcError(f"EFetch db=pmc returned HTTP {response.status_code} "
                       f"for PMC{digits}")
    text = response.text or ""
    if not text.strip():
        raise PmcError(f"EFetch db=pmc returned an empty body for PMC{digits}")
    # NCBI serves "not in the Open Access subset" as a 200 carrying an <error>
    # element and no <article>. Taking it at face value would parse to zero
    # references and read as an article with an empty bibliography.
    if "<article" not in text:
        detail = ""
        error = re.search(r"<error[^>]*>(.*?)</error>", text, re.S)
        if error:
            detail = " -- " + re.sub(r"\s+", " ", error.group(1)).strip()
        # NAME THE RIGHT LAYER. A bare number is a valid PMCID and a valid PMID,
        # so "17353335" silently became PMC17353335 and came back absent -- and
        # the licensing sentence below then blamed the Open Access subset for
        # what was a wrong-identifier mistake. Only the raw input distinguishes
        # them, so a caller that kept it gets the hint and one that did not is
        # left with the message it always had.
        if raw_input and "pmc" not in str(raw_input).casefold():
            raise PmcError(
                f"EFetch db=pmc has no PMC{digits}{detail}. You supplied "
                f"{str(raw_input).strip()!r} with no PMC prefix; a bare number "
                "is accepted as a PMCID, so if that was a PMID it was read as "
                f"PMC{digits}. Convert the PMID to its PMCID first -- they are "
                "different identifiers for different records, and this article "
                "may not be the one you meant")
        raise PmcError(
            f"EFetch db=pmc returned no <article> for PMC{digits}{detail}. Most "
            "often this means the paper is not in the PMC Open Access subset, "
            "which is a fact about licensing, not about the paper")
    return text


_FRONT_PMID_RE = re.compile(
    r'<article-id[^>]*pub-id-type="pmid"[^>]*>\s*(\d+)\s*</article-id>', re.I)
_FRONT_TITLE_RE = re.compile(
    r"<article-title[^>]*>(.*?)</article-title>", re.I | re.S)


def _front_identity(xml: str) -> dict:
    """The article's OWN pmid and title, read from ``<front>``.

    WHY NOT refs[0]. ``load_article`` took both fields off the first parsed
    REFERENCE, which carries them only as a back-pointer to its source document.
    That works whenever the bibliography parsed and reports "no PMID in the XML"
    whenever it did not -- including for PMC7977842, whose front matter prints
    ``<article-id pub-id-type="pmid">17353335</article-id>`` two lines above the
    withheld body. An article's identity does not depend on its bibliography
    parsing, so it must not be read through it.

    This never overrides the production parser: ``load_article`` prefers what
    ``parse_pmc_xml`` returned and falls back here only for the empty case.
    """
    pmid = ""
    match = _FRONT_PMID_RE.search(xml or "")
    if match:
        pmid = match.group(1)
    title = ""
    # The FIRST article-title in the document is the article's own; later ones
    # belong to reference entries, which is why this does not scan past <front>.
    front = (xml or "").split("</front>", 1)[0]
    match = _FRONT_TITLE_RE.search(front)
    if match:
        title = re.sub(r"<[^>]+>", "", match.group(1))
        title = re.sub(r"\s+", " ", title).strip()
    return {"pmid": pmid, "title": title}


def load_article(pmcid: str, *, api_key: str = "", email: str = DEFAULT_EMAIL,
                 session=None, cited_only: bool = True) -> dict:
    """One real PMC article, parsed into pickable reference rows.

    ``cited_only`` keeps the references a marker in the body actually points at
    (``cited_in_body``), which is the population the taxonomy is defined over: a
    reference nothing cites carries no claim to judge. It is a filter and never a
    silent one -- the counts below report both numbers, so a paper whose markers
    failed to parse shows up as a collapse rather than as a short list.
    """
    raw_input = str(pmcid or "")
    pmcid = normalize_pmcid(pmcid)
    xml = fetch_article_xml(pmcid, api_key=api_key, email=email, session=session,
                            raw_input=raw_input)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False,
                                         encoding="utf-8") as handle:
            handle.write(xml)
            tmp = handle.name
        refs = parse_pmc_xml(tmp, source_pmcid=pmcid)
    except PmcError:
        raise
    except Exception as exc:                              # noqa: BLE001
        raise PmcError(f"{pmcid} parsed as {type(exc).__name__}: {exc}") from exc
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    rows = [_row(ref) for ref in refs]
    usable = [r for r in rows if r["citing_sentence"]] if cited_only else rows
    front = _front_identity(xml)
    # A document whose publisher withheld the body still printed its own id and
    # title in <front>. Reporting them as absent said "this XML carries no PMID"
    # about an XML that carries one, which sends the reader after a parser bug
    # instead of at the licensing note the same document also carries.
    return {
        "citing_pmcid": pmcid,
        "citing_pmid": (refs[0].source_pmid if refs else "") or front["pmid"],
        "citing_title": (refs[0].source_title if refs else "") or front["title"],
        "full_text_withheld": not refs and "does not allow downloading" in xml,
        "counts": {
            "references_parsed": len(rows),
            # THE THREE NUMBERS THAT DIAGNOSE A THIN RESULT. A paper with 60
            # parsed references and 2 carrying a citance is a citance-linking
            # miss, not a paper with two citations, and only the comparison says
            # which one you are looking at.
            "marker_in_body": sum(1 for r in rows if r["cited_in_body"]),
            "with_citance": sum(1 for r in rows if r["citing_sentence"]),
            "offered": len(usable),
        },
        "references": usable,
    }


def _row(ref) -> dict:
    """One parsed reference, in the field names the packet already uses.

    Carries four fields the F3-F7 bench never reads -- publication_type, raw,
    volume, pages -- because the Band-1 gate does: scope exclusion reads the
    publication type, and the metadata comparison behind F2 weighs volume and
    pages. Dropping them here would make the gate's answer about a thinner
    reference than the document actually printed.
    """
    claimed = ref.claimed
    return {
        "citation_id": ref.citation_id,
        "cited_marker": ref.cited_reference_marker,
        "citing_sentence": ref.citance,
        "citing_source_section": ref.citance_source_section,
        "cited_in_body": ref.cited_in_body is True,
        "co_cited_with": max(0, len(ref.citance_group_members) - 1),
        "cited_claimed": {
            "title": claimed.title,
            "authors": list(claimed.authors or []),
            "year": claimed.year,
            "journal": claimed.journal,
            "claimed_pmid": claimed.claimed_pmid,
            "claimed_doi": claimed.claimed_doi,
            "volume": claimed.volume,
            "pages": claimed.pages,
            "publication_type": claimed.publication_type,
            "first_author_is_collab": claimed.first_author_is_collab,
            "ext_link": claimed.ext_link,
            "raw": claimed.raw,
        },
    }


def load_abstract(pmid: str, *, api_key: str = "", email: str = DEFAULT_EMAIL,
                  session=None) -> dict:
    """The cited work's abstract through the band's own reader.

    ``evidence_reader.fetch_abstract`` folds PubMed's "no abstract available"
    sentinels to None rather than passing the sentinel text on to a model. That
    fold is reported here as ``found: false``, because an absent abstract is a
    real state of a real paper and the packet must show it as absent rather than
    as a fetch that was never made.
    """
    pmid = str(pmid or "").strip()
    if not pmid.isdigit():
        raise PmcError(f"{pmid!r} is not a PMID")
    text = _fetch_abstract(pmid, api_key=api_key, email=email, session=session)
    return {"pmid": pmid, "found": text is not None, "abstract": text or "",
            "source": "evidence_reader.fetch_abstract (EFetch db=pubmed)"}


def load_pubmeta(pmid: str, *, api_key: str = "", email: str = DEFAULT_EMAIL,
                 session=None) -> dict:
    """The cited work's F5 metadata, through the finder the F5 seam itself uses.

    WHY THIS EXISTS. F5 retrieval runs two streams -- ``pubmed_esearch_claim``
    and ``pubmed_esearch_mesh`` -- and ``build_mesh_query`` over an empty term
    list yields an empty query. So a packet carrying no ``cited_mesh_terms``
    retrieved on one stream of two and said nothing about the half that never
    ran: not a wrong answer, but a quietly thinner one, and the page offered no
    field to fill and no route to fetch. The abstract route already pays for an
    EFetch ``db=pubmed`` round trip and keeps only the abstract; this returns the
    rest of the record it already fetched.

    It is ``PubMedCandidateFinder.fetch_metadata``, not a second parse of PubMed
    XML, so the MeSH terms that reach the packet are the ones the live finder
    would itself have used for the cited work. A reimplementation here could
    disagree with the seam and the disagreement would look like an F5 result.

    ``found: false`` means PubMed answered and held no usable record. A transport
    failure raises instead -- an outage must never reach a packet as an absence.
    """
    # Imported here, not at module scope: the finder pulls in the F5 retrieval
    # stack, and the article/abstract/body routes must not pay for it.
    from ..diagnose.candidate_finder import CandidateFinderError, PubMedCandidateFinder

    pmid = str(pmid or "").strip()
    if not pmid.isdigit():
        raise PmcError(f"{pmid!r} is not a PMID")
    finder = PubMedCandidateFinder(api_key=api_key, email=email, session=session)
    try:
        record = finder.fetch_metadata(pmid)
    except CandidateFinderError as exc:
        raise PmcError(
            f"PubMed metadata lookup failed for PMID {pmid}: {exc}. This is an "
            "outage, not an empty record -- rerun rather than treating the "
            "MeSH terms as absent") from exc
    if record is None:
        return {"pmid": pmid, "found": False, "mesh_terms": [],
                "mesh_major_terms": [], "publication_types": [],
                "pub_date": "", "title": "", "authors": [],
                "source": "f5_candidate_finder.PubMedCandidateFinder.fetch_metadata"}
    return {
        "pmid": pmid,
        "found": True,
        "mesh_terms": list(record.get("mesh_terms") or []),
        "mesh_major_terms": list(record.get("mesh_major_terms") or []),
        "publication_types": list(record.get("publication_types") or []),
        "pub_date": record.get("pub_date") or "",
        "pub_date_precision": record.get("pub_date_precision") or "",
        "title": record.get("title") or "",
        "authors": list(record.get("authors") or []),
        "source": "f5_candidate_finder.PubMedCandidateFinder.fetch_metadata",
    }


def load_fulltext(pmid: str, *, api_key: str = "", email: str = DEFAULT_EMAIL,
                  session=None) -> dict:
    """The cited work's PMC body, filtered to the sections F7 admits.

    F7 reads ``methods``, ``results``, ``table`` and ``figure`` and nothing else
    -- never the abstract, introduction or discussion, which are exactly where
    authors restate other people's findings and so are where the attribution
    error F7 hunts would be indistinguishable from correct reporting. The reader
    returns every section it found; this filters, COUNTS WHAT IT DROPPED, and
    hands back both numbers.

    ``retrieval_complete`` is passed through untouched. It is the DEC-032
    predicate that licenses reading silence as absence, and a bench that quietly
    upgraded an incomplete retrieval would be manufacturing the one condition the
    band refuses to assume.
    """
    pmid = str(pmid or "").strip()
    if not pmid.isdigit():
        raise PmcError(f"{pmid!r} is not a PMID")
    result = _fetch_fulltext(pmid, api_key=api_key, email=email, session=session)
    if result is None:
        raise PmcError(f"PMID {pmid} cannot be attempted for full text")
    sections = result.get("sections") or []
    admitted = [{"label": s.get("label"), "title": s.get("title") or "",
                 "text": s.get("text") or ""}
                for s in sections if s.get("label") in F7_SECTION_LABELS]
    return {
        "pmid": pmid,
        "pmcid": result.get("pmcid") or "",
        "resolved": bool(result.get("resolved")),
        "retrieval_complete": bool(result.get("retrieval_complete")),
        "incomplete_reasons": list(result.get("incomplete_reasons") or []),
        "sections_present": list(result.get("sections_present") or []),
        "sections": admitted,
        "counts": {"returned": len(sections), "f7_admissible": len(admitted),
                   "dropped_not_body": len(sections) - len(admitted)},
        "source": result.get("source") or "",
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="cde.runtime.sandbox_pmc",
        description="Pull a real PMC article, abstract or body into packet shape.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    article = sub.add_parser("article", help="parse a citing paper's references")
    article.add_argument("pmcid")
    article.add_argument("--all", action="store_true",
                         help="include references no body marker points at")
    abstract = sub.add_parser("abstract", help="the cited work's abstract")
    abstract.add_argument("pmid")
    body = sub.add_parser("fulltext", help="the cited work's F7-admissible body")
    body.add_argument("pmid")
    pubmeta = sub.add_parser("pubmeta",
                             help="the cited work's MeSH terms, types and date")
    pubmeta.add_argument("pmid")
    for p in (article, abstract, body, pubmeta):
        p.add_argument("--ncbi-key", default="")
        p.add_argument("--email", default=DEFAULT_EMAIL)
    args = parser.parse_args(argv)

    try:
        if args.cmd == "article":
            out = load_article(args.pmcid, api_key=args.ncbi_key,
                               email=args.email, cited_only=not args.all)
        elif args.cmd == "abstract":
            out = load_abstract(args.pmid, api_key=args.ncbi_key, email=args.email)
        elif args.cmd == "pubmeta":
            out = load_pubmeta(args.pmid, api_key=args.ncbi_key, email=args.email)
        else:
            out = load_fulltext(args.pmid, api_key=args.ncbi_key, email=args.email)
    except PmcError as exc:
        print(f"[sandbox-pmc-error] {exc}", file=sys.stderr)
        return 2
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
