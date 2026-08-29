"""The cache breakpoint, the token ledger, and the price table.

WHY THE ADAPTER IS NOT IN ``band_prompts``. That file is the frozen substrate --
blob OID pinned by ``mint_v1``, sealed into both frozen prompt packages, and
asserted by ``test_band_prompts_blob_oid_is_unchanged``. The adapter that had to
grow therefore lives in ``anthropic_transport``; the frozen one is unchanged and
still correct.

WHAT THESE TESTS ARE FOR. The caching change is defended by ONE claim: the two
content blocks concatenate back to the bytes the model saw before the breakpoint
existed, so the change is billing-only. If that claim is false the change is a
silent prompt edit under a cost label. The first test is that claim, asserted
against the real rendered F5 prompt rather than against a fixture.

The rest guard the honesty of the numbers: an unpriced model and a response
without usage must both come back as ``"not_collected"``, never as 0.0, because
a run that reports zero cost reads as a free run.
"""
from __future__ import annotations

import pytest

from cde.diagnose import contradiction_prompt as fcp
from cde.runtime import model_pricing
from cde.runtime.anthropic_transport import (
    CACHE_BREAK_MARKER, make_anthropic_call, split_cache_blocks,
)
from cde.diagnose.supersession import ComparabilitySource
from cde.runtime.recording_adapter import NOT_COLLECTED, TokenLedger, merge_token_ledgers


CITED = ComparabilitySource(
    abstract=("Estrogen therapy and coronary disease. Among 48470 postmenopausal "
              "women the relative risk of major coronary disease was 0.56. "
              "Current estrogen use is associated with reduced incidence."),
    results="The relative risk of major coronary disease was 0.56.",
    work_id="1870648")
CANDIDATE = ComparabilitySource(
    abstract=("Estrogen plus progestin in healthy postmenopausal women. The "
              "hazard ratio for coronary heart disease was 1.29 over 5.2 years."),
    work_id="12117397")


# -- the safety argument ---------------------------------------------------
def test_cache_split_of_the_real_f5_prompt_is_byte_exact():
    prompt = fcp.render_prompt(CITED, CANDIDATE, "HRT reduces CHD risk")
    blocks = split_cache_blocks(prompt)
    assert isinstance(blocks, list) and len(blocks) == 2
    assert "".join(b["text"] for b in blocks) == prompt.replace(
        CACHE_BREAK_MARKER, "")


def test_the_breakpoint_is_on_the_first_block_only():
    blocks = split_cache_blocks(
        fcp.render_prompt(CITED, CANDIDATE, "HRT reduces CHD risk"))
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in blocks[1]


def test_the_cached_prefix_holds_the_cited_work_and_the_tail_holds_the_candidate():
    """The whole economic claim: what varies per candidate is AFTER the break."""
    head, tail = split_cache_blocks(
        fcp.render_prompt(CITED, CANDIDATE, "HRT reduces CHD risk"))
    assert "48470 postmenopausal" in head["text"]
    assert "48470 postmenopausal" not in tail["text"]
    assert "hazard ratio for coronary heart disease was 1.29" in tail["text"]
    assert "hazard ratio for coronary heart disease was 1.29" not in head["text"]


def test_two_candidates_share_a_byte_identical_cached_prefix():
    other = ComparabilitySource(
        abstract="A different later trial reporting no effect.", work_id="23235609")
    first = split_cache_blocks(fcp.render_prompt(CITED, CANDIDATE, "claim"))
    second = split_cache_blocks(fcp.render_prompt(CITED, other, "claim"))
    assert first[0]["text"] == second[0]["text"]
    assert first[1]["text"] != second[1]["text"]


def test_an_unmarked_prompt_is_sent_exactly_as_before():
    assert split_cache_blocks("plain prompt") == "plain prompt"


def test_a_second_marker_is_refused_rather_than_silently_ignored():
    with pytest.raises(ValueError, match="exactly one cache breakpoint"):
        split_cache_blocks(f"a{CACHE_BREAK_MARKER}b{CACHE_BREAK_MARKER}c")


@pytest.mark.parametrize("prompt", [
    CACHE_BREAK_MARKER + "tail only",
    "head only" + CACHE_BREAK_MARKER,
    f"  {CACHE_BREAK_MARKER}head",
])
def test_a_breakpoint_at_either_end_falls_back_to_one_block(prompt):
    """An empty text block is a 400, and an end breakpoint caches nothing."""
    out = split_cache_blocks(prompt)
    assert isinstance(out, str)
    assert CACHE_BREAK_MARKER not in out


def test_the_f5_prompt_carries_exactly_one_marker():
    assert fcp.F5_CONTRADICTION_PROMPT.count(CACHE_BREAK_MARKER) == 1
    assert fcp.CONTRADICTION_PROMPT_VERSION == "f5_contradiction_v5"
    assert fcp.CONTRADICTION_CACHE_BREAKPOINT_VERSION == "after_cited_source_v1"


# -- what the transport records -------------------------------------------
class _Usage:
    def __init__(self, **kw):
        for key, value in kw.items():
            setattr(self, key, value)


class _Block:
    type = "text"

    def __init__(self, text):
        self.text = text


class _Resp:
    def __init__(self, text, usage):
        self.content = [_Block(text)]
        self.usage = usage


class _Client:
    """Records what was sent and replays a scripted usage block."""

    def __init__(self, usages):
        self.sent = []
        self._usages = list(usages)
        self.messages = self

    def create(self, **kwargs):
        self.sent.append(kwargs)
        return _Resp("{}", self._usages.pop(0))


def test_the_adapter_sends_two_blocks_and_records_the_cache_read():
    client = _Client([
        _Usage(input_tokens=756, output_tokens=400,
               cache_creation_input_tokens=2591, cache_read_input_tokens=0),
        _Usage(input_tokens=760, output_tokens=380,
               cache_creation_input_tokens=0, cache_read_input_tokens=2591),
    ])
    call = make_anthropic_call(client, "claude-opus-5", stage="f5_generator")
    call(fcp.render_prompt(CITED, CANDIDATE, "claim"))
    call(fcp.render_prompt(CITED, CANDIDATE, "claim"))

    content = client.sent[0]["messages"][0]["content"]
    assert isinstance(content, list) and len(content) == 2
    assert content[0]["cache_control"] == {"type": "ephemeral"}

    usage = call.token_ledger.snapshot()
    assert usage["calls"] == 2
    assert usage["cache_read_input_tokens"] == 2591
    assert usage["cache_creation_input_tokens"] == 2591
    assert usage["prompt_tokens_total"] == 756 + 2591 + 760 + 2591
    assert usage["cost_usd"] > 0


def test_a_response_with_no_usage_block_is_counted_not_absorbed():
    """A missing usage block makes the TOTAL unknown, not smaller."""
    client = _Client([None])
    call = make_anthropic_call(client, "claude-opus-5", stage="band")
    call("prompt")
    usage = call.token_ledger.snapshot()
    assert usage["calls"] == 1
    assert usage["usage_missing_calls"] == 1
    assert usage["cost_usd"] == NOT_COLLECTED


def test_an_unpriced_model_reports_not_collected_and_never_zero():
    ledger = TokenLedger(stage="band", model="some-model-we-do-not-price")
    ledger.record_usage(_Usage(input_tokens=10, output_tokens=10))
    assert ledger.snapshot()["cost_usd"] == NOT_COLLECTED


def test_a_merged_total_is_unknown_when_any_stage_is_unpriced():
    priced = TokenLedger(stage="f5_generator", model="claude-opus-5")
    priced.record_usage(_Usage(input_tokens=100, output_tokens=100))
    unpriced = TokenLedger(stage="f5_verifier", model="mystery")
    unpriced.record_usage(_Usage(input_tokens=100, output_tokens=100))
    merged = merge_token_ledgers([priced, unpriced])
    assert merged["total"]["cost_usd"] == NOT_COLLECTED
    assert merged["total"]["input_tokens"] == 200
    assert set(merged["by_stage"]) == {"f5_generator", "f5_verifier"}


def test_two_ledgers_cannot_claim_the_same_stage_name():
    with pytest.raises(ValueError, match="same stage|claim the stage"):
        merge_token_ledgers([TokenLedger(stage="band", model="claude-opus-5"),
                             TokenLedger(stage="band", model="claude-opus-5")])


# -- the price table ------------------------------------------------------
def test_cache_tokens_are_billed_at_their_own_multipliers():
    """input_tokens is the UNCACHED REMAINDER; adding cache tokens to it would
    double-bill every cached request and hide the saving."""
    uncached = model_pricing.cost_usd(
        model="claude-opus-5", input_tokens=3347, output_tokens=400)
    cached = model_pricing.cost_usd(
        model="claude-opus-5", input_tokens=756, output_tokens=400,
        cache_read_input_tokens=2591)
    assert cached < uncached
    # 756 + 2591*0.1 = 1015.1 effective input tokens.
    assert cached == pytest.approx((1015.1 * 5.0 + 400 * 25.0) / 1e6)


def test_an_unknown_model_prices_to_none_not_to_zero():
    assert model_pricing.cost_usd(model="not-a-model", input_tokens=1000) is None
    assert model_pricing.rates_for("claude-opus-5") == (5.00, 25.00)


def test_the_opus_5_prefix_floor_is_tabulated_not_assumed():
    """Not monotonic across generations: 512 here, 4096 on Opus 4.6."""
    assert model_pricing.MIN_CACHEABLE_PREFIX_TOKENS["claude-opus-5"] == 512
    assert model_pricing.MIN_CACHEABLE_PREFIX_TOKENS["claude-opus-4-6"] == 4096


# -- the streaming path ---------------------------------------------------
class _StreamHandle:
    def __init__(self, resp):
        self._resp = resp

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self._resp


class _StreamingClient(_Client):
    def stream(self, **kwargs):
        self.sent.append(kwargs)
        return _StreamHandle(_Resp("{}", self._usages.pop(0)))


def test_a_large_output_budget_needs_streaming_and_says_so_before_the_first_call():
    """The SDK refuses this at the first live call with a message about minutes,
    which reads like a timeout rather than a ceiling."""
    with pytest.raises(ValueError, match="needs stream=True"):
        make_anthropic_call(_Client([]), "claude-opus-5", max_tokens=32768)


def test_the_streaming_path_sends_the_same_request_and_records_the_same_usage():
    client = _StreamingClient([
        _Usage(input_tokens=40000, output_tokens=16000,
               cache_creation_input_tokens=0, cache_read_input_tokens=0)])
    call = make_anthropic_call(client, "claude-opus-5", max_tokens=32768,
                              stage="f5_candidate_screen", stream=True)
    call("a screen prompt")
    assert client.sent[0]["max_tokens"] == 32768
    assert client.sent[0]["messages"][0]["content"] == "a screen prompt"
    usage = call.token_ledger.snapshot()
    assert usage["input_tokens"] == 40000 and usage["output_tokens"] == 16000
    assert usage["cost_usd"] == pytest.approx((40000 * 5.0 + 16000 * 25.0) / 1e6)


def test_a_budget_at_the_ceiling_still_takes_the_non_streaming_path():
    from cde.runtime.anthropic_transport import NONSTREAMING_MAX_TOKENS_CEILING
    client = _Client([_Usage(input_tokens=1, output_tokens=1)])
    call = make_anthropic_call(client, "claude-opus-5",
                              max_tokens=NONSTREAMING_MAX_TOKENS_CEILING)
    call("p")
    assert client.sent and "max_tokens" in client.sent[0]
