"""The recording adapter: one shared receipt across every injected callable.

WHY THIS IS NOT SCAFFOLDING. ``production_launcher.verify_receipt`` runs AFTER
``run_natural_judgment`` returns, so a mis-shaped receipt burns the entire run
before refusing. Until now the only implementation was a four-line stub in a
test file, and every caller had to hand-roll one -- which is exactly how a
receipt ends up mis-shaped.

THE CONTRACT, read off ``verify_receipt``:

* ``.calls`` is a list of dicts, one per provider call;
* every dict carries ``"model"``, equal to the launched model;
* on a temperature-**supporting** model, ``"temperature": 0`` is present;
* on a temperature-**rejecting** model the ``"temperature"`` key is **ABSENT** --
  not ``None``, absent. ``verify_receipt`` refuses any call that merely carries
  the key, because unsupported means the field was never sent;
* same discipline for ``assistant_prefill`` (DEC-071).

EVERY SEAM, NOT THE OBVIOUS ONES. ``wrap_all`` covers the extractor, both
coverage judges, the discriminator, the F4 verifier and any F5/F7 seam callable,
into ONE shared receipt -- so a call made through a seam nobody remembered still
lands in it. A receipt that silently misses a seam is worse than no receipt: it
looks like a complete record of what ran.

WHAT IT CANNOT DO. It records what the wrapped callable was ASKED to do. It
cannot prove the callable contacted the provider it claims. Owning the transport
is the only thing that would, and that is outside this repo.
"""
from __future__ import annotations

import functools
import threading

from . import model_pricing

#: Mirrors production_launcher's sentinels without importing it (the launcher
#: imports judgment_run, and this module must stay usable on its own).
UNSUPPORTED = "unsupported"

#: Mirrors judgment_run's cost_counters sentinel. An absent measurement and a
#: measured zero are different claims and must not share a value.
NOT_COLLECTED = "not_collected"


class AdapterReceipt:
    """A shared, append-only log of what every wrapped callable was asked to send.

    ``model`` is fixed at construction: a receipt records ONE launched model, so
    a stray call under a different id shows up in ``verify_receipt`` as an
    unauthorized model rather than being quietly absorbed.

    ``temperature`` / ``assistant_prefill`` take the RESOLVED values from the
    launcher's governance blocks -- a number, or the string ``"unsupported"``, or
    ``None`` for "not applicable to this run". Anything resolved to
    ``"unsupported"`` or ``None`` is OMITTED from each call dict, never written
    as a null, because absent and null are different claims.
    """

    def __init__(self, *, model: str, temperature=None, assistant_prefill=None):
        if not (model or "").strip():
            raise ValueError("AdapterReceipt requires a nonblank model id")
        self.model = model
        self.temperature = temperature
        self.assistant_prefill = assistant_prefill
        self.calls: list = []
        self._lock = threading.Lock()

    def _base(self) -> dict:
        call: dict = {"model": self.model}
        # ABSENT, not None: verify_receipt refuses a call that carries the key
        # at all on a rejecting model.
        if self.temperature is not None and self.temperature != UNSUPPORTED:
            call["temperature"] = self.temperature
        if (self.assistant_prefill is not None
                and self.assistant_prefill != UNSUPPORTED):
            call["assistant_prefill"] = self.assistant_prefill
        return call

    def record(self, *, seam: str, **extra) -> dict:
        """Append one call. ``seam`` names which injected callable made it."""
        call = self._base()
        call["seam"] = seam
        call.update(extra)
        with self._lock:
            self.calls.append(call)
        return call

    # -- wrapping ---------------------------------------------------------
    def wrap(self, fn, *, seam: str):
        """Wrap one injected callable so every invocation is recorded.

        The call is recorded BEFORE the wrapped callable runs, so a provider
        error still leaves evidence that the attempt was made -- a receipt that
        only logs successes would understate what was sent.
        """
        if fn is None:
            return None

        @functools.wraps(fn)
        def recorded(*args, **kwargs):
            self.record(seam=seam)
            return fn(*args, **kwargs)

        return recorded

    def wrap_all(self, seams: dict) -> dict:
        """Wrap a whole ``{name: callable}`` mapping into this one receipt.

        ``None`` values pass through untouched, so an unwired seam stays unwired
        -- wrapping it would make ``judgment_run``'s pairwise gates think it was
        supplied and silently change which discriminators run.
        """
        return {name: self.wrap(fn, seam=name) for name, fn in seams.items()}

    def summary(self) -> dict:
        with self._lock:
            calls = list(self.calls)
        by_seam: dict = {}
        for c in calls:
            k = c.get("seam", "?")
            by_seam[k] = by_seam.get(k, 0) + 1
        return {"total_calls": len(calls),
                "calls_by_seam": dict(sorted(by_seam.items())),
                "model": self.model}


#: The seam names ``judgment_run`` accepts as injected callables. Wrapping the
#: whole set is the point: a call through a seam nobody remembered still lands
#: in the receipt.
RUN_SEAMS = (
    "extractor", "coverage_judge", "coverage_judge_v3", "fetch_abstract",
    "fetch_reflist", "fetch_fulltext", "discriminator_call_llm",
    "f4_verifier_call_llm", "f3_fetch_reflist", "f3_resolve_pmcid",
    "pubtypes_lookup",
)


def wrap_run_seams(receipt: AdapterReceipt, **seams):
    """Wrap the ``run_natural_judgment`` seam set; returns kwargs to splat.

    Unknown names raise rather than passing through: a typo'd seam name would
    otherwise be forwarded unwrapped and its calls would vanish from the
    receipt, which is precisely the silent gap this module exists to close.
    """
    unknown = sorted(set(seams) - set(RUN_SEAMS))
    if unknown:
        raise ValueError(
            f"unknown seam name(s) {unknown}; a typo'd seam would be forwarded "
            f"UNWRAPPED and its calls would be missing from the receipt. "
            f"Known seams: {list(RUN_SEAMS)}")
    return receipt.wrap_all(seams)


class TokenLedger:
    """A thread-safe running total of what one transport actually billed.

    WHY THIS EXISTS AT ALL. ``judgment_run``'s F5 block has carried
    ``cost_counters.input_tokens`` / ``output_tokens`` / ``cost_usd`` as the
    literal string ``"not_collected"`` since it was written. The slots are a
    declared contract nothing ever filled, so every cost statement about this
    system to date has been a model built on an ASSUMED output length. This
    fills them from ``response.usage``, which is the only honest source.

    WHY IT ALSO CARRIES THE TWO CACHE FIELDS. ``cache_read_input_tokens`` is the
    single piece of evidence that prompt caching is working. Without it a
    breakpoint that silently stopped matching looks identical to one that never
    stopped, except that the run is paying the 1.25x write premium on every
    request and reading nothing back. A caching change that cannot be verified
    is not a saving, it is a claim.

    WHY THREAD-SAFE BUT NOT THREAD-LOCAL, unlike ``PaidCallMeter``. The meter is
    thread-local because a per-RECORD count has to be attributed to the worker
    that produced that record. This is a per-RUN total over one transport, so
    every thread's spend belongs in it; a lock is what that needs.

    ``stage`` names which transport this is (``f5_generator``, ``f5_verifier``,
    ``band``...) so a merged report can say where the money went instead of only
    how much there was.
    """

    __slots__ = ("stage", "model", "calls", "input_tokens", "output_tokens",
                 "cache_creation_input_tokens", "cache_read_input_tokens",
                 "reasoning_tokens", "usage_missing_calls", "_surcharged",
                 "_lock")

    #: The four token counters that make up one call's billable usage, in the
    #: order ``snapshot`` and ``merge_token_ledgers`` report them.
    _COUNTERS = ("input_tokens", "output_tokens",
                 "cache_creation_input_tokens", "cache_read_input_tokens")

    def __init__(self, *, stage: str = "", model: str = ""):
        self.stage = str(stage or "")
        self.model = str(model or "")
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.cache_creation_input_tokens = 0
        self.cache_read_input_tokens = 0
        # Recorded, never added to output_tokens: reasoning tokens bill AS
        # output tokens and are already inside that figure. Adding them would
        # inflate every judge cost. They are kept because they are the dominant
        # term in that cost and the only way to see an effort setting run away.
        # Always present, 0 on the Anthropic path, so one row shape serves both
        # providers and a reader need not know which one produced it.
        self.reasoning_tokens = 0
        # A response whose usage block was absent or unreadable. Counted, not
        # skipped: a ledger that silently dropped it would understate spend and
        # read as a complete measurement.
        self.usage_missing_calls = 0
        # Tokens from calls that crossed the OpenAI long-prompt threshold, held
        # SEPARATELY rather than folded into the totals above. The surcharge is
        # a property of one REQUEST, and this ledger is a running sum over many:
        # pricing the sum would surcharge a thousand short calls that happen to
        # total 272K, and would miss a single long call inside a small total.
        # Two buckets, each priced under its own rates, is the only way a
        # per-request rule survives accumulation.
        self._surcharged = dict.fromkeys(self._COUNTERS, 0)
        self._lock = threading.Lock()

    def record_usage(self, usage) -> None:
        """Add one response's ``usage`` block. Never raises on a odd shape."""
        def field(name: str) -> "int | None":
            value = getattr(usage, name, None)
            if value is None and isinstance(usage, dict):
                value = usage.get(name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                return None
            return value

        prompt_side = [field(name) for name in (
            "input_tokens", "cache_creation_input_tokens",
            "cache_read_input_tokens")]
        completion = field("output_tokens")
        with self._lock:
            self.calls += 1
            if all(value is None for value in prompt_side) and completion is None:
                self.usage_missing_calls += 1
                return
            self.input_tokens += prompt_side[0] or 0
            self.cache_creation_input_tokens += prompt_side[1] or 0
            self.cache_read_input_tokens += prompt_side[2] or 0
            self.output_tokens += completion or 0

    def record_openai_usage(self, usage) -> None:
        """Add one Responses ``usage`` block, normalised by ``openai_transport``.

        WHY THIS IS A SECOND METHOD AND NOT A BRANCH IN ``record_usage``. The two
        providers do not merely spell their counters differently, they report
        them under OPPOSITE conventions: Anthropic's three input figures are
        disjoint and sum to the prompt, OpenAI's ``input_tokens`` IS the prompt
        with the cache figures carved out of it. ``record_usage`` is tolerant by
        design -- it absorbs an odd shape rather than killing a run over a
        renamed field. That tolerance is exactly wrong here: absorbing a
        mis-shaped OpenAI block would skip the containment check the whole
        subtraction rests on, and quietly bill every cached token twice.

        SO THE TWO TOLERANCES DIFFER, DELIBERATELY. An ABSENT usage block is
        counted as missing, same as on the Anthropic path -- the run continues
        and ``cost_usd`` becomes ``"not_collected"``, because a total missing one
        term is unknown, not smaller. A usage block that is PRESENT but fails its
        own arithmetic RAISES: it is evidence that the convention this module
        prices under has changed, and continuing would produce a number that
        looks right.

        The import is local because ``openai_transport`` imports this module for
        :class:`TokenLedger`; at module scope the two would deadlock on import.
        """
        if usage is None:
            with self._lock:
                self.calls += 1
                self.usage_missing_calls += 1
            return
        from . import openai_transport
        counts = openai_transport.usage_dict(usage)
        prompt_tokens = (counts["input_tokens"]
                         + counts["cache_creation_input_tokens"]
                         + counts["cache_read_input_tokens"])
        surcharged = model_pricing.long_prompt_surcharge_applies(
            model=self.model, prompt_tokens=prompt_tokens)
        with self._lock:
            self.calls += 1
            self.reasoning_tokens += counts["reasoning_tokens"]
            for name in self._COUNTERS:
                setattr(self, name, getattr(self, name) + counts[name])
                if surcharged:
                    self._surcharged[name] += counts[name]

    def snapshot(self) -> dict:
        """The ledger as a dict, with ``cost_usd`` priced from the table.

        ``cost_usd`` is ``"not_collected"`` -- never 0.0 -- when the model id is
        absent or unpriced, or when any call came back without usage. A partial
        total presented as a total is worse than no total.
        """
        with self._lock:
            row = {
                "stage": self.stage,
                "model": self.model,
                "calls": self.calls,
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
                "cache_creation_input_tokens": self.cache_creation_input_tokens,
                "cache_read_input_tokens": self.cache_read_input_tokens,
                "reasoning_tokens": self.reasoning_tokens,
                "usage_missing_calls": self.usage_missing_calls,
            }
            surcharged = dict(self._surcharged)
        row["prompt_tokens_total"] = (
            row["input_tokens"] + row["cache_creation_input_tokens"]
            + row["cache_read_input_tokens"])
        # Reported beside the dollars it produced. A cost figure that silently
        # doubled reads like a run that used twice the tokens, and the token
        # counts sitting next to it would say otherwise -- so the flag is what
        # makes the two legible together.
        row["long_prompt_surcharge_applied"] = any(surcharged.values())
        priced = None
        if not row["usage_missing_calls"]:
            # Each bucket at its own rates. `long_prompt` is passed EXPLICITLY
            # in both calls rather than derived: the derivation is per-request
            # and these are sums, so letting cost_usd infer it here is the one
            # way this arithmetic could go wrong.
            plain = model_pricing.cost_usd(
                model=row["model"], long_prompt=False,
                **{name: row[name] - surcharged[name]
                   for name in self._COUNTERS})
            if plain is not None:
                over = model_pricing.cost_usd(
                    model=row["model"], long_prompt=True, **surcharged)
                priced = plain + (over or 0.0)
        row["cost_usd"] = NOT_COLLECTED if priced is None else round(priced, 6)
        row["prices_read_on"] = model_pricing.PRICES_READ_ON
        return row


def merge_token_ledgers(ledgers) -> dict:
    """``{"total": {...}, "by_stage": {...}}`` over an iterable of ledgers.

    Mirrors ``paid_calls.by_stage``: the same shape, so a reader who already
    knows how to read the call ledger can read the token ledger. ``cost_usd``
    on the total is ``"not_collected"`` if ANY contributing stage could not be
    priced -- a sum missing one term is not a smaller sum, it is an unknown one.

    THE TOTAL NAMES ITS MODELS. Since the judge moved to another provider a run
    spans two rate cards, and ``total.models`` lists every one that contributed.
    A single-model total is a claim about the run, and it is a false one here.
    """
    rows = [ledger.snapshot() for ledger in ledgers if ledger is not None]
    by_stage: dict = {}
    for row in rows:
        key = row["stage"] or "unnamed"
        if key in by_stage:
            raise ValueError(
                f"two token ledgers claim the stage name {key!r}; a merged "
                f"report would silently drop one of them")
        by_stage[key] = row
    counters = ("calls", "input_tokens", "output_tokens",
                "cache_creation_input_tokens", "cache_read_input_tokens",
                "reasoning_tokens", "usage_missing_calls",
                "prompt_tokens_total")
    total = {name: sum(row[name] for row in rows) for name in counters}
    # NAMED, not assumed to be one. A two-provider run has two models in it and
    # a total that silently reported only the first would invite the reader to
    # price the whole run off one rate card.
    total["models"] = sorted({row["model"] for row in rows if row["model"]})
    total["long_prompt_surcharge_applied"] = any(
        row["long_prompt_surcharge_applied"] for row in rows)
    costs = [row["cost_usd"] for row in rows]
    total["cost_usd"] = (
        NOT_COLLECTED if any(value == NOT_COLLECTED for value in costs)
        else round(sum(costs), 6))
    total["prices_read_on"] = model_pricing.PRICES_READ_ON
    return {"total": total, "by_stage": dict(sorted(by_stage.items()))}


class PaidCallMeter:
    """A per-thread tally of billed calls made through one closure.

    WHY A METER AND NOT A RECEIPT. The receipt above wraps SEAMS, and a seam is
    not a call: both coverage judges make one model call PER CLAIM inside a
    single ``coverage_judge(claims, evidence)`` invocation, so a receipt entry
    per seam call undercounts a five-claim reference by four. The transport is
    closed over inside the judge and cannot be wrapped from the call site, so
    the count has to come from inside the closure -- which is what this is.

    WHY THREAD-LOCAL, and this is the whole design rather than a detail. The
    judge is built ONCE PER RUN and shared by every worker in
    ``run_natural_judgment``'s pool. A single shared integer read before and
    after one record would hand that record another thread's calls -- precisely
    the defect the pool's own tally refuses ("shared counter mutation would make
    a correct numeric result depend on thread timing"). Each worker sees only
    its own count, and one record is judged start to finish on one thread, so a
    before/after delta on that thread is exactly that record's spend.
    """

    __slots__ = ("_local",)

    def __init__(self):
        self._local = threading.local()

    def count(self) -> int:
        """Billed calls made on THIS thread since the meter was created."""
        return int(getattr(self._local, "n", 0))

    def bump(self) -> None:
        """Book one billed call. Called BEFORE the transport, so a provider
        error still leaves the attempt counted -- an attempt that raised was
        still paid for, and the same rule the receipt follows."""
        self._local.n = self.count() + 1


def paid_call_meter(fn) -> "PaidCallMeter | None":
    """The meter carried by ``fn``, or None if it carries none.

    None is a real answer and must not be read as zero: an injected judge with
    no meter has made an UNKNOWN number of paid calls, not no paid calls. Every
    caller here records that distinction rather than defaulting it to 0.
    """
    meter = getattr(fn, "paid_call_meter", None)
    return meter if isinstance(meter, PaidCallMeter) else None
