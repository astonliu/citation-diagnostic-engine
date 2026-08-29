"""The Band-1 gate (F1 / F2 / F8) for ONE pair, from the same code the corpus runs.

WHY THIS EXISTS. ``sandbox_judge`` runs the F3-F7 judgment band and says so
plainly: it skips the pre-band gate on purpose, so every packet it judges is
treated as already cleared. That is the right scope for a band bench and it
leaves a real question unanswered -- *would this pair have reached the band at
all?* F1 (the cited work does not exist), F2 (the cited work is not the work the
reference claims) and F8 (the cited work was retracted before it was cited) are
decided upstream by deterministic Band-1 code, and a pair they fault never gets
an F3-F7 verdict in production no matter what the band would have said about it.

This module answers that question with ``run.process_reference`` -- the SAME
per-reference entry point ``run.run`` calls for every row of a corpus, and the
same one ``production_launcher`` drives through Band 1. Nothing is reimplemented
here. This file builds a ``Reference`` out of a packet, hands it to the engine,
and reports the label the engine returned along with the accumulated evidence
that produced it.

HOW IT DIFFERS FROM ``sandbox_judge``, AND IT IS NOT A DETAIL.

  * ``sandbox_judge`` makes NO network call. The packet IS the evidence, so its
    answer is about the text you wrote and is reproducible forever.
  * This module is almost ENTIRELY network. It asks PubMed whether a PMID
    resolves, asks Crossref and OpenAlex whether a title exists anywhere, and
    asks PubMed whether a record carries ``Retracted Publication``. Its answer is
    about the live databases AS OF NOW and can change tomorrow without the packet
    changing at all.

WHAT A SYNTHETIC PAIR GETS BACK, STATED UP FRONT SO IT IS NOT MISREAD AS A
FINDING. A hand-authored PMID does not exist in PubMed. The gate will look it up,
find nothing, search three databases for the claimed title, find nothing, and
return F1 or a held state -- and it will be RIGHT: the work really is not there.
That is a fact about the identifier you invented, not about the citation you were
trying to model. The F3-F7 band is where a synthetic pair is meaningful, because
the band judges the text in the packet. The gate is meaningful only for a pair
whose identifiers are real, which is what ``sandbox_pmc`` exists to supply.

PRECISION-FIRST IS THE ENGINE'S, AND IT IS VISIBLE HERE. Band 1 will not accuse
on evidence it failed to gather: a lookup that did not answer holds the reference
rather than reading as an absence (``decide`` requires ``fetch_answered`` and
``confirm.fully_answered`` before F1 is reachable). So ``unverifiable`` and
``human_review`` are ordinary, correct outcomes and are reported as their own
class -- never folded in with a fault.

ONE MODEL CALL, SOMETIMES. The expensive path runs ``llm_filter`` on a flagged
survivor. It is recorded through the same ``AdapterReceipt`` seam name production
books it under (``f1_llm_filter``), so the count is comparable with a real run's.
Every short-circuit in ``process_reference`` -- retracted source, proved
same-work, an unanswered claimed-PMID fetch, an exact-DOI case -- reaches
``decide`` without paying for it, and the receipt shows that by staying at zero.

Usage:
    python -m cde.runtime.sandbox_preband packet.json
    python -m cde.runtime.sandbox_preband packet.json --dry-run
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time

import requests

from ..diagnose import pipeline as jr
from ..refs import run as band1
from ..refs.preband_contract import CITATION_ID_RE
from ..refs.preband_disposition import (
    BAND2_ADMITTING_LABELS,
    CLEARING_LABELS,
    DISPOSITION_LABELS,
    FAULT_LABELS,
    OPERATIONAL_LABELS,
    SAME_WORK_LABELS,
    resolved_identifier,
)
from .ratelimit import configure_ncbi
from .recording_adapter import AdapterReceipt
from ..refs.schema import ClaimedRef, Reference
from ..refs.unscoreable import is_non_article_reference

#: The packet shape ``sandbox_judge`` reads. The gate takes the SAME object, so
#: one authored pair can be put to both engines without being retyped -- which is
#: the only way the two answers are about the same thing.
PACKET_SCHEMA = "cre_sandbox_packet_v1"

#: Band 1's own model seam, named exactly as ``production_launcher`` books it
#: (``recorded_band1_complete``). Same string, so a bench receipt and a
#: production receipt can be read side by side without a translation table.
BAND1_SEAM = "f1_llm_filter"

#: What each label AUTHORIZES, which is the only question a caller actually has.
#: Read off ``preband_disposition``'s own frozensets rather than restated here:
#: a label added to the engine's vocabulary and not to this map is reported as
#: ``unknown`` instead of being silently sorted into a class it may not belong in.
LABEL_CLASSES = (
    ("admit", CLEARING_LABELS | SAME_WORK_LABELS),
    ("fault", FAULT_LABELS),
    ("held", OPERATIONAL_LABELS),
)


class PrebandError(ValueError):
    """The packet cannot be turned into a Band-1 reference."""


def label_class(label: str) -> str:
    for name, members in LABEL_CLASSES:
        if label in members:
            return name
    return "unknown"


def _year(value) -> "int | None":
    """The four-digit year out of ``2010``, ``"2010"`` or ``"2010-11-01"``.

    ``ClaimedRef.year`` is an int and the packet's field is free text, because
    F5 needs a full date there. Taking the leading four digits is the same
    reduction the parser performs on a JATS ``<year>``; anything that is not a
    year yields None, which every Band-1 comparison already treats as "no year
    claimed" rather than guessing one.
    """
    if isinstance(value, int):
        return value
    match = re.match(r"\s*(\d{4})", str(value or ""))
    return int(match.group(1)) if match else None


def build_reference(packet: dict) -> Reference:
    """The ``Reference`` Band 1 processes, built from a packet rather than XML.

    Every field the parser sets from ``<ref>`` is set here from the packet's
    ``cited_claimed``, including the four the F3-F7 bench never reads --
    ``publication_type``, ``raw``, ``volume``, ``pages`` -- because Band 1 does
    read them: ``is_non_article_reference`` excludes a book or a database from
    scope on the publication type alone, and the metadata comparison that decides
    F2 weighs volume and pages. A packet that omits them is not refused; the gate
    simply has less to compare, and the log says which fields were empty.
    """
    if packet.get("schema") != PACKET_SCHEMA:
        raise PrebandError(
            f"packet schema {packet.get('schema')!r} is not {PACKET_SCHEMA!r}")
    citation_id = str(packet.get("citation_id") or "").strip()
    if not citation_id:
        raise PrebandError("packet is missing required field 'citation_id'")
    cited = packet.get("cited_claimed") or {}
    if not isinstance(cited, dict):
        raise PrebandError("cited_claimed must be an object")

    claimed = ClaimedRef(
        title=str(cited.get("title") or "").strip(),
        authors=[str(a) for a in (cited.get("authors") or [])],
        year=_year(cited.get("year")),
        journal=str(cited.get("journal") or "").strip(),
        claimed_pmid=str(cited.get("claimed_pmid") or "").strip(),
        claimed_doi=str(cited.get("claimed_doi") or "").strip(),
        raw=str(cited.get("raw") or "").strip(),
        volume=str(cited.get("volume") or "").strip(),
        pages=str(cited.get("pages") or "").strip(),
        publication_type=str(cited.get("publication_type") or "").strip(),
        first_author_is_collab=bool(cited.get("first_author_is_collab")),
        ext_link=str(cited.get("ext_link") or "").strip(),
    )
    return Reference(
        citation_id=citation_id,
        citance=str(packet.get("citing_sentence") or ""),
        claimed=claimed,
        cited_reference_marker=str(packet.get("cited_marker") or ""),
        source_pmcid=str(packet.get("citing_pmcid") or "").strip(),
        source_pmid=str(packet.get("citing_pmid") or "").strip(),
        source_title=str(packet.get("citing_title") or "").strip(),
        # A packet carries one reference and no document, so nobody walked the
        # body looking for a marker pointing at it. None is the truthful value
        # and the one that keeps the uncited-reference scope exclusion -- which
        # only an explicit False licenses -- off a pair that was never examined.
        cited_in_body=None,
    )


def plan_for(ref: Reference, *, f8_timing: bool) -> dict:
    """Which Band-1 routes this reference can reach, and what each one asks.

    Deliberately NOT a prediction of the label. It reports the inputs that are
    present and the lookups they make reachable; the label is the engine's to
    return, and a page that guessed it here would be answering with itself.
    """
    has_pmid = bool(ref.claimed.claimed_pmid)
    has_doi = bool(ref.claimed.claimed_doi)
    lookups = []
    if has_pmid:
        lookups.append("PubMed EFetch on the claimed PMID (does it resolve, and "
                       "to what)")
    else:
        lookups.append("structured bibliographic lookup, because no PMID is "
                       "claimed")
    if has_doi and not has_pmid:
        lookups.append("exact-DOI resolution across the configured providers")
    lookups.append("title + first-author search across PubMed, Crossref and "
                   "OpenAlex, on a flagged survivor only")
    if f8_timing:
        lookups.append("F8 retraction-notice timing against the citing paper's "
                       "publication date")
    else:
        lookups.append("PubMed publication types on the resolved record, for the "
                       "F8 retraction tri-state")
    return {
        "citation_id": ref.citation_id,
        "canonical_citation_id": bool(CITATION_ID_RE.match(ref.citation_id)),
        "claimed_pmid": ref.claimed.claimed_pmid,
        "claimed_doi": ref.claimed.claimed_doi,
        "claimed_title": ref.claimed.title,
        "citing_pmid": ref.source_pmid,
        "f8_timing": f8_timing,
        "scope_excluded_offline": is_non_article_reference(ref.claimed),
        "network": "live: PubMed, Crossref, OpenAlex",
        "reachable_lookups": lookups,
        "labels": sorted(DISPOSITION_LABELS),
        "admitting_labels": sorted(BAND2_ADMITTING_LABELS),
    }


def _f8_fetch_meta(ncbi_key: str, mailto: str, session):
    """The metadata seam ``run.run`` builds for ``f8_timing``, built the same way.

    Copied in shape, not in substance: it is the same ``PubMedCandidateFinder``
    with the same per-work memo, so the boundary dates F8 compares come from the
    same place a corpus run gets them.
    """
    from ..diagnose.candidate_finder import PubMedCandidateFinder
    from ..refs.ncbi_meta import DEFAULT_EMAIL

    finder = PubMedCandidateFinder(
        api_key=ncbi_key, email=mailto or DEFAULT_EMAIL, session=session)
    memory: dict = {}

    def fetch_meta(work_id):
        key = str(work_id or "").strip()
        if key not in memory:
            memory[key] = finder.fetch_metadata(key)
        value = memory[key]
        return dict(value) if isinstance(value, dict) else None

    return fetch_meta


def screen(packet: dict, *, model: str, api_key: str = "", ncbi_key: str = "",
           mailto: str = "", f8_timing: bool = True,
           dry_run: bool = False) -> dict:
    """Run one pair through Band 1. Returns the disposition and its evidence.

    ``dry_run`` builds the reference and reports the reachable routes without a
    single network or model call. It is the honest answer to "what would this
    ask" -- a blank label would read as a clear.
    """
    ref = build_reference(packet)
    plan = plan_for(ref, f8_timing=f8_timing)

    if f8_timing and not ref.source_pmid.isdigit():
        # REFUSE RATHER THAN RETURN A LABEL THAT MEANS SOMETHING ELSE. With
        # f8_timing on and no citing PMID, `assess_f8_timing` returns UNRESOLVED
        # ("work_identity_unavailable") and `decide` turns that into UNSCOREABLE
        # -- for EVERY pair, before any of the F1/F2 evidence is even weighed. A
        # reader would see a uniform "unscoreable" and reasonably conclude the
        # gate had judged their citations, when in fact it never got to them.
        raise PrebandError(
            "the F8 timing gate needs the CITING paper's PMID (citing_pmid): it "
            "compares the retraction-notice date against the date the citing "
            "paper appeared, and with no citing date every pair resolves "
            "UNSCOREABLE before F1 or F2 is weighed. Supply citing_pmid, or turn "
            "the timing gate off to fall back to the retraction tri-state on the "
            "cited record alone")

    if dry_run:
        return {"dry_run": True, "plan": plan,
                "reference": ref.to_log_record()}

    # THE SAME RECEIPT IDENTITY sandbox_judge builds. The pinned Opus model
    # rejects a temperature parameter, so production records the resolved value
    # as the string "unsupported" (DEC-070) and verify_receipt refuses a call
    # that carries the key at all. Pinning a number here would receipt a
    # different adapter than the one Band 1 actually runs.
    receipt = AdapterReceipt(model=model, temperature=jr.TEMPERATURE_UNSUPPORTED)
    complete = band1.make_completer(model, api_key)

    def recorded_complete(prompt):
        # Recorded BEFORE the call, exactly as production_launcher does it, so a
        # provider error still leaves evidence the attempt was made.
        receipt.record(seam=BAND1_SEAM)
        return complete(prompt)

    configure_ncbi(bool(ncbi_key))
    session = requests.Session()
    started = time.time()
    band1.process_reference(
        ref, recorded_complete, ncbi_key=ncbi_key,
        crossref_mailto=mailto, openalex_mailto=mailto, session=session,
        f8_fetch_meta=(_f8_fetch_meta(ncbi_key, mailto, session)
                       if f8_timing else None))
    record = ref.to_log_record()
    label = str(ref.label or "")

    return {
        "plan": plan,
        "label": label,
        # A label the engine can emit but this schema does not know is reported
        # as such rather than being sorted into a class it may not belong in.
        "known_label": label in DISPOSITION_LABELS,
        "class": label_class(label),
        "cleared": label in CLEARING_LABELS,
        # BOTH BOOLEANS, because one cannot say both true things at once: a
        # `same_work` row is admitted to the F3-F7 band AND is not an F2 clear.
        "band2_admitted": label in BAND2_ADMITTING_LABELS,
        "confidence": ref.confidence,
        "rationale": ref.rationale,
        "decided_by": str((record.get("log") or {}).get("decided_by") or ""),
        "resolved_identifier": resolved_identifier(record),
        "reference": record,
        "receipt": receipt.summary(),
        "elapsed": round(time.time() - started, 2),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="cde.runtime.sandbox_preband",
        description="Run one packet's pair through the real Band-1 F1/F2/F8 gate.")
    parser.add_argument("packet", help="path to a cre_sandbox_packet_v1 JSON file")
    parser.add_argument("--model", default="claude-opus-5",
                        help="model for the llm_filter stage on the expensive path")
    parser.add_argument("--api-key", default="",
                        help="defaults to $ANTHROPIC_API_KEY")
    parser.add_argument("--ncbi-key", default="",
                        help="raises the NCBI rate limit; optional")
    parser.add_argument("--mailto", default="",
                        help="contact address sent to Crossref and OpenAlex")
    parser.add_argument("--no-f8-timing", action="store_true",
                        help="skip the retraction-notice timing gate and use the "
                             "retraction tri-state on the cited record alone")
    parser.add_argument("--dry-run", action="store_true",
                        help="report the reachable routes; zero network, zero calls")
    args = parser.parse_args(argv)

    with open(args.packet, encoding="utf-8") as fh:
        packet = json.load(fh)

    try:
        result = screen(packet, model=args.model, api_key=args.api_key,
                        ncbi_key=args.ncbi_key, mailto=args.mailto,
                        f8_timing=not args.no_f8_timing, dry_run=args.dry_run)
    except PrebandError as exc:
        print(f"[sandbox-preband-error] {exc}", file=sys.stderr)
        return 2

    print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
