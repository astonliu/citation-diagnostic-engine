"""F1 calibration probe -- the gate for a Band-1 model swap (DEC-084).

WHY THIS EXISTS. ``docs/F1_BAND1_HAIKU_SWITCH_SPEC.md`` makes two probes the gate
on switching ``llm_filter`` from ``claude-opus-5`` to ``claude-haiku-4-5``:

  Probe A  a dead PMID whose claimed work is absent from all three databases
           must SURVIVE the model filter and reach the terminal absence route
           (``confirm_not_found_human_review``). If a weaker model calls that
           reference ``formatting_discrepancy``, ``decide()`` clears it and the
           route stops firing SILENTLY -- no error, no zero-count anomaly a
           batch would surface, just a stage that never fires. Probe A is the
           only thing between that and a paid run.

           AMENDED 2026-08-25: probe A asserted ``F1 HIGH``. That route no
           longer accuses -- three empty title searches cannot establish that no
           such work exists, and the route names no work, so it supports neither
           F1 nor F2 (see ``decide.py``) -- and it now HOLDS the row for human
           adjudication. What probe A gates is unchanged, because the label was
           never the thing being measured: it is whether the MODEL returns a
           surviving verdict on a reference that deserves one. That is now
           asserted directly, on ``llm_verdict`` and the reason code, which is
           what the gate was reading the label as a proxy for.
  Probe B  PMID 31665581 with the record's own title must **clear**.

Probe B as written never reaches the model -- an exact metadata match is not
flagged, so ``llm_filter`` is not called at all. Asserting that is still worth
doing (a clean reference cannot be turned into an accusation by any model) but it
measures the pipeline, not the model. So a third row, **Probe B-prime**, is a
FLAGGED reference -- claimed title mismatching the record its PMID resolves to --
whose claimed work IS found by Crossref. It reaches the model on every run, and
its purpose is honest about which part it tests:

  * it is the JSON-parse and verdict-plumbing row. A model whose output does not
    parse shows up here as ``uncertain`` with ``unparseable LLM output``.
  * its never-accused-by-absence outcome is DETERMINISTIC, not a model result:
    ``found_anywhere`` forecloses the absence route for all four verdicts
    (fabrication -> F2, reference_error -> F2, formatting -> cleared, uncertain
    -> cleared by title identity). It proves the conjunction holds under the new
    model; it does not prove the new model is well calibrated.

So the calibration weight sits on Probe A. A is the row a weaker model can
actually break.

WHAT IS AND IS NOT LIVE. Every NCBI / Crossref / OpenAlex response is replayed
from a fake session, byte-fixed, so the only variable in the run is the model.
The model call IS live and costs money (three prompts, ~400 output tokens each).
Nothing here is a gold label, an evaluation input, or a reported number: these are
CODE-PATH fixtures whose expected outputs follow from ``decide()``'s conjunction.

RUN IT BEFORE ANY PAID BATCH, on whatever model the batch will use:

    cd citation_repair_F1_handoff
    ANTHROPIC_API_KEY=... PYTHONPATH=. python tools/F1_CALIBRATION_PROBE.py \
        --model claude-haiku-4-5

Exit status is 0 only if all three rows pass. Anything else means do not run the
batch. ``--model`` is required and is echoed in the output: a probe whose model is
not the batch's model has proved nothing.
"""
from __future__ import annotations

import argparse
import os
import sys

from cre.f1 import confirm, lookup, run
from cre.f1 import schema as S
from cre.f1.schema import ClaimedRef, Reference

EFETCH = lookup.EFETCH
ESEARCH = confirm.PUBMED_ESEARCH
ESUMMARY = confirm.PUBMED_ESUMMARY
CROSSREF = confirm.CROSSREF_URL
OPENALEX = confirm.OPENALEX_URL

#: A real, PubMed-indexed record, trimmed to the fields ``lookup`` reads.
#: Chosen because 31665581 is one of the run's standing regression PMIDs.
MEDLINE_31665581 = """
PMID- 31665581
DP  - 2019 Oct 31
TI  - Purple Urine after Catheterization.
AU  - Chen L
TA  - N Engl J Med
"""

REAL_TITLE = "Purple Urine after Catheterization."


class FakeResponse:
    def __init__(self, status_code=200, text="", json_data=None, headers=None):
        self.status_code = status_code
        self.text = text
        self._json = json_data
        self.headers = headers or {}

    def json(self):
        if self._json is None:
            raise ValueError("no json body")
        return self._json


class FakeSession:
    def __init__(self, handler):
        self.handler = handler
        self.urls: list[str] = []

    def get(self, url, params=None, timeout=None):
        self.urls.append(url)
        return self.handler(url, params)


def _searches_all_empty(url, _params):
    """All three databases healthy, answering, and none of them has the work.

    Healthy-and-empty is the ONLY search shape that is evidence of absence; an
    errored search holds the reference instead (F1_FABRICATION_GUARD_SPEC).
    """
    if url in (ESEARCH, ESUMMARY):
        return FakeResponse(json_data={"esearchresult": {"idlist": []}})
    if url == CROSSREF:
        return FakeResponse(json_data={"status": "ok", "message": {"items": []}})
    if url == OPENALEX:
        return FakeResponse(json_data={"results": []})
    raise AssertionError(f"unexpected url {url}")


def _searches_find(title):
    def handler(url, params):
        if url == CROSSREF:
            return FakeResponse(json_data={
                "status": "ok", "message": {"items": [{"title": [title]}]}})
        return _searches_all_empty(url, params)
    return handler


class CountingCompleter:
    """Wraps the live completer so a probe can assert it was NOT called."""

    def __init__(self, inner):
        self.inner = inner
        self.calls: list[str] = []

    def __call__(self, prompt: str) -> str:
        raw = self.inner(prompt)
        self.calls.append(raw)
        return raw


#: The terminal route for "claimed PMID answered-absent, claimed title found in
#: none of the three databases that all answered". A model that calls probe A a
#: formatting discrepancy never reaches it.
ABSENCE_ROUTE = "confirm_not_found_human_review"

#: The verdicts that SURVIVE the filter. Either one carries probe A to the route
#: above; the gate is about surviving at all, not about which of the two.
SURVIVING = (S.V_FABRICATION, S.V_REFERENCE_ERROR)


def probe_a(complete):
    """Dead PMID + work absent everywhere -> survives the filter and reaches the
    terminal absence route. Guards recall."""
    ref = Reference("probeA", "citance",
                    ClaimedRef(title="A plausible sounding study of nothing",
                               claimed_pmid="99999999", authors=["Smith J"],
                               year=2020))

    def handler(url, params):
        if url == EFETCH:
            # HTTP 200 with an empty body: verified live 2026-08-16 as NCBI's
            # answer for a nonexistent PMID, and the only shape read as absence.
            return FakeResponse(status_code=200, text="")
        return _searches_all_empty(url, params)

    out = run.process_reference(ref, complete, session=FakeSession(handler))
    # The model half of the gate: a surviving verdict. A weaker model answering
    # `formatting_discrepancy` fails HERE, which is the whole point of probe A.
    # The reason code is asserted alongside it so that a pipeline change which
    # short-circuits the row before the terminal branch also fails, rather than
    # passing on a verdict that no longer reaches anything.
    ok = (out.log.llm_verdict in SURVIVING
          and out.log.decided_by == ABSENCE_ROUTE
          and out.log.pmid_transport_status == S.FETCH_ANSWERED_ABSENT)
    return ok, out


def probe_b(complete):
    """PMID 31665581 with its own title -> cleared, and the model is never asked."""
    ref = Reference("probeB", "citance",
                    ClaimedRef(title=REAL_TITLE, claimed_pmid="31665581",
                               authors=["Chen L"], year=2019,
                               journal="N Engl J Med"))

    def handler(url, params):
        if url == EFETCH:
            return FakeResponse(text=MEDLINE_31665581)
        return _searches_all_empty(url, params)

    out = run.process_reference(ref, complete, session=FakeSession(handler))
    ok = out.label == S.CLEARED and out.label not in (S.F1, S.F2)
    return ok, out


def probe_b_prime(complete):
    """Flagged reference whose claimed work IS findable -> reaches the model,
    and cannot be accused on an absence under any of the four verdicts.

    The claimed PMID resolves to an unrelated real record (the mismatch that
    flags), and Crossref answers with the claimed title (the hit that forecloses
    the absence route). This row exists to exercise ``llm_filter`` end to end --
    prompt built, call made, JSON parsed, verdict recorded on the log -- on a
    reference where no model answer can produce an accusation from an absence.
    """
    claimed = "A plausible sounding study of nothing"
    ref = Reference("probeB'", "citance",
                    ClaimedRef(title=claimed, claimed_pmid="31665581",
                               authors=["Smith J"], year=2020))

    def handler(url, params):
        if url == EFETCH:
            return FakeResponse(text=MEDLINE_31665581)   # an UNRELATED paper
        return _searches_find(claimed)(url, params)

    out = run.process_reference(ref, complete, session=FakeSession(handler))
    parsed = out.log.llm_verdict in (S.V_FABRICATION, S.V_FORMATTING,
                                    S.V_REFERENCE_ERROR, S.V_UNCERTAIN)
    unparseable = "unparseable LLM output" in (out.log.notes or "")
    return (out.label != S.F1 and out.log.decided_by != ABSENCE_ROUTE
            and parsed and not unparseable), out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--model", required=True,
                    help="the model id the paid batch will use, e.g. "
                         "claude-haiku-4-5")
    ap.add_argument("--max-tokens", type=int, default=400,
                    help="output budget; 400 matches run.make_completer and the "
                         "notebook's MODEL_MAX_TOKENS")
    args = ap.parse_args(argv)

    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        print("ANTHROPIC_API_KEY is not set; these rows make live model calls.",
              file=sys.stderr)
        return 2

    print(f"F1 CALIBRATION PROBE | model={args.model} "
          f"| max_tokens={args.max_tokens}")
    print("network: NCBI/Crossref/OpenAlex are REPLAYED; the model call is LIVE.")

    inner = run.make_completer(args.model, key, max_tokens=args.max_tokens)
    counter = CountingCompleter(inner)

    rows = []
    ok_a, out_a = probe_a(counter)
    calls_after_a = len(counter.calls)
    rows.append(("A  dead PMID, absent everywhere",
                 f"a surviving verdict -> {ABSENCE_ROUTE}", ok_a, out_a))

    ok_b, out_b = probe_b(counter)
    b_made_a_call = len(counter.calls) > calls_after_a
    ok_b = ok_b and not b_made_a_call
    rows.append(("B  31665581, exact title", "cleared (no model call)",
                 ok_b, out_b))

    ok_bp, out_bp = probe_b_prime(counter)
    rows.append(("B' flagged, work findable", "a parseable verdict, never "
                 "accused on an absence", ok_bp, out_bp))

    print()
    for name, expected, ok, out in rows:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
        print(f"         expected      : {expected}")
        print(f"         label         : {out.label} ({out.confidence})")
        print(f"         llm_verdict   : {out.log.llm_verdict or '(not run)'}")
        print(f"         decided_by    : {out.log.decided_by}")
    if b_made_a_call:
        print("\nNOTE: probe B called the model. An exact metadata match must "
              "not flag; that is a pipeline change, not a model result.")

    print(f"\nlive model calls made: {len(counter.calls)}")
    failed = [n for n, _, ok, _ in rows if not ok]
    if failed:
        print("\nGATE FAILED: " + "; ".join(failed))
        print("Do NOT start a paid batch on this model. A model that clears "
              "probe A as a formatting discrepancy makes the absence route "
              "unreachable, and the batch will report zero for a reason no "
              "count reveals.")
        return 1
    print("\nGATE PASSED for " + args.model + ". Record this line next to the "
          "batch it authorises.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
