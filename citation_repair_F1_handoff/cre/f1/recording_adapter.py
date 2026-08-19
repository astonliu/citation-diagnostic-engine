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

#: Mirrors production_launcher's sentinels without importing it (the launcher
#: imports judgment_run, and this module must stay usable on its own).
UNSUPPORTED = "unsupported"


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
