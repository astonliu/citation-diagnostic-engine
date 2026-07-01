"""CRE F1 (fabricated citation) detection stage.

Faithful reimplementation of the Topaz et al. (Lancet 2026) verification
pipeline, with an Opus-class LLM filter in place of their Haiku step.

F1 is a conjunction: (claimed-PMID mismatch OR dead PMID) AND survives the LLM
formatting filter AND claimed content not found in PubMed/Crossref/OpenAlex.
References without a claimed PMID are 'unverifiable', never F1.
"""
from .schema import (
    # taxonomy + states
    ACCURATE, F1, F2, F3, F4, F5, F6, F7, F8, TAXONOMY_LABELS,
    CLEARED, UNVERIFIABLE, HUMAN_REVIEW, UNSCOREABLE,
    V_FABRICATION, V_FORMATTING, V_REFERENCE_ERROR, V_UNCERTAIN,
    # records + helpers
    AtomicClaim, CitedPaper, SourcePaper, Repair, Annotation,
    GoldRecord, PredictionRecord, EvalRecord,
    check_f6_invariant, pipeline_state_to_taxonomy,
    # working object
    Reference, ClaimedRef, RetrievedRecord, StageLog, write_jsonl,
)
from .parser import parse_pmc_xml, iter_pmc_dir, link_citances
from .lookup import (fetch_pubmed, compare_and_flag, title_similarity,
                     fuzzy_biblio_lookup)
from .unscoreable import classify_unscoreable
from .eval_report import (summarize as eval_summarize, band_of, wilson_ci,
                          format_report, build_f2_record,
                          high_band_rate_of_scoreable, assert_f2_fixes_loaded)
from .biblio_match import (
    normalize_title, title_sim, trigram_jaccard, trigram_containment,
    jaro_winkler, field_agreement, match_score, best_match, retrieve_candidates,
    FieldAgreement, MatchResult, BestMatch,
    is_scoreable_title, flag_verdict,
    VERDICT_MATCH, VERDICT_WRONG_PAPER, VERDICT_FORMATTING,
    VERDICT_SAME_WORK_VARIANT, SAME_WORK_TITLE_SIM_MIN,
)
from .biblio_rerank import rerank_stage2
from .llm_filter import llm_filter, build_prompt, parse_verdict
from .confirm import confirm as confirm_refs, found_anywhere, all_errored
from .decide import decide as decide_label
from .run import run as run_pipeline, process_reference, make_completer
from .ratelimit import RateLimiter, request_with_retry, configure_ncbi

__all__ = [
    "ACCURATE", "F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8",
    "TAXONOMY_LABELS", "CLEARED", "UNVERIFIABLE", "HUMAN_REVIEW", "UNSCOREABLE",
    "V_FABRICATION", "V_FORMATTING", "V_REFERENCE_ERROR", "V_UNCERTAIN",
    "AtomicClaim", "CitedPaper", "SourcePaper", "Repair", "Annotation",
    "GoldRecord", "PredictionRecord", "EvalRecord",
    "check_f6_invariant", "pipeline_state_to_taxonomy",
    "Reference", "ClaimedRef", "RetrievedRecord", "StageLog", "write_jsonl",
    "parse_pmc_xml", "iter_pmc_dir", "link_citances", "fetch_pubmed",
    "compare_and_flag", "title_similarity", "fuzzy_biblio_lookup",
    "classify_unscoreable",
    "eval_summarize", "band_of", "wilson_ci", "format_report", "build_f2_record",
    "high_band_rate_of_scoreable", "assert_f2_fixes_loaded",
    "normalize_title", "title_sim", "trigram_jaccard", "trigram_containment",
    "jaro_winkler", "field_agreement", "match_score", "best_match",
    "retrieve_candidates", "FieldAgreement", "MatchResult", "BestMatch",
    "is_scoreable_title", "flag_verdict",
    "VERDICT_MATCH", "VERDICT_WRONG_PAPER", "VERDICT_FORMATTING",
    "VERDICT_SAME_WORK_VARIANT", "SAME_WORK_TITLE_SIM_MIN",
    "rerank_stage2",
    "llm_filter", "build_prompt",
    "parse_verdict", "confirm_refs", "found_anywhere", "all_errored",
    "decide_label", "run_pipeline", "process_reference", "make_completer",
    "RateLimiter", "request_with_retry", "configure_ncbi",
]
