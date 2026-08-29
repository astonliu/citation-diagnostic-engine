"""cde/runtime/openai_transport.py -- the OpenAI provider adapter, Responses API only.

WHY A SECOND ADAPTER RATHER THAN A BRANCH IN THE FIRST. ``anthropic_transport``
is the module every taxonomy already reaches the provider through, and its value
is that it is ONE chokepoint. A provider branch inside it would keep the
chokepoint but put two request shapes, two usage conventions and two cache
mechanisms in one function; the request shapes share nothing but the prompt
string. So the SEAM stays single -- ``completer.make_completer`` is the one thing
callers touch -- and the transports stay separate. ``anthropic_transport`` is not
edited by this work at all, which is what keeps the Anthropic path byte-identical
in behaviour and every prior run's cost figure reproducible.

WHY THE JUDGE MOVES OFF THE ANTHROPIC PATH AT ALL. ``production_launcher``
already names the limitation in its own words: ``claude-opus-5`` judging coverage
of claims ``claude-opus-5`` extracted. A model scoring its own family's output
scores against its own preferences, and no amount of prompt discipline removes
that. Changing the CHECKPOINT would not have helped; changing the FAMILY is the
entire point of the change. The generator side is untouched and stays on
Anthropic -- otherwise the comparison is confounded from the other end.

RESPONSES API, NOT CHAT COMPLETIONS. Chat Completions is still supported and not
deprecated, so this is a choice, not a requirement: the ``max`` reasoning effort
is Responses-only and the cached-token counter sits at a DIFFERENT path on each
surface (``usage.input_tokens_details`` vs ``usage.prompt_tokens_details``).
Supporting both would double the accounting surface -- the part of this wiring
most able to fail silently -- for no capability this project uses.

NO TEMPERATURE, FOR A DIFFERENT REASON THAN DEC-070. On the pinned Claude model
the parameter is rejected as deprecated. Here it is rejected because GPT-5.6 is a
reasoning model and ``temperature`` is on OpenAI's unsupported-parameter list for
that family: a non-default value returns HTTP 400 ``unsupported_value``, "Only
the default (1) value is supported". ``top_p`` is unsupported the same way.
Neither is ever built into the request. The consequence has to be said plainly
rather than filed away: THERE IS NO GREEDY-DECODING CONTROL ON THIS MODEL. See
:data:`DETERMINISM_NOTE` -- nothing downstream may claim deterministic judging.

WHAT IS NOT SETTLED, and is enforced rather than assumed. ``usage.input_tokens``
on this API is the WHOLE prompt with ``cached_tokens`` as a subset, which is the
opposite of Anthropic's disjoint reporting and exactly the confusion that
double-bills a cached request. The docs do not state it in those words, so
:func:`usage_dict` does not take it on trust: it normalises to the repo's
disjoint convention and then CHECKS its own arithmetic against ``total_tokens``,
raising when the identity fails. An unverified accounting assumption that fails
loudly on the first live call is acceptable; one that returns a plausible number
is not.
"""
from __future__ import annotations

import os

from .anthropic_transport import CACHE_BREAK_MARKER
from .recording_adapter import TokenLedger

#: The model this project judges with. The CONCRETE id, never the ``gpt-5.6``
#: alias: the alias is documented as routing to Sol and can be repointed, and a
#: run pinned to an alias records a pin it does not have. There are no dated
#: snapshot ids published for this family, so this is the strongest available
#: reproducibility step -- not a strong one, which is the point of
#: :data:`DETERMINISM_NOTE`.
JUDGE_MODEL_ID = "gpt-5.6-sol"

#: Verbatim into the run manifest wherever a determinism claim would otherwise
#: go. A STRING and not a boolean, because "we could not pin this" is not a
#: value of "deterministic".
DETERMINISM_NOTE = (
    "NOT deterministic and not claimed to be. gpt-5.6-sol rejects temperature "
    "and top_p (reasoning-model family, HTTP 400 unsupported_value), no seed "
    "parameter is confirmed present or honoured on the Responses API for this "
    "model, and OpenAI's own seed documentation disclaims determinism where seed "
    "does exist ('Determinism is not guaranteed'). Reproducibility here means a "
    "pinned concrete model id, pinned reasoning/verbosity settings, and recorded "
    "raw responses -- NOT identical text on a repeat call. Any downstream claim "
    "of 'deterministic judging' is false for this path.")

#: ``reasoning.effort`` values the API accepts, lowest first.
REASONING_EFFORTS = ("none", "low", "medium", "high", "xhigh", "max")

#: What the judge runs at, and why the LOW end. Fewest reasoning tokens means
#: least run-to-run variation and least cost, and reasoning tokens bill as output
#: tokens at $20/MTok -- they are the dominant term in judge spend. ``xhigh`` and
#: ``max`` are not used unless a pilot shows they CHANGE VERDICTS; slower and
#: dearer at the same verdict is not an upgrade.
JUDGE_REASONING_EFFORT = "low"

#: ``text.verbosity``. A judge emits a verdict, not an essay: fewer output tokens
#: and fewer places for two runs to diverge.
JUDGE_VERBOSITY = "low"

#: ``reasoning.mode`` is left at its default ``standard`` and is never sent.
#: ``pro`` adds parallel model work, which adds variance to a stage whose whole
#: job is to be stable.

#: Below this many prompt tokens the cache does not engage AT ALL on GPT-5.6+ --
#: no error, ``cached_tokens`` simply stays 0. A strict minimum, unlike the
#: per-model floors in ``model_pricing.MIN_CACHEABLE_PREFIX_TOKENS``. A judge
#: prompt shorter than this will never report a cache hit, and the run log has to
#: say so rather than leave a permanent zero looking like a broken breakpoint.
MIN_CACHEABLE_PROMPT_TOKENS = 1_024

#: Ceiling on ``max_output_tokens`` for this model.
MAX_OUTPUT_TOKENS_CEILING = 128_000


class OpenAIUsageError(ValueError):
    """A usage block that cannot be trusted to price a call."""


def openai_client(api_key: str = "", *, max_retries: int = 2,
                  timeout: float = 600.0):
    """An ``openai.OpenAI``, with the retry budget written down rather than defaulted.

    ``max_retries`` is passed EXPLICITLY even though 2 is also the SDK's own
    default. The number governs how many times a 429 is silently paid for again,
    and a retry budget that lives in a vendor default is a cost decision nobody
    in this repo made. The SDK's backoff is exponential with jitter and honours
    ``Retry-After``, which is what the rate-limit guide asks for, so there is no
    hand-rolled loop here -- unlike ``run.complete``, which needs one because it
    has to convert an exhausted retry budget into ``""`` rather than an
    exception.

    ``timeout`` matches the SDK's 10-minute default and is likewise explicit: at
    ``max`` effort a single response can legitimately run for minutes, so this is
    a number that has to be chosen with the effort setting rather than inherited.
    """
    from openai import OpenAI
    key = api_key or os.environ.get("OPENAI_API_KEY")
    if not (key or "").strip():
        raise ValueError(
            "no OpenAI API key: pass api_key or set OPENAI_API_KEY. Refused "
            "here rather than at the first call, which would fail one record "
            "deep into a run.")
    return OpenAI(api_key=key, max_retries=max_retries, timeout=timeout)


def split_cache_blocks_openai(prompt: str):
    """``prompt`` -> ``(input items, prompt_cache_options or None)``.

    THE SAME SPLIT-POINT LOGIC AS THE ANTHROPIC PATH, and deliberately the same
    marker: :data:`~cde.runtime.anthropic_transport.CACHE_BREAK_MARKER` is already
    embedded in the rendered prompts, and a second marker for a second provider
    would mean the two providers no longer receive the same prompt. Only the
    thing EMITTED at the split changes -- ``cache_control`` there, an explicit
    ``prompt_cache_breakpoint`` here.

    The safety argument is also the same one, and is the reason this is worth
    asserting in a test rather than eyeballing: the block texts concatenate back
    to the prompt with the marker removed, so the model sees the token stream it
    would have seen with no breakpoint at all. A billing change, not a prompt
    change.

    ``prompt_cache_options: {"mode": "explicit"}`` accompanies a marked prompt.
    Without it the service ALSO places an implicit breakpoint at the latest user
    message, and the docs are explicit that once the mode is explicit "only
    explicit breakpoints are used for cache reads and writes" -- so the option is
    what makes the marker the operative one instead of a decoration.

    An unmarked prompt returns ``(prompt-as-one-block, None)``: no options, so
    the implicit breakpoint stands and caching still happens, just not where this
    module chose. Falling back to no caching would be a silent cost regression.
    """
    if not isinstance(prompt, str):
        raise ValueError("prompt must be a string")
    count = prompt.count(CACHE_BREAK_MARKER)
    if count > 1:
        # Same refusal as the Anthropic path and for the same reason: the API
        # allows several breakpoints, but a second marker in a prompt meant to
        # carry one is a mistake, and caching at the first would look like it
        # worked.
        raise ValueError(
            f"prompt carries {count} {CACHE_BREAK_MARKER} markers; this adapter "
            "places exactly one cache breakpoint and would silently ignore the "
            "rest")
    if count == 0:
        return [_user([_text(prompt)])], None
    head, tail = prompt.split(CACHE_BREAK_MARKER, 1)
    # An empty content block is a 400, and a breakpoint at either end of the
    # prompt caches nothing anyway: fall back to the implicit path rather than
    # sending a request the provider will refuse.
    if not head.strip() or not tail.strip():
        return [_user([_text(head + tail)])], None
    return ([_user([_text(head, breakpoint=True), _text(tail)])],
            {"mode": "explicit"})


def _text(text: str, *, breakpoint: bool = False) -> dict:
    block = {"type": "input_text", "text": text}
    if breakpoint:
        block["prompt_cache_breakpoint"] = {"mode": "explicit"}
    return block


def _user(content: list) -> dict:
    return {"role": "user", "content": content}


def usage_dict(usage) -> dict:
    """One Responses ``usage`` block -> this repo's canonical five counters.

    The four Anthropic names are kept EXACTLY, because ``model_pricing.cost_usd``
    and every cost record downstream are keyed on them. A fifth,
    ``reasoning_tokens``, is added; the Anthropic path reports 0 for it so the
    schema is uniform across providers rather than provider-shaped.

    THE MAPPING, and the one place it is not a rename:

    ==============================  ===================================
    canonical                       Responses source
    ==============================  ===================================
    ``input_tokens``                ``input_tokens`` MINUS the two below
    ``output_tokens``               ``output_tokens``
    ``cache_creation_input_tokens`` ``input_tokens_details.cache_write_tokens``
    ``cache_read_input_tokens``     ``input_tokens_details.cached_tokens``
    ``reasoning_tokens``            ``output_tokens_details.reasoning_tokens``
    ==============================  ===================================

    THE SUBTRACTION IS THE WHOLE FUNCTION. Anthropic reports its three input
    figures DISJOINTLY -- the prompt is their sum -- and ``cost_usd`` is written
    against that convention, pricing ``input_tokens`` at the full rate and the
    cache figures at their own multipliers. OpenAI reports ``input_tokens`` as
    the WHOLE prompt with ``cached_tokens`` a subset of it. Passing it through
    unchanged would bill every cached token twice: once at $4.00/MTok inside
    ``input_tokens`` and again at $0.40/MTok as a cache read. That error makes
    caching look like it costs money.

    AND IT IS CHECKED, NOT ASSUMED. The docs show the field nesting without
    stating the containment, so the subtraction rests on OpenAI's established
    convention rather than on a quoted sentence. So the identity
    ``input_tokens + output_tokens == total_tokens`` is asserted here on every
    call: it holds only if ``input_tokens`` is the whole prompt, and would fail
    if the figures were disjoint (the total would then also carry the cache
    counters). The first live call therefore CONFIRMS or REFUSES the convention
    this function is built on. That is what turns an unverified accounting
    assumption into a settled one, without a number ever being reported on the
    strength of a guess.

    ``reasoning_tokens`` is recorded and NOT added to anything. Reasoning tokens
    "are billed as output tokens" and are already counted inside
    ``output_tokens``; adding them would inflate every judge cost. They are kept
    because they are the dominant term in that cost and the only way to see an
    effort setting running away.

    A MISSING FIELD RAISES. It does not become 0. A judge run that prices at
    $0.00 because a field name moved is worse than a crash: the crash is fixed
    before the batch, the $0.00 is discovered in a paper.
    """
    if usage is None:
        raise OpenAIUsageError(
            "response carried no usage block; refusing to price a call at 0.00")

    def get(obj, name):
        value = getattr(obj, name, None)
        if value is None and isinstance(obj, dict):
            value = obj.get(name)
        return value

    def counter(obj, name, where: str) -> int:
        value = get(obj, name)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise OpenAIUsageError(
                f"usage.{where} is {value!r}, not a non-negative integer. The "
                "field name may have moved; a zero here would silently price "
                "this call at less than it cost.")
        return value

    in_details = get(usage, "input_tokens_details")
    out_details = get(usage, "output_tokens_details")
    if in_details is None or out_details is None:
        raise OpenAIUsageError(
            "usage is missing input_tokens_details or output_tokens_details; "
            "the cache and reasoning counters live there and this run cannot be "
            "costed without them")

    input_total = counter(usage, "input_tokens", "input_tokens")
    output_tokens = counter(usage, "output_tokens", "output_tokens")
    total_tokens = counter(usage, "total_tokens", "total_tokens")
    cached = counter(in_details, "cached_tokens",
                     "input_tokens_details.cached_tokens")
    cache_write = counter(in_details, "cache_write_tokens",
                          "input_tokens_details.cache_write_tokens")
    reasoning = counter(out_details, "reasoning_tokens",
                        "output_tokens_details.reasoning_tokens")

    if input_total + output_tokens != total_tokens:
        raise OpenAIUsageError(
            f"usage arithmetic does not hold: input_tokens={input_total} + "
            f"output_tokens={output_tokens} != total_tokens={total_tokens}. "
            "This module normalises on the documented convention that "
            "input_tokens is the WHOLE prompt with cached_tokens as a subset; "
            "if that stopped being true, the subtraction below would misprice "
            "every cached call, so it refuses instead.")
    if reasoning > output_tokens:
        raise OpenAIUsageError(
            f"reasoning_tokens={reasoning} exceeds output_tokens="
            f"{output_tokens}; they are documented as billed within output "
            "tokens, and if that inverted the output figure is not what was "
            "billed")
    uncached = input_total - cached - cache_write
    if uncached < 0:
        raise OpenAIUsageError(
            f"input_tokens={input_total} is smaller than cached_tokens={cached} "
            f"+ cache_write_tokens={cache_write}, so the two cache counters are "
            "not subsets of it as assumed. Refusing rather than reporting a "
            "negative uncached remainder as a cost.")
    return {
        "input_tokens": uncached,
        "output_tokens": output_tokens,
        "cache_creation_input_tokens": cache_write,
        "cache_read_input_tokens": cached,
        "reasoning_tokens": reasoning,
    }


def make_openai_call(client, model: str = JUDGE_MODEL_ID,
                     max_output_tokens: int = 1024, *,
                     stage: str = "", token_ledger=None,
                     effort: str = JUDGE_REASONING_EFFORT,
                     verbosity: str = JUDGE_VERBOSITY,
                     prompt_cache_key: str = ""):
    """Adapt an ``openai.OpenAI`` client into the same ``Callable[[str], str]``.

    THE SAME CONTRACT AS ``make_anthropic_call``, down to the two attributes it
    hangs on the returned callable (``token_ledger``, ``stage``), so a call site
    cannot tell which provider it got. That is the requirement -- nothing in the
    calling code learns which provider is live -- and it is why the seam
    signature is not widened here.

    NO ``instructions``, and this is a scope decision rather than an oversight.
    The Responses API takes a system prompt at top level, and it is tempting to
    lift the rendered prompt's leading rubric into it. The Anthropic adapter
    sends NO system parameter -- the whole rendered prompt is one user message --
    so splitting it here would send this model a differently-shaped prompt than
    the model it is being compared against, and the comparison would be
    confounded by the wiring. Same prompt, different model, or the measurement
    means nothing.

    ``max_output_tokens`` REPLACES ``max_tokens``, which is on the unsupported
    list for this family and is never sent. It must be sized to the verdict
    schema and no larger: reasoning tokens come out of this same budget, so a
    tight cap both bounds cost and truncates a runaway. There is no streaming
    branch as there is on the Anthropic path -- that exists to dodge an SDK
    non-streaming timeout ceiling derived from ``max_tokens``, which is an
    Anthropic SDK property and has no analogue here.

    ``prompt_cache_key`` routes same-prefix requests to the same cache. Pass a
    STABLE string naming the prompt family (the judge prompt version, say), not
    a per-record one: a unique key per call defeats the point. Keep traffic to
    roughly 15 requests per minute per key, per the docs. It is sent whether or
    not the prompt carries a breakpoint, because it improves matching on the
    implicit path too.
    """
    if effort not in REASONING_EFFORTS:
        raise ValueError(
            f"effort must be one of {list(REASONING_EFFORTS)}, got {effort!r}")
    if verbosity not in ("low", "medium", "high"):
        raise ValueError(
            f"verbosity must be low, medium or high, got {verbosity!r}")
    if not isinstance(max_output_tokens, int) or not (
            0 < max_output_tokens <= MAX_OUTPUT_TOKENS_CEILING):
        raise ValueError(
            f"max_output_tokens must be 1..{MAX_OUTPUT_TOKENS_CEILING}, got "
            f"{max_output_tokens!r}")
    ledger = (token_ledger if token_ledger is not None
              else TokenLedger(stage=stage, model=model))

    def call_llm(prompt: str) -> str:
        items, cache_options = split_cache_blocks_openai(prompt)
        request = {
            "model": model,
            "input": items,
            "max_output_tokens": max_output_tokens,
            # The two controls that REPLACE temperature and top_p. Sent
            # explicitly rather than left to the provider default of medium
            # effort, because the default is the expensive one.
            "reasoning": {"effort": effort},
            "text": {"verbosity": verbosity},
        }
        if cache_options is not None:
            request["prompt_cache_options"] = cache_options
        if prompt_cache_key:
            request["prompt_cache_key"] = prompt_cache_key
        resp = client.responses.create(**request)
        # AFTER the call, matching the Anthropic adapter: this records what was
        # BILLED, and a request that raised has no usage block to read. The paid
        # -call meter books attempts BEFORE the transport, for the opposite
        # reason -- an attempt that raised was still paid for.
        ledger.record_openai_usage(getattr(resp, "usage", None))
        text = getattr(resp, "output_text", None)
        if not (text or "").strip():
            # A judge that returns "" parses to an abstention, and an abstention
            # caused by a truncated or refused response is indistinguishable
            # from one the model meant. Raising keeps a silent empty judgment out
            # of the results -- the caller's retry policy can decide what to do,
            # which is not this transport's decision to make.
            raise ValueError(
                f"gpt judge returned no output text (stage={stage!r}, "
                f"status={getattr(resp, 'status', None)!r}, incomplete_details="
                f"{getattr(resp, 'incomplete_details', None)!r}). An empty "
                "reply parses to an abstention and would be indistinguishable "
                "from a real one; the most likely cause is max_output_tokens "
                "exhausted by reasoning tokens.")
        return text

    call_llm.token_ledger = ledger
    call_llm.stage = stage
    return call_llm
