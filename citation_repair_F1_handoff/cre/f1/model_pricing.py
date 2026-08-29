"""cre/f1/model_pricing.py -- the ONE place a dollar rate is written down.

WHY A MODULE FOR A PRICE TABLE. ``judgment_run``'s F5 block has declared a
``cost_counters.cost_usd`` slot since the block was written, and it has always
held the literal string ``"not_collected"``. Filling it needs rates, and rates
are the one input to a cost number that is neither measured by the run nor
derivable from it -- they are read off a vendor page on a particular day and go
stale silently. Scattering them across the callers that need them is how a run
ends up reporting two different costs for the same tokens.

SO: one table, one date, one function. A model this table does not know returns
``None`` and every caller records ``"not_collected"`` rather than 0.0 -- an
unknown price is not a free call, and the whole point of the ``"not_collected"``
sentinel elsewhere in this repo is that a real zero and an absent measurement
must not wear the same value.

RATES ARE PER MILLION TOKENS, USD, first-party list price. The Claude rates were
read 2026-08-22 from the Anthropic pricing reference bundled with the
``claude-api`` skill (Current Models table). Amazon Bedrock and Vertex AI are
partner-operated with separate pricing and are NOT covered here; a run against
those endpoints must not be costed with this table.

TWO VENDORS, ONE TABLE, AND WHY THAT IS SAFE. The judge runs on OpenAI while the
generator stays on Anthropic, so a run's cost is a sum over two rate cards. They
share this module rather than getting one each because the alternative is two
``cost_usd`` functions, and the moment there are two, a caller can pick the wrong
one for a model and get a plausible number. Model ids are globally unique across
the two vendors, so one lookup cannot be ambiguous. What is NOT shared is
assumed-equal: :data:`PUBLISHED_CACHED_INPUT_USD_PER_MTOK` records OpenAI's
cached-input price as PUBLISHED, and a test asserts it equals the base rate times
:data:`CACHE_READ_MULTIPLIER` -- so if either vendor ever moves off 0.1x, that
fails loudly instead of mispricing every cached call in silence.

THE LONG-PROMPT SURCHARGE IS OPENAI-ONLY AND IS NOT A TIER. Above
:data:`LONG_PROMPT_THRESHOLD_TOKENS` prompt tokens, GPT-5.6 re-rates the WHOLE
request -- 2x input, 1.5x output -- not merely the excess above the line. And the
threshold is keyed to the whole prompt, cached tokens included: a 900K prompt
that is 95% cache hits is still a long prompt. Pricing the uncached remainder
against the threshold would let exactly the cheapest-looking requests slip under
it. No Claude model in this table has such a surcharge, and
:func:`long_prompt_surcharge_applies` returns False for every one of them rather
than leaving the question to the caller.

THE FOUR MULTIPLIERS. Cache reads bill at ~0.1x the base input rate; cache
writes at 1.25x for the default 5-minute TTL and 2x for the 1-hour TTL; the
Batch API halves everything. Only the 5-minute write premium is applied by
default because that is the TTL ``anthropic_transport.make_anthropic_call``
requests --
if the adapter ever asks for ``ttl: "1h"``, ``CACHE_WRITE_MULTIPLIER_1H`` is the
number to switch to, and the switch has to be deliberate.
"""
from __future__ import annotations

#: The day the rates below were read. Bump it with the rates, never separately.
PRICES_READ_ON = "2026-08-22"

#: model id -> (input $/1M, output $/1M). First-party vendor API list price.
#: Adding a row here is HALF of registering a model: ``completer._PROVIDER_BY_MODEL``
#: is the other half, and a test asserts every routable id is also priced,
#: because a routable-but-unpriced id fails one record deep into a paid run.
MODEL_PRICES_USD_PER_MTOK = {
    "claude-fable-5": (10.00, 50.00),
    "claude-mythos-5": (10.00, 50.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-opus-4-7": (5.00, 25.00),
    "claude-opus-4-6": (5.00, 25.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
    # OpenAI, Responses API list price. The judge model (openai_transport).
    "gpt-5.6-sol": (4.00, 20.00),
}

#: OpenAI publishes a cached-input rate DIRECTLY rather than as a multiplier of
#: the base rate. Recorded as published so the two statements can be checked
#: against each other instead of one being derived from the other and agreeing
#: with itself. A vendor that reprices cached input away from 0.1x breaks the
#: test, not the run's arithmetic.
PUBLISHED_CACHED_INPUT_USD_PER_MTOK = {
    "gpt-5.6-sol": 0.40,
}

CACHE_READ_MULTIPLIER = 0.1
CACHE_WRITE_MULTIPLIER_5M = 1.25
CACHE_WRITE_MULTIPLIER_1H = 2.0
BATCH_MULTIPLIER = 0.5

#: Prompt tokens above which the OpenAI long-prompt surcharge re-rates a request.
LONG_PROMPT_THRESHOLD_TOKENS = 272_000

#: What the surcharge does to a request that crosses the line: it re-rates the
#: WHOLE request, not the excess. Two separate numbers because input and output
#: are not scaled alike.
LONG_PROMPT_INPUT_MULTIPLIER = 2.0
LONG_PROMPT_OUTPUT_MULTIPLIER = 1.5

#: Models the surcharge applies to. A frozenset and not a prefix rule: a prefix
#: rule would silently surcharge an unrelated future ``gpt-`` id whose pricing
#: nobody has read.
LONG_PROMPT_SURCHARGE_MODELS = frozenset({
    "gpt-5.6-sol",
})

#: The context-window floor below which a prefix silently does not cache at all
#: -- no error, just ``cache_creation_input_tokens: 0``. Model-dependent and NOT
#: monotonic across generations, so it is tabulated rather than assumed: a
#: 3K-token prefix caches on Claude Opus 5 and silently does not on Opus 4.6.
MIN_CACHEABLE_PREFIX_TOKENS = {
    "claude-opus-5": 512,
    "claude-fable-5": 512,
    "claude-mythos-5": 512,
    "claude-opus-4-8": 1024,
    "claude-sonnet-5": 1024,
    "claude-sonnet-4-6": 1024,
    "claude-opus-4-7": 2048,
    "claude-opus-4-6": 4096,
    "claude-haiku-4-5": 4096,
}


def rates_for(model: str) -> "tuple[float, float] | None":
    """``(input, output)`` $/1M for ``model``, or None if the table lacks it."""
    return MODEL_PRICES_USD_PER_MTOK.get(str(model or "").strip())


def long_prompt_surcharge_applies(*, model: str, prompt_tokens: int) -> bool:
    """Whether ``prompt_tokens`` crosses this model's long-prompt line.

    ``prompt_tokens`` is the WHOLE prompt -- uncached remainder plus cache writes
    plus cache reads -- because that is what the provider measures against the
    threshold. Handing it the uncached remainder instead would let a heavily
    cached 900K-token prompt price as a short one, which is both wrong and
    wrong in the direction that flatters the run.
    """
    if str(model or "").strip() not in LONG_PROMPT_SURCHARGE_MODELS:
        return False
    return int(prompt_tokens or 0) > LONG_PROMPT_THRESHOLD_TOKENS


def cost_usd(*, model: str, input_tokens: int = 0, output_tokens: int = 0,
             cache_creation_input_tokens: int = 0,
             cache_read_input_tokens: int = 0,
             cache_write_multiplier: float = CACHE_WRITE_MULTIPLIER_5M,
             batch: bool = False,
             long_prompt: "bool | None" = None) -> "float | None":
    """Dollars for one call's usage, or None when the model's price is unknown.

    ``input_tokens`` is the UNCACHED REMAINDER, not the whole prompt: the API
    reports the three input figures disjointly and the total prompt is their
    sum. Adding cache tokens to ``input_tokens`` before calling here would
    double-bill every cached request, which is the exact error that makes a
    caching win look like a caching loss.

    ``long_prompt`` is normally left None and DERIVED from the three input
    figures, which is the only way to get it right for a single call: the
    threshold is measured against their sum. Pass it explicitly only where the
    caller knows something this function cannot -- a ledger totalling many calls
    knows that its sum is not one prompt, and must not let an accumulated total
    trip a per-request surcharge.
    """
    rates = rates_for(model)
    if rates is None:
        return None
    price_in, price_out = rates
    if long_prompt is None:
        long_prompt = long_prompt_surcharge_applies(
            model=model,
            prompt_tokens=(int(input_tokens or 0)
                           + int(cache_creation_input_tokens or 0)
                           + int(cache_read_input_tokens or 0)))
    if long_prompt:
        price_in *= LONG_PROMPT_INPUT_MULTIPLIER
        price_out *= LONG_PROMPT_OUTPUT_MULTIPLIER
    billable_input = (
        int(input_tokens or 0)
        + int(cache_creation_input_tokens or 0) * cache_write_multiplier
        + int(cache_read_input_tokens or 0) * CACHE_READ_MULTIPLIER
    )
    total = (billable_input * price_in + int(output_tokens or 0) * price_out) / 1e6
    return total * BATCH_MULTIPLIER if batch else total
