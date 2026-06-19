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
    if label in (UNVERIFIABLE, HUMAN_REVIEW):
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
    # The PMC parser does not populate these yet; default "" => the matcher
    # reports field-agreement None ("can't judge") rather than a false mismatch.
    volume: str = ""
    pages: str = ""


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


@dataclass
class StageLog:
    pmid_present: bool = False
    pmid_resolved: bool = False
    title_similarity: Optional[float] = None    # 0..100 (token-sort, legacy scale)
    match_score: Optional[float] = None         # 0..1 composite (biblio_match.py)
    author_match: Optional[bool] = None
    year_match: Optional[bool] = None
    author_tripwire: Optional[bool] = None   # True = first-author trip-wire fired
    mismatch_flagged: bool = False
    llm_verdict: Optional[str] = None
    db_hits: dict = field(default_factory=dict)
    decided_by: str = ""
    notes: str = ""
    # No-ID branch (references with no claimed PMID).
    noid_lookup_attempted: bool = False      # ran the structured biblio lookup
    noid_not_found: bool = False             # biblio lookup found no confident match


@dataclass
class Reference:
    citation_id: str
    citance: str
    claimed: ClaimedRef
    cited_reference_marker: str = ""
    source_pmcid: str = ""
    source_pmid: str = ""
    source_title: str = ""

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
                "author_tripwire": self.log.author_tripwire,
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
