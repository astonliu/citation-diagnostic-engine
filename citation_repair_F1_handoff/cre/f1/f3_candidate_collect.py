"""F3 (Misattribution) candidate collector -- CALIBRATION-ONLY.

===========================================================================
!!  CALIBRATION-ONLY.  DO NOT USE THIS OUTPUT AS GOLD / IAA EVALUATION DATA. !!
===========================================================================

This tool SURFACES F3 candidates for a human to adjudicate. It sources the
*worked calibration examples* needed to write the F3 section of TAXONOMY.md
(2 positive + 2 negative examples per category). F3 starts from ZERO confirmed
instances: you cannot define the category crisply without having seen one, and
they cannot be fabricated, so this tool finds candidates a human can confirm.

Hard invariants (violating any one makes the output unusable):

  * NEVER assign an F3 label. The tool emits candidates plus an UNFILLED
    four-check worksheet. The judgment is a human's (annotator / Aston):
    automated ground truth on biomedical provenance is unreliable, and a
    machine-assigned label would not be independent of the detector.

  * CALIBRATION-ONLY. This tool HUNTS positives -- it filters to sentences that
    read like origin-attributions. That biases the sample toward the
    egregious-and-findable and destroys prevalence, which is fine for
    calibration/codebook examples but FATAL for evaluation. It must NEVER be
    used to assemble the gold / IAA evaluation set; that set is a defined
    naturally-occurring sample labeled blind at F3's true (tiny) base rate.

  * QUARANTINE. Keep this output separate from any reportable gold/IAA slice.

  * NO SUPPRESSION BY HIDDEN FUNNEL. Every stage that drops a candidate is
    counted in the manifest, so the funnel is auditable
    (attribution hits -> cited-is-review -> review-with-PMCID -> emitted).

F3 scope served here: review/secondary-source misattribution -- a citing
sentence credits a finding to a cited work that is a review/commentary carrying
a result it did not produce, at full coverage. A primary paper that restates
only some claims routes to F6 (coverage gap) and is out of scope. This scope is
what the attribution-lexicon + article-type signals detect near-deterministically.

Run:
    PYTHONPATH=. python -m cre.f1.f3_candidate_collect --xml-dir <dir> \
        --enrich-review --limit 25

Network is downstream only: NCBI (EFetch/ELink, through the shared limiter) is
called only on survivors of the cheap string filter, to confirm the cited
reference is a Review and resolve its PMCID. Some sandboxes cannot reach NCBI;
build/unit-test offline with the network helpers monkeypatched, run live in
Colab with NCBI_API_KEY in Secrets.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
from collections import Counter

import requests

from .parser import parse_pmc_xml
from .ratelimit import configure_ncbi
# The NCBI metadata helpers now live in the shared, production-safe ncbi_meta
# module (the F3-F7 judgment band must not import a CALIBRATION-ONLY module).
# Re-imported (re-exported) here so this tool's public names, API, and tests are
# unchanged after the move.
from .ncbi_meta import (        # noqa: F401  (re-export)
    ncbi_pubtypes, is_review, ncbi_pmid_to_pmcid, ncbi_pmc_reflist,
    REVIEW_PUBTYPES, DEFAULT_EMAIL,
)

DEFAULT_OUT_DIR = "/content/drive/MyDrive/Citation-Integrity/Data"

CALIBRATION_ONLY_WARNING = (
    "CALIBRATION-ONLY. These candidates were HUNTED via an attribution "
    "lexicon (high precision, low recall) to source worked codebook examples. "
    "This sample is biased toward egregious-and-findable cases and does NOT "
    "reflect F3's true base rate. NEVER use it as gold / IAA evaluation data. "
    "No F3 label is machine-assigned; every candidate carries an unfilled "
    "human four-check worksheet. Keep quarantined from any reportable slice."
)


# ==========================================================================
# 5. Attribution lexicon -- the F3 surface fingerprint (priority/origin
#    language). High precision, low recall (correct for calibration). Ordered;
#    first pattern to fire wins, and its matched substring is recorded.
# ==========================================================================
# Verb alternation shared by the "first/originally/initially + verb" patterns.
_VERBS = (
    r"describ(?:ed|es)|report(?:ed|s)|demonstrat(?:ed|es)|show(?:ed|n)|"
    r"identif(?:ied|ies)|characteri[sz]ed|propos(?:ed|es)|discover(?:ed|s)|"
    r"introduc(?:ed|es)|observ(?:ed|es)|document(?:ed|s)|recogni[sz]ed|"
    r"establish(?:ed|es)|defin(?:ed|es)|develop(?:ed|s)"
)

# (name, compiled_regex), all case-insensitive, in priority order.
ATTRIBUTION_PATTERNS: list[tuple[str, "re.Pattern[str]"]] = [
    ("first_verb",    re.compile(rf"\bfirst(?:ly)?\s+(?:{_VERBS})\b", re.I)),
    ("originally",    re.compile(rf"\boriginally\s+(?:{_VERBS})\b", re.I)),
    ("initially",     re.compile(rf"\binitial(?:ly)?\s+(?:{_VERBS})\b", re.I)),
    ("was_first",     re.compile(r"\b(?:was|were)\s+first\b", re.I)),
    ("as_shown_by",   re.compile(
        r"\bas\s+(?:first\s+)?(?:shown|demonstrated|reported|described|noted|"
        r"observed)\s+(?:by|in)\b", re.I)),
    ("seminal",       re.compile(r"\bseminal\b", re.I)),
    ("landmark",      re.compile(
        r"\blandmark\s+(?:study|trial|paper|work|report|publication|article)\b",
        re.I)),
    ("earliest",      re.compile(r"\bearliest\b", re.I)),
    ("pioneered",     re.compile(r"\bpioneer(?:ed|ing)\b", re.I)),
    ("coined",        re.compile(r"\bcoined\b", re.I)),
    ("discovered_by", re.compile(r"\bdiscover(?:ed|y)\s+(?:by|of)\b", re.I)),
    ("credited_to",   re.compile(r"\b(?:credited|attributed)\s+to\b", re.I)),
]
# Deliberately OMITTED as too noisy (must not fire): bare "introduction of",
# lone "pioneer". Precision guards tested in test_f3_candidate_collect.py.

ATTRIBUTION_PATTERN_NAMES = [name for name, _ in ATTRIBUTION_PATTERNS]

# A bracketed/paren multi-cite: "[12, 13]", "(4-6)", "[7–11]". Used only as a
# sort-key signal (cardinality); never a drop filter. Normal (non-raw) string so
# the en/em-dash codepoints embed as literals; the rest are regex escapes.
_MULTICITE_RE = re.compile("[\\[(]\\s*\\d+\\s*[,–—\\-]\\s*\\d+")

# REVIEW_PUBTYPES now lives in ncbi_meta (re-imported above) alongside is_review.


def attribution_hit(sentence: str):
    """Return (pattern_name, matched_substring) for the first firing pattern, or
    None when no attribution pattern fires."""
    if not sentence:
        return None
    for name, rx in ATTRIBUTION_PATTERNS:
        m = rx.search(sentence)
        if m:
            return name, m.group(0)
    return None


def cite_cardinality(sentence: str) -> int:
    """Regex-only cardinality signal for one sentence: 2 when it carries a
    bracketed/paren multi-cite (two grouped numbers), else 1. Never a drop
    filter -- only a sort key. (The per-record estimate additionally maxes this
    against the number of refs sharing the citance; see collect().)"""
    if sentence and _MULTICITE_RE.search(sentence):
        return 2
    return 1


# ==========================================================================
# 6. NCBI helpers -- MOVED to cre/f1/ncbi_meta.py (network, through the shared
#    NCBI limiter). ncbi_pubtypes, is_review, ncbi_pmid_to_pmcid, and
#    ncbi_pmc_reflist are re-imported at the top of this module so the collector
#    keeps calling them as module globals (tests monkeypatch them here). The
#    production F3-F7 judgment band imports them from ncbi_meta directly, so it
#    never depends on this CALIBRATION-ONLY module.
# ==========================================================================


# ==========================================================================
# 7. Candidate record
# ==========================================================================
def _new_verification() -> dict:
    """The human's four-check worksheet. The tool NEVER fills these -- they stay
    null until an annotator adjudicates against the real papers.
      V1 coverage  -- does the cited review state AND support the full claim?
                      (any gap -> F6, not F3 -> discard)
      V2 origin    -- own primary result (ACCURATE -> discard) or restatement
                      of an earlier source (live F3)?
      V3 repair    -- the rightful primary PMID (pick from provenance_candidates)
      V4 loop      -- does that primary actually contain the finding?"""
    return {
        "F3_V1_coverage": None,
        "F3_V2_origin": None,
        "F3_V3_repair_target_pmid": None,
        "F3_V4_loop_closed": None,
        "confirmed_F3": None,
        "annotator": None,
    }


def build_candidate(ref, pattern: str, phrase: str, cardinality: int,
                    ts: int) -> dict:
    c = ref.claimed
    return {
        "candidate_id": ref.citation_id,          # "<citing_pmcid>:<ref_id>"
        "citing_pmcid": ref.source_pmcid,
        "citing_pmid": ref.source_pmid,
        "citing_title": ref.source_title,
        "citing_sentence": ref.citance,
        "attribution_pattern": pattern,
        "attribution_phrase": phrase,
        "cited_marker": ref.cited_reference_marker,
        "cite_cardinality_estimate": cardinality,
        "single_cite_estimate": cardinality == 1,
        "cited_claimed": {
            "title": c.title,
            "authors": list(c.authors),
            "year": c.year,
            "journal": c.journal,
            "claimed_pmid": c.claimed_pmid,
            "claimed_doi": c.claimed_doi,
        },
        "cited_pubtypes": None,
        "cited_is_review": None,
        "cited_pmcid": "",
        "review_fulltext_available": None,
        "provenance_candidates": [],
        "emit_reason": "",
        "note": "",
        "verification": _new_verification(),
        "ts": ts,
    }


# ==========================================================================
# 4. Collector
# ==========================================================================
def _append_jsonl(fh, obj: dict) -> None:
    fh.write(json.dumps(obj, ensure_ascii=False) + "\n")
    fh.flush()


def _load_checkpoint(path: str) -> set:
    done: set = set()
    if not os.path.exists(path):
        return done
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            pmcid = rec.get("pmcid")
            if pmcid:
                done.add(pmcid)
    return done


def _pmcid_from_filename(fn: str) -> str:
    return re.sub(r"\.n?xml$", "", fn)


def collect(xml_dir: str, out_dir: str, *, limit: int = 25,
            max_docs: "int | None" = None, require_review_oa: bool = True,
            enrich_review: bool = False, email: str = DEFAULT_EMAIL,
            api_key: str = "", session=None) -> dict:
    """Surface F3 candidates from a dir of PMC-OA citing papers. Returns the
    run manifest dict (also written to disk). See module docstring: this is
    CALIBRATION-ONLY and never assigns an F3 label."""
    os.makedirs(out_dir, exist_ok=True)
    candidates_path = os.path.join(out_dir, "f3_candidates.jsonl")
    checkpoint_path = os.path.join(out_dir, "f3_collect_checkpoint.jsonl")
    manifest_path = os.path.join(out_dir, "f3_collect_manifest.json")

    done = _load_checkpoint(checkpoint_path)
    configure_ncbi(bool(api_key))
    session = session if session is not None else requests.Session()

    counts = {
        "docs_processed": 0,
        "attribution_hits": 0,
        "attribution_hits_no_pmid": 0,
        "cited_is_review": 0,
        "review_lookup_failed": 0,
        "review_has_pmcid": 0,
        "emitted": 0,
        "filtered_out": 0,
    }

    files = sorted(fn for fn in os.listdir(xml_dir)
                   if fn.endswith((".xml", ".nxml")))

    cand_fh = open(candidates_path, "a", encoding="utf-8")
    ckpt_fh = open(checkpoint_path, "a", encoding="utf-8")
    try:
        scanned = 0
        stop = False
        for fn in files:
            if stop:
                break
            pmcid = _pmcid_from_filename(fn)
            if pmcid in done:
                continue
            if max_docs is not None and scanned >= max_docs:
                break
            scanned += 1
            counts["docs_processed"] += 1
            path = os.path.join(xml_dir, fn)

            try:
                refs = parse_pmc_xml(path, source_pmcid=pmcid)
            except Exception as e:                    # noqa: BLE001 - best-effort
                print(f"[f3-parse-skip] {pmcid}: {e}")
                _append_jsonl(ckpt_fh, {"pmcid": pmcid, "error": str(e)})
                done.add(pmcid)
                continue

            # Per-sentence citation count -- cardinality sort key only, never a
            # drop filter.
            group = Counter(r.citance for r in refs if r.citance)

            for ref in refs:
                if not ref.citance:
                    continue
                hit = attribution_hit(ref.citance)
                if not hit:
                    continue
                counts["attribution_hits"] += 1
                pattern, phrase = hit
                cardinality = max(group[ref.citance],
                                  cite_cardinality(ref.citance), 1)
                rec = build_candidate(ref, pattern, phrase, cardinality,
                                      int(time.time()))

                pmid = ref.claimed.claimed_pmid
                if pmid:
                    pubtypes = ncbi_pubtypes(pmid, api_key, email, session)
                    rec["cited_pubtypes"] = pubtypes
                    review = is_review(pubtypes)
                    rec["cited_is_review"] = review
                    if review is True:
                        counts["cited_is_review"] += 1
                        cited_pmcid = ncbi_pmid_to_pmcid(pmid, api_key, email,
                                                         session)
                        rec["cited_pmcid"] = cited_pmcid
                        if cited_pmcid:
                            counts["review_has_pmcid"] += 1
                            if enrich_review:
                                prov, avail = ncbi_pmc_reflist(
                                    cited_pmcid, api_key, email, session)
                                rec["provenance_candidates"] = prov or []
                                rec["review_fulltext_available"] = avail
                    elif review is None:
                        counts["review_lookup_failed"] += 1
                        rec["review_lookup_status"] = "unknown_transport_or_parse_failure"
                else:
                    counts["attribution_hits_no_pmid"] += 1
                    rec["note"] = ("cited PMID unresolved; it must be resolved "
                                   "before use (title->PMID resolution not "
                                   "implemented in this build -- TODO).")

                # Emit gate.
                if require_review_oa:
                    if rec["cited_is_review"] is True and rec["cited_pmcid"]:
                        rec["emit_reason"] = "review+pmcid"
                        emit = True
                    else:
                        emit = False
                        counts["filtered_out"] += 1
                else:
                    rec["emit_reason"] = "attribution-hit"
                    emit = True

                if emit:
                    _append_jsonl(cand_fh, rec)
                    counts["emitted"] += 1
                    if counts["emitted"] >= limit:
                        stop = True
                        break

            # Checkpoint the file (even when we stopped mid-file at the limit:
            # its emitted candidates are already flushed, so a resume must not
            # reprocess and duplicate them).
            _append_jsonl(ckpt_fh, {"pmcid": pmcid})
            done.add(pmcid)
    finally:
        cand_fh.close()
        ckpt_fh.close()

    manifest = {
        "calibration_only": True,
        "warning": CALIBRATION_ONLY_WARNING,
        "run_params": {
            "xml_dir": xml_dir,
            "out_dir": out_dir,
            "limit": limit,
            "max_docs": max_docs,
            "require_review_oa": require_review_oa,
            "enrich_review": enrich_review,
            "email": email,
            "api_key_present": bool(api_key),
        },
        "attribution_patterns": ATTRIBUTION_PATTERN_NAMES,
        "review_pubtypes": sorted(REVIEW_PUBTYPES),
        "counts": counts,
        "candidates_path": candidates_path,
        "checkpoint_path": checkpoint_path,
        "manifest_path": manifest_path,
    }
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    return manifest


# ==========================================================================
# 8. CLI
# ==========================================================================
def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cre.f1.f3_candidate_collect",
        description="Surface F3 (misattribution) candidates for human "
                    "adjudication. CALIBRATION-ONLY -- never gold/IAA.")
    p.add_argument("--xml-dir", required=True,
                   help="Directory of PMC OA JATS XML (the citing papers).")
    p.add_argument("--out-dir", default=DEFAULT_OUT_DIR,
                   help="Output dir (Drive-first).")
    p.add_argument("--limit", type=int, default=25,
                   help="Stop after this many emitted candidates.")
    p.add_argument("--max-docs", type=int, default=None,
                   help="Cap source docs scanned this run.")
    p.add_argument("--no-require-review-oa", dest="require_review_oa",
                   action="store_false",
                   help="Emit all attribution hits, not just confirmed "
                        "reviews-with-PMCID.")
    p.add_argument("--enrich-review", dest="enrich_review", action="store_true",
                   help="Also fetch+parse each review's ref list for "
                        "provenance candidates.")
    p.add_argument("--email", default=DEFAULT_EMAIL)
    p.add_argument("--api-key", default=os.environ.get("NCBI_API_KEY", ""))
    p.set_defaults(require_review_oa=True, enrich_review=False)
    return p


def main(argv=None) -> dict:
    args = build_arg_parser().parse_args(argv)
    manifest = collect(
        args.xml_dir, args.out_dir, limit=args.limit, max_docs=args.max_docs,
        require_review_oa=args.require_review_oa,
        enrich_review=args.enrich_review, email=args.email, api_key=args.api_key)

    c = manifest["counts"]
    print("=" * 68)
    print("F3 candidate collection -- CALIBRATION-ONLY (not gold/IAA)")
    print("=" * 68)
    print("Funnel:")
    print(f"  docs_processed          : {c['docs_processed']}")
    print(f"  attribution_hits        : {c['attribution_hits']}")
    print(f"    ...no claimed PMID     : {c['attribution_hits_no_pmid']}")
    print(f"  cited_is_review         : {c['cited_is_review']}")
    print(f"  review_has_pmcid        : {c['review_has_pmcid']}")
    print(f"  emitted                 : {c['emitted']}")
    print(f"  filtered_out            : {c['filtered_out']}")
    print("-" * 68)
    print(f"candidates : {manifest['candidates_path']}")
    print(f"manifest   : {manifest['manifest_path']}")
    print(f"checkpoint : {manifest['checkpoint_path']}")
    return manifest


if __name__ == "__main__":       # pragma: no cover
    main()
