"""cde/runtime/anthropic_transport.py -- the provider adapter, with caching and usage.

WHY A NEW MODULE AND NOT AN EDIT TO ``band_prompts``. ``band_prompts.py`` is the
FROZEN SUBSTRATE: ``mint_v1.derive_source_blob_oid`` pins its committed blob OID
against ``semantic_validator_v1.FROZEN_SOURCE_BLOB_OID``, both frozen prompt
packages seal that OID, ``test_band_prompts_blob_oid_is_unchanged`` asserts it,
and roughly a dozen specs restate it as an acceptance condition. Editing that
file at all drifts the seal, so the adapter that needs to change lives here --
the same move ``coverage_prompts_v3`` and ``coverage_aggregate`` already made for
the same reason. ``band_prompts.make_anthropic_call`` is unchanged and remains
correct; it simply cannot grow.

THIS IS STILL ONE CHOKEPOINT. Every taxonomy reaches the provider through one
adapter -- claim extraction and coverage, the F7 generator and verifier, and the
F5 generator, verifier and candidate screen. All of those call sites import from
here, so a change made once still reaches all of them. That property is the whole reason the
caching work was cheap; losing it to per-taxonomy adapters would not have been.

WHAT IT ADDS OVER THE FROZEN ONE, and nothing else:

* A CACHE BREAKPOINT, at the boundary a prompt marks with
  :data:`CACHE_BREAK_MARKER`. The marker is removed and the resulting content
  blocks concatenate back to the original bytes, so the model sees exactly the
  token stream it saw before -- a billing change, not a prompt change. A prompt
  with no marker is sent as one bare string, byte-for-byte as before.
* USAGE, from ``response.usage``, onto a :class:`TokenLedger`. Without
  ``cache_read_input_tokens`` a breakpoint that silently stopped matching is
  indistinguishable from one that is working, except that the run pays the 1.25x
  write premium on every request and reads nothing back.

NO TEMPERATURE, and no other request parameter either (DEC-070): the pinned Opus
model rejects an explicit temperature and the frozen adapter sends none. Nothing
here changes what is sent except the content-block split described above.
"""
from __future__ import annotations

from .recording_adapter import TokenLedger

#: The literal a prompt embeds at the END of its stable region so this adapter
#: can place a cache breakpoint there.
#:
#: WHY A SENTINEL IN THE PROMPT, and not a wider seam type. The seam contract is
#: ``Callable[[str], str]`` -- one prompt string, one reply string, at seven call
#: sites with a test double at each. ``cache_control`` must sit on a
#: CONTENT-BLOCK boundary and a single string has none. Widening the seam to a
#: list of blocks is the textbook fix and touches every seam and every double; a
#: per-taxonomy adapter duplicates the transport and loses the single chokepoint.
#: A marker in the prompt text costs one function and leaves every signature
#: alone.
#:
#: THE MARKER IS NEVER SENT, and a marked prompt's blocks concatenate back to the
#: unmarked bytes. That is what makes adding one a cost change.
CACHE_BREAK_MARKER = "<<<CACHE_BREAK>>>"


def split_cache_blocks(prompt: str):
    """``prompt`` -> what to hand the API as message content.

    Returns the bare string when there is nothing to cache, or a two-block list
    with ``cache_control`` on the first block. ``"".join(block texts)`` always
    equals ``prompt`` with the marker removed, and
    ``test_prompt_caching_and_usage`` asserts exactly that against the real
    rendered F5 prompt, because it is the whole safety argument.

    A prompt with MORE THAN ONE marker raises. The API allows up to four
    breakpoints, but a second marker in a prompt meant to have one is a mistake,
    and caching at the first would look like it worked.
    """
    if not isinstance(prompt, str):
        raise ValueError("prompt must be a string")
    count = prompt.count(CACHE_BREAK_MARKER)
    if count == 0:
        return prompt
    if count > 1:
        raise ValueError(
            f"prompt carries {count} {CACHE_BREAK_MARKER} markers; this adapter "
            "places exactly one cache breakpoint and would silently ignore the "
            "rest")
    head, tail = prompt.split(CACHE_BREAK_MARKER, 1)
    # An empty text block is a 400, and a breakpoint at either end of the prompt
    # caches nothing anyway: fall back to the unmarked path rather than sending
    # a request the provider will refuse.
    if not head.strip() or not tail.strip():
        return head + tail
    return [
        {"type": "text", "text": head,
         "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": tail},
    ]


#: Above this ``max_tokens`` the SDK REFUSES a non-streaming request outright
#: (``_calculate_nonstreaming_timeout``: 3600 * max_tokens / 128000 > 600), so a
#: large output budget is not a tuning choice, it is a streaming requirement.
#: Recorded here as a number with its derivation because the SDK raises a
#: ValueError about minutes, which reads like a timeout rather than a ceiling.
NONSTREAMING_MAX_TOKENS_CEILING = 21_333


def make_anthropic_call(client, model: str, max_tokens: int = 1024, *,
                        stage: str = "", token_ledger=None,
                        stream: bool = False):
    """Adapt an ``anthropic.Anthropic`` client into a ``Callable[[str], str]``.

    Same contract, same request shape and the same omitted parameters as
    ``band_prompts.make_anthropic_call``; pin ``model`` in the run manifest
    alongside the prompt versions, because no measurement is conditional on
    fewer than all of them.

    ``stage`` names this transport in a merged usage report. Pass a DISTINCT one
    per adapter: ``merge_token_ledgers`` refuses to collapse two transports into
    one row, because a report that silently added the screen's tokens to the
    judge's could not say what the screen cost.

    ``stream`` is REQUIRED above :data:`NONSTREAMING_MAX_TOKENS_CEILING` and is
    checked here rather than left to fail at the first live call. It changes the
    transport, not the request: the same model, the same content blocks, the same
    omitted parameters, and ``usage`` read off the assembled final message. The
    batched candidate screen is the one caller that needs it -- its reply grows
    with the batch, and a 400-candidate batch wants more output budget than a
    non-streaming request is allowed to ask for.

    Streaming does not weaken prompt caching: a cache entry becomes readable once
    the first response BEGINS streaming, which is if anything earlier than the
    non-streaming path makes it available.
    """
    if not stream and max_tokens > NONSTREAMING_MAX_TOKENS_CEILING:
        raise ValueError(
            f"max_tokens={max_tokens} needs stream=True: the SDK refuses a "
            f"non-streaming request above {NONSTREAMING_MAX_TOKENS_CEILING} "
            f"tokens, and it refuses it at the first live call rather than here")
    ledger = (token_ledger if token_ledger is not None
              else TokenLedger(stage=stage, model=model))

    def call_llm(prompt: str) -> str:
        request = {
            "model": model, "max_tokens": max_tokens,
            "messages": [{"role": "user",
                          "content": split_cache_blocks(prompt)}],
        }
        if stream:
            with client.messages.stream(**request) as stream_handle:
                resp = stream_handle.get_final_message()
        else:
            resp = client.messages.create(**request)
        # AFTER the call, deliberately. Unlike the paid-call meter -- which books
        # an attempt BEFORE the transport because an attempt that raised was
        # still paid for -- this records what was BILLED, and a request that
        # raised has no usage block to read. The meter counts attempts; this
        # counts tokens.
        ledger.record_usage(getattr(resp, "usage", None))
        return "".join(b.text for b in resp.content
                       if getattr(b, "type", None) == "text")

    call_llm.token_ledger = ledger
    call_llm.stage = stage
    return call_llm
