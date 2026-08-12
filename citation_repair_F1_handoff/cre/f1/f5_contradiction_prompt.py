"""cre/f1/f5_contradiction_prompt.py -- the F5 contradiction judge's prompt.

F5 was the only discriminator in the ladder carrying a named prompt version
(``f5_contradiction_v1``) and NO prompt text anywhere in either repo. This module
supplies it, and supplies it in the shape DEC-047 established for the coverage
judge.

WHY SELECTION, NOT GENERATION. ``f5_supersession._assess_candidate`` verifies both
spans verbatim against ``_source_text`` and turns a miss into ``span_unverifiable``
-> UNASSESSABLE. Asking a model to reproduce source text verbatim is the outlier
design and it fails: FullCite measured prompt-based verbatim generation against
post-hoc alignment at Snippet-F1 12.80% -> 61.87%, and the coverage judge shed the
same defect in 324e430. So the judge POINTS: every populated ``ComparabilitySource``
field is cut into addressable ``s1..sN`` units by :mod:`sentence_spans`, the ids are
rendered into the prompt, and :func:`resolve_span` reads the text back out. A
selected span is verbatim BY CONSTRUCTION, so the module's own check cannot fail on
it. Quoted prose is still accepted and aligned at
:data:`ALIGNMENT_JACCARD_FLOOR`; below the floor nothing is claimed.

A span that cannot be resolved is a RECORDED MISS, not a quarantine and not an
exception -- the DEC-047 rule, and the reason this module returns a source kind
rather than raising.

WHY FOUR STEPS. Xie et al. 2024 (JAMIA, PMID 38758667) is the best credible
contradiction result in the literature -- F1 0.799 (R 0.903 / P 0.716) on
ManConCorpus's 1,040 real pairs -- and it decomposes: synthesise the question,
extract each paper's assertion, summarise consensus/controversy, then judge. Single
verdict prompts underperform it. The seam stays ONE call returning ONE object; the
decomposition lives inside the prompt.

WHY AN ABSTAIN OPTION. Same paper: ternary assertions score R 0.903, forcing a
binary decision drops recall to 0.834. That ~7 points is why ``uncertain`` /
``unclear`` are first-class instructions here rather than a fallback the model finds
on its own.

WHY NO SYNTHETIC EXAMPLES. SciFact's REFUTES is an expert flipping a claim's
direction; COVID-Fact's is one word swapped by a masked LM (MultiVerS's own
inspection found that genuinely refuted only about a third of the time); SCitance
negates via GPT-3.5 "changing as few words as possible". All three teach lexical
polarity flipping. Real supersession is two different effect estimates in two
different populations, so the worked guidance here describes real shapes and this
module ships no fabricated pair as an example.
"""
from __future__ import annotations

from . import sentence_spans as ss
from .f5_supersession import _SCOPE_MISMATCH_AXES as _f5_axes

#: Bumped off ``f5_contradiction_v1``: the contract gained ``scope_mismatch_axis``,
#: and a key-set change is exactly what the version exists to signal.
CONTRADICTION_PROMPT_VERSION = "f5_contradiction_v2"

#: DEC-022: the parser version moves independently of the prompt version. Both are
#: stamped on every record.
RESPONSE_PARSER_VERSION = "strict_f5_contradiction_spanids_v1"

#: The ``ComparabilitySource`` fields that carry evidence text, in the order
#: ``f5_supersession._source_text`` concatenates them -- so what the judge is shown
#: and what the verbatim check reads are the same text in the same order.
SOURCE_LABELS = ("abstract", "methods", "results", "protocol", "registry_record")

SPAN_SOURCE_SELECTED = "selected"      # the judge named ids; we read them out
SPAN_SOURCE_ALIGNED = "aligned"        # the judge quoted prose; we matched it
SPAN_SOURCE_UNRESOLVED = "unresolved"  # neither; a recorded miss

#: Shared with the coverage judge (DEC-047).
ALIGNMENT_JACCARD_FLOOR = 0.7

#: The closed scope-mismatch checklist, re-exported from the contract module so the
#: prompt and the strict parser can never disagree about what is on-list.
#: Rosemblat et al. 2019 (PMID 31473364) funnelled 2,236 candidate contradictory
#: pairs down to 58 apparent and 4 genuine, with 42.6% lost to generic subjects --
#: recording WHICH axis fired is what makes that funnel auditable.
SCOPE_MISMATCH_AXES = _f5_axes


def source_sections(src) -> "list[dict]":
    """``ComparabilitySource`` -> the ``[{label, text}]`` shape the segmenter takes.

    Only populated fields appear: an empty field has no id space, and rendering a
    blank block would only invite a span that cannot resolve."""
    out = []
    for label in SOURCE_LABELS:
        text = getattr(src, label, None)
        if isinstance(text, str) and text.strip():
            out.append({"label": label, "text": text})
    return out


def source_units(src) -> "dict[str, list[dict]]":
    """``{label: [{id, text}, ...]}`` for one ``ComparabilitySource``. PURE."""
    return ss.segment_sections(source_sections(src))


def render_comparability_source(src) -> str:
    """One ``[label]`` block per populated field, each sentence prefixed with its id.

    Document order is the contract, not decoration: a span cites ``(label, id)``, so
    both the block a sentence sits in and its position within that block have to be
    stable. Deterministic by construction -- the segmenter is pure and the label
    order is fixed by :data:`SOURCE_LABELS`."""
    units_by_label = source_units(src)
    blocks = []
    for label in SOURCE_LABELS:
        units = units_by_label.get(label) or []
        if not units:
            continue
        lines = [f"  {unit['id']}  {unit['text']}" for unit in units]
        blocks.append(f"[{label}]\n" + "\n".join(lines))
    return "\n\n".join(blocks)


def resolve_span(entry, units_by_label) -> "tuple[str, str]":
    """``(text, span_source)`` for one span entry from the judge's reply.

    ``{"label": L, "sentence_ids": [...]}`` is read straight out of the id space, so
    the text returned is verbatim by construction. ``{"label": L, "text": ...}`` is
    aligned by best word-level Jaccard and accepted at or above the floor.

    Anything that does not land -- unknown label, unknown id, prose below the floor
    -- returns ``("", UNRESOLVED)``. It NEVER raises: DEC-047 makes an unresolvable
    span a recorded miss, and F5's caller keeps the candidate in
    ``candidate_assessments`` rather than quarantining the reply. This is the one
    place where F5 deliberately differs from ``coverage_prompts_v3._resolve_spans``,
    which raises on an unknown id; there the reply is uninterpretable, here a miss
    is a measurable outcome of a discovery run."""
    if not isinstance(entry, dict):
        return "", SPAN_SOURCE_UNRESOLVED
    label = str(entry.get("label") or "")
    units = (units_by_label or {}).get(label) or []
    if not units:
        return "", SPAN_SOURCE_UNRESOLVED

    ids = entry.get("sentence_ids")
    if ids:
        by_id = {unit["id"]: unit["text"] for unit in units}
        texts = [by_id[sid] for sid in ids if sid in by_id]
        if len(texts) != len(list(ids)) or not texts:
            return "", SPAN_SOURCE_UNRESOLVED
        return " ".join(texts), SPAN_SOURCE_SELECTED

    quoted = entry.get("text")
    if not isinstance(quoted, str) or not quoted.strip():
        return "", SPAN_SOURCE_UNRESOLVED
    unit, score = ss.best_alignment(quoted, units)
    if unit is None or score < ALIGNMENT_JACCARD_FLOOR:
        return "", SPAN_SOURCE_UNRESOLVED
    return unit["text"], SPAN_SOURCE_ALIGNED


F5_CONTRADICTION_PROMPT = """\
You are assessing whether a LATER scientific paper directly contradicts a specific
finding in an EARLIER paper that cited it. Work through four steps in order, then
answer.

STEP 1 -- state the research question both papers are addressing, in one sentence.
If they are not addressing the same question, say so; that is the answer to most of
these pairs.

STEP 2 -- extract the EARLIER paper's assertion on that question, and separately the
LATER paper's assertion. Give each as a direction (increase / decrease / no_effect /
mixed / unclear) plus what was measured, in whom, at what dose or exposure, over what
period.

STEP 3 -- decide whether the two assertions are ABOUT THE SAME THING. This is the
step that decides most cases, and getting it wrong is the dominant failure mode in
this task. Two findings can look opposed and both be true because they differ on one
of these axes:

  species_or_strain          different organism, strain, or cell line
  population_subgroup        different patients, ages, severities, comorbidities
  dose_or_duration           different dose, exposure level, or follow-up length
  route_or_administration    different route, formulation, or delivery
  endpoint_definition        different outcome, measure, threshold, or timepoint
  assay_or_study_design      different assay, instrument, model, or design
  clinical_setting           different care setting, country, or era of practice
  time_period_new_knowledge  the later work had knowledge unavailable earlier
  endogenous_vs_exogenous    naturally occurring versus administered

Name the axis that separates them if one does. Use "none" only when you have checked
the list and the two assertions really are about the same thing. Use "unclear" when
the sources do not tell you enough to decide -- that is a genuine answer here, not a
failure, and it is more useful than a guess.

STEP 4 -- only if STEP 3 found no separating axis, decide whether the later assertion
DIRECTLY contradicts the earlier one: same question, same scope, opposite direction.
A later paper that is merely different, larger, better, or more recent does NOT
contradict. A later paper that refines a magnitude without reversing a direction does
NOT contradict.

ABSTAINING IS A FIRST-CLASS ANSWER, and it is measurably worth using. Where the
evidence does not settle it, use "uncertain" for claim_match and outcome_relation,
and "unclear" for population_relation and scope_mismatch_axis. Do not force a
decision to seem decisive; an abstention is recorded and reviewed by a human, a
wrong confident answer is not caught.

EVIDENCE SPANS -- POINT, DO NOT RETYPE. Each source below is shown as labelled blocks
with numbered sentences. Cite evidence by naming the label and the sentence ids, like
{"label": "results", "sentence_ids": ["s2"]}. Do not retype the sentence. If the
passage you want spans two consecutive sentences, name both ids. Only if no single
sentence carries it may you quote the passage as {"label": ..., "text": ...}.

CITED (earlier) WORK SOURCE:
{cited_source}

CANDIDATE (later) WORK SOURCE:
{candidate_source}

THE CITED FINDING UNDER ASSESSMENT:
{claim_text}

Return ONLY one JSON object with exactly these keys and no others:

  directional_contradiction   true | false   (a real JSON boolean)

  claim_match      match      the two papers are asserting about the SAME claim
                   mismatch   they are asserting about different claims
                   uncertain  the sources do not settle it

  outcome_relation same       the two assertions are the same outcome measure
                   not_same   they are different outcome measures
                   uncertain  the sources do not settle it

  population_relation
      equivalent    the later population is the same as the cited one
      encompassing_direct
                    the later population contains the cited one AND reports the
                    cited subgroup directly
      encompassing_without_qualifying_direct_evidence
                    it contains the cited one but does NOT report that subgroup
                    separately, so the cited claim is not directly addressed
      narrower      the later population is a subset of the cited one
      disjoint      the two populations do not overlap
      unclear       the sources do not settle it

  cited_direction              increase | decrease | no_effect | mixed | unclear
  candidate_direction          increase | decrease | no_effect | mixed | unclear
  magnitude                    large | moderate | small | none | unclear
  scope_mismatch_axis          one value from the STEP 3 list, or none, or unclear
  cited_finding_span           {"label": ..., "sentence_ids": [...]}
  candidate_contradiction_span {"label": ..., "sentence_ids": [...]}
  confidence                   a number between 0 and 1

No prose outside the object, and no second object.
"""
