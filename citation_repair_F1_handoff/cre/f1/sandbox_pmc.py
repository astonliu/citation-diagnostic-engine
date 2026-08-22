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

from .evidence_reader import fetch_abstract as _fetch_abstract
from .fulltext_reader import fetch_fulltext as _fetch_fulltext
from .lookup import EFETCH
from .ncbi_meta import DEFAULT_EMAIL, _ncbi_params
from .parser import parse_pmc_xml
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
                      session=None) -> str:
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
        raise PmcError(
            f"EFetch db=pmc returned no <article> for PMC{digits}{detail}. Most "
            "often this means the paper is not in the PMC Open Access subset, "
            "which is a fact about licensing, not about the paper")
    return text


def load_article(pmcid: str, *, api_key: str = "", email: str = DEFAULT_EMAIL,
                 session=None, cited_only: bool = True) -> dict:
    """One real PMC article, parsed into pickable reference rows.

    ``cited_only`` keeps the references a marker in the body actually points at
    (``cited_in_body``), which is the population the taxonomy is defined over: a
    reference nothing cites carries no claim to judge. It is a filter and never a
    silent one -- the counts below report both numbers, so a paper whose markers
    failed to parse shows up as a collapse rather than as a short list.
    """
    pmcid = normalize_pmcid(pmcid)
    xml = fetch_article_xml(pmcid, api_key=api_key, email=email, session=session)
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
    return {
        "citing_pmcid": pmcid,
        "citing_pmid": refs[0].source_pmid if refs else "",
        "citing_title": refs[0].source_title if refs else "",
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
        prog="cre.f1.sandbox_pmc",
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
    for p in (article, abstract, body):
        p.add_argument("--ncbi-key", default="")
        p.add_argument("--email", default=DEFAULT_EMAIL)
    args = parser.parse_args(argv)

    try:
        if args.cmd == "article":
            out = load_article(args.pmcid, api_key=args.ncbi_key,
                               email=args.email, cited_only=not args.all)
        elif args.cmd == "abstract":
            out = load_abstract(args.pmid, api_key=args.ncbi_key, email=args.email)
        else:
            out = load_fulltext(args.pmid, api_key=args.ncbi_key, email=args.email)
    except PmcError as exc:
        print(f"[sandbox-pmc-error] {exc}", file=sys.stderr)
        return 2
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
