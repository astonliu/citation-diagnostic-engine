"""cre/f1/check_openai_judge.py -- the four checks that need a live call.

WHY THESE ARE NOT PYTEST TESTS. Everything in
``test_openai_judge_wiring`` runs against a double, so it verifies that this code
does what the DOCUMENTATION says the API does. Four statements cannot be settled
that way, and three of them are load-bearing:

* **A. The model id resolves on this account.** ``gpt-5.6-sol`` is documented, but
  a documented id on an account without access is a 404 at the first record.
* **B. The usage counters exist and ``input_tokens`` is the whole prompt.**
  ``openai_transport.usage_dict`` subtracts ``cached_tokens`` out of
  ``input_tokens`` on the strength of OpenAI's established convention, not a
  quoted sentence. If the figures were DISJOINT instead, that subtraction
  underprices every cached call. The module refuses rather than guesses -- it
  asserts ``input + output == total`` on every call -- so a live call is what
  turns the assumption into a settled fact. THIS IS THE CHECK THAT MATTERS MOST.
* **C. ``temperature`` really is rejected.** The spec omits it on OpenAI's
  unsupported-parameter list plus a third-party 400 report. This project has not
  measured it, which is why ``production_launcher`` REFUSES to launch the model
  at all (``TEMPERATURE_UNMEASURED_MODELS``). Running this check is what unblocks
  that refusal, and its output is the evidence to paste into the table.
* **D. Verdict stability.** Not byte equality -- there is no temperature and no
  confirmed seed, so identical text cannot be promised and is not asserted. What
  is asserted is that two runs of the same judge prompt agree on the VERDICT. If
  they do not, the judge is not stable enough to use at that effort setting, and
  that is a finding rather than a flake.

RUN IT BEFORE TRUSTING A BATCH, and after any edit to ``usage_dict``:

    OPENAI_API_KEY=... python -m cre.f1.check_openai_judge

Exit status is 0 only if every check passes. It makes a handful of real,
BILLED calls -- single-digit cents at these prompt sizes, except that check B
deliberately sends a >1,024-token prefix twice to make the cache engage at all.
"""
from __future__ import annotations

import argparse
import sys

from . import openai_transport as ot

#: Long enough to clear the 1,024-token cache floor with room to spare. Below it
#: ``cached_tokens`` stays 0 forever and check B's second half proves nothing --
#: which would read as "caching is broken" rather than "the prompt is too short".
_PREFIX = ("The following is a fixed reference block used only to exceed the "
           "prompt-caching minimum of 1024 tokens so that a cache read can be "
           "observed at all. ") * 90


def _ok(label: str, detail: str = "") -> bool:
    print(f"  PASS  {label}" + (f" -- {detail}" if detail else ""))
    return True


def _fail(label: str, detail: str) -> bool:
    print(f"  FAIL  {label} -- {detail}")
    return False


def check_a(client, model: str) -> bool:
    """Liveness, and that the id was not silently re-routed."""
    print("A. liveness")
    resp = client.responses.create(
        model=model, instructions="Reply with exactly: OK", input="ping",
        max_output_tokens=64, reasoning={"effort": "low"},
        text={"verbosity": "low"})
    text = getattr(resp, "output_text", "") or ""
    if not text.strip():
        return _fail("non-empty output_text", f"got {text!r}")
    served = str(getattr(resp, "model", ""))
    if model not in served:
        # Alias drift: asking for a concrete id and being served something else
        # means the pin is not a pin.
        return _fail("served model contains the requested id",
                     f"asked {model!r}, served {served!r}")
    return _ok("liveness", f"served {served!r}, text {text.strip()[:40]!r}")


def check_b(client, model: str) -> bool:
    """The counters exist, and the containment convention is what we assumed."""
    print("B. usage counters and the containment convention")
    passed = True
    first = client.responses.create(
        model=model, input=_PREFIX + " Answer with one word: fine?",
        max_output_tokens=256, reasoning={"effort": "low"},
        text={"verbosity": "low"}, prompt_cache_key="cre-check-b")
    try:
        counts = ot.usage_dict(first.usage)
    except ot.OpenAIUsageError as exc:
        # The arithmetic check inside usage_dict IS this check. If it raises,
        # the convention the mapping is built on does not hold and the mapping
        # is wrong -- not the check.
        return _fail("usage_dict accepts a live usage block", str(exc))
    raw = first.usage
    passed &= _ok("input+output==total, so input_tokens is the WHOLE prompt",
                  f"{raw.input_tokens}+{raw.output_tokens}=={raw.total_tokens}")
    passed &= _ok("normalised counters",
                  ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    if counts["reasoning_tokens"] > counts["output_tokens"]:
        passed = _fail("reasoning_tokens within output_tokens", "inverted")

    prompt_tokens = raw.input_tokens
    second = client.responses.create(
        model=model, input=_PREFIX + " Answer with one word: also fine?",
        max_output_tokens=256, reasoning={"effort": "low"},
        text={"verbosity": "low"}, prompt_cache_key="cre-check-b")
    cached = second.usage.input_tokens_details.cached_tokens
    if prompt_tokens < ot.MIN_CACHEABLE_PROMPT_TOKENS:
        # Not a failure. A permanent zero below the floor is the documented
        # behaviour, and the run log has to say so rather than call it a bug.
        passed &= _ok(
            "cache read not expected",
            f"prompt is {prompt_tokens} tokens, under the "
            f"{ot.MIN_CACHEABLE_PROMPT_TOKENS} floor; cached_tokens will always "
            "be 0 and that is not a broken breakpoint")
    elif cached > 0:
        passed &= _ok("cache read on the repeat call", f"cached_tokens={cached}")
    else:
        passed &= _ok(
            "cache read NOT observed",
            f"prompt is {prompt_tokens} tokens (over the floor) but "
            "cached_tokens=0 on the repeat. Not a hard failure -- cache "
            "residency is not guaranteed -- but record it: every request is "
            "then paying the write premium and reading nothing back")
    print(f"     RECORD IN A COMMENT NEXT TO usage_dict: input_tokens is a "
          f"{'TOTAL including cached' if raw.input_tokens == raw.total_tokens - raw.output_tokens else 'DISJOINT figure'}")
    return passed


def check_c(client, model: str) -> bool:
    """Temperature rejection, measured rather than assumed."""
    print("C. temperature is rejected")
    try:
        client.responses.create(model=model, input="ping", temperature=0,
                               max_output_tokens=16)
    except Exception as exc:                              # noqa: BLE001
        status = getattr(exc, "status_code", None)
        request_id = getattr(exc, "request_id", None)
        if status == 400:
            return _ok(
                "HTTP 400 on temperature=0",
                f"request_id={request_id!r} -- PASTE THIS into "
                f"production_launcher.TEMPERATURE_REJECTING_MODELS and remove "
                f"the id from TEMPERATURE_UNMEASURED_MODELS; that is the "
                f"first-party measurement DEC-070 requires. Error: {exc}")
        return _fail("HTTP 400 on temperature=0",
                     f"rejected, but with status {status!r}: {exc}")
    return _fail(
        "HTTP 400 on temperature=0",
        "the parameter was ACCEPTED. openai_transport omits it on the basis "
        "that it is rejected; revisit that before running a batch, and consider "
        "whether temperature=0 should now be pinned as DEC-046B does elsewhere")


_JUDGE_PROMPT = (
    "You are scoring one claim against one piece of evidence. Reply with "
    "exactly one line: 'VERDICT: covered' or 'VERDICT: not_covered'.\n\n"
    "CLAIM: Aspirin reduces the risk of a second heart attack.\n"
    "EVIDENCE: In this trial, aspirin lowered recurrent myocardial infarction "
    "from 8.1% to 5.0% over two years.\n")


def check_d(client, model: str, *, effort: str) -> bool:
    """Verdict stability across a repeat. NOT byte equality."""
    print(f"D. verdict stability at effort={effort!r}")
    call = ot.make_openai_call(client, model, 512, stage="check_d",
                              effort=effort,
                              prompt_cache_key="cre-check-d")
    first, second = call(_JUDGE_PROMPT), call(_JUDGE_PROMPT)

    def verdict(text: str) -> str:
        for line in text.splitlines():
            if "VERDICT" in line.upper():
                return line.strip().lower()
        return ""

    v1, v2 = verdict(first), verdict(second)
    if not v1 or not v2:
        return _fail("a verdict line was extractable from both replies",
                     f"{first[:80]!r} / {second[:80]!r}")
    if v1 != v2:
        return _fail(
            "the two runs agree on the verdict",
            f"{v1!r} vs {v2!r}. This is a real finding, not a flake: the judge "
            f"is not stable enough to use at effort={effort!r}. Re-measure at a "
            "lower effort before running a batch")
    identical = first == second
    return _ok(
        "the two runs agree on the verdict",
        f"{v1!r}; prose {'identical' if identical else 'differs'} -- differing "
        "prose is EXPECTED and acceptable, byte equality is not asserted and "
        "cannot be guaranteed on this model")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="cre.f1.check_openai_judge")
    parser.add_argument("--model", default=ot.JUDGE_MODEL_ID)
    parser.add_argument("--api-key", default="")
    parser.add_argument("--effort", default=ot.JUDGE_REASONING_EFFORT,
                        choices=list(ot.REASONING_EFFORTS))
    parser.add_argument("--only", default="abcd",
                        help="subset of checks to run, e.g. 'bc'")
    args = parser.parse_args(argv)

    client = ot.openai_client(args.api_key)
    print(f"model={args.model!r}  effort={args.effort!r}")
    print(ot.DETERMINISM_NOTE)
    print()
    results = {}
    if "a" in args.only:
        results["A"] = check_a(client, args.model)
    if "b" in args.only:
        results["B"] = check_b(client, args.model)
    if "c" in args.only:
        results["C"] = check_c(client, args.model)
    if "d" in args.only:
        results["D"] = check_d(client, args.model, effort=args.effort)
    print()
    failed = sorted(k for k, ok in results.items() if not ok)
    if failed:
        print(f"FAILED: {', '.join(failed)}. Do not run a batch.")
        return 1
    print(f"All checks passed ({', '.join(sorted(results))}). Note that E "
          "(Anthropic regression) and F (price-table guard) are pytest tests, "
          "not live checks: run "
          "`pytest -q cre/f1/test_openai_judge_wiring.py "
          "cre/f1/test_prompt_caching_and_usage.py`.")
    return 0


if __name__ == "__main__":                                # pragma: no cover
    sys.exit(main())
