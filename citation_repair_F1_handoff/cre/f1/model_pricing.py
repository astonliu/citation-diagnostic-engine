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

RATES ARE PER MILLION TOKENS, USD, first-party Claude API list price.
Read 2026-08-22 from the Anthropic pricing reference bundled with the
``claude-api`` skill (Current Models table). Amazon Bedrock and Vertex AI are
partner-operated with separate pricing and are NOT covered here; a run against
those endpoints must not be costed with this table.

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

#: model id -> (input $/1M, output $/1M). First-party Claude API list price.
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
}

CACHE_READ_MULTIPLIER = 0.1
CACHE_WRITE_MULTIPLIER_5M = 1.25
CACHE_WRITE_MULTIPLIER_1H = 2.0
BATCH_MULTIPLIER = 0.5

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


def cost_usd(*, model: str, input_tokens: int = 0, output_tokens: int = 0,
             cache_creation_input_tokens: int = 0,
             cache_read_input_tokens: int = 0,
             cache_write_multiplier: float = CACHE_WRITE_MULTIPLIER_5M,
             batch: bool = False) -> "float | None":
    """Dollars for one call's usage, or None when the model's price is unknown.

    ``input_tokens`` is the UNCACHED REMAINDER, not the whole prompt: the API
    reports the three input figures disjointly and the total prompt is their
    sum. Adding cache tokens to ``input_tokens`` before calling here would
    double-bill every cached request, which is the exact error that makes a
    caching win look like a caching loss.
    """
    rates = rates_for(model)
    if rates is None:
        return None
    price_in, price_out = rates
    billable_input = (
        int(input_tokens or 0)
        + int(cache_creation_input_tokens or 0) * cache_write_multiplier
        + int(cache_read_input_tokens or 0) * CACHE_READ_MULTIPLIER
    )
    total = (billable_input * price_in + int(output_tokens or 0) * price_out) / 1e6
    return total * BATCH_MULTIPLIER if batch else total
