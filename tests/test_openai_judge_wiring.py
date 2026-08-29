"""The judge swap: routing, the two cache mechanisms, and the accounting.

WHAT THESE TESTS DEFEND. The wiring rests on four claims, and each one fails
silently if it is wrong:

1. ROUTING. A model id resolves to exactly one provider, and an id nobody
   registered is refused rather than defaulted. A default provider would run an
   unrecognised model and report it under the wrong vendor's rates.
2. THE PROMPT IS UNCHANGED. The OpenAI cache blocks concatenate back to the same
   bytes the Anthropic path sends -- the same claim
   ``test_prompt_caching_and_usage`` makes for ``cache_control``, restated for
   ``prompt_cache_breakpoint``, because "same prompt, different model" is the
   only thing that makes the two runs comparable.
3. THE ACCOUNTING. OpenAI reports ``input_tokens`` as the WHOLE prompt with
   ``cached_tokens`` inside it; this repo's ``cost_usd`` wants the uncached
   remainder. The subtraction is the single highest-consequence line in the
   change: get it wrong and every cached call is billed twice, at $4.00/MTok and
   again at $0.40/MTok.
4. THE ANTHROPIC PATH IS UNTOUCHED. Its ledger, its receipt and its cost figure
   must be what they were. ``test_prompt_caching_and_usage`` is the regression
   gate for the transport itself; what is added here is that routing a Claude id
   through the new seam produces the same callable as before.

WHAT THEY CANNOT DEFEND, and it has to be said rather than implied. No test here
touches OpenAI. Every one runs against a double, so they check that this code
does what the docs say the API does -- not that the API does it. The four
statements that need a live call to settle (the model id resolves, the usage
identity holds, temperature really 400s, verdicts are stable across repeats) are
:mod:`cde.runtime.check_openai_judge`, which needs a key and is not run by pytest.
"""
from __future__ import annotations

import pytest

from cde.runtime import completer
from cde.runtime import model_pricing
from cde.runtime import openai_transport as ot
from cde.runtime.anthropic_transport import CACHE_BREAK_MARKER
from cde.runtime.recording_adapter import NOT_COLLECTED, TokenLedger, merge_token_ledgers


# -- doubles ---------------------------------------------------------------
class _Usage:
    """A Responses usage block, in OpenAI's containment convention.

    ``input_tokens`` is the WHOLE prompt, so the two cache figures are carved
    OUT of it here rather than added to it -- building the double the other way
    round would make the tests pass against the very mistake they exist to catch.
    """

    def __init__(self, *, uncached=1000, cached=0, cache_write=0, output=200,
                 reasoning=0):
        self.input_tokens = uncached + cached + cache_write
        self.output_tokens = output
        self.total_tokens = self.input_tokens + output
        self.input_tokens_details = type("D", (), {
            "cached_tokens": cached, "cache_write_tokens": cache_write})()
        self.output_tokens_details = type("D", (), {
            "reasoning_tokens": reasoning})()


class _Resp:
    def __init__(self, text="VERDICT: covered", usage=None, status="completed"):
        self.output_text = text
        self.usage = usage if usage is not None else _Usage()
        self.status = status
        self.incomplete_details = None
        self.model = "gpt-5.6-sol"


class _FakeOpenAI:
    """Captures the request instead of sending it."""

    def __init__(self, responses=None):
        self.requests = []
        self._queue = list(responses or [])
        outer = self

        class _Responses:
            def create(self, **kwargs):
                outer.requests.append(kwargs)
                return (outer._queue.pop(0) if outer._queue else _Resp())

        self.responses = _Responses()


class _FakeAnthropic:
    def __init__(self):
        self.requests = []
        outer = self

        class _Messages:
            def create(self, **kwargs):
                outer.requests.append(kwargs)
                return type("M", (), {
                    "content": [type("B", (), {"type": "text",
                                               "text": "ok"})()],
                    "usage": type("U", (), {
                        "input_tokens": 10, "output_tokens": 5,
                        "cache_creation_input_tokens": 0,
                        "cache_read_input_tokens": 0})(),
                })()

        self.messages = _Messages()


# -- 1. routing ------------------------------------------------------------
def test_each_model_id_resolves_to_exactly_one_provider():
    assert completer.provider_for("claude-opus-5") == "anthropic"
    assert completer.provider_for("gpt-5.6-sol") == "openai"


def test_an_unknown_model_id_is_refused_and_never_defaulted():
    with pytest.raises(ValueError, match="no provider is registered"):
        completer.provider_for("gpt-6-omni")


def test_the_alias_is_refused_because_a_run_pinned_to_it_is_not_pinned():
    with pytest.raises(ValueError, match="ALIAS"):
        completer.provider_for("gpt-5.6")


@pytest.mark.parametrize("sibling", ["gpt-5.6-terra", "gpt-5.6-luna",
                                     "gpt-5.6-cyber"])
def test_the_out_of_scope_siblings_are_refused_by_name(sibling):
    with pytest.raises(ValueError, match="out of scope"):
        completer.provider_for(sibling)


def test_every_routable_model_also_has_a_price():
    # The pipeline refuses to run an unpriced model, so a routable-but-unpriced
    # id would fail deep in the run rather than here.
    for model in completer._PROVIDER_BY_MODEL:
        assert model_pricing.rates_for(model) is not None, model


def test_the_judge_and_the_generator_are_detectably_different_families():
    # The whole reason for the change: an object-identity check on two callables
    # passes when both run the same model, and this is the check that does not.
    assert not completer.is_same_family("claude-opus-5", "gpt-5.6-sol")
    assert completer.is_same_family("claude-opus-5", "claude-sonnet-5")


# -- 2. the prompt is unchanged -------------------------------------------
def _marked(head="H" * 40, tail="T" * 40):
    return head + CACHE_BREAK_MARKER + tail


def test_the_openai_blocks_concatenate_back_to_the_unmarked_prompt():
    prompt = _marked()
    items, options = ot.split_cache_blocks_openai(prompt)
    text = "".join(b["text"] for b in items[0]["content"])
    assert text == prompt.replace(CACHE_BREAK_MARKER, "")
    assert options == {"mode": "explicit"}


def test_the_breakpoint_is_on_the_first_block_only():
    items, _ = ot.split_cache_blocks_openai(_marked())
    blocks = items[0]["content"]
    assert blocks[0]["prompt_cache_breakpoint"] == {"mode": "explicit"}
    assert "prompt_cache_breakpoint" not in blocks[1]
    assert all(b["type"] == "input_text" for b in blocks)


def test_explicit_mode_is_only_sent_when_there_is_a_breakpoint_to_make_operative():
    # Sending mode=explicit with no explicit breakpoint would disable the
    # implicit one and cache NOTHING -- a silent cost regression.
    items, options = ot.split_cache_blocks_openai("no marker here")
    assert options is None
    assert items == [{"role": "user",
                      "content": [{"type": "input_text",
                                   "text": "no marker here"}]}]


@pytest.mark.parametrize("prompt", [CACHE_BREAK_MARKER + "tail",
                                    "head" + CACHE_BREAK_MARKER,
                                    "  " + CACHE_BREAK_MARKER + "tail"])
def test_a_breakpoint_at_either_end_falls_back_to_the_implicit_path(prompt):
    items, options = ot.split_cache_blocks_openai(prompt)
    assert len(items[0]["content"]) == 1
    assert options is None
    assert items[0]["content"][0]["text"] == prompt.replace(
        CACHE_BREAK_MARKER, "")


def test_a_second_marker_is_refused_rather_than_silently_ignored():
    with pytest.raises(ValueError, match="markers"):
        ot.split_cache_blocks_openai("a" + CACHE_BREAK_MARKER + "b"
                                     + CACHE_BREAK_MARKER + "c")


def test_both_providers_are_handed_the_same_bytes_for_the_same_prompt():
    from cde.runtime.anthropic_transport import split_cache_blocks
    prompt = _marked()
    anthropic = "".join(b["text"] for b in split_cache_blocks(prompt))
    items, _ = ot.split_cache_blocks_openai(prompt)
    assert "".join(b["text"] for b in items[0]["content"]) == anthropic


# -- the request shape ----------------------------------------------------
def test_temperature_and_top_p_are_never_built_into_the_request():
    client = _FakeOpenAI()
    ot.make_openai_call(client, stage="j")("hello")
    sent = client.requests[0]
    assert "temperature" not in sent and "top_p" not in sent
    # And the unsupported output-budget name is not sent either.
    assert "max_tokens" not in sent and "max_output_tokens" in sent


def test_the_replacement_controls_are_sent_explicitly_not_left_to_default():
    client = _FakeOpenAI()
    ot.make_openai_call(client, stage="j")("hello")
    sent = client.requests[0]
    assert sent["reasoning"] == {"effort": "low"}
    assert sent["text"] == {"verbosity": "low"}
    # `pro` adds variance to a stage whose job is stability: mode is never sent.
    assert "mode" not in sent["reasoning"]


def test_no_instructions_are_sent_so_the_prompt_shape_matches_the_other_provider():
    client = _FakeOpenAI()
    ot.make_openai_call(client, stage="j")("hello")
    assert "instructions" not in client.requests[0]


def test_the_cache_key_is_sent_when_given_and_omitted_when_not():
    client = _FakeOpenAI()
    ot.make_openai_call(client, stage="j", prompt_cache_key="cov-v3")("hi")
    assert client.requests[0]["prompt_cache_key"] == "cov-v3"
    client2 = _FakeOpenAI()
    ot.make_openai_call(client2, stage="j")("hi")
    assert "prompt_cache_key" not in client2.requests[0]


@pytest.mark.parametrize("kwargs", [{"effort": "medium-high"},
                                    {"verbosity": "verbose"},
                                    {"max_output_tokens": 0},
                                    {"max_output_tokens": 200_000}])
def test_an_out_of_range_setting_is_refused_before_the_first_call(kwargs):
    with pytest.raises(ValueError):
        ot.make_openai_call(_FakeOpenAI(), stage="j", **kwargs)


def test_an_empty_reply_raises_instead_of_parsing_as_an_abstention():
    # "" reaches the judge as an abstention, and an abstention caused by a
    # budget exhausted on reasoning tokens is indistinguishable from a real one.
    client = _FakeOpenAI([_Resp(text="", status="incomplete")])
    with pytest.raises(ValueError, match="no output text"):
        ot.make_openai_call(client, stage="j")("hi")


# -- 3. the accounting ----------------------------------------------------
def test_input_tokens_is_normalised_to_the_uncached_remainder():
    counts = ot.usage_dict(_Usage(uncached=300, cached=700, cache_write=0))
    # 1000 reported; 700 of them cached. Passing 1000 through would bill those
    # 700 at the full rate AND again as a cache read.
    assert counts["input_tokens"] == 300
    assert counts["cache_read_input_tokens"] == 700


def test_the_two_cache_counters_land_on_the_repo_s_existing_names():
    counts = ot.usage_dict(_Usage(uncached=10, cached=20, cache_write=30))
    assert counts["cache_read_input_tokens"] == 20
    assert counts["cache_creation_input_tokens"] == 30
    assert counts["input_tokens"] == 10


def test_reasoning_tokens_are_recorded_but_not_added_to_output():
    counts = ot.usage_dict(_Usage(output=500, reasoning=400))
    assert counts["output_tokens"] == 500   # NOT 900
    assert counts["reasoning_tokens"] == 400


def test_a_usage_block_that_fails_its_own_arithmetic_is_refused():
    usage = _Usage(uncached=1000, output=200)
    usage.total_tokens = 9999
    with pytest.raises(ot.OpenAIUsageError, match="arithmetic does not hold"):
        ot.usage_dict(usage)


def test_a_missing_counter_raises_rather_than_becoming_zero():
    usage = _Usage()
    del usage.input_tokens_details.__class__.cached_tokens
    with pytest.raises(ot.OpenAIUsageError, match="cached_tokens"):
        ot.usage_dict(usage)


def test_a_missing_details_object_raises():
    usage = _Usage()
    usage.output_tokens_details = None
    with pytest.raises(ot.OpenAIUsageError, match="details"):
        ot.usage_dict(usage)


def test_cache_counters_larger_than_the_input_total_are_refused():
    usage = _Usage(uncached=0, cached=100, cache_write=100)
    usage.input_tokens = 50            # no longer a superset
    usage.total_tokens = 50 + usage.output_tokens
    with pytest.raises(ot.OpenAIUsageError, match="not subsets"):
        ot.usage_dict(usage)


def test_an_absent_usage_block_is_counted_not_absorbed_as_zero():
    ledger = TokenLedger(stage="band_judge", model="gpt-5.6-sol")
    ledger.record_openai_usage(None)
    row = ledger.snapshot()
    assert row["calls"] == 1 and row["usage_missing_calls"] == 1
    # A run that could not read what it spent must not report a number.
    assert row["cost_usd"] == NOT_COLLECTED


def test_a_present_but_misshaped_usage_block_raises_instead_of_being_absorbed():
    # The tolerance that is right for an ABSENT block is wrong for a mis-shaped
    # one: absorbing it would skip the one check that settles the containment
    # question this module is built on.
    ledger = TokenLedger(stage="band_judge", model="gpt-5.6-sol")
    usage = _Usage()
    usage.total_tokens = 1
    with pytest.raises(ot.OpenAIUsageError):
        ledger.record_openai_usage(usage)


def test_the_ledger_prices_a_judge_call_off_the_published_rates():
    # 100K, deliberately UNDER the 272K surcharge threshold: a round million
    # would be re-rated and the figure below would be testing two things at once.
    client = _FakeOpenAI([_Resp(usage=_Usage(uncached=100_000, output=0))])
    call = ot.make_openai_call(client, stage="band_judge")
    call("hi")
    assert call.token_ledger.snapshot()["cost_usd"] == 0.40


def test_a_cached_prompt_costs_a_tenth_of_an_uncached_one():
    for kwargs, expected in ((dict(uncached=100_000), 0.40),
                             (dict(uncached=0, cached=100_000), 0.04),
                             (dict(uncached=0, cache_write=100_000), 0.50)):
        client = _FakeOpenAI([_Resp(usage=_Usage(output=0, **kwargs))])
        call = ot.make_openai_call(client, stage="s")
        call("hi")
        assert call.token_ledger.snapshot()["cost_usd"] == expected


def test_the_cache_multipliers_agree_with_the_published_per_token_prices():
    # The reason one multiplier table can serve both providers. If OpenAI ever
    # reprices cached input away from 0.1x, this fails instead of the run
    # quietly mispricing every cached call.
    price_in, _ = model_pricing.rates_for("gpt-5.6-sol")
    assert (price_in * model_pricing.CACHE_READ_MULTIPLIER
            == model_pricing.PUBLISHED_CACHED_INPUT_USD_PER_MTOK["gpt-5.6-sol"])
    assert model_pricing.CACHE_WRITE_MULTIPLIER_5M == 1.25   # docs: "1.25x"


def test_the_long_prompt_surcharge_rerates_the_whole_request():
    over = model_pricing.LONG_PROMPT_THRESHOLD_TOKENS + 1
    plain = model_pricing.cost_usd(
        model="gpt-5.6-sol", input_tokens=over, output_tokens=1000)
    # 2x input and 1.5x output for the FULL request, not for the excess.
    assert plain == pytest.approx(over * 8.00 / 1e6 + 1000 * 30.00 / 1e6)


def test_the_surcharge_is_keyed_to_the_whole_prompt_not_the_uncached_remainder():
    # A heavily cached 900K prompt is plainly over the line; pricing only the
    # uncached remainder against the threshold would let it slip under.
    cached = model_pricing.LONG_PROMPT_THRESHOLD_TOKENS + 1
    assert model_pricing.long_prompt_surcharge_applies(
        model="gpt-5.6-sol", prompt_tokens=cached)
    priced = model_pricing.cost_usd(model="gpt-5.6-sol", input_tokens=0,
                                   cache_read_input_tokens=cached)
    assert priced == pytest.approx(cached * 8.00 * 0.1 / 1e6)


def test_the_surcharge_does_not_leak_onto_the_anthropic_models():
    over = model_pricing.LONG_PROMPT_THRESHOLD_TOKENS + 1
    assert not model_pricing.long_prompt_surcharge_applies(
        model="claude-opus-5", prompt_tokens=over)
    assert model_pricing.cost_usd(model="claude-opus-5", input_tokens=over) == (
        pytest.approx(over * 5.00 / 1e6))


def test_the_surcharge_is_reported_beside_the_dollars_it_produced():
    # A cost figure that silently doubled reads like a run that used twice the
    # tokens.
    over = model_pricing.LONG_PROMPT_THRESHOLD_TOKENS + 1
    client = _FakeOpenAI([_Resp(usage=_Usage(uncached=over, output=1))])
    call = ot.make_openai_call(client, stage="band_judge")
    call("hi")
    assert call.token_ledger.snapshot()["long_prompt_surcharge_applied"] is True


def test_an_unpriced_model_still_reports_not_collected_never_zero():
    # The price-table guard: adding a row must not have introduced a default.
    assert model_pricing.rates_for("gpt-5.7-sol") is None
    assert model_pricing.cost_usd(model="gpt-5.7-sol", input_tokens=99) is None


# -- a merged, two-provider report ---------------------------------------
def test_a_merged_report_names_both_models_rather_than_assuming_one():
    generator = TokenLedger(stage="band", model="claude-opus-5")
    generator.record_usage(type("U", (), {
        "input_tokens": 100, "output_tokens": 10,
        "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0})())
    judge = TokenLedger(stage="band_judge", model="gpt-5.6-sol")
    judge.record_openai_usage(_Usage(uncached=100, output=10, reasoning=8))
    merged = merge_token_ledgers([generator, judge])
    assert merged["total"]["models"] == ["claude-opus-5", "gpt-5.6-sol"]
    assert merged["total"]["reasoning_tokens"] == 8
    assert merged["total"]["cost_usd"] == round(
        100 * 5.00 / 1e6 + 10 * 25.00 / 1e6
        + 100 * 4.00 / 1e6 + 10 * 20.00 / 1e6, 6)


def test_the_anthropic_path_reports_zero_reasoning_tokens_not_a_missing_key():
    # Uniform schema across providers: a reader of one row does not have to know
    # which provider produced it to know which keys exist.
    ledger = TokenLedger(stage="band", model="claude-opus-5")
    ledger.record_usage(type("U", (), {
        "input_tokens": 1, "output_tokens": 1,
        "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0})())
    assert ledger.snapshot()["reasoning_tokens"] == 0


# -- 4. the Anthropic path is untouched ----------------------------------
def test_a_claude_id_through_the_new_seam_builds_the_old_transport():
    client = _FakeAnthropic()
    call = completer.make_completer("claude-opus-5", client=client, stage="band")
    assert call("hello") == "ok"
    sent = client.requests[0]
    # Same request shape as before: no temperature, max_tokens not
    # max_output_tokens, one user message.
    assert set(sent) == {"model", "max_tokens", "messages"}
    assert sent["messages"][0]["content"] == "hello"
    assert call.token_ledger.snapshot()["input_tokens"] == 10


def test_an_anthropic_only_option_is_refused_on_the_openai_path_not_dropped():
    with pytest.raises(ValueError, match="Anthropic-only"):
        completer.make_completer("gpt-5.6-sol", client=_FakeOpenAI(),
                                 stream=True)


def test_the_openai_completer_satisfies_the_same_seam_contract():
    call = completer.make_completer("gpt-5.6-sol", client=_FakeOpenAI(),
                                    stage="band_judge")
    assert callable(call)
    assert call("hi") == "VERDICT: covered"
    assert call.stage == "band_judge"
    assert isinstance(call.token_ledger, TokenLedger)


# -- governance -----------------------------------------------------------
def test_the_judge_model_cannot_be_launched_until_temperature_is_measured():
    # DEC-070 admits a model to the rejecting table on FIRST-PARTY evidence
    # only, and the evidence here is OpenAI's docs plus a third-party report --
    # exactly the strength that keeps claude-opus-4-7 out. But leaving the id
    # merely unlisted would resolve it to "temperature=0, sent", for calls that
    # structurally cannot carry it. Refused until check C measures it.
    from cde.runtime import production_launcher as pl
    with pytest.raises(pl.LaunchRefused, match="UNMEASURED"):
        pl.verify_temperature_governance(model="gpt-5.6-sol", temperature=0)
    with pytest.raises(pl.LaunchRefused, match="UNMEASURED"):
        pl.verify_temperature_governance(model="gpt-5.6-sol",
                                         temperature="unsupported")


def test_the_claude_temperature_governance_is_unchanged():
    from cde.runtime import production_launcher as pl
    resolved = pl.verify_temperature_governance(model="claude-opus-5",
                                                temperature=None)
    assert resolved["recorded_value"] == pl.TEMPERATURE_UNSUPPORTED
    assert resolved["sent_to_provider"] is False
    assert pl.verify_temperature_governance(
        model="claude-sonnet-4-5", temperature=0)["recorded_value"] == 0


def test_a_prefill_string_on_the_judge_model_is_refused_but_none_is_fine():
    from cde.runtime import production_launcher as pl
    with pytest.raises(pl.LaunchRefused, match="UNMEASURED"):
        pl.verify_prefill_governance(model="gpt-5.6-sol",
                                     assistant_prefill="{")
    # None records nothing at all, which is the one answer that cannot be false.
    assert pl.verify_prefill_governance(
        model="gpt-5.6-sol", assistant_prefill=None)["recorded_value"] is None


def test_the_determinism_note_refuses_the_claim_rather_than_making_it():
    assert "NOT deterministic" in ot.DETERMINISM_NOTE
    assert "seed" in ot.DETERMINISM_NOTE


def test_the_cache_floor_is_recorded_so_a_permanent_zero_is_explainable():
    # Below 1024 prompt tokens the cache never engages on GPT-5.6+, so
    # cached_tokens stays 0 forever and that is not a broken breakpoint.
    assert ot.MIN_CACHEABLE_PROMPT_TOKENS == 1024
