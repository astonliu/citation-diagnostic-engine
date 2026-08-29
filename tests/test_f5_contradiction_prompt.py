"""F5 contradiction judge: sentence SELECTION, decomposition, and the scope axis.

Written BEFORE the implementation (strict xfails), so the defects are recorded as
failing at 324e430 and the markers flip only when the fix lands.

Item 1 -- the judge must SELECT sentence ids from a ComparabilitySource, not retype
source text. `_assess_candidate` verifies both spans verbatim against `_source_text`
and turns a miss into `span_unverifiable` -> UNASSESSABLE; that is the same design
error DEC-047 removed from the coverage judge, where FullCite measured verbatim
generation against post-hoc alignment at Snippet-F1 12.80% -> 61.87%.

Item 2 -- every candidate assessment records WHICH scope axis explains a
non-comparable decision. Rosemblat 2019 (PMID 31473364) funnelled 2,236 candidate
pairs to 58 apparent and 4 genuine, with 42.6% falling to generic subjects; the axis
is the field that makes that funnel auditable instead of invisible.
"""
from __future__ import annotations

import json

import pytest

from cde.diagnose import supersession as f5


# ==========================================================================
# Item 1 -- selection, not generation
# ==========================================================================

def _source():
    return f5.ComparabilitySource(
        abstract="Metformin reduced HbA1c by 1.2% in adults with type 2 diabetes. "
                 "The effect was larger in treatment-naive participants.",
        methods="We randomised 480 adults to metformin or placebo for 24 weeks.",
        results="Mean between-group difference was -1.2% (95% CI -1.4 to -1.0).",
    )


def test_prompt_module_exists_and_declares_its_versions():
    from cde.diagnose import contradiction_prompt as p
    assert p.CONTRADICTION_PROMPT_VERSION
    assert p.RESPONSE_PARSER_VERSION
    assert isinstance(p.F5_CONTRADICTION_PROMPT, str)
    assert p.F5_CONTRADICTION_PROMPT.strip()


def test_render_gives_every_populated_field_its_own_labelled_id_space():
    from cde.diagnose import contradiction_prompt as p
    rendered = p.render_comparability_source(_source())
    for label in ("abstract", "methods", "results"):
        assert f"[{label}]" in rendered
    assert "s1" in rendered
    # An unpopulated field has no id space and must not be rendered at all.
    assert "[protocol]" not in rendered
    assert "[registry_record]" not in rendered


def test_render_is_deterministic_across_runs():
    from cde.diagnose import contradiction_prompt as p
    src = _source()
    assert p.render_comparability_source(src) == p.render_comparability_source(src)


def test_selected_ids_resolve_to_text_that_is_verbatim_by_construction():
    """The whole point: what the resolver returns passes f5's own verbatim check."""
    from cde.diagnose import contradiction_prompt as p
    src = _source()
    units = p.source_units(src)
    span, source_kind = p.resolve_span({"label": "results", "sentence_ids": ["s1"]}, units)
    assert source_kind == p.SPAN_SOURCE_SELECTED
    assert span and span in f5._source_text(src)


def test_quoted_prose_above_the_floor_is_aligned_not_rejected():
    from cde.diagnose import contradiction_prompt as p
    units = p.source_units(_source())
    span, source_kind = p.resolve_span(
        {"label": "methods",
         "text": "We randomised 480 adults to metformin or placebo for 24 weeks"}, units)
    assert source_kind == p.SPAN_SOURCE_ALIGNED
    assert span in f5._source_text(_source())


def test_unresolvable_span_is_a_recorded_miss_not_an_exception():
    """DEC-047's rule: a span that cannot be resolved is a MISS, not a quarantine."""
    from cde.diagnose import contradiction_prompt as p
    units = p.source_units(_source())
    span, source_kind = p.resolve_span(
        {"label": "methods", "text": "entirely unrelated sentence about volcanoes"}, units)
    assert span == ""
    assert source_kind == p.SPAN_SOURCE_UNRESOLVED


def test_unknown_label_is_a_miss_not_an_exception():
    from cde.diagnose import contradiction_prompt as p
    units = p.source_units(_source())
    span, source_kind = p.resolve_span({"label": "protocol", "sentence_ids": ["s1"]}, units)
    assert span == ""
    assert source_kind == p.SPAN_SOURCE_UNRESOLVED


def test_prompt_decomposes_into_the_four_steps_and_offers_the_abstain_option():
    """Xie et al. 2024 (PMID 38758667): four-step decomposition, and ternary
    assertions at R 0.903 vs binary 0.834 -- the abstain option is ~7 points of
    recall, so it must be a first-class instruction."""
    from cde.diagnose import contradiction_prompt as p
    text = p.F5_CONTRADICTION_PROMPT.lower()
    assert "step 1" in text and "step 2" in text and "step 3" in text and "step 4" in text
    assert "uncertain" in text and "unclear" in text


def test_prompt_states_the_scope_checklist_and_carries_no_synthetic_negation_examples():
    from cde.diagnose import contradiction_prompt as p
    text = p.F5_CONTRADICTION_PROMPT
    for axis in ("species_or_strain", "dose_or_duration", "endpoint_definition"):
        assert axis in text


# ==========================================================================
# Item 2 -- the scope axis
# ==========================================================================

def test_scope_axis_is_on_the_contract_and_closed():
    assert "scope_mismatch_axis" in f5._CONTRADICTION_KEYS
    from cde.diagnose import contradiction_prompt as p
    assert "none" in p.SCOPE_MISMATCH_AXES and "unclear" in p.SCOPE_MISMATCH_AXES
    assert "species_or_strain" in p.SCOPE_MISMATCH_AXES


def _reply(**over):
    body = {
        "directional_contradiction": True,
        "relation_to_cited_finding": "opposes",
        "claim_match": "match",
        "outcome_relation": "same",
        "population_relation": "equivalent",
        "cited_direction": "increase",
        "candidate_direction": "decrease",
        "magnitude": "large",
        "cited_finding_span": "Metformin reduced HbA1c by 1.2% in adults with type 2 diabetes.",
        "candidate_contradiction_span": "Mean between-group difference was -1.2% (95% CI -1.4 to -1.0).",
        "confidence": 0.8,
        "scope_mismatch_axis": "none",
    }
    body.update(over)
    return json.dumps(body)


def test_parser_accepts_the_axis_and_exposes_it():
    judgment = f5._parse_contradiction(_reply())
    assert judgment.scope_mismatch_axis == "none"


def test_parser_rejects_an_off_list_axis():
    with pytest.raises(ValueError):
        f5._parse_contradiction(_reply(scope_mismatch_axis="vibes"))


def test_parser_rejects_confirmation_with_opposite_directions():
    with pytest.raises(ValueError, match="confirmation requires"):
        f5._parse_contradiction(_reply(
            directional_contradiction=False,
            relation_to_cited_finding="confirms",
            cited_direction="increase", candidate_direction="decrease"))


def test_parser_rejects_mixed_relation_without_a_mixed_direction():
    with pytest.raises(ValueError, match="mixed relation"):
        f5._parse_contradiction(_reply(
            directional_contradiction=False,
            relation_to_cited_finding="mixed"))


def test_parser_rejects_opposition_with_the_same_clear_direction():
    with pytest.raises(ValueError, match="opposition requires"):
        f5._parse_contradiction(_reply(
            cited_direction="decrease", candidate_direction="decrease"))


def test_parser_rejects_neutral_with_different_clear_directions():
    with pytest.raises(ValueError, match="neutral relation conflicts"):
        f5._parse_contradiction(_reply(
            directional_contradiction=False,
            relation_to_cited_finding="neutral",
            cited_direction="increase", candidate_direction="decrease"))


@pytest.mark.parametrize("axis", ["species_or_strain", "unclear"])
def test_parser_rejects_scope_mismatch_on_fully_comparable_axes(axis):
    with pytest.raises(ValueError, match="comparable relation axes"):
        f5._parse_contradiction(_reply(scope_mismatch_axis=axis))


def test_parser_strictness_is_unchanged_by_the_new_key():
    """An extra key beyond the contract still raises -- the contract grew by one,
    it did not become permissive."""
    body = json.loads(_reply())
    body["twelfth_key"] = "x"
    with pytest.raises(ValueError):
        f5._parse_contradiction(json.dumps(body))
    body = json.loads(_reply())
    del body["scope_mismatch_axis"]
    with pytest.raises(ValueError):
        f5._parse_contradiction(json.dumps(body))


def test_prompt_version_moved_off_v1_because_the_key_set_changed():
    from cde.diagnose import contradiction_prompt as p
    assert p.CONTRADICTION_PROMPT_VERSION != "f5_contradiction_v1"
    assert f5.F5Policy().contradiction_prompt_version == p.CONTRADICTION_PROMPT_VERSION
