"""The recording adapter must satisfy verify_receipt's contract exactly.

The contract is read off ``production_launcher.verify_receipt``, which runs
AFTER the run completes -- so every shape error here would otherwise be
discovered at the cost of a whole corpus run.
"""
from __future__ import annotations

import pytest

from . import production_launcher as pl
from .recording_adapter import AdapterReceipt, RUN_SEAMS, wrap_run_seams


def test_a_receipt_requires_a_model():
    with pytest.raises(ValueError, match="nonblank model id"):
        AdapterReceipt(model="")


def test_every_call_carries_the_model():
    r = AdapterReceipt(model="claude-opus-5")
    fn = r.wrap(lambda x: x, seam="extractor")
    fn("a")
    fn("b")
    assert [c["model"] for c in r.calls] == ["claude-opus-5"] * 2


# ------------------------------------------- the ABSENT-not-None discipline

def test_a_rejecting_model_omits_the_temperature_key_entirely():
    """verify_receipt refuses any call that merely CARRIES the key."""
    r = AdapterReceipt(model="claude-opus-5", temperature="unsupported",
                       assistant_prefill="unsupported")
    r.wrap(lambda: None, seam="coverage_judge")()
    call = r.calls[0]
    assert "temperature" not in call
    assert "assistant_prefill" not in call
    # And the real verifier accepts it.
    pl.verify_receipt(r, model="claude-opus-5", temperature="unsupported")


def test_a_supporting_model_carries_temperature_zero():
    r = AdapterReceipt(model="claude-sonnet-4-5", temperature=0)
    r.wrap(lambda: None, seam="extractor")()
    assert r.calls[0]["temperature"] == 0
    pl.verify_receipt(r, model="claude-sonnet-4-5", temperature=0)


def test_none_is_omitted_not_written_as_null():
    r = AdapterReceipt(model="claude-opus-5", temperature=None)
    r.wrap(lambda: None, seam="extractor")()
    assert "temperature" not in r.calls[0]


def test_a_supporting_model_carries_the_prefill_string():
    r = AdapterReceipt(model="claude-sonnet-4-5", temperature=0,
                       assistant_prefill="{")
    r.wrap(lambda: None, seam="extractor")()
    assert r.calls[0]["assistant_prefill"] == "{"


# ------------------------------------------------------- every seam, one log

def test_wrap_all_shares_one_receipt_across_every_seam():
    """A call through a seam nobody remembered still lands in the receipt."""
    r = AdapterReceipt(model="claude-opus-5", temperature="unsupported")
    seams = r.wrap_all({
        "extractor": lambda s: [s],
        "coverage_judge": lambda c, e: [],
        "discriminator_call_llm": lambda p: "{}",
        "f4_verifier_call_llm": lambda p: "{}",
    })
    seams["extractor"]("s")
    seams["coverage_judge"]([], {})
    seams["discriminator_call_llm"]("p")
    seams["f4_verifier_call_llm"]("p")
    assert len(r.calls) == 4
    assert r.summary()["calls_by_seam"] == {
        "coverage_judge": 1, "discriminator_call_llm": 1,
        "extractor": 1, "f4_verifier_call_llm": 1}


def test_an_unwired_seam_stays_unwired():
    """Wrapping None would make judgment_run's pairwise gates think the seam was
    supplied, silently changing which discriminators run."""
    r = AdapterReceipt(model="claude-opus-5")
    out = r.wrap_all({"discriminator_call_llm": None, "extractor": lambda s: [s]})
    assert out["discriminator_call_llm"] is None
    assert out["extractor"] is not None


def test_a_typod_seam_name_is_refused():
    """Forwarded unwrapped, its calls would vanish from the receipt -- exactly
    the silent gap this module exists to close."""
    r = AdapterReceipt(model="claude-opus-5")
    with pytest.raises(ValueError, match="unknown seam name"):
        wrap_run_seams(r, extracter=lambda s: [s])


def test_wrap_run_seams_accepts_every_documented_seam():
    r = AdapterReceipt(model="claude-opus-5")
    out = wrap_run_seams(r, **{n: (lambda *a, **k: None) for n in RUN_SEAMS})
    assert set(out) == set(RUN_SEAMS)


def test_the_call_is_recorded_even_when_the_callable_raises():
    """A receipt that logged only successes would understate what was sent."""
    r = AdapterReceipt(model="claude-opus-5")

    def boom():
        raise RuntimeError("provider 500")

    with pytest.raises(RuntimeError):
        r.wrap(boom, seam="extractor")()
    assert len(r.calls) == 1


def test_the_wrapper_preserves_return_values_and_arguments():
    r = AdapterReceipt(model="claude-opus-5")
    fn = r.wrap(lambda a, b=2: (a, b), seam="extractor")
    assert fn(1, b=3) == (1, 3)


# ---------------------------------------------- end-to-end against the gates

def test_a_wrapped_run_passes_both_receipt_gates():
    r = AdapterReceipt(model="claude-opus-5", temperature="unsupported",
                       assistant_prefill="unsupported")
    pl.assert_receipt_shape(r)                 # pre-run gate
    r.wrap(lambda: None, seam="extractor")()
    pl.verify_receipt(r, model="claude-opus-5", temperature="unsupported")


def test_a_receipt_built_for_the_wrong_model_is_caught():
    r = AdapterReceipt(model="claude-sonnet-4-5", temperature=0)
    r.wrap(lambda: None, seam="extractor")()
    with pytest.raises(pl.LaunchRefused, match="unauthorized model"):
        pl.verify_receipt(r, model="claude-opus-5", temperature="unsupported")
