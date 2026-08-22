"""Typed, injectable batched abstract screening for F5.

The screen is an optional cost gate, never an evidence adjudicator.  Only an
explicit ``clear_mismatch`` may avoid deep comparison, and the detector retains
that candidate as unassessable so the screen can never manufacture a confident
negative.  Malformed batches are rejected here; the detector responds by sending
every candidate to deep comparison.
"""
from __future__ import annotations

from dataclasses import dataclass


CANDIDATE_SCREEN_VERSION = "f5_candidate_screen_v1"
SCREEN_DECISIONS = frozenset({"plausible", "clear_mismatch", "uncertain"})
CLAIM_RELEVANCE = frozenset({"match", "mismatch", "uncertain"})
POSSIBLE_RELATIONS = frozenset(
    {"opposes", "confirms", "mixed", "neutral", "uncertain"})


def _sha256(value: str, field: str) -> str:
    if (not isinstance(value, str) or len(value) != 64
            or any(char not in "0123456789abcdef" for char in value)):
        raise ValueError(f"{field} must be a lowercase SHA-256 hex digest")
    return value


@dataclass(frozen=True)
class CandidateScreenDecision:
    candidate_work_id: str
    decision: str
    claim_relevance: str
    possible_relation: str
    missing_facts: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.candidate_work_id, str) or not self.candidate_work_id.strip():
            raise ValueError("candidate_work_id must be a nonblank string")
        if self.decision not in SCREEN_DECISIONS:
            raise ValueError("candidate screen decision is off-enum")
        if self.claim_relevance not in CLAIM_RELEVANCE:
            raise ValueError("candidate screen claim_relevance is off-enum")
        if self.possible_relation not in POSSIBLE_RELATIONS:
            raise ValueError("candidate screen possible_relation is off-enum")
        if (not isinstance(self.missing_facts, tuple)
                or any(not isinstance(value, str) or not value.strip()
                       for value in self.missing_facts)):
            raise ValueError("candidate screen missing_facts must be nonblank strings")
        if (self.decision == "clear_mismatch"
                and (self.claim_relevance != "mismatch"
                     or self.possible_relation != "neutral"
                     or self.missing_facts)):
            raise ValueError(
                "clear_mismatch requires mismatch relevance, neutral relation, "
                "and no missing facts")


@dataclass(frozen=True)
class CandidateScreenBatch:
    decisions: tuple[CandidateScreenDecision, ...]
    prompt_sha256: str
    response_sha256: str
    version: str = CANDIDATE_SCREEN_VERSION

    def __post_init__(self) -> None:
        if self.version != CANDIDATE_SCREEN_VERSION:
            raise ValueError("candidate screen version is unsupported")
        if (not isinstance(self.decisions, tuple)
                or any(not isinstance(row, CandidateScreenDecision)
                       for row in self.decisions)):
            raise ValueError(
                "candidate screen decisions must be CandidateScreenDecision rows")
        identifiers = [row.candidate_work_id for row in self.decisions]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("candidate screen returned duplicate candidate IDs")
        _sha256(self.prompt_sha256, "prompt_sha256")
        _sha256(self.response_sha256, "response_sha256")


def validate_candidate_screen_batch(
        batch: CandidateScreenBatch, expected_candidate_ids) -> dict[str, CandidateScreenDecision]:
    """Validate an ID-keyed batch and return its exact decision mapping."""
    if not isinstance(batch, CandidateScreenBatch):
        raise ValueError("candidate screen must return CandidateScreenBatch")
    expected = tuple(str(value) for value in expected_candidate_ids)
    if len(expected) != len(set(expected)):
        raise ValueError("candidate screen input IDs must be unique")
    observed = tuple(row.candidate_work_id for row in batch.decisions)
    if set(observed) != set(expected) or len(observed) != len(expected):
        raise ValueError("candidate screen IDs do not exactly match candidate IDs")
    return {row.candidate_work_id: row for row in batch.decisions}


# --------------------------------------------------------------------------
# The prompt, the strict parser, and the seam factory.
#
# WHAT WAS HERE BEFORE, AND WHY IT MATTERED. Everything above is contract: the
# two frozen dataclasses and the ID-join validator. There was NO prompt and no
# model-calling implementation, ``F5Policy.candidate_screen_enabled`` defaulted
# to False, and every live run recorded ``screen_decision: not_performed``. So
# the screen's whole economic argument -- that a cheap batched triage lets the
# candidate cap rise without cost rising with it -- had never been tested, and
# the pass rate it turns on was a guess.
#
# ONE CALL FOR THE WHOLE BATCH. ``TemporalAssessorRun`` books exactly
# ``abstract_screen_calls = 1`` per claim and hands over every structurally
# admissible candidate at once. Screening per candidate would spend one model
# call to decide whether to spend one model call, which is the entire saving.
#
# HANDLES, NOT PMIDS, IN THE REPLY. ``validate_candidate_screen_batch`` demands
# the returned ids match the input ids EXACTLY, and a batch that misses on one
# id is discarded whole -- every candidate then goes to deep comparison and the
# screen call is pure loss. Asking a model to echo hundreds of 8-digit PMIDs
# without a single transcription slip is the fragile way to satisfy that. So the
# prompt numbers the candidates ``c1..cN`` and the parser maps the handles back
# to work ids deterministically. The model is asked for what it can reliably
# produce; the identity join is done by code.
#
# THE SCREEN IS SHOWN THE CLAIM, NOT THE CITED PAPER. That is the seam's
# signature (``screen(claim=..., candidates=...)``) and it is not widened here.
# The prompt is therefore written for the question the screen can actually
# answer -- "could this paper bear on this claim" -- and never asks about a
# cited work it cannot see.
# --------------------------------------------------------------------------
import hashlib
import json

#: Bumped independently of :data:`CANDIDATE_SCREEN_VERSION`, which versions the
#: CONTRACT above and is stamped on every screened candidate. This versions the
#: prompt text, which is a separate artifact that will move more often.
CANDIDATE_SCREEN_PROMPT_VERSION = "f5_candidate_screen_prompt_v1"
CANDIDATE_SCREEN_PARSER_VERSION = "strict_f5_screen_handles_v1"

#: Chars per token, for budgeting only. The repo's own rough conversion; good to
#: about +/-10% and never used where an exact count matters.
_CHARS_PER_TOKEN = 3.7

#: Default per-candidate abstract budget, in characters. At the shipped
#: ``CANDIDATE_CAP`` of 50 this leaves essentially every PubMed abstract intact
#: (the median is well under 1,600 chars), so the default cap gets a full-text
#: screen and only a raised cap pays anything for the batching.
DEFAULT_ABSTRACT_CHARS = 1600

#: Ceiling on the whole rendered screen prompt. THIS IS THE REAL LIMIT ON THE
#: CANDIDATE CAP, and it is worth stating plainly: one call for the whole batch
#: means the batch has to fit in one context. 400 candidates at a full abstract
#: each is ~150K tokens, which both fits badly and costs more than the deep
#: comparisons it is meant to avoid. So the per-candidate budget SHRINKS as the
#: batch grows, the prompt says so, and the seam counts how many abstracts it
#: cut. A screen that silently truncated would let the model read absence of
#: evidence as evidence of absence.
DEFAULT_MAX_PROMPT_TOKENS = 60_000

#: Never cut below this: a title plus a sentence or two is the floor at which
#: "is this about the same question" is still answerable at all.
MIN_ABSTRACT_CHARS = 240

_SCREEN_ROW_KEYS = frozenset(
    {"candidate", "decision", "claim_relevance", "possible_relation",
     "missing_facts"})
_SCREEN_TOP_KEYS = frozenset({"screened"})


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _reject_duplicate_keys(pairs) -> dict:
    obj: dict = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError(f"duplicate JSON key: {key}")
        obj[key] = value
    return obj


def abstract_budget(candidate_count: int, *,
                    abstract_chars: int = DEFAULT_ABSTRACT_CHARS,
                    max_prompt_tokens: int = DEFAULT_MAX_PROMPT_TOKENS) -> int:
    """Per-candidate abstract character budget for a batch of this size.

    Returns ``abstract_chars`` while the whole batch fits, and shrinks toward
    :data:`MIN_ABSTRACT_CHARS` as it stops fitting. PURE, so the budget a record
    reports can be recomputed from the candidate count alone.
    """
    if candidate_count < 1:
        return abstract_chars
    instructions_chars = len(CANDIDATE_SCREEN_PROMPT)
    available = int(max_prompt_tokens * _CHARS_PER_TOKEN) - instructions_chars
    per_candidate = max(available // candidate_count, MIN_ABSTRACT_CHARS)
    return max(min(abstract_chars, per_candidate), MIN_ABSTRACT_CHARS)


def _clip(text: str, limit: int) -> "tuple[str, bool]":
    """``(text, was_cut)``, cut back to the last sentence end before ``limit``.

    Cutting mid-sentence invites the model to answer about a fragment; cutting
    at a period leaves it a whole thought. When no period is available the hard
    cut stands, and either way the caller marks the block as truncated.
    """
    clean = " ".join((text or "").split())
    if len(clean) <= limit:
        return clean, False
    window = clean[:limit]
    stop = max(window.rfind(". "), window.rfind("? "), window.rfind("! "))
    if stop >= MIN_ABSTRACT_CHARS // 2:
        return window[:stop + 1], True
    return window.rstrip(), True


def render_screen_prompt(claim: str, candidates, *,
                         abstract_chars: int = DEFAULT_ABSTRACT_CHARS,
                         max_prompt_tokens: int = DEFAULT_MAX_PROMPT_TOKENS
                         ) -> "tuple[str, dict[str, str], dict]":
    """``(prompt, handle -> work_id, render_stats)``.

    Substitution is by explicit REPLACE, not ``str.format``: the prompt shows the
    model literal JSON braces, and format would read them as fields and raise.
    """
    rows = tuple(candidates or ())
    limit = abstract_budget(
        len(rows), abstract_chars=abstract_chars,
        max_prompt_tokens=max_prompt_tokens)
    handles: dict[str, str] = {}
    blocks, truncated = [], 0
    for index, candidate in enumerate(rows, start=1):
        handle = f"c{index}"
        work_id = str(getattr(candidate, "id", "") or "").strip()
        if not work_id:
            raise ValueError("candidate screen received a candidate with no id")
        if work_id in handles.values():
            raise ValueError(
                f"candidate screen received work id {work_id!r} twice; the "
                "batch join is by id and one of the two would be lost")
        handles[handle] = work_id
        title = " ".join(str(getattr(candidate, "title", "") or "").split())
        abstract, was_cut = _clip(
            str(getattr(candidate, "abstract", "") or ""), limit)
        truncated += 1 if was_cut else 0
        lines = [f"[{handle}] published {getattr(candidate, 'pub_date', '') or 'unknown'}",
                 f"  title: {title or '(no title available)'}"]
        if abstract:
            lines.append(f"  abstract: {abstract}"
                         + ("  [ABSTRACT TRUNCATED -- more text exists]"
                            if was_cut else ""))
        else:
            lines.append("  abstract: (none available)")
        blocks.append("\n".join(lines))

    prompt = (CANDIDATE_SCREEN_PROMPT
              .replace("<<CLAIM_TEXT>>", claim or "")
              .replace("<<CANDIDATE_COUNT>>", str(len(rows)))
              .replace("<<CANDIDATE_HANDLES>>",
                       ", ".join(handles) or "(none)")
              .replace("<<CANDIDATES>>", "\n\n".join(blocks)))
    stats = {
        "candidates": len(rows),
        "abstract_chars_per_candidate": limit,
        "abstracts_truncated": truncated,
        "prompt_chars": len(prompt),
        "prompt_version": CANDIDATE_SCREEN_PROMPT_VERSION,
        "parser_version": CANDIDATE_SCREEN_PARSER_VERSION,
    }
    return prompt, handles, stats


def parse_screen_batch(text: str, handles: "dict[str, str]", *,
                       prompt: str) -> CandidateScreenBatch:
    """Strict-JSON reply -> a validated :class:`CandidateScreenBatch`.

    Fails closed with ``ValueError`` on anything off-contract. The detector
    catches that and sends every candidate to deep comparison, so a malformed
    reply costs the screen call and nothing else -- never a wrong answer.
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError("empty candidate screen output")
    try:
        obj = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"candidate screen reply is not one bare JSON object: {exc}") from exc
    if not isinstance(obj, dict):
        raise ValueError(
            f"candidate screen top-level JSON must be an object: {type(obj).__name__}")
    keys = frozenset(obj)
    if keys != _SCREEN_TOP_KEYS:
        raise ValueError(
            "candidate screen keys mismatch: "
            f"missing={sorted(_SCREEN_TOP_KEYS - keys)} "
            f"extra={sorted(keys - _SCREEN_TOP_KEYS)}")
    rows = obj["screened"]
    if not isinstance(rows, list):
        raise ValueError("candidate screen 'screened' must be a JSON array")

    decisions, seen = [], set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("candidate screen row must be a JSON object")
        row_keys = frozenset(row)
        if row_keys != _SCREEN_ROW_KEYS:
            raise ValueError(
                "candidate screen row keys mismatch: "
                f"missing={sorted(_SCREEN_ROW_KEYS - row_keys)} "
                f"extra={sorted(row_keys - _SCREEN_ROW_KEYS)}")
        handle = row["candidate"]
        if not isinstance(handle, str) or handle not in handles:
            raise ValueError(
                f"candidate screen named an unknown candidate handle {handle!r}")
        if handle in seen:
            raise ValueError(
                f"candidate screen returned handle {handle!r} twice")
        seen.add(handle)
        facts = row["missing_facts"]
        if facts is None:
            facts = []
        if not isinstance(facts, list) or any(
                not isinstance(value, str) for value in facts):
            raise ValueError("candidate screen missing_facts must be a list of strings")
        decisions.append(CandidateScreenDecision(
            candidate_work_id=handles[handle],
            decision=row["decision"],
            claim_relevance=row["claim_relevance"],
            possible_relation=row["possible_relation"],
            missing_facts=tuple(
                value.strip() for value in facts if value and value.strip())))
    return CandidateScreenBatch(
        decisions=tuple(decisions),
        prompt_sha256=_sha256_text(prompt),
        response_sha256=_sha256_text(text))


def make_candidate_screen(complete, *,
                          abstract_chars: int = DEFAULT_ABSTRACT_CHARS,
                          max_prompt_tokens: int = DEFAULT_MAX_PROMPT_TOKENS,
                          render_log=None):
    """``screen(claim=..., candidates=...) -> CandidateScreenBatch``.

    ``complete(prompt) -> str`` is injected, so this module still makes no
    network or model call of its own and stays a testable leaf. The returned
    callable carries ``.calls``, ``.prompt_version`` and ``.render_log`` so a run
    can report what the screen actually did rather than what it was configured
    to do -- including how much of each abstract it was able to show.
    """
    if not callable(complete):
        raise ValueError("candidate screen needs a callable completion seam")
    log = render_log if render_log is not None else []

    def screen(*, claim: str, candidates) -> CandidateScreenBatch:
        screen.calls += 1
        prompt, handles, stats = render_screen_prompt(
            claim, candidates, abstract_chars=abstract_chars,
            max_prompt_tokens=max_prompt_tokens)
        log.append(stats)
        raw = complete(prompt)
        batch = parse_screen_batch(raw, handles, prompt=prompt)
        # Validate here as well as in the detector. The detector's failure mode
        # is to discard and fall back; catching a mismatched batch at its source
        # means the seam's own tests can name the defect.
        validate_candidate_screen_batch(batch, tuple(handles.values()))
        return batch

    screen.calls = 0
    screen.prompt_version = CANDIDATE_SCREEN_PROMPT_VERSION
    screen.parser_version = CANDIDATE_SCREEN_PARSER_VERSION
    screen.contract_version = CANDIDATE_SCREEN_VERSION
    screen.render_log = log
    return screen


CANDIDATE_SCREEN_PROMPT = """\
You are triaging a list of candidate papers before an expensive detailed
comparison. Your job is NOT to decide anything about the science. Your job is to
say, for each candidate, whether a careful reader would need to read it in full
to know whether it bears on one specific claim.

THE CLAIM UNDER ASSESSMENT:
<<CLAIM_TEXT>>

WHAT YOU ARE AND ARE NOT DECIDING. A later paper "bears on" this claim if it
reports on the same question -- broadly the same intervention or exposure, the
same kind of outcome, and a population the claim would be taken to cover. You
are NOT deciding whether it agrees with the claim, whether it is good evidence,
or whether it supersedes anything. Those questions are settled downstream by a
reader who gets the full text; you only decide who gets read.

THE ASYMMETRY THAT SHOULD GOVERN EVERY BORDERLINE CALL. A candidate you mark
"plausible" or "uncertain" is read in full and costs a little money. A candidate
you mark "clear_mismatch" is NEVER read, and if it was in fact the paper that
overturns the claim, nothing downstream can recover it. The two errors are not
symmetric and you should not treat them as if they were. Mark "clear_mismatch"
only when a competent reader would agree, from the title and abstract alone,
that this paper is about a different question -- a different disease, a different
intervention, a different field, a methods or protocol paper with no findings, an
unrelated organism. When you find yourself reasoning about whether the effect
might still apply, that reasoning is the signal that the answer is "uncertain".

ABSTRACTS MAY BE CUT. Where a block says [ABSTRACT TRUNCATED] you are seeing the
beginning of the abstract and not the rest. Missing text is missing, not absent:
never treat a truncated abstract as evidence that the paper lacks something. If
what you can see does not settle it, the answer is "uncertain".

THE <<CANDIDATE_COUNT>> CANDIDATES:

<<CANDIDATES>>

Return ONLY one JSON object, with exactly one key:

  {"screened": [ ...one row per candidate... ]}

Each row is an object with EXACTLY these five keys and no others:

  candidate          the handle exactly as given: one of <<CANDIDATE_HANDLES>>

  decision           plausible       could bear on the claim; read it in full
                     uncertain       cannot tell from what is shown; read it
                     clear_mismatch  plainly a different question; do not read

  claim_relevance    match      it is about this claim's question
                     mismatch   it is about a different question
                     uncertain  what is shown does not settle it

  possible_relation  opposes    it may point against the claim
                     confirms   it may support the claim
                     mixed      it may report results on both sides
                     neutral    it bears on the claim's question neither way
                     uncertain  what is shown does not settle the direction

  missing_facts      a JSON list of short strings naming what a reader would
                     still need that the shown text does not give (for example
                     "population age range", "outcome definition"). Use [] when
                     nothing important is missing. Keep it to at most three
                     items; this is a triage note, not an analysis.

FOUR HARD RULES. These are enforced by a strict parser, and a reply that breaks
one is REJECTED WHOLE -- not just that row. The entire screen is then discarded
and every candidate is read in full, so breaking a rule wastes this call and
achieves nothing. Breaking a rule is strictly worse than abstaining.

  1. EXACTLY ONE ROW PER CANDIDATE, for every handle listed above, with no
     duplicates and no handles that were not listed. <<CANDIDATE_COUNT>> handles
     in, <<CANDIDATE_COUNT>> rows out.

  2. "clear_mismatch" REQUIRES claim_relevance = "mismatch" AND
     possible_relation = "neutral" AND missing_facts = []. That combination is
     the only one the contract accepts, and it says exactly what
     "clear_mismatch" means: the paper is about something else, so it points
     neither way, and no further fact would change that. If you want to record a
     possible relation, or name a missing fact, or hedge the relevance, then this
     is NOT a clear mismatch -- use "uncertain".

  3. Use the listed vocabulary verbatim. No other values, no null, no empty
     strings, no added prose fields.

  4. One bare JSON object. No prose before or after it, no code fences, no
     second object, and no repeated keys.
"""
