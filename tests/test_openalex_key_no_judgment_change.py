"""THE REGRESSION GUARD for the OpenAlex API key: it moves no judgment.

The key is an authentication and billing parameter. It buys the right to keep
asking OpenAlex questions after the anonymous $0.10/day allowance runs out; it
is not allowed to change a single answer.

``fixtures/openalex_keyless_baseline.json`` is the frozen output of this exact
batch run against the PRE-CHANGE engine
(5602a3b46ba7785a5ae88210486020b985a3b3a0), captured by replaying the
pre-change modules out of git and diffing. This test asserts the current engine
reproduces it byte for byte, with no key AND with a key set.

The batch is small but it is not arbitrary -- it covers every route the change
touches, crossed with all three LLM verdicts:

  * a claimed PMID whose title agrees          (cleared)
  * a claimed PMID resolving to another paper  (F2, ``confirm_found_f2``)
  * a claimed PMID that does not resolve, title found nowhere
    (the absence route, ``confirm_not_found_human_review``)
  * no PMID, title findable                    (no-ID fuzzy lookup, leg B)
  * no PMID, title findable nowhere            (the confirmation path, leg A)
  * no PMID with a printed DOI, resolvable and not  (leg C)

If this fails, either a judgment moved or the transport fixtures did. Both are
worth stopping for; neither should be "fixed" by regenerating the baseline
without first explaining which per-reference verdict changed and why.

THE BASELINE HAS BEEN REGENERATED ONCE, and this is that explanation.

  2026-08-25 -- ONE row moved: ``pmid-dead-unfindable|fabrication``, from
  ``F1 HIGH / confirm_not_found_f1`` to
  ``human_review MED / confirm_not_found_human_review``. The absence route was
  disconnected from BOTH findings in ``decide.py``: three empty title searches
  cannot establish that no such work exists (F1), and the route names no work at
  all, so it cannot support "the printed metadata identifies the wrong one"
  (F2) either. The other 17 rows are byte-identical, which is the point of
  regenerating from this batch rather than editing the file: the OpenAlex key
  still moves no judgment, and neither did anything else.

  Note what that leaves: this batch no longer produces an F1 row anywhere. It
  never covered the one route that still can (``exact_doi_absent_confirm_not_
  found_f1``) -- ``noid-doi-unfindable`` holds on ``exact_doi_incomplete_hold``
  because ``_route`` answers 404 for the DOI system, so the lookup is INCOMPLETE
  rather than ANSWERED-ABSENT. That was true before this change too; it is
  recorded here because the coverage guard below no longer names F1 and the
  reason must not be mistaken for the guard having been loosened to pass.

Run:  PYTHONPATH=<repo> python -m pytest tests/test_openalex_key_no_judgment_change.py -q
"""
from __future__ import annotations
import json
import os

import pytest

from cde.runtime import ratelimit
from cde.refs.run import process_reference
from cde.refs.schema import ClaimedRef, Reference

FIX = os.path.join(os.path.dirname(__file__), "fixtures")
BASELINE = os.path.join(FIX, "openalex_keyless_baseline.json")

TITLE_A = "Hallmarks of cancer: the next generation"
#: Deliberately unfindable. Every provider below returns an EMPTY result set for
#: it -- an honest "searched, answered, found nothing", never an error, so the
#: F1 path is reached by evidence rather than by an outage.
TITLE_B = "A title that exists in no database whatsoever 12345"

VERDICTS = ('{"verdict": "uncertain", "reason": "r"}',
            '{"verdict": "fabrication", "reason": "r"}',
            '{"verdict": "formatting_discrepancy", "reason": "r"}')


class _Resp:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code, self._json, self.text = status_code, json_data, text
        self.headers = {}

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json


def _route(url, params):
    """One recorded answer per (provider, query). Keyed on the QUERY, not the
    call index, so the two runs cannot drift just because they made calls in a
    different order."""
    p = params or {}
    title = (p.get("term") or p.get("query.bibliographic") or p.get("search")
             or str(p.get("filter", "")))
    unknown = "no database whatsoever" in title
    if "esearch" in url:
        return _Resp(200, {"esearchresult": {"idlist": [] if unknown else ["1"]}})
    if "esummary" in url:
        return _Resp(200, {"result": {"1": {"title": TITLE_A}}})
    if "efetch" in url:
        return _Resp(200, None, text="")
    if "crossref" in url:
        items = [] if unknown else [{
            "title": [TITLE_A], "author": [{"family": "Hanahan"}],
            "issued": {"date-parts": [[2011]]},
            "container-title": ["Cell"], "DOI": "10.1/a"}]
        return _Resp(200, {"status": "ok", "message": {"items": items}})
    if "openalex" in url:
        results = [] if unknown else [{
            "id": "W1", "title": TITLE_A, "doi": "https://doi.org/10.1/a",
            "publication_year": 2011,
            "authorships": [{"author": {"display_name": "D Hanahan"}}],
            "primary_location": {"source": {"display_name": "Cell"}}}]
        return _Resp(200, {"results": results})
    return _Resp(404, None)


class _Session:
    def get(self, url, params=None, timeout=None):
        return _route(url, params)


BATCH = (
    ("pmid-match", ClaimedRef(title=TITLE_A, authors=["Hanahan"], year=2011,
                              journal="Cell", claimed_pmid="21376230")),
    ("noid-findable", ClaimedRef(title=TITLE_A, authors=["Hanahan"], year=2011,
                                 journal="Cell")),
    ("noid-unfindable", ClaimedRef(title=TITLE_B, authors=["Nobody"],
                                   year=2019, journal="J Nowhere")),
    ("noid-doi", ClaimedRef(title=TITLE_A, authors=["Hanahan"], year=2011,
                            journal="Cell", claimed_doi="10.1/a")),
    ("noid-doi-unfindable", ClaimedRef(title=TITLE_B, authors=["Nobody"],
                                       year=2019, journal="J Nowhere",
                                       claimed_doi="10.9/zz")),
    ("pmid-dead-unfindable", ClaimedRef(title=TITLE_B, authors=["Nobody"],
                                        year=2019, journal="J Nowhere",
                                        claimed_pmid="99999999")),
)


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setattr(ratelimit.time, "sleep", lambda *a, **k: None)
    for limiter in (ratelimit.NCBI, ratelimit.CROSSREF, ratelimit.OPENALEX,
                    ratelimit.DATACITE, ratelimit.BIORXIV):
        monkeypatch.setattr(limiter, "wait", lambda: None)


def _run_batch(api_key: str) -> list[dict]:
    out = []
    for verdict in VERDICTS:
        for cid, claimed in BATCH:
            label = json.loads(verdict)["verdict"]
            ref = Reference(f"{cid}|{label}", "PMC1", claimed)
            # Omitted entirely when empty, exactly as the engine's own seams do
            # it -- a keyless run must not even change a call signature.
            kwargs = {"openalex_api_key": api_key} if api_key else {}
            process_reference(ref, lambda _p, v=verdict: v, ncbi_key="",
                              crossref_mailto="c@x", openalex_mailto="o@x",
                              session=_Session(), **kwargs)
            out.append({"citation_id": ref.citation_id, "label": ref.label,
                        "confidence": ref.confidence,
                        "rationale": ref.rationale,
                        "log": ref.to_log_record()})
    return json.loads(json.dumps(out, sort_keys=True, default=str))


def _baseline() -> list[dict]:
    with open(BASELINE, encoding="utf-8") as fh:
        return json.load(fh)


def test_the_baseline_covers_the_routes_it_claims_to():
    """Guards the guard. A baseline that had drifted to all-cleared would pass
    the two tests below while proving nothing.

    Asserted on ``decided_by``, not on the label set. The label check this
    replaces named F1 and F2, and half of it went vacuous the moment the absence
    route stopped ending in F1 -- a coverage guard that can be satisfied by the
    route it guards disappearing is not a guard. Reason codes name the ROUTES
    the docstring above claims this batch crosses, so a route that vanishes
    fails here instead of silently reducing what the baseline proves."""
    rows = _baseline()
    routes = {row["log"]["log"]["decided_by"] for row in rows}
    assert {"confirm_found_f2",                    # the F2 finding
            "confirm_not_found_human_review",      # the absence route, PMID leg
            "noid_confirm_not_found_human_review", # the absence route, leg A
            "noid_metadata_match",                 # leg B
            "exact_doi_incomplete_hold",           # leg C
            "title_search_identity_settled",
            "llm_formatting"} <= routes, routes
    assert {"F2", "cleared", "human_review"} <= {row["label"] for row in rows}
    assert len(rows) == len(BATCH) * len(VERDICTS)


def test_a_keyless_run_reproduces_the_pre_change_verdicts_exactly():
    assert _run_batch("") == _baseline()


def test_a_keyed_run_reproduces_the_same_verdicts():
    """The half that matters for the corpus: turning the key ON must buy
    OpenAlex answers and nothing else."""
    assert _run_batch("SOME-KEY") == _baseline()


def test_the_two_runs_agree_reference_by_reference():
    """Stated per reference rather than per batch, so a failure names the row."""
    keyless = {row["citation_id"]: row for row in _run_batch("")}
    keyed = {row["citation_id"]: row for row in _run_batch("SOME-KEY")}
    assert keyless.keys() == keyed.keys()
    for cid in keyless:
        assert keyless[cid] == keyed[cid], cid
