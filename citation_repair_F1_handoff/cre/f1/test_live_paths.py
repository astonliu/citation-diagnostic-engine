"""Fixture-based tests for the live-network paths (HANDOFF task 4).

These cover the functions the offline tests could not: MEDLINE parsing against
real EFetch output, the three confirmation searches against recorded JSON, the
rate-limit/retry helper, the Anthropic completer's block extraction + retry, the
author-mismatch trip-wire, the all-errored decide safeguard, and citance linking.

No network is touched: recorded responses in ./fixtures are replayed through a
fake requests session, and the Anthropic SDK client is monkeypatched.

Run:  PYTHONPATH=<repo> python -m pytest cre/f1/test_live_paths.py -q
"""
from __future__ import annotations
import json
import os
import types

import pytest
import requests

from cre.f1 import lookup, confirm, run, ratelimit
from cre.f1 import schema as S
from cre.f1.lookup import _parse_medline, compare_and_flag, fetch_pubmed
from cre.f1.confirm import (search_pubmed, search_crossref, search_openalex,
                            found_anywhere, all_errored)
from cre.f1.ratelimit import request_with_retry, RateLimiter
from cre.f1.decide import decide
from cre.f1.parser import parse_pmc_xml
from cre.f1.schema import Reference, ClaimedRef, RetrievedRecord

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def _fx(name: str) -> str:
    with open(os.path.join(FIX, name)) as f:
        return f.read()


# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------
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
    """Routes GETs through a handler(url, params, call_index) -> FakeResponse."""
    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    def get(self, url, params=None, timeout=None):
        r = self.handler(url, params, len(self.calls))
        self.calls.append((url, params))
        return r


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    # Keep throttle + backoff logic exercised, but never actually sleep.
    monkeypatch.setattr(ratelimit.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(run.time, "sleep", lambda *a, **k: None)


# --------------------------------------------------------------------------
# _parse_medline
# --------------------------------------------------------------------------
def test_parse_medline_multiline_title_authors_cn():
    rec = _parse_medline(_fx("medline_multiline.txt"), "33301246")
    assert rec.resolved
    assert rec.title.startswith("Global burden of 369 diseases")
    # multi-line TI must be joined into one continuous string
    assert "204 countries and territories" in rec.title
    assert rec.title.endswith("Study 2019.")
    # AU surnames extracted, plus the corporate CN author appended
    assert "Vos" in rec.authors and "Lim" in rec.authors
    assert "GBD 2019 Diseases and Injuries Collaborators" in rec.authors
    assert rec.year == 2020
    assert rec.journal == "Lancet"          # TA preferred over JT
    assert rec.pmid == "33301246"


def test_parse_medline_epub_only_date():
    rec = _parse_medline(_fx("medline_epub_only.txt"), "32000001")
    assert rec.resolved
    assert rec.year == 2020                  # from DEP (no DP present)
    assert "Roe" in rec.authors


def test_parse_medline_book_title_fallback():
    rec = _parse_medline(_fx("medline_book_no_ti.txt"), "30000001")
    assert rec.resolved
    assert rec.title == "A Reference Book of Clinical Things"   # BTI fallback


def test_parse_medline_junk_unresolved():
    rec = _parse_medline("SO  - some trailing junk\nLR  - 20200101\n", "5")
    assert rec.resolved is False             # no PMID and no title


# --------------------------------------------------------------------------
# fetch_pubmed (EFetch) + retry
# --------------------------------------------------------------------------
def test_fetch_pubmed_replays_medline():
    medline = _fx("medline_multiline.txt")
    sess = FakeSession(lambda url, p, n: FakeResponse(200, text=medline))
    rec = fetch_pubmed("33301246", session=sess)
    assert rec.resolved and rec.title.startswith("Global burden")
    assert len(sess.calls) == 1


def test_fetch_pubmed_retries_then_succeeds():
    medline = _fx("medline_multiline.txt")

    def handler(url, p, n):
        return FakeResponse(429) if n < 2 else FakeResponse(200, text=medline)

    sess = FakeSession(handler)
    rec = fetch_pubmed("33301246", session=sess)
    assert rec.resolved
    assert len(sess.calls) == 3              # two 429s, then a 200


def test_fetch_pubmed_gives_up_on_persistent_429():
    sess = FakeSession(lambda url, p, n: FakeResponse(429))
    rec = fetch_pubmed("33301246", session=sess)
    assert rec.resolved is False
    assert len(sess.calls) == 4              # 1 + max_retries(3)


# --------------------------------------------------------------------------
# confirmation searches
# --------------------------------------------------------------------------
TITLE = ("Global burden of 369 diseases and injuries in 204 countries and "
         "territories, 1990-2019: a systematic analysis for the Global Burden "
         "of Disease Study 2019")


def test_search_pubmed_replays_esearch_esummary():
    esearch = json.loads(_fx("pubmed_esearch.json"))
    esummary = json.loads(_fx("pubmed_esummary.json"))

    def handler(url, p, n):
        if "esearch" in url:
            return FakeResponse(200, json_data=esearch)
        return FakeResponse(200, json_data=esummary)

    score = search_pubmed(TITLE, s=FakeSession(handler))
    assert score is not None and score >= 95.0


def test_search_crossref_list_title():
    data = json.loads(_fx("crossref_works.json"))
    score = search_crossref(TITLE, s=FakeSession(lambda u, p, n: FakeResponse(200, json_data=data)))
    assert score is not None and score >= 95.0    # title is a list -> joined


def test_search_crossref_error_returns_none():
    score = search_crossref(TITLE, s=FakeSession(lambda u, p, n: FakeResponse(500)))
    assert score is None                            # errored, not "found nothing"


def test_search_openalex_null_title_uses_display_name():
    data = json.loads(_fx("openalex_works.json"))
    score = search_openalex(TITLE, s=FakeSession(lambda u, p, n: FakeResponse(200, json_data=data)))
    assert score is not None and score >= 95.0      # first result title is null


def test_found_anywhere_ignores_none_and_thresholds():
    assert found_anywhere({"pubmed": None, "crossref": 90.0, "openalex": None})
    assert not found_anywhere({"pubmed": None, "crossref": 40.0, "openalex": 0.0})
    assert all_errored({"pubmed": None, "crossref": None, "openalex": None})
    assert not all_errored({"pubmed": None, "crossref": 0.0, "openalex": None})


# --------------------------------------------------------------------------
# request_with_retry
# --------------------------------------------------------------------------
def test_request_with_retry_backs_off_then_succeeds():
    def handler(url, p, n):
        return FakeResponse(503) if n < 1 else FakeResponse(200, text="ok")

    sess = FakeSession(handler)
    r = request_with_retry(sess, "http://x", {}, limiter=RateLimiter(1000),
                           base_backoff=0.0)
    assert r.status_code == 200 and len(sess.calls) == 2


def test_request_with_retry_reraises_connection_error():
    class Boom(FakeSession):
        def get(self, url, params=None, timeout=None):
            self.calls.append((url, params))
            raise requests.ConnectionError("down")

    sess = Boom(lambda *a: None)
    with pytest.raises(requests.RequestException):
        request_with_retry(sess, "http://x", {}, base_backoff=0.0)
    assert len(sess.calls) == 4              # 1 + 3 retries before re-raising


# --------------------------------------------------------------------------
# make_completer (Anthropic SDK) -- block extraction + retry classification
# --------------------------------------------------------------------------
class _FakeAPIError(Exception):
    def __init__(self, status_code):
        super().__init__(f"status {status_code}")
        self.status_code = status_code


def _msg(*blocks):
    return types.SimpleNamespace(content=list(blocks))


def _text_block(text):
    return types.SimpleNamespace(type="text", text=text)


def _install_fake_anthropic(monkeypatch, behavior):
    import anthropic

    class FakeAnthropic:
        def __init__(self, api_key=None):
            self._n = 0
            self.messages = types.SimpleNamespace(create=self._create)

        def _create(self, **kw):
            self._n += 1
            return behavior(self._n)

    monkeypatch.setattr(anthropic, "Anthropic", FakeAnthropic)


def test_completer_extracts_text(monkeypatch):
    payload = '{"verdict": "fabrication", "reason": "invented"}'
    _install_fake_anthropic(monkeypatch, lambda n: _msg(_text_block(payload)))
    complete = run.make_completer("model-x", api_key="k")
    assert complete("prompt") == payload


def test_completer_skips_non_text_blocks(monkeypatch):
    thinking = types.SimpleNamespace(type="thinking", thinking="hmm")
    _install_fake_anthropic(monkeypatch,
                            lambda n: _msg(thinking, _text_block("answer")))
    complete = run.make_completer("model-x", api_key="k")
    assert complete("prompt") == "answer"


def test_completer_empty_or_refusal_returns_blank(monkeypatch):
    _install_fake_anthropic(monkeypatch, lambda n: _msg())   # no content blocks
    complete = run.make_completer("model-x", api_key="k")
    assert complete("prompt") == ""          # parse_verdict -> uncertain, no crash


def test_completer_retries_transient_then_succeeds(monkeypatch):
    def behavior(n):
        if n < 3:
            raise _FakeAPIError(529)         # overloaded
        return _msg(_text_block("ok"))

    _install_fake_anthropic(monkeypatch, behavior)
    complete = run.make_completer("model-x", api_key="k", base_backoff=0.0)
    assert complete("prompt") == "ok"


def test_completer_raises_on_non_retryable(monkeypatch):
    _install_fake_anthropic(monkeypatch,
                            lambda n: (_ for _ in ()).throw(_FakeAPIError(401)))
    complete = run.make_completer("model-x", api_key="k")
    with pytest.raises(_FakeAPIError):
        complete("prompt")


# --------------------------------------------------------------------------
# author-mismatch trip-wire (HANDOFF task 2)
# --------------------------------------------------------------------------
def _ref_with_resolved(claimed_title, claimed_authors, resolved_title,
                       resolved_authors):
    ref = Reference("c", "", ClaimedRef(title=claimed_title,
                                        authors=claimed_authors,
                                        claimed_pmid="1"))
    ref.retrieved = RetrievedRecord(resolved=True, title=resolved_title,
                                    authors=resolved_authors, pmid="1")
    return ref


def test_tripwire_flags_similar_title_wrong_first_author():
    ref = _ref_with_resolved("Aspirin and mortality in adults", ["Smith"],
                             "Aspirin and mortality in adults", ["Jones", "Lee"])
    assert compare_and_flag(ref, 85.0, author_tripwire=True) is True
    assert ref.log.author_tripwire is True
    assert ref.log.mismatch_flagged is True


def test_tripwire_silent_when_first_author_present():
    ref = _ref_with_resolved("Aspirin and mortality in adults", ["Smith"],
                             "Aspirin and mortality in adults", ["Smith", "Jones"])
    assert compare_and_flag(ref, 85.0, author_tripwire=True) is False
    assert ref.log.author_tripwire is False


def test_tripwire_disabled_does_not_flag():
    ref = _ref_with_resolved("Aspirin and mortality in adults", ["Smith"],
                             "Aspirin and mortality in adults", ["Jones"])
    assert compare_and_flag(ref, 85.0, author_tripwire=False) is False


def test_tripwire_no_data_does_not_flag():
    # resolved record has no authors -> cannot judge -> must not trip
    ref = _ref_with_resolved("Aspirin and mortality in adults", ["Smith"],
                             "Aspirin and mortality in adults", [])
    assert compare_and_flag(ref, 85.0, author_tripwire=True) is False
    assert ref.log.author_tripwire is None


# --------------------------------------------------------------------------
# decide() all-errored safeguard
# --------------------------------------------------------------------------
def test_decide_all_errored_escalates_not_f1():
    ref = Reference("x", "", ClaimedRef(title="t", claimed_pmid="1"))
    ref.log.pmid_present = True
    ref.log.pmid_resolved = True
    out = decide(ref, True, S.V_FABRICATION,
                 {"pubmed": None, "crossref": None, "openalex": None})
    assert out.label == S.HUMAN_REVIEW
    assert out.log.decided_by == "confirm_all_errored"


def test_decide_partial_error_still_decides_f1():
    # one search succeeded (found nothing), others errored -> still F1-eligible
    ref = Reference("x", "", ClaimedRef(title="t", claimed_pmid="1"))
    ref.log.pmid_present = True
    ref.log.pmid_resolved = True
    out = decide(ref, True, S.V_FABRICATION,
                 {"pubmed": 0.0, "crossref": None, "openalex": None})
    assert out.label == S.F1


# --------------------------------------------------------------------------
# citance linking (HANDOFF task 3)
# --------------------------------------------------------------------------
def test_citance_linking_attaches_sentences_and_markers():
    refs = parse_pmc_xml(os.path.join(FIX, "pmc_with_citances.xml"),
                         source_pmcid="PMC9")
    by = {r.citation_id: r for r in refs}
    assert set(by) == {"PMC9:B1", "PMC9:B2", "PMC9:B3"}

    b1 = by["PMC9:B1"]
    assert "Aspirin reduces mortality" in b1.citance       # first citance wins
    assert b1.cited_reference_marker == "1"

    b2 = by["PMC9:B2"]
    assert "quantum widgets" in b2.citance.lower()
    assert b2.cited_reference_marker == "2"

    b3 = by["PMC9:B3"]                                      # cited only in nested <p>
    assert "combined endpoint" in b3.citance.lower()
    assert b3.cited_reference_marker == "3"


def test_part_title_chapter_ref_extracted():
    refs = parse_pmc_xml(os.path.join(FIX, "pmc_chapter_ref.xml"),
                         source_pmcid="PMCX")
    by = {r.citation_id: r for r in refs}
    b1 = by["PMCX:B1"]
    assert b1.claimed.title == "Effects of setting on psychedelic outcomes"
    assert b1.claimed.claimed_pmid == "35138585"
