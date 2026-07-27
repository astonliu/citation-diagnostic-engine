"""F2-F: resolve the intended work A, then compare it to B.

The matcher scores the CLAIMED written metadata against B -- the record the
claimed PMID resolves to. It never resolves the work the citation was TRYING to
name (call it A). This module does: it dereferences A from the written metadata
through a cascade, compares A to B, and emits a three-way outcome so a flag can
carry a PROPOSED repair rather than only a verdict.

    A resolves to B                -> the identifier is correct, the bibliography
                                      text is corrupt                (not_f2)
    A resolves to nothing anywhere -> A is not a resolvable work     (unscoreable)
    A resolves to C != B           -> genuine F2; C is the proposed  (f2_with_repair)
                                      repair target

Cascade (stop at the first confident hit; cheapest/most reliable first):
    1. Cited DOI     -- when written_doi is present and differs from the resolved
                        DOI, dereference it through the registration-agency-
                        appropriate provider (``dereference_doi``): Crossref, or
                        DataCite for arXiv/DataCite prefixes, with a DataCite
                        fallback on ANY Crossref miss. A Crossref 404 is NOT a dead
                        DOI (spec §14.4) -- arXiv (10.48550) registers with
                        DataCite, not Crossref.
    2. PubMed ESearch -- title + first author + year.
    3. Crossref       -- query.bibliographic on the full reference string
                        (REQUIRED: some cited venues PubMed does not index).
    4. OpenAlex       -- backstop.

Preprint->published relation (spec §14.3): before the cascade, a cited
bioRxiv/medRxiv preprint DOI is checked against ``api.biorxiv.org``; when its
authoritative published version IS B, A and B are one work under a version
relation and the row routes ``not_f2`` with NO proposed repair (handles
PMC8887078:R1). A DataCite ``resourceTypeGeneral == "Preprint"`` is carried as a
``"Preprint"`` publication type so ``is_preprint_resolved`` fires on a resolved
arXiv/DataCite preprint exactly as on a Crossref ``posted-content`` one (spec §9).

DISCIPLINE (F2_MATCHER_REVISION_SPEC guardrails):
  * DETECTOR OUTPUT IS NEVER GOLD. This module emits evidence and a *proposed*
    repair. It MUST NOT write a label, and the proposal stays hidden from the
    annotator until after commit. The returned object carries no taxonomy label.
  * A PRE-FLIGHT ROUND-TRIP (``preflight``) must pass before any batch.
  * Abstract-overlap comparison is legitimate here ONLY because A now resolves to
    a real record with its own abstract; it separates "same work re-presented"
    (preprint->published, translation, republication) from "genuinely different
    paper" -- the SAME_WORK vs TRUE_F2 call. It is evidence for a human, not a
    label.

Network: this environment cannot reach NCBI/Crossref/OpenAlex; every entry point
takes an injectable ``session`` and routes through the shared rate limiters, so
the unit tests drive the cascade with a fake session and the live batch runs in
Colab. Batch is checkpointed to JSONL so a 3 req/s run is resumable.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field, asdict
from typing import Callable, Iterable, Optional

import requests

from .schema import ClaimedRef, RetrievedRecord
from .ratelimit import CROSSREF, DATACITE, BIORXIV, NCBI, request_with_retry
from .lookup import fetch_pubmed
from .biblio_match import (retrieve_candidates, best_match, title_sim,
                           _coerce_year, _crossref_record)
from .work_identity import doi_equivalent, _norm_doi

CROSSREF_WORKS = "https://api.crossref.org/works"
DATACITE_DOIS = "https://api.datacite.org/dois"
BIORXIV_DETAILS = "https://api.biorxiv.org/details"
PUBMED_ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"

# DOI registration-agency routing (spec §14.4: a Crossref 404 is NOT a dead DOI).
# arXiv registers with DataCite, not Crossref, so its DOIs must dereference there.
# Extend as other DataCite-registered preprint registrants are confirmed.
_DATACITE_PREFIXES = ("10.48550/",)                     # arXiv
# A date-stamped 10.1101 DOI is a bioRxiv/medRxiv PREPRINT; bioRxiv's own API
# supplies the authoritative preprint->published relation and the abstract.
_BIORXIV_DATESTAMP_RE = re.compile(r"^10\.1101/\d{4}\.\d{2}\.\d{2}\.")

# Outcome codes (NOT taxonomy labels -- routing hints for the human queue).
OUTCOME_NOT_F2 = "not_f2"                 # A resolves to B: identifier is correct
OUTCOME_F2_WITH_REPAIR = "f2_with_repair"  # A resolves to C != B: proposed repair = C
OUTCOME_UNSCOREABLE = "unscoreable"        # A resolves to nothing resolvable
OUTCOME_UNDETERMINED = "undetermined"      # cascade errored on the network, retry later

# A candidate is a confident resolution of A when its title matches the written
# title this closely (0..1). Kept conservative: a wrong A is worse than an
# unresolved A (which routes to unscoreable, not to a false repair).
A_TITLE_CONFIDENT = 0.90
# When A and B share a DOI, or A's title matches B this closely, A == B.
AB_SAME_TITLE = 0.90


@dataclass
class AResolution:
    """Evidence + a PROPOSED repair. Carries no taxonomy label by construction."""
    outcome: str
    a_source: str = ""                 # which cascade step resolved A
    a_record: Optional[dict] = None    # the resolved A (RetrievedRecord as dict)
    proposed_repair: Optional[dict] = None  # C = A when A != B (pmid/doi/title)
    a_vs_b_title_sim: Optional[float] = None
    a_vs_b_doi_match: Optional[bool] = None
    evidence: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Cascade steps -- each returns a resolved RetrievedRecord or None
# ---------------------------------------------------------------------------
def _json_or_none(resp):
    if resp is None or resp.status_code != 200:
        return None
    try:
        return resp.json()
    except ValueError:
        return None


def _datacite_record(attrs: dict) -> RetrievedRecord:
    """Parse a DataCite ``data.attributes`` object into a RetrievedRecord.

    ``types.resourceTypeGeneral == "Preprint"`` is DataCite's analogue of
    Crossref's ``type: posted-content`` (spec §9), so it is carried through as a
    ``"Preprint"`` publication type -- that makes ``is_preprint_resolved`` fire on
    a resolved arXiv/DataCite preprint exactly as it does on a Crossref one."""
    titles = attrs.get("titles") or []
    title = " ".join(t.get("title", "") for t in titles if t.get("title")).strip()
    authors = []
    for c in attrs.get("creators") or []:
        if not isinstance(c, dict):
            continue
        name = c.get("familyName") or c.get("name") or ""
        if name:
            authors.append(name)
    journal = ((attrs.get("container") or {}).get("title") or "")
    rtg = ((attrs.get("types") or {}).get("resourceTypeGeneral") or "")
    ptypes = ["Preprint"] if rtg.strip().lower() == "preprint" else (
        [rtg] if rtg else [])
    return RetrievedRecord(
        resolved=True, title=title, authors=authors,
        year=_coerce_year(attrs.get("publicationYear")),
        journal=journal, doi=(attrs.get("doi") or "").lower(),
        publication_types=ptypes)


def resolve_by_datacite_doi(doi: str, session=None,
                            errors: Optional[list] = None) -> Optional[RetrievedRecord]:
    """Dereference a DOI through DataCite (``api.datacite.org/dois/{doi}``, open,
    no key). Required for arXiv (10.48550) and any DOI Crossref does not carry.
    A THROWN request appends to ``errors`` (retrieval_incomplete); a non-200 (e.g.
    a 404 = DOI not carried here) is a clean miss and does NOT (§14.4)."""
    doi = (doi or "").strip()
    if not doi:
        return None
    try:
        resp = request_with_retry(session, f"{DATACITE_DOIS}/{doi}", {},
                                  limiter=DATACITE, timeout=20)
    except requests.RequestException:
        if errors is not None:
            errors.append("datacite_deref")
        return None
    data = _json_or_none(resp)
    if not data or not isinstance(data.get("data"), dict):
        return None
    rec = _datacite_record(data["data"].get("attributes") or {})
    return rec if (rec.title or rec.doi) else None


def _crossref_deref(doi: str, session=None,
                    errors: Optional[list] = None) -> Optional[RetrievedRecord]:
    try:
        resp = request_with_retry(session, f"{CROSSREF_WORKS}/{doi}", {},
                                  limiter=CROSSREF, timeout=20)
    except requests.RequestException:
        if errors is not None:
            errors.append("crossref_deref")
        return None
    data = _json_or_none(resp)
    if not data or "message" not in data:
        return None                          # non-200 / 404: not carried, fall back
    rec = _crossref_record(data["message"])
    rec.resolved = True
    return rec if (rec.title or rec.doi) else None


def dereference_doi(doi: str, session=None,
                    errors: Optional[list] = None) -> Optional[RetrievedRecord]:
    """Resolve a DOI to a record via the registration-agency-appropriate provider
    (spec §14.4). arXiv/DataCite prefixes go straight to DataCite; every other DOI
    tries Crossref first and FALLS BACK to DataCite on a Crossref miss -- a
    Crossref 404 is not evidence the DOI is dead, only that it is registered with a
    different agency. Returns None only when no provider carries the DOI; a thrown
    request is recorded in ``errors`` so the caller can route it to undetermined."""
    doi = _norm_doi(doi)
    if not doi:
        return None
    if any(doi.startswith(p) for p in _DATACITE_PREFIXES):
        return resolve_by_datacite_doi(doi, session=session, errors=errors)
    return _crossref_deref(doi, session=session, errors=errors) or \
        resolve_by_datacite_doi(doi, session=session, errors=errors)


def biorxiv_published_doi(doi: str, session=None) -> str:
    """The published-version DOI for a date-stamped bioRxiv/medRxiv preprint DOI,
    via ``api.biorxiv.org/details/{server}/{doi}`` (open, no key). Returns "" when
    the preprint has no recorded publication or the lookup fails. This is the
    authoritative preprint->published relation (spec §14.3) -- it does not depend
    on the publisher having deposited an ``is-preprint-of`` link in Crossref."""
    doi = _norm_doi(doi)
    if not _BIORXIV_DATESTAMP_RE.match(doi):
        return ""
    for server in ("biorxiv", "medrxiv"):
        try:
            resp = request_with_retry(session, f"{BIORXIV_DETAILS}/{server}/{doi}",
                                      {}, limiter=BIORXIV, timeout=20)
        except requests.RequestException:
            continue
        data = _json_or_none(resp)
        if not data:
            continue
        coll = data.get("collection") or []
        for entry in coll:
            pub = (entry.get("published") or "").strip()
            if pub and pub.upper() != "NA":
                return pub.lower()
    return ""


def resolve_by_cited_doi(claimed: ClaimedRef, resolved_doi: str,
                         session=None, errors: Optional[list] = None
                         ) -> Optional[RetrievedRecord]:
    """Step 1. When the citation carries its OWN DOI that differs from the resolved
    record's DOI, dereference it through the agency-appropriate provider
    (``dereference_doi``: Crossref, DataCite for arXiv/DataCite prefixes, with a
    DataCite fallback on any Crossref miss). The cheapest, most reliable path -- a
    DOI is a globally unique work identifier. Returns None when there is no
    distinct cited DOI or no provider carries it."""
    doi = (claimed.claimed_doi or "").strip()
    if not doi or doi_equivalent(doi, resolved_doi):
        return None
    return dereference_doi(doi, session=session, errors=errors)


def resolve_by_pubmed(claimed: ClaimedRef, *, api_key: str = "", email: str = "",
                      session=None, errors: Optional[list] = None
                      ) -> Optional[RetrievedRecord]:
    """Step 2. ESearch on title + first author + year, then EFetch the top PMID
    and confirm it against the written title. Returns None on no confident hit; a
    thrown ESearch request is recorded in ``errors``."""
    if not claimed.title:
        return None
    terms = [f"{claimed.title}[Title]"]
    if claimed.authors:
        terms.append(f"{claimed.authors[0]}[Author]")
    if claimed.year:
        terms.append(f"{claimed.year}[DP]")
    try:
        es = _json_or_none(request_with_retry(
            session, PUBMED_ESEARCH,
            {"db": "pubmed", "term": " AND ".join(terms), "retmode": "json",
             "retmax": 3, **({"api_key": api_key} if api_key else {})},
            limiter=NCBI, timeout=20))
    except requests.RequestException:
        if errors is not None:
            errors.append("pubmed_esearch")
        return None
    if not es:
        return None
    ids = es.get("esearchresult", {}).get("idlist", []) or []
    for pmid in ids:
        rec = fetch_pubmed(pmid, api_key=api_key, email=email, session=session)
        if rec.resolved and title_sim(claimed.title, rec.title) >= A_TITLE_CONFIDENT:
            return rec
    return None


def resolve_by_candidates(claimed: ClaimedRef, session=None,
                          errors: Optional[list] = None
                          ) -> Optional[RetrievedRecord]:
    """Steps 3-4. Crossref query.bibliographic + OpenAlex backstop, via the shared
    ``retrieve_candidates``. Returns the best candidate when it confidently
    matches the written title, else None. Crossref is REQUIRED, not optional:
    several cited venues (Plant and Soil, J Great Lakes Res, MiMB volumes) are not
    PubMed-indexed, and 3 of the seed-37 HIGH true positives cite them. A thrown
    search request is recorded in ``errors`` (via retrieve_candidates), so a flaky
    Crossref never masquerades as 'A not found' -- decisive for PMC8015328:ref011,
    whose A (Nematology, not PubMed-indexed) must come from Crossref."""
    cands = retrieve_candidates(claimed, n=5, session=session, errors=errors)
    if not cands:
        return None
    bm = best_match(claimed, cands)
    if not bm.found or bm.best is None:
        return None
    if title_sim(claimed.title, bm.best.record.title) >= A_TITLE_CONFIDENT:
        rec = bm.best.record
        rec.resolved = True
        return rec
    return None


_CASCADE_DEFAULT = ("cited_doi", "pubmed", "candidates")


def resolve_a(claimed: ClaimedRef, resolved_b: RetrievedRecord, *,
              api_key: str = "", email: str = "", session=None,
              steps: Iterable[str] = _CASCADE_DEFAULT,
              errors: Optional[list] = None) -> tuple[str, Optional[RetrievedRecord]]:
    """Run the cascade, stopping at the first confident resolution of A. Returns
    ``(source, a_record)`` -- ``source`` is the step name, ``a_record`` is None
    when nothing resolved A. A thrown request in any step appends to ``errors`` so
    the caller can tell 'A not found (all sources completed)' from 'a source
    errored' (spec §14.6)."""
    for step in steps:
        if step == "cited_doi":
            a = resolve_by_cited_doi(claimed, resolved_b.doi if resolved_b else "",
                                     session=session, errors=errors)
        elif step == "pubmed":
            a = resolve_by_pubmed(claimed, api_key=api_key, email=email,
                                  session=session, errors=errors)
        elif step == "candidates":
            a = resolve_by_candidates(claimed, session=session, errors=errors)
        else:
            continue
        if a is not None:
            return (step, a)
    return ("", None)


# ---------------------------------------------------------------------------
# A vs B comparison + three-way routing
# ---------------------------------------------------------------------------
def _a_equals_b(a: RetrievedRecord, b: RetrievedRecord) -> tuple[bool, float, Optional[bool]]:
    """Is the resolved A the SAME record as B? True when their DOIs agree, or
    (no DOI to compare) their titles match at/above ``AB_SAME_TITLE``. Returns
    ``(same, title_sim, doi_match)``."""
    ts = title_sim(a.title, b.title) if (a.title and b.title) else 0.0
    doi_m: Optional[bool] = None
    if a.doi and b.doi:
        doi_m = doi_equivalent(a.doi, b.doi)
        if doi_m:
            return (True, ts, True)
        return (False, ts, False)      # DOIs present and disagree -> different work
    return (ts >= AB_SAME_TITLE, ts, doi_m)


def _repair_from(a: RetrievedRecord) -> dict:
    """The PROPOSED repair target C = A. pmid/doi/title only -- enough to re-cite,
    nothing that could be mistaken for a label."""
    return {"pmid": a.pmid or "", "doi": a.doi or "", "title": a.title or "",
            "year": a.year, "journal": a.journal or ""}


def _biorxiv_version_family(claimed: ClaimedRef, resolved_b: RetrievedRecord,
                            session=None) -> Optional[str]:
    """If the CITED DOI is a bioRxiv/medRxiv preprint whose authoritative
    published version is B's DOI, A and B are one work under a version relation
    (spec §14.3 intra-work: preprint). Returns the published DOI when it matches
    B, else None. Handles PMC8887078:R1 -- cited bioRxiv 10.1101/2020.02.07.937862,
    published 10.1038/s41564-020-0695-z == B, the SARS-CoV-2 naming paper."""
    pub = biorxiv_published_doi(claimed.claimed_doi, session=session)
    if pub and resolved_b.doi and doi_equivalent(pub, resolved_b.doi):
        return pub
    return None


def assess_a_vs_b(claimed: ClaimedRef, resolved_b: RetrievedRecord, *,
                  api_key: str = "", email: str = "", session=None,
                  steps: Iterable[str] = _CASCADE_DEFAULT) -> AResolution:
    """Resolve A and route the pair three ways. Emits evidence + a proposed
    repair; never a label. ``OUTCOME_UNDETERMINED`` is returned only when the
    cascade could not run to a conclusion (kept distinct from ``unscoreable`` so a
    network failure is never mistaken for 'A is not a work')."""
    # Preprint->published relation first: a cited bioRxiv/medRxiv preprint whose
    # published version IS B is the same work (declared version family), even
    # though the preprint and published DOIs differ -- so it must NOT propose a
    # repair against its own published form.
    pub = _biorxiv_version_family(claimed, resolved_b, session=session)
    if pub is not None:
        return AResolution(
            outcome=OUTCOME_NOT_F2, a_source="biorxiv_relation",
            a_vs_b_doi_match=False,
            evidence={"relation": "preprint_of", "preprint_doi": claimed.claimed_doi,
                      "published_doi": pub, "b_doi": resolved_b.doi})
    errors: list = []
    source, a = resolve_a(claimed, resolved_b, api_key=api_key, email=email,
                          session=session, steps=steps, errors=errors)
    if a is None:
        # §14.6: a THROWN request (retrieval_incomplete) must not be conflated with
        # a clean miss. A clean miss -> unscoreable; a source error -> undetermined,
        # so a flaky run never silently shrinks the scoreable population.
        if errors:
            return AResolution(
                outcome=OUTCOME_UNDETERMINED, a_source=source,
                evidence={"reason": "a required source errored (retrieval "
                          "incomplete); A undetermined", "provider_errors": errors})
        return AResolution(outcome=OUTCOME_UNSCOREABLE, a_source=source,
                           evidence={"reason": "A did not resolve in any source"})
    same, ts, doi_m = _a_equals_b(a, resolved_b)
    ar = AResolution(
        outcome=OUTCOME_NOT_F2 if same else OUTCOME_F2_WITH_REPAIR,
        a_source=source, a_record=asdict(a),
        a_vs_b_title_sim=round(ts, 4), a_vs_b_doi_match=doi_m,
        evidence={"a_title": a.title, "b_title": resolved_b.title,
                  "a_doi": a.doi, "b_doi": resolved_b.doi,
                  "a_is_preprint": bool(a.publication_types
                                        and "preprint" in
                                        {p.lower() for p in a.publication_types})})
    if not same:
        ar.proposed_repair = _repair_from(a)
    return ar


# ---------------------------------------------------------------------------
# Pre-flight + checkpointed batch
# ---------------------------------------------------------------------------
def preflight(session=None, *, api_key: str = "", email: str = "") -> dict:
    """A single, known round-trip that MUST pass before any batch. Resolves a
    reference whose cited DOI differs from a (wrong) resolved DOI and asserts the
    cascade reaches ``f2_with_repair`` with the cited DOI as the repair target.
    Returns a small report dict; raises RuntimeError if the round-trip is broken.

    Uses the DOI step only (deterministic, one request), so a green pre-flight
    proves connectivity + parsing + routing without depending on search ranking.
    Pass a live ``session`` in Colab; the unit tests pass a fake one."""
    claimed = ClaimedRef(
        title="A convolutional network approach to variant detection",
        authors=["Aguilar"], year=2020, journal="Bioinformatics",
        claimed_doi="10.1093/bioinformatics/btz100")
    resolved_b = RetrievedRecord(
        resolved=True, title="An unrelated resolved paper", authors=["Other"],
        year=2019, doi="10.9999/wrong.doi", pmid="99999999")
    ar = assess_a_vs_b(claimed, resolved_b, session=session, api_key=api_key,
                       email=email, steps=("cited_doi",))
    if ar.outcome != OUTCOME_F2_WITH_REPAIR or not ar.proposed_repair:
        raise RuntimeError(
            f"F2-F pre-flight FAILED: expected {OUTCOME_F2_WITH_REPAIR} with a "
            f"proposed repair, got {ar.outcome!r} (a_source={ar.a_source!r}). "
            f"Do not start a batch.")
    return {"ok": True, "outcome": ar.outcome, "a_source": ar.a_source,
            "proposed_repair": ar.proposed_repair}


def resolve_a_batch(rows: Iterable[dict], out_path: str, *,
                    claimed_of: Callable[[dict], ClaimedRef],
                    resolved_of: Callable[[dict], RetrievedRecord],
                    id_of: Callable[[dict], str],
                    api_key: str = "", email: str = "", session=None,
                    steps: Iterable[str] = _CASCADE_DEFAULT) -> dict:
    """Resolve A for every row, checkpointing each result to ``out_path`` (JSONL)
    so a rate-limited run is resumable: an id already present in ``out_path`` is
    skipped. Requires ``preflight`` to have passed (call it first). Returns a
    summary counter; writes evidence + proposed repairs, never labels."""
    done: set[str] = set()
    if os.path.exists(out_path):
        with open(out_path) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        done.add(json.loads(line).get("id", ""))
                    except ValueError:
                        continue
    counts = {OUTCOME_NOT_F2: 0, OUTCOME_F2_WITH_REPAIR: 0,
              OUTCOME_UNSCOREABLE: 0, OUTCOME_UNDETERMINED: 0, "skipped": 0}
    with open(out_path, "a") as f:
        for row in rows:
            rid = id_of(row)
            if rid in done:
                counts["skipped"] += 1
                continue
            ar = assess_a_vs_b(claimed_of(row), resolved_of(row), api_key=api_key,
                               email=email, session=session, steps=steps)
            counts[ar.outcome] = counts.get(ar.outcome, 0) + 1
            f.write(json.dumps({"id": rid, **asdict(ar)}, ensure_ascii=False) + "\n")
            f.flush()
    return counts
