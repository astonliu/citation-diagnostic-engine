"""ITEM 3 -- the Band 1 disposition map, pinned.

WHAT THIS GUARDS. Band 1 decides which references Band 2 is ALLOWED to judge, so
its label map is upstream of every Band 2 denominator. A reference that moves
from ``cleared`` to ``unscoreable`` does not merely change an F1/F2 count -- it
silently leaves Band 2's population, and every rate Band 2 reports shifts under
it with nothing failing.

The whole map is frozen, not the counts: two references swapping ``F1`` and
``F2`` leaves the counts identical, and that is exactly the kind of rewiring a
restructure can cause.

NO NETWORK. Every HTTP leg is replayed through a recorded handler, and a URL the
recording does not know RAISES rather than returning an empty result -- an
unrecognised leg answered with "no hits" is indistinguishable from a genuine
absence, which is the specific confusion Band 1's fabrication guard exists to
prevent, and it must not be reintroduced by the test harness.
"""
from __future__ import annotations

import json

import pytest

from cde.refs import confirm
from cde.refs import lookup
from cde.runtime import ratelimit
from cde.refs import run
from cde.refs.schema import ClaimedRef, Reference

from .conftest import assert_golden

EFETCH = lookup.EFETCH
ESEARCH = confirm.PUBMED_ESEARCH
CROSSREF = confirm.CROSSREF_URL
OPENALEX = confirm.OPENALEX_URL


#: A real MEDLINE record, trimmed to the fields Band 1 reads. Resolves to
#: exactly what the reference claims, so the reference CLEARS.
MEDLINE_MATCH = """
PMID- 30000001
DP  - 2019 Mar 04
TI  - A randomised trial of drug X for outcome Y.
AU  - Smith J
TA  - J Test Med
"""

#: Resolves, but to an UNRELATED paper -> the claimed PMID points elsewhere (F2).
MEDLINE_WRONG = """
PMID- 30000002
DP  - 2019 Oct 31
TI  - Purple urine after catheterization.
AU  - Chen L
TA  - N Engl J Med
"""


class _Response:
    def __init__(self, status_code=200, text="", json_data=None):
        self.status_code = status_code
        self.text = text
        self._json = json_data
        self.headers = {}

    def json(self):
        if self._json is None:
            raise ValueError("no json body")
        return self._json


class _Session:
    def __init__(self, handler):
        self.handler = handler
        self.urls: list[str] = []

    def get(self, url, params=None, timeout=None):
        self.urls.append(url)
        return self.handler(url, params or {})


#: citation_id -> the reference Band 1 is given, and the recorded world it meets.
CORPUS = [
    # Resolves to the paper it claims: cleared.
    ("b1-01", "A randomised trial of drug X for outcome Y", "30000001"),
    # Resolves to a DIFFERENT paper: the claimed PMID is wrong (F2 territory).
    ("b1-02", "A randomised trial of drug X for outcome Y", "30000002"),
    # Dead PMID, but the title is found in Crossref: real paper, wrong id --
    # never an F1 accusation.
    ("b1-03", "An overview of early mobilisation in critical care", "30000003"),
    # Dead PMID and no database has the title, all three answering healthily:
    # the only shape that licenses F1 at all.
    ("b1-04", "A study that does not exist anywhere at all", "30000004"),
    # No title to search on: unscoreable, never a fabrication accusation.
    ("b1-05", "", "30000005"),
]


def _handler(url, params):
    if url == EFETCH:
        pmid = str(params.get("id") or "")
        if pmid == "30000001":
            return _Response(text=MEDLINE_MATCH)
        if pmid == "30000002":
            return _Response(text=MEDLINE_WRONG)
        if pmid in ("30000003", "30000004", "30000005"):
            return _Response(text="")          # answered, no such record
        raise AssertionError(f"efetch for an unrecorded pmid {pmid!r}")
    if url == ESEARCH:
        return _Response(json_data={"esearchresult": {"idlist": []}})
    if url == CROSSREF:
        title = str(params.get("query.bibliographic") or params.get("query") or "")
        if "early mobilisation" in title:
            return _Response(json_data={"status": "ok", "message": {"items": [
                {"title": ["An overview of early mobilisation in critical care"]}]}})
        return _Response(json_data={"status": "ok", "message": {"items": []}})
    if url == OPENALEX:
        return _Response(json_data={"results": []})
    raise AssertionError(
        f"Band 1 issued a request to {url!r}, which this recording does not "
        f"know. Answering it with an empty result would look like a genuine "
        f"absence and could turn a transport gap into an F1 accusation.")


#: citation title fragment -> the recorded LLM-filter verdict for it. Keyed on
#: the prompt because that is what the seam actually receives; a single fixed
#: verdict for every reference would drive the whole corpus into one bucket and
#: leave the F1 and F2 branches -- the two that make a public accusation --
#: entirely unexercised by the pinned map.
_VERDICTS = {
    "randomised trial of drug X for outcome Y": "reference_error",
    "early mobilisation in critical care": "uncertain",
    "does not exist anywhere at all": "fabrication",
}


def _llm(prompt):
    for fragment, verdict in _VERDICTS.items():
        if fragment.lower() in prompt.lower():
            return json.dumps({"verdict": verdict, "reason": "recorded"})
    return json.dumps({"verdict": "uncertain", "reason": "recorded"})


@pytest.fixture(scope="module")
def band1_rows(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("band1")
    refs = [Reference(cid, "A citing sentence [1].",
                      ClaimedRef(title=title, claimed_pmid=pmid,
                                 authors=["Smith J"], year=2019))
            for cid, title, pmid in CORPUS]
    monkey = pytest.MonkeyPatch()
    monkey.setattr(ratelimit.time, "sleep", lambda *a, **k: None)
    monkey.setattr(run.time, "sleep", lambda *a, **k: None)
    monkey.setattr(run, "make_completer", lambda *a, **k: _llm)
    monkey.setattr(run.requests, "Session", lambda: _Session(_handler))
    try:
        summary = run.run("", str(tmp / "ds.jsonl"), str(tmp / "logs.jsonl"),
                          model="test-model", refs=refs)
    finally:
        monkey.undo()
    rows = [json.loads(line) for line in
            (tmp / "logs.jsonl").read_text(encoding="utf-8").splitlines()]
    return summary, rows


def test_every_reference_keeps_its_band1_label(band1_rows):
    """The full ``citation_id -> label`` map, not merely its histogram."""
    _, rows = band1_rows
    assert_golden("band1_dispositions.txt", [
        "\t".join((row["citation_id"], str(row.get("label") or ""),
                   str(row.get("confidence") or ""),
                   str((row.get("log") or {}).get("same_work_reason") or ""),
                   str((row.get("log") or {}).get("unscoreable_reason") or ""),
                   str((row.get("log") or {}).get("verdict") or "")))
        for row in rows])


def test_the_band1_label_counts_are_unchanged(band1_rows):
    """The histogram Band 2's population is drawn from."""
    import collections
    _, rows = band1_rows
    counts = collections.Counter(str(row.get("label") or "") for row in rows)
    assert_golden("band1_label_counts.txt",
                  [f"{label}\t{n}" for label, n in counts.items()])


def test_the_model_calling_it_a_fabrication_does_not_make_it_one(band1_rows):
    """THE ONE BAND 1 ERROR THAT MAKES A FALSE PUBLIC ACCUSATION.

    ``b1-04`` is the strongest case the corpus can build FOR an F1: a PMID that
    answers and has no such record, a title no database returns, all three
    searches healthy and complete, and an LLM filter that says outright
    ``fabrication``. The recorded answer is ``human_review``, not ``F1`` -- the
    accusation needs more than a model's opinion, and the deciding code holds.

    Pinned end-to-end rather than per-unit because the guard is spread across
    ``lookup`` (answered-absent vs resolver-error), ``confirm`` (a skipped
    search scores None, never 0.0) and ``decide`` (the completeness gate). A
    restructure that pulled those three apart would leave every unit test green
    and turn this into an accusation.

    ``b1-05`` is the same guarantee from the other end: with no title there is
    nothing to have searched for, so there is nothing to be confident about.
    """
    _, rows = band1_rows
    by_id = {row["citation_id"]: row for row in rows}
    assert by_id["b1-04"]["label"] != "F1"
    assert by_id["b1-05"]["label"] != "F1"


def test_a_dead_pmid_whose_title_a_database_knows_is_cleared_not_accused(
        band1_rows):
    """``b1-03``: the PMID does not resolve, but the work plainly exists.

    A wrong identifier attached to a real paper is a formatting defect, not a
    fabricated citation, and conflating the two is the failure mode that would
    put a real author's real paper in a Results table as invented.
    """
    _, rows = band1_rows
    by_id = {row["citation_id"]: row for row in rows}
    assert by_id["b1-03"]["label"] == "cleared"
