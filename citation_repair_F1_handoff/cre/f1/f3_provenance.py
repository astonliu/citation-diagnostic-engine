"""F3 (misattribution) provenance discriminator -- the first real machine
assessor for the typed F3-F7 judgment engine.

This module builds an injected, offline-testable ``ProvenanceAssessor`` that
satisfies ``judgment_engine``'s locked ``ProvenanceAssessment`` contract. Given
the fully-supported claims of a citation-claim pair plus the cited work's
evidence, it decides ``PROPER_ORIGIN`` / ``MISATTRIBUTED_CONFIRMED`` /
``UNJUDGEABLE`` and, only when misattribution is confirmed, carries a real
reflist-sourced origin chain and evidence spans.

DEFINITIONAL LOCK (protocol/engine, NOT the old prose)
    F3 is PROVENANCE-ONLY. Zero support is F6, not F3. The engine reaches this
    assessor ONLY when every atomic claim is SUPPORTED (``decide_judgment``
    enforces the ordering), and the boundary this assessor discriminates is
    F3-vs-ACCURATE: does the cited work RESTATE (review/secondary) rather than
    ORIGINATE an origin-sensitive claim? This module never treats an unsupported
    claim as F3 and never uses the "zero atomic claims supported -> F3"
    definition.

CONSERVATIVE POSTURE (ADVISOR LOCK still open)
    The origin-sensitivity definition, trace sources, maximum hop count, and the
    inaccessible/competing-origin rule are ADVISOR-LOCK knobs that are not yet
    preregistered. They live here as explicit ``F3Policy`` parameters defaulting
    conservative. Until they are locked every genuinely ambiguous (``unclear``),
    unresolved, or unsatisfied-policy path returns ``UNJUDGEABLE`` -- the assessor
    NEVER forces F3 on ``unclear``/unresolved input, and NEVER fabricates an
    origin chain. A claim confidently classified ``not_origin_sensitive`` carries
    no misattribution risk, so it is provenance-proper (contributes to
    ``PROPER_ORIGIN``), not held. F3 predictions from this assessor are therefore
    provisional.

OFFLINE / INJECTED
    Every I/O seam is injected; the module and its tests make no network or paid
    call. ``call_llm`` is a ``Callable[[str], str]`` (prompt in, text out, same
    shape as ``band_prompts``). ``fetch_abstract(id) -> Optional[str]`` fetches an
    abstract (V2 uses it for the cited work, V4 for the candidate primary).
    ``fetch_reflist(pmcid) -> (candidates, available)`` matches
    ``ncbi_meta.ncbi_pmc_reflist``'s return shape. In production the wiring step
    (out of scope here) composes ``ncbi_pmid_to_pmcid`` + ``ncbi_pmc_reflist``
    behind these seams; PMID->PMCID resolution is a caller concern, so an
    unresolved cited PMCID simply routes the restatement path to ``UNJUDGEABLE``.

The mechanical candidate-assembly reuses ``f3_candidate_collect``'s reflist ->
``provenance_candidates`` semantics (refs that carry a title become candidates);
it does NOT reuse that module's calibration-only attribution lexicon, which
would bias detection and contaminate the category.

Strict-JSON model parsing mirrors the frozen ``band_prompts._loads_strict``
pattern (duplicate-key rejection, exact key set, no fences/prose, no coercion).
It is replicated here rather than imported so this module stays a self-contained
leaf and is unaffected by concurrent edits to ``band_prompts``.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Callable, Optional

from .judgment_engine import (
    ProvenanceAssessment,
    ProvenanceState,
    SupportState,
)

# Seam type aliases (documentation only).
CallLLM = Callable[[str], str]
FetchAbstract = Callable[[str], Optional[str]]
FetchReflist = Callable[[str], tuple]
ProvenanceAssessor = Callable[
    [tuple, tuple], ProvenanceAssessment
]


# --------------------------------------------------------------------------
# Policy -- the ADVISOR-LOCK knobs as explicit, preregisterable parameters.
# Defaults are the conservative protocol defaults; do NOT invent tighter values.
# When the locked policy is unsatisfied, the assessor returns UNJUDGEABLE.
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class F3Policy:
    origin_sensitive_prompt_version: str = "f3_v2_origin_v1"
    v3_select_prompt_version: str = "f3_v3_select_v1"
    v4_prompt_version: str = "f3_v4_loopclose_v1"
    trace_sources: tuple = ("cited_reflist",)   # reflist-only
    max_hop_count: int = 1                        # 1 = single hop, reflist-only
    unresolved_state: str = "UNJUDGEABLE"         # never "F3"


DEFAULT_F3_POLICY = F3Policy()


# --------------------------------------------------------------------------
# Prompt substrate. Versions are stamped into every assessment's rationale so a
# run is conditional on them. Prompts judge origin against supplied evidence
# ONLY -- never citation-marker style, never priority buzzwords, never outside
# knowledge (the calibration attribution lexicon is deliberately not used).
# --------------------------------------------------------------------------
F3_V2_ORIGIN_PROMPT = """\
You judge the PROVENANCE of ONE atomic claim that a citing sentence attributes to a cited \
work. The claim is already established as supported by the cited work's abstract. Your only \
task is to decide, from the abstract alone, whether the cited work ORIGINATES the finding \
(reports it as its own primary result) or merely RESTATES/ATTRIBUTES a finding first \
established in an earlier source (as a review or secondary source would), and whether the \
claim is ORIGIN-SENSITIVE at all (its scientific meaning depends on who first established it).

Judge ONLY against the supplied abstract and the publication-type context. Never use outside \
knowledge. Never infer origin from citation-marker style or from priority buzzwords in the \
citing sentence.

OUTPUT FIELDS
- verdict (string, exactly one of):
    "originates"           - the abstract reports this finding as this work's own primary result
    "restatement"          - the abstract restates/attributes a finding established in an earlier
                             primary source (review/secondary framing)
    "not_origin_sensitive" - the claim's scientific meaning does not depend on who originated it
    "unclear"              - the abstract does not let you decide
- evidence_span (string): a verbatim span from the abstract that grounds the verdict. For
    "restatement" this MUST be the span showing the cited work attributing/citing rather than
    reporting a primary result. Empty string when none applies.
- rationale (string): one sentence.

CITED-WORK PUBLICATION TYPE
<<PUBTYPE>>

ATOMIC CLAIM
<<CLAIM>>

CITED-WORK ABSTRACT
<<ABSTRACT>>

Return ONLY a JSON object with exactly these keys:
{"verdict": "<one of the four>", "evidence_span": "<verbatim span or empty string>", "rationale": "<one sentence>"}
No prose or markdown fences.
"""

F3_V3_SELECT_PROMPT = """\
A cited work RESTATED the finding below rather than originating it. You are given the cited \
work's own reference list. Select the ONE reference that most plausibly ORIGINATES the finding \
(the rightful primary source), or none if no listed reference plausibly does.

Judge only from the supplied titles and years. Do not invent references and do not guess beyond \
the list.

OUTPUT FIELDS
- selected_index (integer index into the candidate list below, or null when no listed reference
    plausibly originates the finding)
- rationale (string): one sentence.

FINDING (the restated atomic claim)
<<CLAIM>>

RESTATEMENT SPAN (how the cited work attributed it)
<<RESTATEMENT_SPAN>>

CANDIDATE REFERENCES (from the cited work's reference list)
<<CANDIDATES>>

Return ONLY a JSON object with exactly these keys:
{"selected_index": <integer or null>, "rationale": "<one sentence>"}
No prose or markdown fences.
"""

F3_V4_LOOPCLOSE_PROMPT = """\
You judge whether a candidate PRIMARY source actually ORIGINATES a finding. You are given the \
finding and the candidate primary's abstract. Decide whether that abstract itself REPORTS the \
finding as a result present in this work -- not merely mentions, reviews, or cites it.

Judge ONLY against the supplied abstract. Never use outside knowledge.

OUTPUT FIELDS
- contains_finding (JSON boolean): true only when the abstract itself reports the finding as a
    result of this work.
- evidence_span (string): a verbatim span from the abstract that reports the finding. Empty
    string when contains_finding is false.
- rationale (string): one sentence.

FINDING
<<CLAIM>>

CANDIDATE PRIMARY ABSTRACT
<<ABSTRACT>>

Return ONLY a JSON object with exactly these keys:
{"contains_finding": <true or false>, "evidence_span": "<verbatim span or empty string>", "rationale": "<one sentence>"}
No prose or markdown fences.
"""

_V2_VERDICTS = frozenset(
    {"originates", "restatement", "not_origin_sensitive", "unclear"}
)
_V2_KEYS = frozenset({"verdict", "evidence_span", "rationale"})
_V3_KEYS = frozenset({"selected_index", "rationale"})
_V4_KEYS = frozenset({"contains_finding", "evidence_span", "rationale"})

# Deterministic "no usable abstract" sentinels (mirrors band_prompts' gate; kept
# local so this module does not depend on a concurrently-edited file).
_MISSING_ABSTRACT_SENTINELS = frozenset({
    "", "none", "null", "n/a", "na", "not available", "unavailable",
    "(no abstract available)",
})


# --------------------------------------------------------------------------
# Strict-JSON parsing. Malformed MODEL output fails closed (ValueError); the
# orchestrator quarantines. This mirrors band_prompts._loads_strict exactly:
# one bare JSON object, no duplicate keys, exact key set, no fences/prose.
# --------------------------------------------------------------------------
def _reject_duplicate_keys(pairs) -> dict:
    obj: dict = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError(f"duplicate JSON key: {key}")
        obj[key] = value
    return obj


def _loads_strict(text: str, expected_keys: frozenset) -> dict:
    if not isinstance(text, str) or not text.strip():
        raise ValueError("empty model output")
    try:
        obj = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"model output is not one bare JSON object: {exc}"
        ) from exc
    if not isinstance(obj, dict):
        raise ValueError(
            f"top-level JSON must be an object: {type(obj).__name__}"
        )
    keys = frozenset(obj)
    if keys != expected_keys:
        raise ValueError(
            "JSON keys mismatch: "
            f"missing={sorted(expected_keys - keys)} "
            f"extra={sorted(keys - expected_keys)}"
        )
    return obj


def _clean_rationale(value: object) -> str:
    # rationale is a log-only field; explicit null normalizes to "" (parallel to
    # band_prompts). A non-string, non-null value is still malformed.
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("rationale must be a string or null")
    return value.strip()


def _parse_v2(text: str) -> tuple[str, str, str]:
    obj = _loads_strict(text, _V2_KEYS)
    verdict = obj["verdict"]
    if not isinstance(verdict, str) or verdict not in _V2_VERDICTS:
        raise ValueError(f"verdict must be one of {sorted(_V2_VERDICTS)}")
    span = obj["evidence_span"]
    if not isinstance(span, str):
        raise ValueError("evidence_span must be a string")
    return verdict, span.strip(), _clean_rationale(obj["rationale"])


def _parse_v3(text: str, candidate_count: int) -> tuple[Optional[int], str]:
    obj = _loads_strict(text, _V3_KEYS)
    index = obj["selected_index"]
    if index is not None:
        # bool is a subclass of int; reject it explicitly.
        if type(index) is not int:
            raise ValueError("selected_index must be an integer or null")
        if index < 0 or index >= candidate_count:
            raise ValueError("selected_index is out of range")
    return index, _clean_rationale(obj["rationale"])


def _parse_v4(text: str) -> tuple[bool, str, str]:
    obj = _loads_strict(text, _V4_KEYS)
    contains = obj["contains_finding"]
    if type(contains) is not bool:
        raise ValueError("contains_finding must be an actual JSON boolean")
    span = obj["evidence_span"]
    if not isinstance(span, str):
        raise ValueError("evidence_span must be a string")
    return contains, span.strip(), _clean_rationale(obj["rationale"])


# --------------------------------------------------------------------------
# Mechanical helpers.
# --------------------------------------------------------------------------
def _abstract_present(text: object) -> bool:
    return (
        isinstance(text, str)
        and text.strip().casefold() not in _MISSING_ABSTRACT_SENTINELS
    )


def _fill_prompt(template: str, replacements: dict[str, str]) -> str:
    """Fill trusted template tokens once; tokens in untrusted values stay inert."""
    if not replacements:
        return template
    pattern = re.compile("|".join(
        re.escape(token) for token in sorted(replacements, key=len, reverse=True)
    ))
    return pattern.sub(lambda match: replacements[match.group(0)], template)


def _assemble_candidates(reflist_result: object) -> list[dict]:
    """Normalize a fetch_reflist return into V3 candidates.

    Reuses f3_candidate_collect's reflist->provenance_candidates semantics: a
    reference becomes a candidate when it carries a title. We additionally
    require a nonblank claimed_pmid, because V3->V4 must fetch the candidate's
    abstract and the confirmed origin chain must carry a real primary id.

    A missing/malformed seam RETURN (network best-effort) yields [] -> the
    restatement path holds as UNJUDGEABLE. Only malformed MODEL output raises.
    """
    if not isinstance(reflist_result, tuple) or len(reflist_result) != 2:
        return []
    raw, _available = reflist_result
    if not raw:
        return []
    if not isinstance(raw, (list, tuple)):
        return []
    out: list[dict] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        title = entry.get("title")
        pmid = entry.get("claimed_pmid")
        if (
            isinstance(title, str) and title.strip()
            and isinstance(pmid, str) and pmid.strip()
        ):
            out.append({
                "title": title.strip(),
                "claimed_pmid": pmid.strip(),
                "year": entry.get("year"),
            })
    return out


def _render_candidates(candidates: list[dict]) -> str:
    lines = []
    for i, cand in enumerate(candidates):
        year = cand.get("year")
        year_str = f" ({year})" if year else ""
        lines.append(f"[{i}] {cand['title']}{year_str}")
    return "\n".join(lines)


def _fmt_pmid(pmid: object) -> str:
    return f"PMID:{str(pmid).strip()}"


def _cited_chain_id(cited_pmid, cited_pmcid) -> str:
    if cited_pmid and str(cited_pmid).strip():
        return _fmt_pmid(cited_pmid)
    return f"PMCID:{str(cited_pmcid).strip()}"


def _policy_stamp(policy: F3Policy) -> str:
    return (
        f"[F3 policy origin_sensitive_prompt_version="
        f"{policy.origin_sensitive_prompt_version} "
        f"v3_select_prompt_version={policy.v3_select_prompt_version} "
        f"v4_prompt_version={policy.v4_prompt_version} "
        f"trace_sources={list(policy.trace_sources)} "
        f"max_hop_count={policy.max_hop_count} "
        f"unresolved_state={policy.unresolved_state}]"
    )


def _unjudgeable(policy: F3Policy, detail: str) -> ProvenanceAssessment:
    return ProvenanceAssessment(
        ProvenanceState.UNJUDGEABLE,
        rationale=f"{_policy_stamp(policy)} {detail}",
    )


def _proper_origin(policy: F3Policy, detail: str) -> ProvenanceAssessment:
    return ProvenanceAssessment(
        ProvenanceState.PROPER_ORIGIN,
        rationale=f"{_policy_stamp(policy)} {detail}",
    )


# --------------------------------------------------------------------------
# Factory.
# --------------------------------------------------------------------------
def make_provenance_assessor(
    *,
    call_llm: CallLLM,
    fetch_reflist: FetchReflist,
    fetch_abstract: FetchAbstract,
    cited_pmid,
    cited_pmcid=None,
    cited_is_review: Optional[bool] = None,
    policy: F3Policy = DEFAULT_F3_POLICY,
) -> ProvenanceAssessor:
    """Build a ``ProvenanceAssessor`` for one cited work.

    The returned callable matches the engine's ``ProvenanceAssessor`` contract
    ``(claims, support) -> ProvenanceAssessment`` and is invoked ONLY after every
    claim is SUPPORTED (the engine guarantees this). It walks the protocol's
    V2 (origin/restatement) -> V3 (rightful-primary from the cited reflist) ->
    V4 (loop-closed on the primary's abstract) and returns
    ``MISATTRIBUTED_CONFIRMED`` only when all three close; otherwise
    ``UNJUDGEABLE`` (never a fabricated F3). See the module docstring.
    """
    if not isinstance(policy, F3Policy):
        raise TypeError("policy must be an F3Policy")

    pubtype_ctx = (
        "review/secondary" if cited_is_review is True
        else "primary" if cited_is_review is False
        else "unknown"
    )

    def _v2(claim: str, cited_abstract: str) -> tuple[str, str, str]:
        prompt = _fill_prompt(F3_V2_ORIGIN_PROMPT, {
            "<<PUBTYPE>>": pubtype_ctx,
            "<<CLAIM>>": claim,
            "<<ABSTRACT>>": cited_abstract,
        })
        return _parse_v2(call_llm(prompt))

    def _v3(claim: str, restatement_span: str,
            candidates: list[dict]) -> tuple[Optional[int], str]:
        prompt = _fill_prompt(F3_V3_SELECT_PROMPT, {
            "<<CLAIM>>": claim,
            "<<RESTATEMENT_SPAN>>": restatement_span,
            "<<CANDIDATES>>": _render_candidates(candidates),
        })
        return _parse_v3(call_llm(prompt), len(candidates))

    def _v4(claim: str, primary_abstract: str) -> tuple[bool, str, str]:
        prompt = _fill_prompt(F3_V4_LOOPCLOSE_PROMPT, {
            "<<CLAIM>>": claim,
            "<<ABSTRACT>>": primary_abstract,
        })
        return _parse_v4(call_llm(prompt))

    def _trace_one_claim(
        claim: str, restatement_span: str
    ) -> Optional[ProvenanceAssessment]:
        """V3 + V4 for a single restatement claim. Returns a confirmed F3
        assessment, or None when this claim cannot be confirmed (caller holds)."""
        # Locked-policy gate: only the reflist source is authorized, and at least
        # one hop must be permitted. Anything else -> cannot trace (hold).
        if "cited_reflist" not in policy.trace_sources:
            return None
        if policy.max_hop_count < 1:
            return None
        if not (cited_pmcid and str(cited_pmcid).strip()):
            # Cited PMCID unresolved: production wiring resolves PMID->PMCID
            # before this point; offline / unresolved -> cannot fetch reflist.
            return None
        candidates = _assemble_candidates(fetch_reflist(str(cited_pmcid).strip()))
        if not candidates:
            return None
        # V3: single hop into the cited work's own reference list (reflist-only,
        # default max_hop_count=1). Multi-hop tracing is an open ADVISOR-LOCK item
        # and is not traced here, so a confirmed chain is exactly cited->primary.
        selected_index, _v3_reason = _v3(claim, restatement_span, candidates)
        if selected_index is None:
            return None
        primary = candidates[selected_index]
        primary_pmid = primary["claimed_pmid"]
        primary_abstract = fetch_abstract(primary_pmid)
        if not _abstract_present(primary_abstract):
            return None
        contains, origin_span, _v4_reason = _v4(claim, primary_abstract)
        if not contains or not origin_span:
            return None
        if origin_span not in primary_abstract:
            return None
        # Terminal rule: V2=restatement AND rightful primary identified within the
        # hop budget AND V4 confirms it contains the finding. Real reflist-sourced
        # ids + real spans; the contract enforces non-empty tuples.
        return ProvenanceAssessment(
            ProvenanceState.MISATTRIBUTED_CONFIRMED,
            origin_chain=(
                _cited_chain_id(cited_pmid, cited_pmcid),
                _fmt_pmid(primary_pmid),
            ),
            evidence_spans=(restatement_span, origin_span),
            rationale=(
                f"{_policy_stamp(policy)} V2=restatement; rightful primary "
                f"{_fmt_pmid(primary_pmid)} selected from the cited reflist and "
                f"V4-confirmed to originate the finding."
            ),
        )

    def assessor(
        claims: tuple, support: tuple
    ) -> ProvenanceAssessment:
        # Engine invokes this only under full support; be robust regardless.
        if not claims:
            return _unjudgeable(policy, "no atomic claims to assess for provenance")
        if (
            not isinstance(support, tuple)
            or len(support) != len(claims)
            or any(
                getattr(row, "claim_index", None) != index
                or getattr(row, "state", None) is not SupportState.SUPPORTED
                for index, row in enumerate(support)
            )
        ):
            return _unjudgeable(
                policy,
                "provenance requires complete, index-aligned SUPPORTED claims",
            )

        cited_fetch_id = (
            str(cited_pmid).strip() if cited_pmid and str(cited_pmid).strip()
            else str(cited_pmcid).strip() if cited_pmcid and str(cited_pmcid).strip()
            else ""
        )
        if not cited_fetch_id:
            return _unjudgeable(
                policy, "cited work identifier unavailable; cannot fetch abstract"
            )
        cited_abstract = fetch_abstract(cited_fetch_id)
        if not _abstract_present(cited_abstract):
            return _unjudgeable(
                policy, "cited work abstract unavailable; provenance not assessable"
            )

        # A claim that either originates in the cited work OR is not
        # origin-sensitive carries no misattribution risk; either signal makes
        # the pair provenance-OK by construction. Only a genuine unknown
        # (unclear) or an unresolved/unanchored restatement holds the pair.
        saw_non_f3 = False
        held_reasons: list[str] = []
        for claim in claims:
            verdict, span, _reason = _v2(claim, cited_abstract)
            if verdict == "originates":
                # The cited work is the primary source: no misattribution risk.
                saw_non_f3 = True
                continue
            if verdict == "not_origin_sensitive":
                # The claim's meaning does not depend on who originated it, so it
                # cannot be F3; its provenance is proper by construction. This is
                # the interim rule pending ADVISOR-LOCK #1 (which may refine WHAT
                # counts as origin-sensitive, not that a confidently-not-sensitive
                # claim is not an F3 risk).
                saw_non_f3 = True
                continue
            if verdict == "unclear":
                # A genuine unknown still holds the pair; never forced to F3.
                held_reasons.append("unclear claim held")
                continue
            # verdict == "restatement"
            if not span:
                # No restatement span to anchor the origin chain -> cannot
                # confirm without fabricating a span -> hold.
                held_reasons.append("restatement without an anchoring span")
                continue
            if span not in cited_abstract:
                held_reasons.append(
                    "restatement span is not verbatim in the cited abstract")
                continue
            confirmed = _trace_one_claim(claim, span)
            if confirmed is not None:
                return confirmed
            held_reasons.append("restatement not confirmed within hop budget")

        # Aggregation (a confirmed restatement already returned early):
        if held_reasons:
            # Any unclear claim, or any unresolved/unanchored restatement: hold.
            # Never force F3, never declare accurate on an open question.
            return _unjudgeable(policy, "; ".join(dict.fromkeys(held_reasons)))
        if saw_non_f3:
            # Every claim either originates in the cited work or is not
            # origin-sensitive: no misattribution risk -> proper origin.
            return _proper_origin(
                policy,
                "every assessed claim originates or is not origin-sensitive",
            )
        # Defensive: no verdict category matched (should not occur).
        return _unjudgeable(policy, "provenance unresolved")

    return assessor
