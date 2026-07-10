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

SINGLE SOURCE OF TRUTH for versions: this module owns the two version constants.
On freeze, replace judgment_band.py lines 65-66
    CLAIM_EXTRACT_PROMPT_VERSION = "claim_extract_v1"
    COVERAGE_PROMPT_VERSION = "coverage_v1"
with
    from .band_prompts import CLAIM_EXTRACT_PROMPT_VERSION, COVERAGE_PROMPT_VERSION
so text and version can never drift (band_prompts stays a leaf: it does NOT import
judgment_band). Bump the suffix (v1 -> v2) on any text change.

DESIGN LOCKS (do not silently drift):
  * Coverage = PRESENCE only (Option A, ZD 2026-07-07): not origin (F3
    discriminator), not strength (future F4 discriminator). Specificity DOES gate:
    added specificity the evidence does not confirm -> not established (False).
  * Tri-state is load-bearing: True established, False gap (routes F6), None
    UNKNOWN (never a gap). judgment_band.route decides with `is False`/`is True`.
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
CLAIM_EXTRACT_PROMPT_VERSION = "claim_extract_v1"
COVERAGE_PROMPT_VERSION = "coverage_v1"

# --------------------------------------------------------------------------
# Prompt 1 -- atomic claim extraction (citing sentence only; inline markers)
# --------------------------------------------------------------------------
CLAIM_EXTRACT_PROMPT = """\
You decompose a scientific citing sentence into ATOMIC CLAIMS for citation-fault analysis.

An atomic claim is a single, self-contained factual assertion the sentence makes about work \
it cites: one subject, one predicate, one finding. A sentence that asserts several things \
yields several atomic claims.

RULES
1. Extract only assertions the sentence attributes to a cited reference. The sentence may \
carry inline citation markers (for example "[28]", "[28,29,30,31]", or a superscript number \
like "102"); a clause that carries such a marker is a cited assertion. Drop clauses that \
carry no citation marker -- those are the citing authors' own framing, not a claim about a \
cited work.
2. One assertion per claim. Split coordinated or multi-part statements ("A and B", \
"A, whereas B", lists) into separate claims.
3. Preserve specificity verbatim. Keep every qualifier that narrows the assertion: species, \
animal model or cell line, population, dose, direction of effect, magnitude, timeframe, and \
experimental context (for example "in ApoE-deficient mice", "in humans", "at 24 hours"). \
Never generalize a specific context (do not turn "ApoE-deficient mice" into "mice").
4. Preserve the asserted strength/modality verbatim. If the sentence says "causes", keep \
"causes"; if it says "is associated with", keep that. Do not soften or strengthen. Strength \
is checked downstream, not here.
5. Do not add, infer, merge, or interpret. Extract only what the sentence states. Do not \
import outside knowledge.
6. Strip the citation markers themselves from the claim text; keep the assertion wording.
7. If the sentence makes no checkable factual claim about a cited reference (it cites for \
methods, background framing, or "see also"), return an empty list.

EXAMPLES

Citing sentence: "Metformin activates AMPK and inhibits hepatic gluconeogenesis in \
diet-induced obese rats [7]."
{"claims": ["Metformin activates AMPK", "Metformin inhibits hepatic gluconeogenesis in diet-induced obese rats"]}

Citing sentence: "Standard protocols were used for RNA extraction [3]."
{"claims": []}

Citing sentence: "Whereas early work reported only an association [4], later studies showed \
that BRCA1 loss causes genomic instability in human cells [5]."
{"claims": ["Early work reported only an association", "BRCA1 loss causes genomic instability in human cells"]}

Citing sentence: "While kinase X was originally developed for leukemia,12 drug Y is the \
prime example of a purpose-built therapy."
{"claims": ["Kinase X was originally developed for leukemia"]}

TASK
Citing sentence: "<<CITING_SENTENCE>>"

Return ONLY a JSON object of the form {"claims": [...]} with no prose and no markdown fences.
"""

# --------------------------------------------------------------------------
# Prompt 2 -- coverage judgment for ONE atomic claim (presence, tri-state)
# --------------------------------------------------------------------------
COVERAGE_PROMPT = """\
You judge whether a cited paper ESTABLISHES a single atomic claim, for citation-fault analysis.

You are given ONE atomic claim and the available evidence from the cited paper (its title and \
abstract, and when present its reference list). Decide whether the paper states and supports \
the claim.

"ESTABLISHES" MEANS PRESENCE ONLY -- three narrow rules:
1. Presence, NOT origin. Whether the paper is the rightful or original source of the finding \
is NOT your concern. A paper that restates a finding from elsewhere but clearly states and \
supports it still ESTABLISHES the claim here.
2. Presence, NOT strength. If the paper addresses the claim's subject and finding but at a \
weaker strength or modality (it reports an association where the claim says "causes"), the \
claim is still ESTABLISHED (true). Strength mismatch is assessed downstream, not here.
3. Specificity must be met. If the claim asserts a specific context the evidence does not \
confirm (claim says "in ApoE-deficient mice" but the evidence discusses only "mice" \
generally), the claim is NOT established -- the specific assertion is unconfirmed.

VERDICTS (tri-state)
- true  : the evidence states and supports the claim's assertion (presence met, specificity \
met). Weaker strength still counts as true.
- false : the evidence is silent on the claim, does not address it, contradicts it, or \
asserts it only at a lower specificity than the claim requires.
- null  : the available evidence is genuinely insufficient to decide (for example the \
abstract does not cover the relevant result and full text would be required). null means \
UNKNOWN, not a gap. Use null only when you truly cannot tell -- never as a hedge for a \
decision you can make.

Judge only against the provided evidence. Do not use outside knowledge to fill gaps in the \
evidence. When the verdict is "false" because the paper contradicts (rather than omits) the \
claim, say so in the rationale.

ATOMIC CLAIM
<<ATOMIC_CLAIM>>

CITED-PAPER EVIDENCE
<<EVIDENCE>>

Return ONLY a JSON object of the form
{"established": true | false | null, "rationale": "<one sentence>", "evidence_span": "<verbatim span from the evidence, or empty string>"}
with no prose and no markdown fences.
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

    def as_judge_dict(self) -> dict:
        """The dict shape judgment_band.coverage_verdicts reads."""
        return {
            "established": self.established,
            "rationale": self.rationale,
            "evidence_span": self.evidence_span,
        }


# --------------------------------------------------------------------------
# Evidence rendering: the evidence DICT -> prompt text
# --------------------------------------------------------------------------
def render_evidence(evidence: dict) -> str:
    """Render the assemble_evidence dict into the text the coverage prompt sees.

    Coverage judges PRESENCE against the cited paper's title + abstract. The
    review reference list is deliberately NOT included -- it is F3-discriminator
    input, downstream, not a coverage input.
    """
    if isinstance(evidence, str):          # tolerate a bare abstract string
        return evidence.strip()
    abstract = (evidence.get("cited_abstract") or "").strip()
    if not abstract:
        return "(no abstract available)"
    return abstract


# --------------------------------------------------------------------------
# Robust JSON extraction (models occasionally wrap output in fences / prose)
# --------------------------------------------------------------------------
_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


def _loads_lenient(text: str) -> dict:
    if text is None:
        raise ValueError("empty model output")
    s = _FENCE_RE.sub("", text.strip())
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    start, end = s.find("{"), s.rfind("}")
    if start != -1 and end != -1 and end > start:
        return json.loads(s[start : end + 1])
    raise ValueError(f"could not parse JSON object from model output: {text!r}")


def parse_claims(text: str) -> List[str]:
    obj = _loads_lenient(text)
    claims = obj.get("claims", [])
    if not isinstance(claims, list):
        raise ValueError(f"'claims' is not a list: {claims!r}")
    return [str(c).strip() for c in claims if str(c).strip()]


def _coerce_tristate(v) -> Optional[bool]:
    """True/False only; anything else is unknown (None) -- mirrors
    judgment_band._tristate, but raises on clearly-malformed non-empty tokens so
    stabilization surfaces bad output instead of silently reading it as None."""
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        low = v.strip().lower()
        if low == "true":
            return True
        if low == "false":
            return False
        if low in ("null", "none", "unknown", ""):
            return None
    raise ValueError(f"unrecognized established value: {v!r}")


def parse_coverage(text: str) -> CoverageVerdict:
    obj = _loads_lenient(text)
    return CoverageVerdict(
        established=_coerce_tristate(obj.get("established")),
        rationale=str(obj.get("rationale", "")).strip(),
        evidence_span=str(obj.get("evidence_span", "")).strip(),
        raw=text,
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


def make_anthropic_call(client, model: str, max_tokens: int = 1024, temperature: float = 0.0):
    """Adapt an anthropic.Anthropic client into a Callable[[str], str].

    Pin `model` in the run manifest alongside CLAIM_EXTRACT_PROMPT_VERSION and
    COVERAGE_PROMPT_VERSION -- the precision number is conditional on all three.
    temperature=0.0 for reproducible frozen-substrate behavior.
    """
    def call_llm(prompt: str) -> str:
        resp = client.messages.create(
            model=model, max_tokens=max_tokens, temperature=temperature,
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
