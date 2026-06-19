"""Optional Stage 2: MedCPT cross-encoder tie-breaker (HANDOFF_BIBLIO_MATCH).

Built ONLY for the ambiguous case -- when :func:`biblio_match.best_match` returns
``ambiguous=True`` (the top two candidates are within ``margin``). The Stage-1
matcher is deterministic and ships independently; this module is the open-weight,
zero-API-cost disambiguator layered on top.

  model: "ncbi/MedCPT-Cross-Encoder"  (open weights, public domain)

It scores each ``(claimed-metadata, candidate-title)`` pair with the cross-encoder,
re-picks the top candidate by the model's relevance score, then re-applies the same
accept/margin gate. The cross-encoder's calibrated relevance probability (sigmoid of
the logit) replaces the Stage-1 composite for this re-decision -- disambiguating
near-identical titles is exactly what the cross-encoder is for.

**Setup caveat:** MedCPT needs a recent ``transformers`` (>=4.20). The ``sarol`` env's
``transformers==4.2.2`` is too old -- install/upgrade in a separate env. If torch /
transformers / the weights are unavailable (the default here), every entry point
**degrades gracefully**: it returns ``None``, and the caller keeps the Stage-1
``BestMatch``. Nothing in Stage 1 depends on this module.
"""
from __future__ import annotations

from typing import Optional

from .schema import ClaimedRef, RetrievedRecord
from .biblio_match import BestMatch, MatchResult, match_score

Claimed = ClaimedRef

MODEL_NAME = "ncbi/MedCPT-Cross-Encoder"

# Module-level cache so the (heavy) weights load at most once per process.
_MODEL = None
_TOKENIZER = None
_LOAD_FAILED = False


def _load_model():
    """Lazy-load the cross-encoder once. Returns (model, tokenizer) or
    (None, None) if torch/transformers/the weights are unavailable. Never raises
    -- an unavailable model is a normal, expected state (Stage 1 then stands)."""
    global _MODEL, _TOKENIZER, _LOAD_FAILED
    if _MODEL is not None and _TOKENIZER is not None:
        return _MODEL, _TOKENIZER
    if _LOAD_FAILED:
        return None, None
    try:
        import torch  # noqa: F401
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
        _TOKENIZER = AutoTokenizer.from_pretrained(MODEL_NAME)
        _MODEL = AutoModelForSequenceClassification.from_pretrained(MODEL_NAME)
        _MODEL.eval()
        return _MODEL, _TOKENIZER
    except Exception:                          # noqa: BLE001 - optional dependency
        _LOAD_FAILED = True
        return None, None


def _claimed_text(claimed: Claimed) -> str:
    return " ".join(str(p) for p in (
        claimed.title, claimed.authors[0] if claimed.authors else "",
        claimed.year or "", claimed.journal) if p)


def _candidate_text(cand: RetrievedRecord) -> str:
    # Abstracts would go here when available (OpenAlex abstract_inverted_index /
    # Crossref abstract); title-only is fine when absent, as it is on
    # RetrievedRecord today.
    return cand.title or ""


def _cross_encoder_scores(claimed: Claimed, candidates: list[RetrievedRecord]
                          ) -> Optional[list[float]]:
    """Relevance probability (sigmoid of logit), one per candidate, or None if
    the model is unavailable."""
    model, tokenizer = _load_model()
    if model is None or tokenizer is None:
        return None
    import torch
    left = _claimed_text(claimed)
    pairs = [[left, _candidate_text(c)] for c in candidates]
    with torch.no_grad():
        enc = tokenizer(pairs, truncation=True, padding=True,
                        return_tensors="pt", max_length=512)
        logits = model(**enc).logits.squeeze(-1)
        probs = torch.sigmoid(logits)
    return [float(p) for p in probs.reshape(-1)]


def rerank_stage2(claimed: Claimed, candidates: list[RetrievedRecord],
                  accept: float = 0.85, margin: float = 0.05
                  ) -> Optional[BestMatch]:
    """Re-rank ambiguous candidates with the MedCPT cross-encoder and re-apply
    the accept/margin gate. Returns a fresh :class:`BestMatch`, or ``None`` to
    signal "model unavailable -- keep Stage 1" (the caller falls back).

    The returned ``MatchResult.score`` is the cross-encoder relevance probability;
    ``title_sim`` and ``fields`` are carried from the Stage-1 :func:`match_score`
    so the evidence trail stays populated.
    """
    if not candidates:
        return None
    scores = _cross_encoder_scores(claimed, candidates)
    if scores is None:                         # model unavailable -> degrade
        return None

    results = []
    for cand, ce in zip(candidates, scores):
        stage1 = match_score(claimed, cand)
        results.append(MatchResult(score=round(float(ce), 4),
                                   title_sim=stage1.title_sim,
                                   fields=stage1.fields, record=cand))
    results.sort(key=lambda m: m.score, reverse=True)
    top = results[0]
    ambiguous = len(results) > 1 and (top.score - results[1].score) < margin
    confident = top.score >= accept and not ambiguous
    return BestMatch(found=True, best=top, confident=confident,
                     ambiguous=ambiguous, runners_up=results[1:])
