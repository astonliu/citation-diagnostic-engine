from __future__ import annotations

import pytest

from .f5_candidate_screen import (
    CandidateScreenBatch, CandidateScreenDecision,
    validate_candidate_screen_batch,
)


def row(work_id="W2", *, decision="plausible", relevance="match",
        relation="uncertain", missing=()):
    return CandidateScreenDecision(
        candidate_work_id=work_id, decision=decision,
        claim_relevance=relevance, possible_relation=relation,
        missing_facts=tuple(missing))


def batch(*rows):
    return CandidateScreenBatch(
        decisions=tuple(rows), prompt_sha256="a" * 64,
        response_sha256="b" * 64)


def test_screen_batch_is_joined_by_exact_candidate_ids_not_position():
    mapped = validate_candidate_screen_batch(
        batch(row("W3"), row("W2")), ("W2", "W3"))
    assert set(mapped) == {"W2", "W3"}
    assert mapped["W2"].candidate_work_id == "W2"


@pytest.mark.parametrize("rows,expected", [
    ((row("W2"),), ("W2", "W3")),
    ((row("W2"), row("W9")), ("W2", "W3")),
])
def test_screen_batch_rejects_missing_or_foreign_ids(rows, expected):
    with pytest.raises(ValueError, match="exactly match"):
        validate_candidate_screen_batch(batch(*rows), expected)


def test_clear_mismatch_cannot_contradict_its_own_uncertainty_fields():
    with pytest.raises(ValueError, match="clear_mismatch"):
        row("W2", decision="clear_mismatch", relevance="mismatch",
            relation="opposes")
    with pytest.raises(ValueError, match="clear_mismatch"):
        row("W2", decision="clear_mismatch", relevance="mismatch",
            relation="neutral", missing=("population",))


def test_batch_rejects_duplicate_ids_and_non_sha_hashes():
    with pytest.raises(ValueError, match="duplicate"):
        batch(row("W2"), row("W2"))
    with pytest.raises(ValueError, match="SHA-256"):
        CandidateScreenBatch(
            decisions=(row("W2"),), prompt_sha256="not-a-hash",
            response_sha256="b" * 64)


# --------------------------------------------------------------------------
# The prompt, the parser, and the seam. Everything above tests the CONTRACT;
# these test the implementation that ships against it.
#
# THE INVARIANT THAT KEEPS BREAKING THIS SEAM. Three F5 changes in a row were
# blocked by a strict parser rule the prompt never stated, and the model then
# tripped it. So the first thing asserted here is that every clause of
# CandidateScreenDecision.__post_init__ is spelled out in the prompt text.
# --------------------------------------------------------------------------
import json

from .f5_candidate_screen import (
    CANDIDATE_SCREEN_PROMPT, DEFAULT_ABSTRACT_CHARS, MIN_ABSTRACT_CHARS,
    abstract_budget, make_candidate_screen, parse_screen_batch,
    render_screen_prompt,
)
from .f5_supersession import CandidateWork


def work(work_id, *, title="A later trial", abstract="Some findings.",
         date="2012-05-31"):
    return CandidateWork(id=work_id, title=title, abstract=abstract,
                         pub_date=date)


CANDIDATES = (work("12117397"), work("23235609", title="A Cochrane review"))
CLAIM = "postmenopausal hormone therapy reduces coronary heart disease risk"


def reply(*rows):
    return json.dumps({"screened": list(rows)})


def row_for(handle, *, decision="plausible", relevance="match",
            relation="uncertain", missing=()):
    return {"candidate": handle, "decision": decision,
            "claim_relevance": relevance, "possible_relation": relation,
            "missing_facts": list(missing)}


# -- the prompt states the rules the parser enforces ----------------------
def test_the_prompt_states_every_clear_mismatch_conjunct():
    text = CANDIDATE_SCREEN_PROMPT
    assert 'claim_relevance = "mismatch"' in text
    assert 'possible_relation = "neutral"' in text
    assert "missing_facts = []" in text
    assert "REJECTED WHOLE" in text


def test_the_prompt_states_the_whole_closed_vocabulary():
    text = CANDIDATE_SCREEN_PROMPT
    for value in ("plausible", "clear_mismatch", "uncertain",
                  "match", "mismatch",
                  "opposes", "confirms", "mixed", "neutral"):
        assert value in text, value


def test_the_prompt_names_the_asymmetry_that_should_govern_borderline_calls():
    """A screened-out superseder is unrecoverable; a screened-in dud costs cents."""
    assert "NEVER read" in CANDIDATE_SCREEN_PROMPT
    assert "TRUNCATED" in CANDIDATE_SCREEN_PROMPT


# -- rendering ------------------------------------------------------------
def test_handles_are_ordinals_and_map_back_to_work_ids():
    prompt, handles, stats = render_screen_prompt(CLAIM, CANDIDATES)
    assert handles == {"c1": "12117397", "c2": "23235609"}
    assert "[c1] published 2012-05-31" in prompt
    assert CLAIM in prompt
    assert stats["candidates"] == 2
    # The PMIDs are never asked for in the reply: the join is done by code.
    assert "c1, c2" in prompt


def test_a_duplicate_work_id_is_refused_because_the_join_would_lose_one():
    with pytest.raises(ValueError, match="twice"):
        render_screen_prompt(CLAIM, (work("111"), work("111")))


def test_a_candidate_with_no_id_is_refused():
    class Idless:
        id = "  "
    with pytest.raises(ValueError, match="no id"):
        render_screen_prompt(CLAIM, (Idless(),))


def test_a_truncated_abstract_is_marked_so_absence_is_not_read_as_evidence():
    long = work("999", abstract="Sentence one. " * 400)
    prompt, _handles, stats = render_screen_prompt(CLAIM, (long,))
    assert stats["abstracts_truncated"] == 1
    assert "[ABSTRACT TRUNCATED" in prompt


def test_an_untruncated_abstract_carries_no_truncation_marker():
    """The instructions explain the marker, so look only at the rendered block."""
    prompt, _handles, stats = render_screen_prompt(CLAIM, CANDIDATES)
    assert stats["abstracts_truncated"] == 0
    blocks = prompt[prompt.index("[c1]"):prompt.index("Return ONLY one JSON")]
    assert "[ABSTRACT TRUNCATED" not in blocks


def test_a_missing_abstract_says_so_rather_than_rendering_an_empty_block():
    prompt, _h, _s = render_screen_prompt(CLAIM, (work("999", abstract=""),))
    assert "abstract: (none available)" in prompt


def test_the_per_candidate_budget_shrinks_as_the_batch_grows():
    """One call for the whole batch means the batch has to fit one context."""
    assert abstract_budget(1) == DEFAULT_ABSTRACT_CHARS
    assert abstract_budget(50) == DEFAULT_ABSTRACT_CHARS
    assert abstract_budget(400) < abstract_budget(200) < DEFAULT_ABSTRACT_CHARS
    assert abstract_budget(100000) == MIN_ABSTRACT_CHARS


# -- parsing --------------------------------------------------------------
def test_a_well_formed_batch_maps_handles_to_work_ids():
    prompt, handles, _s = render_screen_prompt(CLAIM, CANDIDATES)
    batch = parse_screen_batch(
        reply(row_for("c1"), row_for("c2", decision="clear_mismatch",
                                     relevance="mismatch", relation="neutral")),
        handles, prompt=prompt)
    mapped = {d.candidate_work_id: d.decision for d in batch.decisions}
    assert mapped == {"12117397": "plausible", "23235609": "clear_mismatch"}
    assert len(batch.prompt_sha256) == 64 and len(batch.response_sha256) == 64


def test_a_lazy_clear_mismatch_is_refused_by_the_contract_it_broke():
    prompt, handles, _s = render_screen_prompt(CLAIM, CANDIDATES)
    with pytest.raises(ValueError, match="clear_mismatch"):
        parse_screen_batch(
            reply(row_for("c1", decision="clear_mismatch", relevance="mismatch",
                          relation="opposes"),
                  row_for("c2")),
            handles, prompt=prompt)


@pytest.mark.parametrize("bad,match", [
    ("not json at all", "not one bare JSON object"),
    ('["a"]', "must be an object"),
    ('{"screened": [], "extra": 1}', "keys mismatch"),
    ('{"rows": []}', "keys mismatch"),
    ('{"screened": {}}', "must be a JSON array"),
    ('{"screened": [1]}', "must be a JSON object"),
    ('{"screened": [{"candidate": "c1"}]}', "row keys mismatch"),
])
def test_off_contract_replies_fail_closed(bad, match):
    _p, handles, _s = render_screen_prompt(CLAIM, CANDIDATES)
    with pytest.raises(ValueError, match=match):
        parse_screen_batch(bad, handles, prompt="p")


def test_a_duplicate_json_key_is_rejected():
    _p, handles, _s = render_screen_prompt(CLAIM, CANDIDATES)
    with pytest.raises(ValueError, match="duplicate JSON key"):
        parse_screen_batch('{"screened": [], "screened": []}', handles, prompt="p")


def test_an_unknown_or_repeated_handle_is_rejected():
    _p, handles, _s = render_screen_prompt(CLAIM, CANDIDATES)
    with pytest.raises(ValueError, match="unknown candidate handle"):
        parse_screen_batch(reply(row_for("c9")), handles, prompt="p")
    with pytest.raises(ValueError, match="twice"):
        parse_screen_batch(reply(row_for("c1"), row_for("c1")), handles, prompt="p")


def test_an_off_enum_value_is_rejected_rather_than_coerced():
    _p, handles, _s = render_screen_prompt(CLAIM, CANDIDATES)
    with pytest.raises(ValueError, match="off-enum"):
        parse_screen_batch(reply(row_for("c1", decision="maybe"),
                                 row_for("c2")), handles, prompt="p")


def test_blank_missing_facts_are_dropped_not_passed_through():
    """The contract refuses a blank string; dropping whitespace is not coercion
    of a VALUE, it is refusing to record an empty note as a note."""
    _p, handles, _s = render_screen_prompt(CLAIM, CANDIDATES)
    batch = parse_screen_batch(
        reply(row_for("c1", missing=["population", "   ", ""]), row_for("c2")),
        handles, prompt="p")
    assert batch.decisions[0].missing_facts == ("population",)


# -- the seam -------------------------------------------------------------
def test_the_seam_makes_one_call_for_the_whole_batch():
    calls = []

    def complete(prompt):
        calls.append(prompt)
        return reply(row_for("c1"), row_for("c2"))

    screen = make_candidate_screen(complete)
    batch = screen(claim=CLAIM, candidates=CANDIDATES)
    assert len(calls) == 1
    assert screen.calls == 1
    assert len(batch.decisions) == 2
    assert screen.render_log[0]["candidates"] == 2


def test_the_seam_refuses_a_short_batch_at_its_source():
    """The detector would discard it and fall open; catching it here means the
    seam's own tests can name the defect instead of the run going quiet."""
    screen = make_candidate_screen(lambda _p: reply(row_for("c1")))
    with pytest.raises(ValueError, match="exactly match"):
        screen(claim=CLAIM, candidates=CANDIDATES)


def test_the_seam_needs_a_callable_transport():
    with pytest.raises(ValueError, match="callable"):
        make_candidate_screen(None)
