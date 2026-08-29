"""
cre/f1/band_prompts.py

Frozen-substrate prompts for the F3-F7 judgment band: the two prompt strings the
band reasons over, their pinned version strings, and LLM-backed callables shaped
to match judgment_band's injected-callable contract.

INTERFACE (verified against judgment_band.py @ 79522bd on feat/f2-final-revision):
    Extractor     = Callable[[str], list]          # extractor(sentence) -> list[str]
    CoverageJudge = Callable[[list, dict], list]    # judge(claims, evidence) -> list[dict]
    FetchAbstract = Callable[[str], Optional[str]]
    FetchReflist  = Callable[[str], tuple]

  * `extractor` receives ONLY the citing sentence -- no cited marker/pmid. It must
    scope from inline citation markers present in the sentence text.
  * `coverage_judge` is BATCH: it receives the whole claims list plus the evidence
    DICT that assemble_evidence builds, and returns one verdict dict PER claim, in
    order. judgment_band.coverage_verdicts reads three keys off each dict:
    `established` (tri-state True/False/None), `rationale`, `evidence_span`.
  * evidence dict keys (from assemble_evidence): `cited_pmid`, `cited_abstract`,
    `cited_is_review`, `review_reflist`, `review_fulltext_available`. Coverage
    judges PRESENCE against `cited_abstract`; `review_reflist` is F3-discriminator
    input, downstream, and is NOT a coverage input.

SINGLE SOURCE OF TRUTH for versions: this module owns the two version constants;
judgment_band imports them.  Bump the suffix on every text change so prompt text
and item/manifest stamps cannot drift.  This module stays a leaf and does NOT
import judgment_band.

DESIGN LOCKS (do not silently drift):
  * Coverage = PRESENCE only (Option A, ZD 2026-07-07): not origin (F3
    discriminator), not strength (future F4 discriminator). Specificity DOES gate:
    added specificity the evidence does not confirm -> not established (False).
  * Coverage is abstract-scoped. A usable abstract always yields True or False;
    None is reserved for the deterministic no-usable-abstract path.  True means
    engaged, not contradicted, and no load-bearing specificity unconfirmed.
  * Model output is strict structured JSON: `engages_subject`, `contradicts`,
    and `unconfirmed_specifics`.  Code, not the model, aggregates `established`.
  * These prompts are FROZEN substrate: they change what the annotator sees.
    Stabilize on a small batch, then freeze by commit. rationale/evidence_span are
    for the SEPARATE non-blind proposed-verdict log; they never enter the blind
    annotation payload (annotation_payload enforces this in judgment_band).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Callable, List, Optional

# --------------------------------------------------------------------------
# Pinned prompt versions (single source of truth; judgment_band imports these)
# --------------------------------------------------------------------------
CLAIM_EXTRACT_PROMPT_VERSION = "claim_extract_v3"
COVERAGE_PROMPT_VERSION = "coverage_v2"

# --------------------------------------------------------------------------
# Prompt 1 -- atomic claim extraction (citing sentence only; inline markers)
# --------------------------------------------------------------------------
CLAIM_EXTRACT_PROMPT = """\
You decompose a scientific citing sentence into ATOMIC CLAIMS for citation-fault analysis.

An atomic claim is one self-contained factual assertion attributed to the cited reference \
under review: ONE subject, ONE predicate, ONE finding.

RULES
1. Extract only assertions attributed to the cited reference. Inline citation markers may \
look like "[28]", "[28,29,30,31]", superscript "102", or "[1]". A sentence-final marker \
does not automatically attribute the citing authors' own purpose, method, or action (for \
example, "we compared our results") to the cited source. Drop that framing.
2. Split every independently judgeable predicate:
   a. Split coordinated clauses and predicates when each is separately asserted ("activates A and inhibits B"). \
Do not split alternatives joined by "or" when the sentence asserts only the disjunction \
under a shared modality; preserve the complete disjunctive predicate as one claim.
   b. Split distinct stacked properties ("first inhibitor developed for X").
   c. Never merge "developed for", "approved for", and "used for".
   d. Split result-bearing source descriptions. A phrase such as "findings from the trial \
conducted by Consortium C" asserts both who conducted the trial and what the trial \
produced/reported. Nominal forms such as "findings from", "associations from", and \
"results of" still carry a result predicate.
   e. Split independent reporting meta-properties and the embedded finding. Priority \
("first"), a claimed reporting date/year, and the reported scientific proposition are \
independently judgeable assertions. Preserve the shared finding inside each meta-claim so \
it remains self-contained; do not deduplicate claims whose predicates differ.
3. Preserve specificity verbatim: named study/consortium/entity, species, strain or genetic \
model, cell line, population/indication, dose, direction, magnitude, timeframe, and context.
4. Preserve asserted strength/modality verbatim ("causes" versus "associated with").
5. Preserve priority and temporal wording ("first", "originally", "recently", a year).
6. Do not add, infer, merge, or interpret. Strip citation markers from claim text.
7. If there is no checkable factual assertion attributable to the cited source, return an \
empty list.

EXAMPLES
Citing sentence: "Metformin activates AMPK and inhibits hepatic gluconeogenesis in diet-induced obese rats [7]."
{"claims": ["Metformin activates AMPK", "Metformin inhibits hepatic gluconeogenesis in diet-induced obese rats"]}

Citing sentence: "Treatment A may slow or stop disease progression in mice [7]."
{"claims": ["Treatment A may slow or stop disease progression in mice"]}

Citing sentence: "We compared our estimates with the associations from a trial conducted by Consortium C [1]."
{"claims": ["Consortium C conducted the trial", "The trial produced the associations"]}

Citing sentence: "Study Q was the first to report in 2012 that protein P binds receptor R [1]."
{"claims": ["Study Q was the first to report that protein P binds receptor R", "Study Q reported in 2012 that protein P binds receptor R", "Protein P binds receptor R"]}

Citing sentence: "Therapy R was developed for disease X, approved for disease X, and used to treat disease X [1]."
{"claims": ["Therapy R was developed for disease X", "Therapy R was approved for disease X", "Therapy R was used to treat disease X"]}

Citing sentence: "Standard protocols were used for RNA extraction [3]."
{"claims": []}

TASK
Citing sentence: "<<CITING_SENTENCE>>"

Return ONLY a JSON object with exactly one key, {"claims": [...]}, with no prose or markdown fences.
"""

# --------------------------------------------------------------------------
# Prompt 2 -- coverage judgment for ONE atomic claim (presence, tri-state)
# --------------------------------------------------------------------------
COVERAGE_PROMPT = """\
You analyze whether a cited paper's supplied abstract supports ONE atomic claim for \
abstract-scoped citation-fault analysis. Report structured findings ONLY. Do NOT output an \
established verdict; deterministic downstream code decides it.

The supplied evidence is a present, usable abstract. Judge only against that abstract; never \
fill gaps with outside knowledge.

OUTPUT FIELDS
- engages_subject (JSON boolean): true when the abstract addresses the SAME finding/result, \
including at a more general level or weaker causal strength. Off-topic or silent is false.
- contradicts (JSON boolean): true only when engaged evidence is incompatible with the claim. \
If engages_subject is false, this MUST be false.
- unconfirmed_specifics (JSON list of nonempty strings): every load-bearing part of the claim \
not established by the abstract. If engages_subject is false, this MUST be empty.

LOCKED RULES
1. Presence, NOT origin. Rightful/original-source provenance is downstream.
2. Presence, NOT strength. Weaker causal/modal strength still counts as coverage for the \
same finding; do not list strength mismatch as an unconfirmed specific.
3. Semantic paraphrase is allowed. Referential synonyms or ordinary paraphrases of the same \
entity class and relation are covered. A specificity gap requires genuine narrowing, such as \
a named strain/taxon/entity/study/consortium, genetic model, cell line, population/indication, \
dose, numeric magnitude, direction, time point, or experimental context. For example, \
"normal gut commensals" and "intestinal microbiota" can name the same resident-gut entity \
class; wording alone is not a gap. Never use this rule to equate genuinely different or \
hierarchically narrower entities.
4. Do not compose an unstated causal pathway. A claim using "through", "via", "by", \
"mediates", or an equivalent mechanism relation requires the abstract to link that mechanism \
to that outcome. Separately establishing the outcome and a putative mechanism is insufficient.
5. Predicate identity is exact and directional:
   - "approved for X" requires explicit regulatory approval/authorization for X;
   - "used for X" requires explicit actual administration, prescribing, or clinical use;
   - "developed for X" requires explicit design/development intent directed at X.
   No implication runs between these predicates unless the abstract states both.
6. Named identifiers, priority, claimed year, and numeric/comparative magnitude are \
load-bearing. Purely qualitative, unquantified scale adjectives such as "large" are \
descriptive rather than independently load-bearing.
7. Return actual JSON booleans true/false, never strings. Do not return null or an \
"established" field.

EXAMPLES
Claim: "Drug X reduced infarct size in ApoE-deficient mice."
Evidence: "Drug X reduced infarct size in mice."
{"engages_subject": true, "contradicts": false, "unconfirmed_specifics": ["ApoE-deficient model"], "rationale": "The abstract does not state the claimed genetic model.", "evidence_span": "Drug X reduced infarct size in mice"}

Claim: "Compound A reduces tumors through inhibition of pathway P."
Evidence: "Compound A reduced tumors. In a separate assay, Compound A inhibited pathway P."
{"engages_subject": true, "contradicts": false, "unconfirmed_specifics": ["inhibition of pathway P mediates tumor reduction"], "rationale": "The abstract states the outcome and pathway effect separately but does not link them causally.", "evidence_span": "Compound A reduced tumors"}

Claim: "Compound A reduces tumors through inhibition of pathway P."
Evidence: "Compound A reduced tumors by inhibiting pathway P."
{"engages_subject": true, "contradicts": false, "unconfirmed_specifics": [], "rationale": "The abstract explicitly links pathway inhibition to tumor reduction.", "evidence_span": "Compound A reduced tumors by inhibiting pathway P"}

Claim: "Elevated CRP causes cardiovascular disease."
Evidence: "Elevated CRP is associated with increased cardiovascular disease risk."
{"engages_subject": true, "contradicts": false, "unconfirmed_specifics": [], "rationale": "The same finding is present at weaker strength, which is downstream of coverage.", "evidence_span": "Elevated CRP is associated with increased cardiovascular disease risk"}

Claim: "Aspirin irreversibly inhibits platelet cyclooxygenase-1."
Evidence: "We studied photosystem II assembly in cyanobacteria."
{"engages_subject": false, "contradicts": false, "unconfirmed_specifics": [], "rationale": "The abstract is off-topic.", "evidence_span": ""}

ATOMIC CLAIM
<<ATOMIC_CLAIM>>

CITED-PAPER ABSTRACT
<<EVIDENCE>>

Return ONLY a JSON object with exactly these keys:
{"engages_subject": true, "contradicts": false, "unconfirmed_specifics": [], "rationale": "<one sentence>", "evidence_span": "<verbatim span or empty string>"}
No prose or markdown fences.
"""


# --------------------------------------------------------------------------
# Output struct (verbose form, for the non-blind proposed-verdict/debug log)
# --------------------------------------------------------------------------
@dataclass
class CoverageVerdict:
    established: Optional[bool]          # True / False / None (tri-state)
    rationale: str = ""
    evidence_span: str = ""
    raw: str = ""                        # raw model text, debugging only
    engages_subject: Optional[bool] = None
    contradicts: Optional[bool] = None
    unconfirmed_specifics: tuple[str, ...] = ()

    def as_judge_dict(self) -> dict:
        """The dict shape judgment_band.coverage_verdicts reads."""
        return {
            "established": self.established,
            "rationale": self.rationale,
            "evidence_span": self.evidence_span,
        }


# --------------------------------------------------------------------------
# Evidence sufficiency + rendering: the evidence DICT -> prompt text
# --------------------------------------------------------------------------
_NO_ABSTRACT_TEXT = "(no abstract available)"
_MISSING_ABSTRACT_SENTINELS = {
    "", "none", "null", "n/a", "na", "not available", "unavailable",
    _NO_ABSTRACT_TEXT,
}


def evidence_is_usable(evidence) -> bool:
    """True only for a present, non-sentinel abstract string.

    This deterministic check is the sole path into/out of the band's unknown
    state: usable evidence must receive a Boolean coverage decision; unusable
    evidence receives ``established=None`` without an LLM call.
    """
    if isinstance(evidence, str):
        abstract = evidence
    elif isinstance(evidence, dict):
        abstract = evidence.get("cited_abstract")
    else:
        return False
    return (
        isinstance(abstract, str)
        and abstract.strip().casefold() not in _MISSING_ABSTRACT_SENTINELS
    )


def render_evidence(evidence: dict) -> str:
    """Render the assemble_evidence dict into the text the coverage prompt sees.

    Coverage judges PRESENCE against the cited paper's title + abstract. The
    review reference list is deliberately NOT included -- it is F3-discriminator
    input, downstream, not a coverage input.
    """
    if not evidence_is_usable(evidence):
        return _NO_ABSTRACT_TEXT
    if isinstance(evidence, str):          # tolerate a bare abstract string
        return evidence.strip()
    return evidence["cited_abstract"].strip()


# --------------------------------------------------------------------------
# Strict JSON extraction. Malformed output fails closed; no fences/prose,
# duplicate keys, extra keys, or type coercion are accepted.
# --------------------------------------------------------------------------
_CLAIM_KEYS = frozenset({"claims"})
_COVERAGE_KEYS = frozenset({
    "engages_subject", "contradicts", "unconfirmed_specifics",
    "rationale", "evidence_span",
})
_CITATION_MARKER_RE = re.compile(r"\[\s*\d+(?:\s*,\s*\d+)*\s*\]")


def _reject_duplicate_keys(pairs) -> dict:
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError(f"duplicate JSON key: {key}")
        obj[key] = value
    return obj


def _loads_strict(text: str, expected_keys: frozenset[str]) -> dict:
    if not isinstance(text, str) or not text.strip():
        raise ValueError("empty model output")
    try:
        obj = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except json.JSONDecodeError as exc:
        raise ValueError(f"model output is not one bare JSON object: {exc}") from exc
    if not isinstance(obj, dict):
        raise ValueError(f"top-level JSON must be an object: {type(obj).__name__}")
    keys = frozenset(obj)
    if keys != expected_keys:
        raise ValueError(
            "JSON keys mismatch: "
            f"missing={sorted(expected_keys - keys)} extra={sorted(keys - expected_keys)}"
        )
    return obj


def parse_claims(text: str) -> List[str]:
    obj = _loads_strict(text, _CLAIM_KEYS)
    claims = obj["claims"]
    if not isinstance(claims, list):
        raise ValueError(f"'claims' is not a list: {claims!r}")
    out = []
    seen = set()
    for index, claim in enumerate(claims):
        if not isinstance(claim, str) or not claim.strip():
            raise ValueError(f"claims[{index}] must be a nonempty string")
        cleaned = claim.strip()
        if _CITATION_MARKER_RE.search(cleaned):
            raise ValueError(f"claims[{index}] contains a citation marker: {cleaned!r}")
        if cleaned in seen:
            raise ValueError(f"duplicate claim: {cleaned!r}")
        seen.add(cleaned)
        out.append(cleaned)
    return out


def aggregate_coverage(
    engages_subject: bool, contradicts: bool, unconfirmed_specifics: list[str]
) -> bool:
    """Deterministically map strict structured fields to coverage Boolean."""
    if type(engages_subject) is not bool:
        raise ValueError("engages_subject must be an actual JSON boolean")
    if type(contradicts) is not bool:
        raise ValueError("contradicts must be an actual JSON boolean")
    if not isinstance(unconfirmed_specifics, list):
        raise ValueError("unconfirmed_specifics must be a list")
    if not engages_subject and (contradicts or unconfirmed_specifics):
        raise ValueError(
            "engages_subject=false requires contradicts=false and "
            "unconfirmed_specifics=[]"
        )
    return engages_subject and not contradicts and not unconfirmed_specifics


def parse_coverage(text: str) -> CoverageVerdict:
    obj = _loads_strict(text, _COVERAGE_KEYS)
    engages = obj["engages_subject"]
    contradicts = obj["contradicts"]
    unconfirmed = obj["unconfirmed_specifics"]
    if type(engages) is not bool:
        raise ValueError("engages_subject must be an actual JSON boolean")
    if type(contradicts) is not bool:
        raise ValueError("contradicts must be an actual JSON boolean")
    if not isinstance(unconfirmed, list):
        raise ValueError("unconfirmed_specifics must be a list")
    cleaned_unconfirmed = []
    for index, value in enumerate(unconfirmed):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(
                f"unconfirmed_specifics[{index}] must be a nonempty string"
            )
        cleaned_unconfirmed.append(value.strip())
    if len(set(cleaned_unconfirmed)) != len(cleaned_unconfirmed):
        raise ValueError("unconfirmed_specifics contains duplicates")
    rationale = obj["rationale"]
    evidence_span = obj["evidence_span"]
    # rationale is a log-only field (it feeds the non-blind proposed-verdict log,
    # never the blind annotation payload), so a blank or explicit-null value must
    # NOT discard an otherwise-valid coverage decision. Normalize null/blank to
    # "". The exact 5-key schema is still enforced by _loads_strict above, so a
    # MISSING rationale key still fails closed; only its VALUE is made
    # non-load-bearing here. A non-string, non-null value is still malformed.
    if rationale is None:
        rationale = ""
    elif not isinstance(rationale, str):
        raise ValueError("rationale must be a string or null")
    if not isinstance(evidence_span, str):
        raise ValueError("evidence_span must be a string")
    established = aggregate_coverage(engages, contradicts, cleaned_unconfirmed)
    return CoverageVerdict(
        established=established,
        rationale=rationale.strip(),
        evidence_span=evidence_span.strip(),
        raw=text,
        engages_subject=engages,
        contradicts=contradicts,
        unconfirmed_specifics=tuple(cleaned_unconfirmed),
    )


# --------------------------------------------------------------------------
# LLM plumbing.  `call_llm` is any Callable[[str], str]: prompt in, text out.
# --------------------------------------------------------------------------
def extract_atomic_claims_llm(call_llm: Callable[[str], str], citing_sentence: str) -> List[str]:
    prompt = CLAIM_EXTRACT_PROMPT.replace("<<CITING_SENTENCE>>", citing_sentence)
    return parse_claims(call_llm(prompt))


def judge_coverage_verbose(
    call_llm: Callable[[str], str], atomic_claim: str, evidence
) -> CoverageVerdict:
    if not evidence_is_usable(evidence):
        return CoverageVerdict(
            established=None,
            rationale="no usable abstract (deterministic evidence-sufficiency gate)",
            evidence_span="",
        )
    prompt = (
        COVERAGE_PROMPT
        .replace("<<ATOMIC_CLAIM>>", atomic_claim)
        .replace("<<EVIDENCE>>", render_evidence(evidence))
    )
    return parse_coverage(call_llm(prompt))


def make_extractor(call_llm: Callable[[str], str]) -> Callable[[str], List[str]]:
    """Return an `extractor(sentence) -> list[str]` matching Extractor."""
    def extractor(sentence: str) -> List[str]:
        return extract_atomic_claims_llm(call_llm, sentence)
    return extractor


def make_coverage_judge(call_llm: Callable[[str], str]) -> Callable[[list, dict], list]:
    """Return a `coverage_judge(claims, evidence) -> list[dict]` matching
    CoverageJudge. Judges each claim in its own call (one claim per prompt is
    more reliable than batching), renders the evidence dict once, and returns one
    verdict dict per claim in order, keyed established/rationale/evidence_span.
    """
    def coverage_judge(claims: list, evidence: dict) -> list:
        if not evidence_is_usable(evidence):
            return [
                {
                    "established": None,
                    "rationale": (
                        "no usable abstract "
                        "(deterministic evidence-sufficiency gate)"
                    ),
                    "evidence_span": "",
                }
                for _ in claims
            ]
        evidence_text = render_evidence(evidence)
        out = []
        for claim in claims:
            prompt = (
                COVERAGE_PROMPT
                .replace("<<ATOMIC_CLAIM>>", claim)
                .replace("<<EVIDENCE>>", evidence_text)
            )
            out.append(parse_coverage(call_llm(prompt)).as_judge_dict())
        return out
    return coverage_judge


def make_anthropic_call(client, model: str, max_tokens: int = 1024):
    """Adapt an anthropic.Anthropic client into a Callable[[str], str].

    Pin `model` in the run manifest alongside CLAIM_EXTRACT_PROMPT_VERSION and
    COVERAGE_PROMPT_VERSION -- the precision number is conditional on all three.
    The temperature parameter is deliberately omitted: the pinned Opus model
    rejects an explicit temperature argument.
    """
    def call_llm(prompt: str) -> str:
        resp = client.messages.create(
            model=model, max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
    return call_llm


# --------------------------------------------------------------------------
# route -- byte-for-byte semantics of judgment_band.route, over verdict DICTS.
# Kept for offline verification; judgment_band.route remains the source of truth.
# --------------------------------------------------------------------------
def route_from_verdicts(verdicts: list) -> str:
    """verdicts: list of dicts each carrying `established` (tri-state).

      * any `established is False`      -> "F6_FLAGGED"
      * all `established is True`       -> "FULL_COVERAGE" (vacuously true if empty)
      * otherwise (some None, no False) -> "HELD_LOW_CONFIDENCE"

    NOTE: judgment_band.route returns FULL_COVERAGE for an empty list (vacuous
    all()). Whether zero-claim items ever reach route is controlled upstream by
    build_item (excluded_no_citance) and extract_atomic_claims.
    """
    established = [v.get("established") if isinstance(v, dict) else v for v in verdicts]
    if any(e is False for e in established):
        return "F6_FLAGGED"
    if all(e is True for e in established):
        return "FULL_COVERAGE"
    return "HELD_LOW_CONFIDENCE"
