"""Data structures for CRE — F1 stage pipeline + CitationRepair-1000 records.

Two label spaces, kept separate on purpose:

  * Pipeline states (processing outcomes of the F1 detector):
        cleared | unverifiable | human_review  + the taxonomy codes it can emit
  * Taxonomy labels (the dataset/eval vocabulary): F1..F8 and ACCURATE.

The dataset speaks taxonomy codes ONLY. Pipeline states like `cleared` never
appear in a dataset `label` field — they map to ACCURATE / dropped / review.

Record shapes (one citation per record):
  GoldRecord        — human-annotated ground truth
  PredictionRecord  — system output (carries its evidence trail)
  EvalRecord        — gold vs prediction, scored (label + repair)

Versioning lives in the dataset manifest / filename, NOT in every record.
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Optional
import json

# ---- Taxonomy labels (dataset + eval vocabulary) ----
ACCURATE = "accurate"
F1, F2, F3, F4, F5, F6, F7, F8 = "F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8"
TAXONOMY_LABELS = {ACCURATE, F1, F2, F3, F4, F5, F6, F7, F8}

# ---- Pipeline processing states (F1 detector internals) ----
CLEARED = "cleared"
UNVERIFIABLE = "unverifiable"
HUMAN_REVIEW = "human_review"
# A (claimed, resolved) pair that cannot bear a title-vs-title comparison at all:
# a non-title input (journal name / regulatory code / committee string), a
# placeholder ("[Not Available]"), or a book/container record cited as a chapter.
# Reported as a named, counted bucket (never silently dropped) and EXCLUDED from
# both the flagged pool and the F2 numerator. Maps to None (dropped from the
# dataset) exactly like UNVERIFIABLE — it is a coverage bucket, never ACCURATE.
UNSCOREABLE = "unscoreable"

# ---- Claimed-PMID retrieval transport status ----
# THE DISTINCTION THAT TURNS AN OUTAGE INTO AN ACCUSATION.
#
# `resolved=False` used to mean two different things: "EFetch answered and this
# PMID has no record" (real fabrication evidence) and "EFetch never answered"
# (no evidence at all). A partial NCBI outage therefore labelled real, indexed
# papers F1 — at HIGH confidence, because decide() reads an unresolved PMID as
# the STRONGER signal. A dead PMID and a failed fetch were byte-identical in the
# durable record.
#
# This is the same distinction `fulltext_reader.py` draws between `no_pmcid`
# (the resolver ANSWERED and there is nothing) and `resolver_error` (the
# resolver did not answer), and the vocabulary is deliberately shared — see the
# docstring there, which records that conflating them corrupted a number once
# already. Measured against live NCBI 2026-08-16: a nonexistent PMID returns
# HTTP 200 with an EMPTY BODY, which is what makes ANSWERED_ABSENT detectable at
# all; a malformed one returns 400, which is a RESOLVER_ERROR (the server
# rejected the request, it did not report an absence).
#
# Only ANSWERED_ABSENT is evidence. RESOLVER_ERROR holds the reference.
FETCH_NOT_ATTEMPTED = "not_attempted"     # no claimed PMID; no request was made
FETCH_ANSWERED_RECORD = "answered_record"  # answered, record parsed -> resolved
FETCH_ANSWERED_ABSENT = "answered_absent"  # answered, no such record -> evidence
FETCH_RESOLVER_ERROR = "resolver_error"   # did NOT answer -> never evidence

#: Statuses that carry no information about whether the claimed work exists.
FETCH_NO_EVIDENCE = frozenset({FETCH_RESOLVER_ERROR})


def fetch_answered(status: str) -> bool:
    """True when the claimed-PMID fetch actually produced an answer.

    An empty status is the pre-transport-status default carried by old cached
    records. It is read as "answered" so replaying a historical log does not
    silently reclassify every one of its rows as an outage.
    """
    return status not in FETCH_NO_EVIDENCE


# ---- LLM filter verdicts ----
V_FABRICATION = "fabrication"
V_FORMATTING = "formatting_discrepancy"
V_REFERENCE_ERROR = "reference_error"
V_UNCERTAIN = "uncertain"


# =====================================================================
# Atomic claims and the F6 invariant
# =====================================================================
@dataclass
class AtomicClaim:
    text: str
    supported: bool
    evidence_text: str = ""
    evidence_location: str = ""


def check_f6_invariant(label: str, claims: "list[AtomicClaim]") -> Optional[str]:
    """Enforce the binding between atomic-claim booleans and the citation label.

    Returns None if consistent, else an error message.

    Rule (claim-decidable categories only):
      * all claims supported            -> label must NOT be F6
      * at least one claim unsupported  -> label must NOT be ACCURATE
    F4/F5/F7 are NOT derivable from claim-support alone, so the invariant does
    not constrain them; F1/F2/F8 are existence/metadata level and carry no
    atomic claims to bind.
    """
    if not claims:
        return None
    all_supported = all(c.supported for c in claims)
    any_unsupported = any(not c.supported for c in claims)
    if all_supported and label == F6:
        return "F6 (partial support) but every atomic claim is supported."
    if any_unsupported and label == ACCURATE:
        return "label ACCURATE but at least one atomic claim is unsupported."
    return None


# =====================================================================
# Shared paper metadata
# =====================================================================
@dataclass
class CitedPaper:
    pmid: str = ""
    doi: str = ""
    title: str = ""
    authors: list[str] = field(default_factory=list)
    year: Optional[int] = None


@dataclass
class SourcePaper:
    pmid: str = ""
    doi: str = ""
    title: str = ""
    year: Optional[int] = None


@dataclass
class Repair:
    action: Optional[str] = None
    recommended_references: list[dict] = field(default_factory=list)
    repair_rationale: Optional[str] = None


@dataclass
class Annotation:
    annotator_id: str
    label: str
    secondary_label: Optional[str] = None
    confidence: float = 1.0


# =====================================================================
# Three record types
# =====================================================================
@dataclass
class GoldRecord:
    citation_id: str
    citance: str
    cited_reference_marker: str
    cited_paper: CitedPaper
    source_paper: SourcePaper
    label: str
    secondary_label: Optional[str] = None
    atomic_claims: list[AtomicClaim] = field(default_factory=list)
    repair: Repair = field(default_factory=Repair)
    rationale: str = ""
    annotations: list[Annotation] = field(default_factory=list)
    source: str = "expert_annotation"
    label_metadata: dict = field(default_factory=dict)

    def validate(self) -> None:
        if self.label not in TAXONOMY_LABELS:
            raise ValueError(f"{self.citation_id}: label {self.label!r} "
                             f"not in taxonomy {sorted(TAXONOMY_LABELS)}")
        err = check_f6_invariant(self.label, self.atomic_claims)
        if err:
            raise ValueError(f"{self.citation_id}: {err}")

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PredictionRecord:
    citation_id: str
    label: str
    secondary_label: Optional[str] = None
    rationale: str = ""
    repair: Repair = field(default_factory=Repair)
    annotations: list[Annotation] = field(default_factory=list)
    evidence: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class EvalRecord:
    citation_id: str
    gold: dict
    prediction: dict
    evaluation: dict

    @staticmethod
    def score(citation_id: str, gold, pred) -> "EvalRecord":
        g = gold.to_dict() if isinstance(gold, GoldRecord) else gold
        p = pred.to_dict() if isinstance(pred, PredictionRecord) else pred
        g_label, g_sec = g["label"], g.get("secondary_label")
        p_label, p_sec = p["label"], p.get("secondary_label")
        label_correct = g_label == p_label
        sec_correct = g_sec == p_sec

        g_rep = g.get("repair") or {}
        p_rep = p.get("repair") or {}
        if not g_rep.get("action"):
            repair_correct = None
        else:
            action_ok = g_rep.get("action") == p_rep.get("action")
            repair_correct = action_ok and _refs_match(
                g_rep.get("recommended_references", []),
                p_rep.get("recommended_references", []))
        return EvalRecord(
            citation_id=citation_id,
            gold={"label": g_label, "secondary_label": g_sec},
            prediction={"label": p_label, "secondary_label": p_sec,
                        "confidence": p.get("confidence",
                                            _conf_from_annotations(p))},
            evaluation={
                "label_correct": label_correct,
                "secondary_label_correct": sec_correct,
                "exact_match": label_correct and sec_correct,
                "repair_correct": repair_correct,
            })

    def to_dict(self) -> dict:
        return {"citation_id": self.citation_id, "gold": self.gold,
                "prediction": self.prediction, "evaluation": self.evaluation}


def _refs_match(gold_refs: list, pred_refs: list) -> bool:
    def ids(refs):
        out = set()
        for r in refs:
            if r.get("pmid"):
                out.add(("pmid", str(r["pmid"])))
            if r.get("doi"):
                out.add(("doi", str(r["doi"]).lower()))
        return out
    g, p = ids(gold_refs), ids(pred_refs)
    return bool(g & p) if g else (not p)


def _conf_from_annotations(p: dict) -> Optional[float]:
    anns = p.get("annotations") or []
    return anns[0].get("confidence") if anns else None


# =====================================================================
# Pipeline state -> taxonomy label mapping
# =====================================================================
def pipeline_state_to_taxonomy(label: str) -> Optional[str]:
    """Map an F1-detector outcome to a dataset taxonomy label.
    None -> drop from dataset (unverifiable) or hold out of gold (human_review)."""
    if label in (F1, F2, F8):
        return label
    if label == CLEARED:
        return ACCURATE
    if label in (UNVERIFIABLE, HUMAN_REVIEW, UNSCOREABLE):
        return None
    return label if label in TAXONOMY_LABELS else None


# =====================================================================
# F1 detector working object — emits PredictionRecord
# =====================================================================
@dataclass
class ClaimedRef:
    title: str = ""
    authors: list[str] = field(default_factory=list)
    year: Optional[int] = None
    journal: str = ""
    claimed_pmid: str = ""
    claimed_doi: str = ""
    raw: str = ""
    # Structured fields used by the bibliographic matcher (biblio_match.py).
    # Empty remains the safe "can't judge" value for malformed citations.
    volume: str = ""
    pages: str = ""
    # F2-E: leading bibliographic furniture (author run / chapter label / article-
    # type label) excised from ``title`` at parse time, kept verbatim so every edit
    # to the scored title is reviewable. Empty when no furniture was removed.
    written_title_excised: str = ""
    # True only when the first written author came from a JATS <collab> element.
    # This is provenance for corporate-author comparison; ``authors`` remains the
    # verbatim evidence and is never rewritten by a matcher rule.
    first_author_is_collab: bool = False


@dataclass
class RetrievedRecord:
    resolved: bool = False
    title: str = ""
    authors: list[str] = field(default_factory=list)
    year: Optional[int] = None
    journal: str = ""
    pmid: str = ""
    # Carried by candidates from the bibliographic matcher (biblio_match.py).
    # doi enables DOI-based candidate dedup; volume/pages feed field agreement.
    doi: str = ""
    volume: str = ""
    pages: str = ""
    # True when ``title`` is a book/container title (MEDLINE BTI, OpenAlex
    # type=book) rather than an article/chapter title. The UNSCOREABLE gate uses
    # this so a chapter cite resolving to its parent book is not title-matched.
    is_container: bool = False
    # True when ``year`` came from the electronic-publication date (MEDLINE DEP)
    # because no print date (DP) was present -- an epub-ahead-of-print / preprint
    # signal. The field matcher widens its year tolerance for such a record so a
    # preprint->publication year gap on the SAME work is not read as a mismatch.
    year_from_dep: bool = False
    # MEDLINE evidence retained for identity-aware same-work decisions.  These
    # fields are additive and default empty so old caches remain readable.
    alternate_titles: list[str] = field(default_factory=list)
    language: str = ""
    publication_types: list[str] = field(default_factory=list)
    related_pmids: dict[str, list[str]] = field(default_factory=dict)
    # Why ``resolved`` is what it is: one of the FETCH_* statuses above. Empty
    # is the old-cache default and is read as "answered" (see fetch_answered).
    # ``resolved=False`` alone is NOT evidence of anything until this is read.
    transport_status: str = ""
    # MEDLINE CollectiveName (CN) provenance.  Old caches omit the field and read
    # safely as False; newly written caches preserve the authoritative signal.
    has_collective_author: bool = False


@dataclass
class StageLog:
    pmid_present: bool = False
    pmid_resolved: bool = False
    # One of the FETCH_* statuses. THE FIELD THAT KEEPS AN OUTAGE OUT OF THE
    # RECORD AS AN ACCUSATION: without it, ``pmid_resolved=False`` in a durable
    # log is unreadable — a dead PMID and a 429 that survived every retry look
    # identical forever after. Empty is the old-cache default.
    pmid_transport_status: str = ""
    title_similarity: Optional[float] = None    # 0..100 (token-sort, legacy scale)
    match_score: Optional[float] = None         # 0..1 composite (biblio_match.py)
    # Full field-agreement verdict tuple. Logged so the eval layer can BAND the
    # flagged pool on the raw (True/False/None) verdicts directly — never on the
    # non-invertible Delta=score-ts (a corroborating boost can mask a year/author
    # disagreement, so Delta>=0 does NOT imply "no field disagreed").
    author_match: Optional[bool] = None
    first_author_match: Optional[bool] = None
    year_match: Optional[bool] = None
    journal_match: Optional[bool] = None
    volume_match: Optional[bool] = None
    pages_match: Optional[bool] = None
    doi_match: Optional[bool] = None
    author_tripwire: Optional[bool] = None   # True = first-author trip-wire fired
    # True when the strong-corroboration override floored this score to accept
    # (author+journal agree on a low-title-similarity pair). Logged so the eval
    # layer can COUNT the override-cleared population -- the known same-author/
    # same-journal residual whose size must be measured, not assumed.
    override_fired: bool = False
    mismatch_flagged: bool = False
    # A proof-backed explanation that the identifier resolves to a version,
    # translation, correction, or malformed rendering of the same work.  Such
    # rows are quarantined for human review and never auto-labelled F1/F2.
    same_work_reason: str = ""
    identity_signals: list[str] = field(default_factory=list)
    # Set when the (claimed, resolved) pair is not a scoreable title comparison
    # (see UNSCOREABLE). Names the reason; routes the ref out of the F2 numerator.
    unscoreable_reason: Optional[str] = None
    llm_verdict: Optional[str] = None
    db_hits: dict = field(default_factory=dict)
    decided_by: str = ""
    notes: str = ""
    # No-ID branch (references with no claimed PMID).
    noid_lookup_attempted: bool = False      # ran the structured biblio lookup
    noid_not_found: bool = False             # biblio lookup found no confident match
    # F8 -- retracted source (RESEARCH_PLAN_v2.2 §4.3). TRI-STATE, never a bare
    # bool:
    #   True  -- the resolved record's PubMed publication types include
    #            "Retracted Publication"
    #   False -- the types were fetched and that type is absent
    #   None  -- UNKNOWN: no resolved PMID to look up, or the lookup failed
    # Test with ``is True`` / ``is False``, the same discipline as author_match. A
    # falsy check would read an EFetch outage as "not retracted" and let a
    # retracted source clear -- the same defect class as ``resolver_error`` in the
    # F3-F7 full-text path: an absence of signal must stay distinguishable from a
    # signal of absence. An unknown state is never an F8 (precision-first).
    retracted: Optional[bool] = None
    # The §5.6 reason code carried by an F8 row; "" on every other row. Set by
    # decide() at the point the row takes the F8 route, so it names the ROUTE
    # taken rather than merely restating ``retracted`` (a retracted row that the
    # UNSCOREABLE branch claims first keeps retracted=True and this field "").
    retraction_reason: str = ""


@dataclass
class Reference:
    citation_id: str
    citance: str
    claimed: ClaimedRef
    cited_reference_marker: str = ""
    source_pmcid: str = ""
    source_pmid: str = ""
    source_title: str = ""
    # CO-CITATION GROUP (the sentence occurrence this reference was cited in).
    #
    # ``citance`` alone is what the band judged against, and it is attached to
    # every reference the sentence cites INDEPENDENTLY. That is the whole F6
    # co-citation defect: eight references sharing one sentence were each asked
    # "does this paper support the whole claim?", and each supports part, which is
    # the definition of F6. The group is the missing context, and it exists at
    # parse time -- ``link_citances`` resolves a sentence's markers and used to
    # throw the membership away.
    #
    # ``citance_group_id`` is "<citing_pmcid>:g<NN>", NN a zero-padded
    # document-order index over sentence occurrences that cite at least one
    # resolvable reference. Empty when the reference has no citance, or when the
    # Reference was built outside the parser -- an empty id is read as "no group
    # known", which every consumer treats as a singleton, i.e. exactly the
    # pre-group behaviour.
    #
    # ``citance_group_members`` holds the citation_ids of every reference whose
    # citance was assigned FROM THIS SAME sentence occurrence, in document order,
    # deduplicated, and INCLUDING this reference. It is deliberately not "every
    # reference the sentence mentions": ``link_citances`` is first-citance-wins, so
    # a reference already carrying an earlier sentence is judged against THAT
    # sentence and its verdicts concern THAT sentence's claims. Including it here
    # would aggregate verdicts over two different claim lists.
    citance_group_id: str = ""
    citance_group_members: list[str] = field(default_factory=list)
    # RANGE EXPANSION provenance. A citing sentence that renders "9-13" is
    # usually marked up as two xrefs -- one on 9, one on 13 -- with a literal
    # dash between them; references 10, 11 and 12 are cited on the page and
    # carry no xref at all. Measured over corpus_frozen_v1: 63 rendered ranges,
    # ALL of them with unlinked interiors, affecting 115 references.
    #
    # ``citance_group_inferred_members`` is the subset of
    # ``citance_group_members`` recovered that way, and
    # ``citance_marker_inferred`` says THIS reference is one of them. Kept as a
    # separate list rather than folded in, because an inferred member is a
    # deduction from contiguous numbering and an asserted one is a link the
    # publisher wrote: a reader must be able to tell them apart, and both counts
    # must be reportable.
    citance_group_inferred_members: list[str] = field(default_factory=list)
    citance_marker_inferred: bool = False
    # MARKER CLUSTERS -- which claims this reference was actually cited FOR.
    #
    # The co-citation group above is the whole SENTENCE, and a sentence can cite
    # two different things: "...antibodies 52,53 and pH sensitive fluorescent
    # micelles 54,55..." cites 52,53 for the antibodies and 54,55 for the
    # micelles. All four were asked all four claims, and B55 was flagged F6 on
    # "fluorophore-labelled antibodies were successfully clinically translated".
    # The verdict was right; the question was wrong.
    #
    # ``citance_marker_clusters`` is the sentence's clusters -- maximal runs of
    # adjacent markers -- each with its index, its offset and end IN THE CITANCE
    # STRING, the marker text it renders, its id ("<group_id>:c<NN>") and the
    # citation_ids sitting in it. ``citance_marker_cluster_index`` /
    # ``citance_marker_cluster_id`` name THIS reference's cluster.
    #
    # EMPTY IS THE COMMON CASE AND MEANS "unchanged": a sentence with one cluster
    # records none at all, and so does an author-year document, for which the
    # positional rule is undefined. ``citance_citation_style`` says which rule
    # applied, so a whole-sentence row is never silently indistinguishable from a
    # row nothing was tried on.
    citance_citation_style: str = ""
    citance_marker_clusters: list[dict] = field(default_factory=list)
    citance_marker_cluster_index: int = -1
    citance_marker_cluster_id: str = ""

    retrieved: RetrievedRecord = field(default_factory=RetrievedRecord)
    log: StageLog = field(default_factory=StageLog)

    label: Optional[str] = None
    confidence: str = ""
    rationale: str = ""

    def to_prediction(self, annotator_id: str = "citation_repair_llm_v1",
                      conf: Optional[float] = None) -> PredictionRecord:
        tax = pipeline_state_to_taxonomy(self.label or "")
        out_label = tax if tax is not None else (self.label or "")
        c = conf if conf is not None else \
            {"HIGH": 0.95, "MED": 0.7, "LOW": 0.4}.get(self.confidence, 0.5)
        return PredictionRecord(
            citation_id=self.citation_id,
            label=out_label,
            secondary_label=None,
            rationale=self.rationale,
            repair=Repair(),
            annotations=[Annotation(annotator_id=annotator_id,
                                    label=out_label, confidence=c)],
            evidence={
                "title_similarity": self.log.title_similarity,
                "match_score": self.log.match_score,
                "pmid_resolved": self.log.pmid_resolved,
                # Ships WITH pmid_resolved, always. A consumer that reads the
                # boolean without the status can reconstruct the original defect.
                "pmid_transport_status": self.log.pmid_transport_status,
                "author_match": self.log.author_match,
                "first_author_match": self.log.first_author_match,
                "year_match": self.log.year_match,
                "journal_match": self.log.journal_match,
                "volume_match": self.log.volume_match,
                "pages_match": self.log.pages_match,
                "doi_match": self.log.doi_match,
                "same_work_reason": self.log.same_work_reason,
                "identity_signals": self.log.identity_signals,
                "author_tripwire": self.log.author_tripwire,
                "unscoreable_reason": self.log.unscoreable_reason,
                "retracted": self.log.retracted,
                "retraction_reason": self.log.retraction_reason,
                "llm_verdict": self.log.llm_verdict,
                "db_hits": self.log.db_hits,
                "decided_by": self.log.decided_by,
                "pipeline_state": self.label,
            },
        )

    def to_log_record(self) -> dict:
        return {
            "citation_id": self.citation_id,
            "label": self.label,
            "confidence": self.confidence,
            "claimed": asdict(self.claimed),
            "retrieved": asdict(self.retrieved),
            "log": asdict(self.log),
            "rationale": self.rationale,
        }


def write_jsonl(records: list[dict], path: str) -> None:
    with open(path, "w") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
