"""The closed terminal-outcome vocabulary and the router that assigns it.

WHY THIS EXISTS. ``judgment_run`` used to answer two different questions with one
field. ``disposition`` says WHERE IN THE PIPELINE a pair stopped -- a mechanical
fact, useful for debugging -- and it was also read as WHAT WE CONCLUDED ABOUT THE
CITATION, which it is not. The two readings diverge in exactly the places that
matter:

* ``held_no_atomic_claims`` was reported as "this citation asserts no claim". On
  the natural run's 408 non-queue references, 209 citing sentences were ONLY a
  bibliography marker (``"4-6"``, ``"7"``, ``"5,8,10,19"``) -- a BROKEN INPUT the
  old non-tiling segmenter manufactured, never a statement about the citation.
  A broken parse read as "nothing to check" is a false negative that no
  downstream reader can distinguish from a real one.
* The same segmenter cut boundaries one marker cluster early, gluing the run that
  ENDED one sentence to the FRONT of the next. Those records were judged against
  THE WRONG SENTENCE and nothing said so, because the result still looked like
  prose. A prose test alone does not catch it; see
  :data:`CLAIM_INPUT_LEADING_MARKER`.
* ``quarantine_parse`` was terminal. It bundled a deterministic producer/consumer
  hash-representation defect, a strict-JSON contract failure, an empty model
  response, a truncated model response, an invalid schema, and a semantic
  validation refusal -- six different faults with six different correct answers --
  into one bucket that stopped the pair for good.
* An F3-F7 discriminator was reachable on a reference whose cited text could not
  be retrieved at all, because the gate asked whether the PAPER WAS IDENTIFIED
  rather than whether ITS TEXT WAS READABLE. Those are different facts, and only
  the second one makes a claim judgeable.

So the pipeline's stopping point and the citation's outcome are now separate
fields. ``disposition`` keeps its exact meaning and vocabulary. ``terminal_outcome``
is a CLOSED vocabulary of eight, one per durable record, and this module is its
only sanctioned producer.

THE RULE THAT GOVERNS EVERY BRANCH: ``NONE`` is an ASSERTION, not a default. It
means a complete prose citing sentence was actually read, every wired stage ran,
and no F3-F7 fault was found. Nothing that failed to read its input, failed to
parse a response, or failed a stage may reach it. Where the pipeline is uncertain
about the CITATION it says ``UNJUDGEABLE``; where the pipeline is broken about the
INPUT or its own contract it says ``HUMAN_REVIEW_REQUIRED`` with a reason from a
closed list. A pair is never silently cleared by a defect in this engine.

This module performs NO network call, NO model call, and NO I/O. It is a pure
function of one durable record, so the routing can be replayed over any saved
prediction file and audited without re-running the band.
"""
from __future__ import annotations

import re

#: Bump when the outcome vocabulary, the reason vocabulary, or the routing
#: changes. Written into every record and into the run manifest so a consumer can
#: tell which router produced a file.
TERMINAL_OUTCOME_VERSION = "terminal_outcome_v1"

# --- the eight terminal outcomes (closed) --------------------------------
#: An F3-F7 taxonomy finding was established. These, and only these, carry a
#: blind gold-label payload into ``judgment_band_annotation_queue.jsonl``.
OUTCOME_F3 = "F3"
OUTCOME_F4 = "F4"
OUTCOME_F5 = "F5"
OUTCOME_F6 = "F6"
OUTCOME_F7 = "F7"
#: A complete prose citing sentence was READ, every wired stage RAN, and no fault
#: was found. An assertion about the citation, never a fallback.
OUTCOME_NONE = "NONE"
#: The pipeline ran and remains uncertain ABOUT THE CITATION: insufficient or
#: ambiguous evidence, unjudgeable strength, unjudgeable provenance, a per-pair
#: stage failure, or a structurally valid response that failed validation.
#: Not a defect, not a human's problem, and never a finding.
OUTCOME_UNJUDGEABLE = "UNJUDGEABLE"
#: The ENGINE is broken for this pair -- its input or its own response contract --
#: and a human must look. Always carries a reason from ``HUMAN_REVIEW_REASONS``.
OUTCOME_HUMAN_REVIEW = "HUMAN_REVIEW_REQUIRED"

FINDING_OUTCOMES = (OUTCOME_F7, OUTCOME_F6, OUTCOME_F4, OUTCOME_F3, OUTCOME_F5)
TERMINAL_OUTCOMES = frozenset(
    set(FINDING_OUTCOMES)
    | {OUTCOME_NONE, OUTCOME_UNJUDGEABLE, OUTCOME_HUMAN_REVIEW})

# --- the closed human-review reason vocabulary ---------------------------
#: The citing sentence was only a bibliography marker and the repaired
#: surrounding-sentence parse did not recover prose. A broken input.
REASON_MALFORMED_CLAIM_INPUT = "malformed_claim_input"
#: The reference carries no citing sentence at all after reparse.
REASON_EMPTY_CLAIM_INPUT = "empty_claim_input"
#: Prose was read, the extractor was retried, and it still returned no claims on
#: a sentence that does assert something empirical.
REASON_CLAIM_EXTRACTION_EMPTY = "claim_extraction_empty_after_retry"
#: The model returned nothing, through the retry budget including a larger-token
#: attempt.
REASON_EMPTY_MODEL_RESPONSE = "empty_model_response_after_retry"
#: The model returned bytes that are not one bare JSON object (truncated or
#: unterminated), through the same budget.
REASON_MALFORMED_MODEL_RESPONSE = "malformed_model_response_after_retry"
#: A strict key contract failed and we refuse to silently repair model JSON.
#: Reserved for a contract failure the retry budget could not clear AND that the
#: router cannot honestly call a citation-level uncertainty.
REASON_SCHEMA_CONTRACT_FAILURE = "schema_contract_failure_after_retry"
#: The saved source record itself cannot be read as a citation pair.
REASON_CORRUPT_SOURCE_RECORD = "corrupt_source_record"

#: NOT a human-review reason, and NOT a defect. No ``<xref ref-type="bibr">``
#: anywhere in the citing document points at this reference: it is listed in the
#: bibliography and cited nowhere. An uncited reference has no citing sentence,
#: therefore no attributed claim, therefore nothing for F3-F7 to judge -- the same
#: class as a reference to a database or a website. A SCOPE EXCLUSION, machine-
#: final, in neither queue.
#:
#: Only an explicit ``cited_in_body is False`` reaches this. An empty citance on a
#: reference a marker DOES point at is the opposite finding -- the parser failed to
#: reach a marker that exists -- and stays a human-review item. Collapsing the two
#: would bury a parser defect inside a scope exclusion, which is precisely the
#: shape of silent drop this outcome vocabulary exists to prevent.
REASON_UNCITED_REFERENCE = "uncited_reference"

#: NOT a human-review reason. Band 1 identified the cited work and no text of it
#: can be retrieved -- no PubMed record, no PMC full text, nothing to judge the
#: claim against. Terminal UNJUDGEABLE, decided BEFORE any model call.
REASON_CITED_TEXT_UNAVAILABLE = "cited_text_unavailable"

#: Re-exported from ``reason_registry`` so the router and its consumers read the
#: same closed set, and a caller does not have to import two modules to tell a
#: scope exclusion from a judgment.
from .reason_registry import TERMINAL_SCOPE_EXCLUSION_REASONS  # noqa: E402

HUMAN_REVIEW_REASONS = frozenset({
    REASON_MALFORMED_CLAIM_INPUT,
    REASON_EMPTY_CLAIM_INPUT,
    REASON_CLAIM_EXTRACTION_EMPTY,
    REASON_EMPTY_MODEL_RESPONSE,
    REASON_MALFORMED_MODEL_RESPONSE,
    REASON_SCHEMA_CONTRACT_FAILURE,
    REASON_CORRUPT_SOURCE_RECORD,
})

# --- claim-input status (what the parser actually handed the extractor) ---
#: A complete prose sentence with no leading marker run. The ONLY status that may
#: reach ``NONE``.
CLAIM_INPUT_PROSE = "prose"
#: Digits, commas, dashes and wrapper punctuation only -- a bibliography marker
#: the segmenter left stranded as a "sentence". A broken input, NOT "no claim".
CLAIM_INPUT_MARKER_ONLY = "marker_only"
#: Nonblank, carries no letter, and is not a pure marker either (``"99% (54)."``).
#: A truncated numeric fragment: still not a sentence, still never ``NONE``.
CLAIM_INPUT_FRAGMENT = "numeric_fragment"
#: PROSE THAT BEGINS WITH SOMEONE ELSE'S MARKERS. The segmenter cut the boundary
#: one marker cluster too early, so the run that TERMINATED the previous sentence
#: was glued to the FRONT of the next one -- and every reference in that cluster
#: was then judged against a sentence it does not cite. PMC10908279:cit0017 and
#: cit0018 both stored "13,17,18 All procedures followed good laboratory practice
#: (GLP)."; the XML has 13,17,18 ending the sentence BEFORE it. The stored citance
#: is not merely untidy, it is THE WRONG SENTENCE, shifted forward by one. A scope
#: failure, and never ``NONE``.
CLAIM_INPUT_LEADING_MARKER = "leading_marker_bleed"
#: No citing sentence at all.
CLAIM_INPUT_EMPTY = "empty"

#: Statuses that are BROKEN INPUT. None of them may terminate as ``NONE``, and
#: every one of them must be reparsed before it is believed.
BROKEN_CLAIM_INPUT = frozenset({
    CLAIM_INPUT_MARKER_ONLY, CLAIM_INPUT_FRAGMENT,
    CLAIM_INPUT_LEADING_MARKER, CLAIM_INPUT_EMPTY})

#: Any letter in any script. Deliberately a LETTER test rather than an allowlist
#: of punctuation: an allowlist silently reclassifies the first marker style it
#: has not seen as prose, and prose is the status that may become NONE.
_LETTER_RE = re.compile(r"[^\W\d_]", re.UNICODE)

#: Every dash Unicode gives a publisher for a range, plus the comma and the
#: digits. What a bibliography marker run is allowed to contain.
_MARKER_CHARS = set("0123456789,-‐‑‒–—―")
#: Punctuation a marker may be WRAPPED in or trailed by -- ``(1)``, ``[1]``,
#: ``1.`` -- stripped before the run itself is tested.
_MARKER_WRAPPERS = "()[]{}.,;:"


def _is_marker_run(token: str) -> bool:
    """True when ``token`` is a bare bibliography-marker run.

    Wrapper punctuation is stripped first because ``(32)`` and ``[12]`` are the
    same object as ``32`` -- the publisher's rendering is not a semantic
    difference, and treating it as one is what lets a bled marker through as
    prose.
    """
    core = token.strip(_MARKER_WRAPPERS)
    return bool(core) and all(ch in _MARKER_CHARS for ch in core)


def has_leading_marker_run(citing_sentence) -> bool:
    """True when the citance OPENS with a bare marker run.

    Fails closed on purpose. A genuine sentence opening with a number
    ("2019 was the first year ...") is flagged too, and that costs exactly one
    reparse: if the repaired parse no longer opens with a marker run the record
    proceeds normally, so a false positive is self-correcting. The opposite error
    is not -- a bled citance that passes as prose is judged against a sentence it
    does not cite, and can report ``NONE`` for a claim nobody checked.
    """
    if not isinstance(citing_sentence, str):
        return False
    tokens = citing_sentence.strip().split()
    return bool(tokens) and _is_marker_run(tokens[0])


def claim_input_status(citing_sentence) -> str:
    """Classify what the parser handed the claim extractor.

    Five states, and the caller must keep them distinguishable: only
    :data:`CLAIM_INPUT_PROSE` can ever reach ``NONE``.
    """
    if not isinstance(citing_sentence, str) or not citing_sentence.strip():
        return CLAIM_INPUT_EMPTY
    if _LETTER_RE.search(citing_sentence) is None:
        stripped = citing_sentence.strip()
        if all(ch in _MARKER_CHARS or ch.isspace() or ch in _MARKER_WRAPPERS
               for ch in stripped):
            return CLAIM_INPUT_MARKER_ONLY
        return CLAIM_INPUT_FRAGMENT
    if has_leading_marker_run(citing_sentence):
        return CLAIM_INPUT_LEADING_MARKER
    return CLAIM_INPUT_PROSE


# --- parse-failure taxonomy ----------------------------------------------
# The single ``quarantine_parse`` bucket, split by the fault it actually is.
# Each bucket names a DIFFERENT correct answer; that is the whole reason for the
# split. Matched on the exact producer messages, most specific first.

#: The producer hashed one representation of a section and the consumer hashed
#: another. Deterministic, ours, and fixed at the representation (see
#: ``f5_evidence_store.adapt_fulltext_sections``). Never a human's problem.
PARSE_CONTENT_SHA_MISMATCH = "content_sha256_mismatch"
#: The model answered with the wrong KEY SET -- extra ``sibling_*`` siblings, or a
#: missing ``cited_strength_span``/``citing_strength_span``. Structurally valid
#: JSON, wrong contract. Retried, never silently repaired.
PARSE_SCHEMA_KEY_MISMATCH = "schema_key_mismatch"
#: The model returned no bytes.
PARSE_EMPTY_RESPONSE = "empty_model_response"
#: The model returned bytes that do not parse as one bare JSON object --
#: unterminated string, truncation.
PARSE_MALFORMED_RESPONSE = "malformed_model_response"
#: A field is present with a shape the schema forbids (e.g. ``dimensions`` that is
#: not the four ladder keys). The model answered the wrong SHAPE, not the wrong
#: keys, and no retry budget makes that a citation-level fact.
PARSE_INVALID_SCHEMA = "invalid_schema"
#: The response parsed, carried the right keys and shapes, and failed a SEMANTIC
#: or PROVENANCE check -- a span not verbatim in its bound section, a span bound
#: to an unknown section, a direction/relation contradiction, a date ordering.
#: The pipeline is intact; the JUDGMENT is not established.
PARSE_SEMANTIC_VALIDATION = "semantic_validation_failure"
#: Nothing above matched. Fails closed to the loudest answer rather than guessing.
PARSE_UNCLASSIFIED = "unclassified_parse_failure"

_PARSE_PATTERNS = (
    # Ours, deterministic, representation-level. Both the F5 consumer and the F7
    # builder phrase it their own way; both are the same defect class.
    (PARSE_CONTENT_SHA_MISMATCH,
     re.compile(r"content_sha256 does not (match stored text|bind its source text)")),
    (PARSE_EMPTY_RESPONSE, re.compile(r"empty model output")),
    (PARSE_MALFORMED_RESPONSE,
     re.compile(r"not one bare JSON object|Unterminated string|"
                r"Expecting value|Expecting ',' delimiter|Extra data")),
    (PARSE_SCHEMA_KEY_MISMATCH, re.compile(r"JSON keys mismatch")),
    # Shape violations of a present field. Enumerated rather than pattern-guessed
    # so a new validator message lands in UNCLASSIFIED and is SEEN, not folded
    # into a bucket whose answer it may not share.
    (PARSE_INVALID_SCHEMA,
     re.compile(r"dimensions must be an object with exactly the four ladder keys")),
    (PARSE_SEMANTIC_VALIDATION,
     re.compile(r"is not verbatim in its bound section|"
                r"bound to an unknown section sha256|"
                r"requires the same clear cited and candidate direction|"
                r"conflicts with different clear source directions|"
                r"require scope_mismatch_axis|"
                r"must be strictly before as_of_date")),
)


def classify_parse_failure(message) -> str:
    """Which of the six faults ``quarantine_parse`` was hiding, or UNCLASSIFIED.

    Matching is on the producer's message because that is the only thing the
    failure carries; each pattern is pinned to a message a module in this package
    emits, and ``test`` coverage of those modules is what keeps them in step. An
    unrecognised message is NOT guessed into a neighbouring bucket -- it becomes
    :data:`PARSE_UNCLASSIFIED`, which routes to human review.
    """
    text = message if isinstance(message, str) else str(message or "")
    for bucket, pattern in _PARSE_PATTERNS:
        if pattern.search(text):
            return bucket
    return PARSE_UNCLASSIFIED


#: How many ATTEMPTS each bucket gets in total (the first call plus retries), and
#: whether one of those retries raises the token ceiling. A bucket whose fault
#: cannot be cleared by asking again gets exactly one attempt -- retrying a
#: deterministic hash mismatch or a semantic refusal spends money to reproduce the
#: same bytes. Empty and truncated responses are the two faults a retry genuinely
#: fixes, and truncation is the one a bigger ceiling fixes.
_RETRY_BUDGET = {
    PARSE_EMPTY_RESPONSE: (3, True),
    PARSE_MALFORMED_RESPONSE: (3, True),
    PARSE_SCHEMA_KEY_MISMATCH: (2, False),
    PARSE_INVALID_SCHEMA: (1, False),
    PARSE_SEMANTIC_VALIDATION: (1, False),
    PARSE_CONTENT_SHA_MISMATCH: (1, False),
    PARSE_UNCLASSIFIED: (1, False),
}

#: Claim extraction gets one bounded retry: an empty extraction on real prose is
#: usually a transient refusal, and a second ask is cheap next to reporting a
#: false NONE.
CLAIM_EXTRACTION_ATTEMPTS = 2


def retry_budget(bucket: str) -> tuple:
    """``(max_attempts, allow_larger_token_retry)`` for a parse-failure bucket."""
    return _RETRY_BUDGET.get(bucket, (1, False))


#: Where a bucket lands once its attempts are spent. The two response-shape
#: faults are the engine's own contract breaking and a human must see them. The
#: rest are citation-level uncertainty or a defect we own and have fixed, and
#: neither is a human's problem.
_BUCKET_TERMINAL = {
    PARSE_EMPTY_RESPONSE: (OUTCOME_HUMAN_REVIEW, REASON_EMPTY_MODEL_RESPONSE),
    PARSE_MALFORMED_RESPONSE: (OUTCOME_HUMAN_REVIEW,
                               REASON_MALFORMED_MODEL_RESPONSE),
    # A wrong key set is a structurally valid response. We refuse to repair the
    # JSON and we refuse to call the citation judged: UNJUDGEABLE, per the
    # semantic-uncertainty rule, not a human queue item.
    PARSE_SCHEMA_KEY_MISMATCH: (OUTCOME_UNJUDGEABLE, "schema_key_mismatch_after_retry"),
    PARSE_INVALID_SCHEMA: (OUTCOME_UNJUDGEABLE, "invalid_schema_response"),
    PARSE_SEMANTIC_VALIDATION: (OUTCOME_UNJUDGEABLE, "semantic_validation_failure"),
    # Ours, deterministic, and repaired at the representation. If it still fires
    # the row is unjudged, not a human's to adjudicate.
    PARSE_CONTENT_SHA_MISMATCH: (OUTCOME_UNJUDGEABLE,
                                 "evidence_representation_defect"),
    PARSE_UNCLASSIFIED: (OUTCOME_HUMAN_REVIEW, REASON_SCHEMA_CONTRACT_FAILURE),
}


def parse_failure_terminal(bucket: str) -> tuple:
    """``(terminal_outcome, reason)`` for an EXHAUSTED parse-failure bucket."""
    return _BUCKET_TERMINAL.get(
        bucket, (OUTCOME_HUMAN_REVIEW, REASON_SCHEMA_CONTRACT_FAILURE))


# --- the router -----------------------------------------------------------

def _findings(record) -> list:
    raw = record.get("findings") or []
    return [f for f in FINDING_OUTCOMES if f in set(raw)]


def resolve(record) -> tuple:
    """``(terminal_outcome, reason)`` for one durable record.

    Pure, total, and ORDERED: the first branch that matches wins, and the order
    encodes the priority the rules require. Read it top to bottom as the routing
    contract itself.
    """
    if not isinstance(record, dict) or not str(
            record.get("citation_id") or "").strip():
        return OUTCOME_HUMAN_REVIEW, REASON_CORRUPT_SOURCE_RECORD

    # 1. AN ESTABLISHED FINDING OUTRANKS EVERYTHING. A pair that reached a
    #    taxonomy verdict has been judged; a later stage failure does not
    #    retroactively unjudge it, and the finding is what the annotation queue
    #    exists to gold-label.
    findings = _findings(record)
    if findings:
        return findings[0], "taxonomy_finding"

    # 2. BROKEN INPUT BEFORE ANYTHING ELSE. A marker-only, bled, or absent citing
    #    sentence means the extractor was never shown the right sentence, so
    #    "no claims" is a statement about our parser, not about the citation.
    #    Checked before the parse buckets because an input we never read
    #    correctly makes every downstream signal meaningless.
    status = record.get("claim_input_status") or claim_input_status(
        record.get("citing_sentence"))
    if status == CLAIM_INPUT_EMPTY:
        # NO CITANCE, AND NOTHING IN THE DOCUMENT CITES IT -> out of scope.
        # `is False` exactly: None means no document was walked (a Reference
        # built outside the parser), and an unexamined reference must never be
        # silently excluded on a field nobody set.
        if record.get("cited_in_body") is False:
            return OUTCOME_UNJUDGEABLE, REASON_UNCITED_REFERENCE
        # No citance, but a marker DOES point at this reference. That is a
        # PARSER DEFECT -- the marker exists and the citance walk did not reach
        # it -- and it is a real review item, not a scope exclusion.
        return OUTCOME_HUMAN_REVIEW, REASON_EMPTY_CLAIM_INPUT
    if status in BROKEN_CLAIM_INPUT:
        return OUTCOME_HUMAN_REVIEW, REASON_MALFORMED_CLAIM_INPUT

    # 3. NO RETRIEVABLE CITED TEXT -> UNJUDGEABLE, BEFORE ANY MODEL CALL.
    #    Band 1 identifying the paper is NOT the same fact as the paper's text
    #    being readable. PMC8544026:B1 is an IEEE conference paper matched to
    #    Crossref DOI 10.1109/icra.2016.7487344 at 100% title similarity, HIGH
    #    confidence -- and it has no PubMed record and no PMC full text. The
    #    identity is certain and there is nothing to judge the claim AGAINST.
    #    Running a discriminator here would ask a model to compare a claim with
    #    an empty evidence set, and an F3-F7 label produced that way is a
    #    confident answer about nothing.
    if record.get("cited_text_retrievable") is False:
        return OUTCOME_UNJUDGEABLE, REASON_CITED_TEXT_UNAVAILABLE

    # 4. EXCLUDED BEFORE THE BAND EVER RAN. Band 1 asserted a fault, reached no
    #    verdict, or the reference carried no cited work -- so the F3-F7 question
    #    was never asked of it. That is UNJUDGEABLE, and it must be caught HERE:
    #    the no-claims branch below would otherwise read an excluded record's
    #    empty `atomic_claims` as a failed extraction and queue it for a human,
    #    who has nothing to adjudicate because nothing was ever extracted.
    disposition = str(record.get("disposition") or "")
    if disposition.startswith("excluded_"):
        return OUTCOME_UNJUDGEABLE, f"preband_{disposition}"

    # 5. A PARSE FAILURE THE RETRY BUDGET COULD NOT CLEAR, routed by which of the
    #    six faults it actually was.
    unresolved = record.get("parse_failure")
    if isinstance(unresolved, dict) and not unresolved.get("resolved"):
        return parse_failure_terminal(
            unresolved.get("bucket") or classify_parse_failure(
                unresolved.get("message")))

    # 6. NO CLAIMS ON PROSE. The extractor read a real sentence, was retried, and
    #    still found nothing. NONE only when the sentence genuinely asserts
    #    nothing empirical -- and that has to be ATTESTED by the stage that
    #    decided it, never inferred from the empty list, which is exactly how the
    #    old path turned 179 broken parses into "no claim".
    if not (record.get("atomic_claims") or []):
        attested = record.get("claim_extraction_asserts_nothing")
        retried = int((record.get("claim_extraction_attempts") or 0)) >= 2
        if attested is True and retried:
            return OUTCOME_NONE, "prose_sentence_asserts_nothing_empirical"
        return OUTCOME_HUMAN_REVIEW, REASON_CLAIM_EXTRACTION_EMPTY

    # 7. A PER-PAIR STAGE FAILURE. Claims and evidence survive on the record
    #    (that is the point), but a stage that did not run cannot contribute to a
    #    clean bill of health.
    if record.get("stage_failures"):
        return OUTCOME_UNJUDGEABLE, "stage_failure"

    # 8. SEMANTIC UNCERTAINTY WITH A STRUCTURALLY VALID RESPONSE. Insufficient
    #    evidence, ambiguous evidence, unjudgeable strength, unjudgeable
    #    provenance -- the pipeline worked and the answer is "we cannot say".
    if record.get("hold_reasons"):
        return OUTCOME_UNJUDGEABLE, "held_" + str(
            record.get("disposition") or "unspecified")

    # 9. Everything ran, nothing was found, and the input was real prose with no
    #    leading marker run.
    return OUTCOME_NONE, "all_wired_stages_ran_no_fault"


def is_human_review(outcome: str) -> bool:
    return outcome == OUTCOME_HUMAN_REVIEW


def carries_finding(outcome: str) -> bool:
    return outcome in set(FINDING_OUTCOMES)


def assert_valid(outcome: str, reason: str) -> None:
    """Fail closed on an outcome or reason outside the closed vocabulary."""
    if outcome not in TERMINAL_OUTCOMES:
        raise ValueError(
            f"terminal_outcome {outcome!r} is outside the closed vocabulary "
            f"{sorted(TERMINAL_OUTCOMES)}")
    if outcome == OUTCOME_HUMAN_REVIEW and reason not in HUMAN_REVIEW_REASONS:
        raise ValueError(
            f"HUMAN_REVIEW_REQUIRED reason {reason!r} is outside the closed "
            f"vocabulary {sorted(HUMAN_REVIEW_REASONS)}")
