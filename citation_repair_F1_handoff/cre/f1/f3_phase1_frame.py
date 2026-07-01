"""F3 Phase-1 sampling frame — two-phase (screening) design for the F3 gold set.

WHY THIS EXISTS
---------------
The f3_candidate_collect hunter is CALIBRATION-ONLY (F3-DI1): it uses a
high-precision / low-recall attribution lexicon to *find* worked codebook
examples, and its output is biased toward egregious-and-findable cases. Per
F3-DI2 it must NEVER seed the gold / IAA set. Measured hunt yield is ~1 strong
F3 per ~500 docs, so hunting to a gold n of ~125 is both infeasible (~62.5k
docs) and forbidden.

This module builds the gold set the correct way: a two-phase / screening-
sampling design (standard for rare-category gold sets — epidemiology two-phase
studies, IR pooled relevance judging).

  Phase 1 (this module): a cheap, high-recall, MECHANICAL screen applied to a
    RANDOM OA frame (NOT the review-dense hunting pool). Two detector-
    independent questions only:
      S1 (metadata)   : does the cited PMID resolve to a review-family pubtype?
      S2 (structural) : does the citing sentence carry a specific factual claim?
    A citation passing S1 AND S2 is in the ELIGIBLE STRATUM.

  Phase 2 (human, elsewhere): V1-V4 verification on the eligible stratum, plus a
    random audit of the INELIGIBLE stratum to measure the miss rate. Reweighting
    by the Phase-1 inclusion probabilities then gives an unbiased prevalence
    estimate and a SAMPLED (not hunted) verified positive set with honest CIs.

DETECTOR-INDEPENDENCE (F3-E2/E3, standing guardrail #7)
-------------------------------------------------------
Phase 1 must stay mechanical. "Cited work is a review" (a metadata lookup) and
"sentence makes a specific factual claim" (a structural test, optionally an LLM
claim-SPECIFICITY classifier) are detector-independent. "Is this F3?" is NOT and
must never enter the screen — otherwise the gold set is contaminated by the
detector it is later used to evaluate. This module assigns NO F3 label; every
eligible citation carries an unfilled V1-V4 worksheet for a human.

The LLM refinement (S2 claim-specificity) is injected as a pluggable callable so
this module is fully offline-testable; the classifier judges SPECIFICITY, not
provenance. See ``specificity_classifier`` on :func:`screen_dir`.

Reuses the detector's own parser (``parse_pmc_xml``) so the sampling frame is
identical to what the F1 eval will later see, and ``ncbi_pubtypes`` / ``is_review``
for S1.

Usage
-----
    # mechanical S2 only (no LLM):
    python -m cre.f1.f3_phase1_frame --xml-dir <random_oa_pool> \\
        --out-dir <data> --email you@example.org

    # with LLM S2 refinement, the harness/notebook injects a classifier:
    from cre.f1.f3_phase1_frame import screen_dir
    screen_dir(xml_dir, out_dir, api_key=..., email=...,
               specificity_classifier=my_host_llm_classifier)
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import time
from collections import Counter
from typing import Callable, Optional

from .parser import parse_pmc_xml
from .f3_candidate_collect import (
    ncbi_pubtypes,
    is_review,
    _pmcid_from_filename,
    _append_jsonl,
    DEFAULT_EMAIL,
)

# --- S2 mechanical proxy knobs --------------------------------------------
MIN_CONTENT_TOKENS = 6

# Sentences that structurally are NOT a specific factual claim about a finding.
_METHODS_HEDGE = re.compile(
    r"\b(we (used|performed|recruited|measured|calculated|analy[sz]ed)|"
    r"as (described|shown|reported) (in|by|previously)|"
    r"see (also|ref|figure|table)|"
    r"data (not shown|available)|"
    r"according to the manufacturer)\b",
    re.I,
)

# S2 category vocabulary used by an LLM classifier (SPECIFICITY, not F3).
S2_CATEGORIES = (
    "specific_factual_claim",
    "background_general",
    "methods_procedural",
    "editorial_navigational",
    "hedge_uncertain",
)

# Classifier contract: given a list of citing sentences, return a list (aligned)
# of dicts {"category": <one of S2_CATEGORIES>, "carries_specific_claim": bool}.
# One dict per input; a failed item may be None (treated as unrefined -> falls
# back to the mechanical S2 verdict for that row).
SpecificityClassifier = Callable[[list], list]


def s2_mechanical(sentence: str) -> bool:
    """Cheap, detector-independent structural proxy for 'carries a specific
    factual claim'. Permissive by design; refine with an LLM classifier for a
    tighter stratum (see :func:`screen_dir`)."""
    if not sentence:
        return False
    words = re.findall(r"[A-Za-z][A-Za-z\-]+", sentence)
    if len(words) < MIN_CONTENT_TOKENS:
        return False
    if _METHODS_HEDGE.search(sentence):
        return False
    return True


def _new_worksheet() -> dict:
    """Human Phase-2 worksheet, all null. Identical field set to the hunter's
    worksheet so downstream tooling is shared."""
    return {
        "F3_V1_coverage": None,      # does the review state AND support the claim?
        "F3_V2_origin": None,        # own primary result (ACCURATE) or restatement (F3)?
        "F3_V3_repair_target_pmid": None,  # rightful primary from review reflist
        "F3_V4_loop_closed": None,   # does that primary contain the finding?
        "confirmed_F3": None,
        "annotator": None,
    }


def screen_dir(
    xml_dir: str,
    out_dir: str,
    *,
    api_key: str = "",
    email: str = DEFAULT_EMAIL,
    max_docs: Optional[int] = None,
    specificity_classifier: Optional[SpecificityClassifier] = None,
    session=None,
) -> dict:
    """Run the Phase-1 screen over a directory of PMC OA JATS XML.

    Emits three files in ``out_dir``:
      * ``f3_phase1_frame.jsonl``    — every parsed citation + S1/S2 flags.
      * ``f3_phase1_eligible.jsonl`` — the eligible stratum + null V1-V4
        worksheet (the human Phase-2 queue).
      * ``f3_phase1_manifest.json``  — funnel counts, params, and the sizing
        inputs (citations/doc, eligible/citation, full-text yield).

    ``specificity_classifier`` (optional): a callable implementing
    :data:`SpecificityClassifier`. When supplied, S2 is decided by the
    classifier's ``carries_specific_claim`` (an LLM claim-SPECIFICITY judgment,
    detector-independent); when absent, S2 uses :func:`s2_mechanical`.
    """
    os.makedirs(out_dir, exist_ok=True)
    if session is None:
        import requests
        session = requests.Session()

    counts = dict(
        docs_processed=0,
        docs_with_body=0,
        citations_total=0,
        with_cited_pmid=0,
        s1_cited_is_review=0,
        s2_claim_bearing=0,
        pubtype_unresolved=0,
        eligible_stratum=0,
    )
    pt_cache: dict = {}

    # --- pass 1: parse + S1, collect rows ---
    rows: list = []
    files = sorted(glob.glob(os.path.join(xml_dir, "*.xml")))
    for fn in files:
        if max_docs is not None and counts["docs_processed"] >= max_docs:
            break
        pmcid = _pmcid_from_filename(os.path.basename(fn))
        try:
            refs = parse_pmc_xml(fn, source_pmcid=pmcid)
        except Exception as e:  # noqa: BLE001 - best effort
            rows.append({"source_pmcid": pmcid, "parse_error": str(e)})
            continue
        counts["docs_processed"] += 1
        doc_had_citance = False
        for ref in refs:
            if not ref.citance:
                continue
            doc_had_citance = True
            counts["citations_total"] += 1
            pmid = ref.claimed.claimed_pmid
            rec = {
                "citation_id": ref.citation_id,
                "source_pmcid": pmcid,
                "cited_marker": ref.cited_reference_marker,
                "citing_sentence": ref.citance,
                "cited_claimed_pmid": pmid,
                "cited_claimed_title": ref.claimed.title,
                "S1_cited_is_review": None,
                "cited_pubtypes": None,
                "S2_claim_bearing_mechanical": s2_mechanical(ref.citance),
                "S2_category": None,        # filled if classifier supplied
                "S2_carries_claim": None,   # final S2 verdict
                "eligible": False,
            }
            if rec["S2_claim_bearing_mechanical"]:
                counts["s2_claim_bearing"] += 1
            if pmid:
                counts["with_cited_pmid"] += 1
                if pmid not in pt_cache:
                    pt_cache[pmid] = ncbi_pubtypes(pmid, api_key, email, session)
                pts = pt_cache[pmid]
                rec["cited_pubtypes"] = pts
                rev = is_review(pts)
                rec["S1_cited_is_review"] = rev
                if rev is None:
                    counts["pubtype_unresolved"] += 1
                elif rev:
                    counts["s1_cited_is_review"] += 1
            rows.append(rec)
        if doc_had_citance:
            counts["docs_with_body"] += 1

    # --- pass 2: optional LLM S2 refinement over rows that clear S1 ---
    # Only classify rows where S1 is True (the only rows that can be eligible),
    # to keep the classifier budget small.
    s1_rows = [r for r in rows if r.get("S1_cited_is_review")]
    if specificity_classifier is not None and s1_rows:
        verdicts = specificity_classifier([r["citing_sentence"] for r in s1_rows])
        for r, v in zip(s1_rows, verdicts):
            if not v:
                # classifier failed on this item -> fall back to mechanical S2
                r["S2_carries_claim"] = r["S2_claim_bearing_mechanical"]
                continue
            r["S2_category"] = v.get("category")
            r["S2_carries_claim"] = bool(v.get("carries_specific_claim"))
    else:
        for r in rows:
            if "parse_error" in r:
                continue
            r["S2_carries_claim"] = r["S2_claim_bearing_mechanical"]

    # --- eligibility gate + emit ---
    frame_fh = open(os.path.join(out_dir, "f3_phase1_frame.jsonl"), "w")
    elig_fh = open(os.path.join(out_dir, "f3_phase1_eligible.jsonl"), "w")
    for r in rows:
        if "parse_error" not in r:
            if r.get("S1_cited_is_review") and r.get("S2_carries_claim"):
                r["eligible"] = True
                counts["eligible_stratum"] += 1
                q = dict(r)
                q["phase2_worksheet"] = _new_worksheet()
                _append_jsonl(elig_fh, q)
        _append_jsonl(frame_fh, r)
    frame_fh.close()
    elig_fh.close()

    parseable = counts["docs_with_body"] or 1
    cites = counts["citations_total"] or 1
    manifest = {
        "screen": "F3 Phase-1 sampling frame (two-phase; detector-independent)",
        "warning": (
            "SAMPLING FRAME, not a detector. S1/S2 are mechanical (S2 optionally "
            "an LLM claim-SPECIFICITY classifier). F3 prevalence within the "
            "eligible stratum is decided by a HUMAN in Phase-2 (V1-V4). No F3 "
            "label assigned here. Run on a RANDOM OA frame, never the review-"
            "dense hunting pool (that biases the denominator)."
        ),
        "detector_independent": True,
        "s2_mode": "llm_specificity" if specificity_classifier else "mechanical",
        "params": {
            "xml_dir": xml_dir,
            "out_dir": out_dir,
            "max_docs": max_docs,
            "MIN_CONTENT_TOKENS": MIN_CONTENT_TOKENS,
        },
        "counts": counts,
        "distinct_cited_pmids_looked_up": len(pt_cache),
        # sizing inputs for the two-phase gold-set size calculation
        "sizing": {
            "citations_per_parseable_doc": round(cites / parseable, 3),
            "eligible_per_citation": round(counts["eligible_stratum"] / cites, 4),
            "eligible_per_parseable_doc": round(
                counts["eligible_stratum"] / parseable, 3
            ),
            "note": (
                "docs_to_get_N_verified_F3 = N / (eligible_per_parseable_doc * "
                "within_stratum_F3_rate); within_stratum_F3_rate comes from "
                "human Phase-2, not from this module."
            ),
        },
    }
    json.dump(manifest, open(os.path.join(out_dir, "f3_phase1_manifest.json"), "w"),
              indent=2)
    return manifest


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cre.f1.f3_phase1_frame",
        description="F3 Phase-1 sampling frame (two-phase; detector-independent). "
                    "CALIBRATION/GOLD sampling — assigns no F3 label.",
    )
    p.add_argument("--xml-dir", required=True,
                   help="Directory of PMC OA JATS XML from a RANDOM OA frame.")
    p.add_argument("--out-dir", required=True)
    p.add_argument("--max-docs", type=int, default=None)
    p.add_argument("--email", default=DEFAULT_EMAIL)
    p.add_argument("--api-key", default="")
    return p


def main(argv=None) -> None:
    args = build_arg_parser().parse_args(argv)
    key = args.api_key or os.environ.get("NCBI_API_KEY", "")
    email = args.email or os.environ.get("NCBI_EMAIL", DEFAULT_EMAIL)
    t0 = time.time()
    # CLI runs mechanical S2 only; the LLM refinement is injected via the API
    # (screen_dir(specificity_classifier=...)) from the harness/notebook, which
    # is where host.llm lives.
    man = screen_dir(args.xml_dir, args.out_dir, api_key=key, email=email,
                     max_docs=args.max_docs)
    print(json.dumps(man["counts"], indent=2))
    print(json.dumps(man["sizing"], indent=2))
    print(f"--- f3_phase1_frame done in {time.time() - t0:.0f}s ---")


if __name__ == "__main__":
    main()
