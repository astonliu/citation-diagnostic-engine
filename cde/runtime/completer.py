"""cde/runtime/completer.py -- one seam, two providers, chosen by the model id.

WHAT THIS IS FOR. Seven call sites across five taxonomies inject a
``Callable[[str], str]``. Until now every one of them built that callable by
calling ``anthropic_transport.make_anthropic_call`` directly, so moving ONE stage
to another provider would have meant editing every site that might host it. This
is the single place that decision is made, and it is the only thing a call site
touches. Nothing downstream of it can tell which provider is live, which is the
requirement: a taxonomy's behaviour must not be conditional on the transport
underneath it.

PROVIDER IS DERIVED FROM THE MODEL ID, NOT PASSED ALONGSIDE IT. Model ids are
already globally unique across the two providers, so a separate ``provider=``
argument would be a second source of truth that can disagree with the first --
and the failure when it disagrees is a request built for one provider and sent to
the other. One lookup, no argument. An id the table does not know RAISES; there
is no default provider, because defaulting would send an unrecognised id to
whichever vendor happened to be listed first and report the run under it.

WHAT IS DELIBERATELY NOT UNIFIED. ``max_tokens`` versus ``max_output_tokens``,
streaming, and the cache-block shape stay inside their own transports. This
module dispatches; it does not translate. A translation layer that made the two
request shapes look identical would have to decide what an Anthropic-only
argument means on OpenAI, and every such decision is a place the two paths quietly
stop being comparable.
"""
from __future__ import annotations

from . import openai_transport
from .anthropic_transport import make_anthropic_call

#: model id -> provider. THE one source of truth for the routing decision.
#: Anthropic ids are listed from ``model_pricing.MODEL_PRICES_USD_PER_MTOK``
#: rather than pattern-matched on the ``claude-`` prefix: a prefix rule silently
#: accepts a typo'd id (``claude-opus-6``) and routes it somewhere, where an
#: explicit table refuses it.
_PROVIDER_BY_MODEL = {
    "claude-fable-5": "anthropic",
    "claude-mythos-5": "anthropic",
    "claude-opus-5": "anthropic",
    "claude-opus-4-8": "anthropic",
    "claude-opus-4-7": "anthropic",
    "claude-opus-4-6": "anthropic",
    "claude-sonnet-5": "anthropic",
    "claude-sonnet-4-6": "anthropic",
    "claude-haiku-4-5": "anthropic",
    "gpt-5.6-sol": "openai",
}

#: Ids this project will NOT route, with the reason, so the refusal explains
#: itself instead of reading as an omission. An alias is refused because a run
#: pinned to one records a pin it does not have.
_REFUSED_MODELS = {
    "gpt-5.6": (
        "an ALIAS documented as routing to gpt-5.6-sol, not a concrete id. It "
        "can be repointed, so a run pinned to it is not pinned. Use "
        "'gpt-5.6-sol'."),
    "gpt-5.6-terra": "out of scope for this project; only Sol is wired.",
    "gpt-5.6-luna": "out of scope for this project; only Sol is wired.",
    "gpt-5.6-cyber": "out of scope for this project; only Sol is wired.",
}


def provider_for(model: str) -> str:
    """``"anthropic"`` or ``"openai"``. Raises on anything else.

    Kept public because the manifest has to record WHICH PROVIDER a stage ran
    against, and deriving that at the record site from the same table the
    transport used is how the two cannot disagree.
    """
    key = str(model or "").strip()
    if key in _PROVIDER_BY_MODEL:
        return _PROVIDER_BY_MODEL[key]
    if key in _REFUSED_MODELS:
        raise ValueError(f"model {key!r} is refused: {_REFUSED_MODELS[key]}")
    raise ValueError(
        f"unknown model id {key!r}: no provider is registered for it. Add it to "
        f"completer._PROVIDER_BY_MODEL AND to "
        f"model_pricing.MODEL_PRICES_USD_PER_MTOK -- there is deliberately no "
        f"default provider, because guessing one would run the model and report "
        f"it under the wrong vendor's rates. Known: "
        f"{sorted(_PROVIDER_BY_MODEL)}")


def is_same_family(model_a: str, model_b: str) -> bool:
    """Whether two model ids come from the same provider family.

    EXISTS BECAUSE THE JUDGE SWAP TURNS ON EXACTLY THIS QUESTION. A judge must
    not be the same family as the model that generated or surfaced what it is
    judging -- same-family judging lets a model's own preferences score its own
    output, and changing the checkpoint within a family does not fix it. The
    F5/F7 bundles already refuse ``generator is verifier``, but that is an OBJECT
    identity test and two callables over the same model pass it. This is the
    test that does not.
    """
    return provider_for(model_a) == provider_for(model_b)


def make_completer(model: str, api_key: str = "", max_tokens: int = 1024, *,
                   stage: str = "", token_ledger=None, stream: bool = False,
                   client=None, prompt_cache_key: str = ""):
    """Build the injected ``Callable[[str], str]`` for ``model``.

    ONE CONTRACT, BOTH PROVIDERS: one prompt string in, one response string out,
    with ``.token_ledger`` and ``.stage`` hung on the returned callable and usage
    recorded into the same ledger type either way. No call site changes and none
    of them learn which provider answered.

    ``max_tokens`` is the output budget under BOTH providers -- it reaches
    Anthropic as ``max_tokens`` and OpenAI as ``max_output_tokens``, which is the
    one rename this module does perform, because the two mean the same thing and
    ``max_tokens`` is on OpenAI's unsupported list for reasoning models. Note
    that the budgets are not equivalent in practice: on the OpenAI path reasoning
    tokens are drawn from it, so a cap that comfortably fits a verdict on Claude
    can be exhausted before any verdict is emitted here. That failure is loud --
    the transport raises on empty output text rather than returning ``""``.

    ``stream`` is an ANTHROPIC-ONLY argument and is refused, not ignored, on the
    OpenAI path. It exists to dodge a ceiling the Anthropic SDK derives from
    ``max_tokens``; there is no analogue here, so accepting and dropping it would
    let a caller believe it had asked for something.

    ``client`` is for tests: pass a double and no real client is constructed. In
    production it stays None and each completer gets its own client, which is
    what keeps two roles' connection state independent.
    """
    provider = provider_for(model)
    if provider == "anthropic":
        if client is None:
            from anthropic import Anthropic
            import os
            client = Anthropic(
                api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))
        return make_anthropic_call(client, model, max_tokens, stage=stage,
                                   token_ledger=token_ledger, stream=stream)
    if stream:
        raise ValueError(
            f"stream=True is an Anthropic-only transport option and does not "
            f"apply to {model!r}; it exists to dodge the Anthropic SDK's "
            "non-streaming max_tokens ceiling, which has no analogue on the "
            "Responses API. Dropping it silently would let this call site "
            "believe it had asked for streaming.")
    if client is None:
        client = openai_transport.openai_client(api_key)
    return openai_transport.make_openai_call(
        client, model, max_tokens, stage=stage, token_ledger=token_ledger,
        prompt_cache_key=prompt_cache_key)
