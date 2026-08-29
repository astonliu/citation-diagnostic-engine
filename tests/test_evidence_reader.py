"""Offline tests for cre/f1/evidence_reader.fetch_abstract (Part B).

Fully offline: ``session`` is injected as a stub, so no network is ever touched;
``time.sleep`` is neutralized so retry/limiter backoff costs no wall-clock. Each
test maps to one row of the Part B acceptance table."""
from __future__ import annotations

import time

import pytest

from cde.claims import band_prompts as bp
from cde.claims import abstracts as er
from cde.claims import band as jb


# EFetch pubmed/abstract XML. fetch_abstract reads every <AbstractText> via
# ElementTree.iter, so the exact wrapper depth is irrelevant to the parse.
PUBMED_XML = (
    '<?xml version="1.0"?>'
    "<PubmedArticleSet><PubmedArticle><MedlineCitation><Article>"
    "<Abstract><AbstractText>{body}</AbstractText></Abstract>"
    "</Article></MedlineCitation></PubmedArticle></PubmedArticleSet>"
)

STRUCTURED_XML = (
    '<?xml version="1.0"?>'
    "<PubmedArticleSet><PubmedArticle><MedlineCitation><Article><Abstract>"
    '<AbstractText Label="BACKGROUND" NlmCategory="BACKGROUND">Sepsis is common.</AbstractText>'
    '<AbstractText Label="METHODS" NlmCategory="METHODS">We ran an RCT.</AbstractText>'
    '<AbstractText Label="RESULTS" NlmCategory="RESULTS">Mortality fell.</AbstractText>'
    '<AbstractText Label="CONCLUSIONS" NlmCategory="CONCLUSIONS">Treatment helps.</AbstractText>'
    "</Abstract></Article></MedlineCitation></PubmedArticle></PubmedArticleSet>"
)

NO_ABSTRACT_XML = (
    '<?xml version="1.0"?>'
    "<PubmedArticleSet><PubmedArticle><MedlineCitation><Article>"
    "<ArticleTitle>A paper with no abstract</ArticleTitle>"
    "</Article></MedlineCitation></PubmedArticle></PubmedArticleSet>"
)


class _Resp:
    def __init__(self, status_code=200, text=""):
        self.status_code = status_code
        self.text = text
        self.headers = {}


class _StubSession:
    """Records .get calls and returns a fixed response every time."""

    def __init__(self, response):
        self._response = response
        self.calls = 0

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        return self._response


class _BoomSession:
    """Any .get is a test failure -- proves the call was served without HTTP."""

    def get(self, url, params=None, timeout=None):
        raise AssertionError("no HTTP request expected")


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Neutralize retry/limiter backoff so the suite never sleeps for real."""
    monkeypatch.setattr(time, "sleep", lambda *a, **k: None)


def test_normal_single_abstract_returned_exactly():
    sess = _StubSession(_Resp(text=PUBMED_XML.format(body="Aspirin inhibits COX-1.")))
    assert er.fetch_abstract("111", session=sess) == "Aspirin inhibits COX-1."
    assert sess.calls == 1


def test_structured_abstract_all_sections_doc_order_labels_preserved():
    sess = _StubSession(_Resp(text=STRUCTURED_XML))
    assert er.fetch_abstract("222", session=sess) == (
        "BACKGROUND: Sepsis is common.\n\n"
        "METHODS: We ran an RCT.\n\n"
        "RESULTS: Mortality fell.\n\n"
        "CONCLUSIONS: Treatment helps."
    )


def test_record_with_no_abstracttext_returns_none():
    sess = _StubSession(_Resp(text=NO_ABSTRACT_XML))
    assert er.fetch_abstract("333", session=sess) is None


def test_http_500_after_retries_returns_none_no_raise():
    sess = _StubSession(_Resp(status_code=500, text=""))
    assert er.fetch_abstract("444", session=sess) is None
    # request_with_retry default max_retries=3 -> 4 attempts, then returns 500.
    assert sess.calls == 4


def test_sentinel_abstract_text_returns_none():
    """An abstract whose text is literally a sentinel ('N/A') folds to None so it
    is never routed to the coverage model."""
    sess = _StubSession(_Resp(text=PUBMED_XML.format(body="N/A")))
    assert er.fetch_abstract("555", session=sess) is None


def test_empty_pmid_returns_none_without_request():
    sess = _BoomSession()
    assert er.fetch_abstract("", session=sess) is None
    assert er.fetch_abstract(None, session=sess) is None


def test_none_flows_through_assemble_evidence_to_held_with_no_llm_call():
    """Row 6: a None fetch -> evidence_is_usable False -> established None ->
    route HELD_LOW_CONFIDENCE, with ZERO coverage LLM calls."""
    sess = _StubSession(_Resp(text=NO_ABSTRACT_XML))
    assert er.fetch_abstract("999", session=sess) is None

    item = {"cited_pmid": "999"}
    ev = jb.assemble_evidence(
        item, fetch_abstract=lambda pmid: er.fetch_abstract(pmid, session=sess))
    assert bp.evidence_is_usable(ev) is False

    calls = []

    def call_llm(prompt):
        calls.append(prompt)
        raise AssertionError("coverage LLM must not be called")

    verdicts = jb.coverage_verdicts(
        ["a claim"], ev, judge=bp.make_coverage_judge(call_llm))
    assert calls == []
    assert [v["established"] for v in verdicts] == [None]
    assert jb.route(verdicts) == jb.ROUTE_HELD


def test_cache_hit_performs_no_second_http_request(tmp_path):
    cache_dir = str(tmp_path / "abstracts")
    sess = _StubSession(_Resp(text=PUBMED_XML.format(body="Cached abstract body.")))
    first = er.fetch_abstract("777", session=sess, cache_dir=cache_dir)
    assert first == "Cached abstract body."
    assert sess.calls == 1

    # Second call: a session that raises on .get -- it must be served from cache.
    second = er.fetch_abstract("777", session=_BoomSession(), cache_dir=cache_dir)
    assert second == "Cached abstract body."


def test_failure_is_not_cached_so_transient_errors_retry(tmp_path):
    """A None result is never cached, so a later run refetches rather than
    freezing a transient failure."""
    cache_dir = str(tmp_path / "abstracts")
    fail = _StubSession(_Resp(status_code=500, text=""))
    assert er.fetch_abstract("888", session=fail, cache_dir=cache_dir) is None
    # No cache written -> the recovered upstream is fetched on the next call.
    ok = _StubSession(_Resp(text=PUBMED_XML.format(body="Now available.")))
    assert er.fetch_abstract("888", session=ok, cache_dir=cache_dir) == "Now available."
    assert ok.calls == 1
