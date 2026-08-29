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

from cde.refs import lookup
from cde.refs import confirm
from cde.refs import run
from cde.runtime import ratelimit
from cde.refs import parser
from cde.refs import schema as S
from cde.refs.lookup import _parse_medline, compare_and_flag, fetch_pubmed
from cde.refs.confirm import (search_pubmed, search_crossref, search_openalex,
                            found_anywhere, all_errored)
from cde.runtime.ratelimit import request_with_retry, RateLimiter
from cde.refs.decide import decide
from cde.refs.parser import parse_pmc_xml
from cde.refs.schema import Reference, ClaimedRef, RetrievedRecord

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


def test_parse_medline_retains_identity_metadata():
    text = """PMID- 12345
TI  - English indexed title.
TT  - Título vernáculo.
AU  - Smith AB
DP  - 2020
TA  - J Test
AID - 10.1234/ABC.5 [doi]
VI  - 12
PG  - e10-e20
LA  - spa
PT  - Journal Article
PT  - Corrected and Republished Article
CIN - Comment in: J Test. 2021. PMID: 99999
"""
    rec = _parse_medline(text, "12345")
    assert rec.doi == "10.1234/ABC.5"
    assert rec.volume == "12" and rec.pages == "e10-e20"
    assert rec.alternate_titles == ["Título vernáculo."]
    assert rec.language == "spa"
    assert rec.publication_types == [
        "Journal Article", "Corrected and Republished Article"]
    assert rec.related_pmids == {"CIN": ["99999"]}


def test_parser_retains_volume_pages_and_direct_collaboration(tmp_path):
    xml = b"""<article><body/><back><ref-list><ref id='r7'>
      <element-citation>
        <collab>World Medical Association</collab>
        <article-title>Ethical principles for medical research</article-title>
        <source>Bull WHO</source><year>2013</year><volume>91</volume>
        <fpage>219</fpage><lpage>219</lpage>
        <pub-id pub-id-type='pmid'>12345</pub-id>
      </element-citation></ref>
    </ref-list></back></article>"""
    path = tmp_path / "PMC7.xml"
    path.write_bytes(xml)
    ref = parse_pmc_xml(str(path), source_pmcid="PMC7")[0]
    assert ref.citation_id == "PMC7:r7"
    assert ref.claimed.authors == ["World Medical Association"]
    assert ref.claimed.volume == "91"
    assert ref.claimed.pages == "219"


def test_iter_pmc_dir_scopes_before_parsing(tmp_path, monkeypatch):
    (tmp_path / "PMC_KEEP.xml").write_text("<article/>")
    (tmp_path / "PMC_SKIP.xml").write_text("<article/>")
    parsed = []

    def fake_parse(path, source_pmcid=""):
        parsed.append(source_pmcid)
        return []

    monkeypatch.setattr(parser, "parse_pmc_xml", fake_parse)
    assert list(parser.iter_pmc_dir(
        str(tmp_path), source_pmcids={"PMC_KEEP"})) == []
    assert parsed == ["PMC_KEEP"]


def test_iter_pmc_dir_scoped_flat_layout_skips_directory_walk(
        tmp_path, monkeypatch):
    (tmp_path / "PMC_KEEP.xml").write_text("<article/>")
    parsed = []

    def fake_parse(path, source_pmcid=""):
        parsed.append(source_pmcid)
        return []

    def forbidden_walk(_path):
        raise AssertionError("flat scoped lookup must not enumerate the corpus")

    monkeypatch.setattr(parser, "parse_pmc_xml", fake_parse)
    monkeypatch.setattr(parser.os, "walk", forbidden_walk)
    assert list(parser.iter_pmc_dir(
        str(tmp_path), source_pmcids={"PMC_KEEP"})) == []
    assert parsed == ["PMC_KEEP"]


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


def _slept_on_retry_after(monkeypatch, headers, **kw):
    """Drive one 429 -> 200 exchange and report the sleeps it performed."""
    slept = []
    monkeypatch.setattr(ratelimit.time, "sleep", lambda s: slept.append(s))

    def handler(url, p, n):
        if n < 1:
            return FakeResponse(429, headers=headers)
        return FakeResponse(200, text="ok")

    resp = request_with_retry(FakeSession(handler), "http://x", {}, **kw)
    return resp, slept


def test_retry_after_is_clamped_to_max_backoff(monkeypatch):
    """OpenAlex answered a spent daily quota with `Retry-After: 52266` (14.5h).
    A sleeping thread cannot be interrupted, so honouring that literally
    strands the worker for the rest of the run."""
    resp, slept = _slept_on_retry_after(
        monkeypatch, {"Retry-After": "52266"}, base_backoff=0.5, max_backoff=8.0)
    assert resp.status_code == 200
    assert slept == [8.0]
    assert slept[0] <= 8.0


def test_retry_after_below_ceiling_is_honoured(monkeypatch):
    """The server's pacing still wins when it asks for less than the ceiling."""
    _, slept = _slept_on_retry_after(
        monkeypatch, {"Retry-After": "2"}, base_backoff=0.5, max_backoff=8.0)
    assert slept == [2.0]


def test_retry_after_garbage_falls_back_to_exponential(monkeypatch):
    _, slept = _slept_on_retry_after(
        monkeypatch, {"Retry-After": "garbage"}, base_backoff=0.5,
        max_backoff=8.0)
    assert slept == [min(0.5 * (2 ** 0), 8.0)]


def test_no_retry_after_header_uses_exponential(monkeypatch):
    _, slept = _slept_on_retry_after(
        monkeypatch, {}, base_backoff=0.5, max_backoff=8.0)
    assert slept == [min(0.5 * (2 ** 0), 8.0)]


def test_retry_after_clamp_still_retries_not_gives_up(monkeypatch):
    """The clamp shortens the wait; it must never turn a retry into a
    give-up (guard PMID 31665581 depends on the 429 path being exhausted,
    then surfaced as FETCH_RESOLVER_ERROR rather than an absence)."""
    slept = []
    monkeypatch.setattr(ratelimit.time, "sleep", lambda s: slept.append(s))
    sess = FakeSession(
        lambda u, p, n: FakeResponse(429, headers={"Retry-After": "52266"}))
    r = request_with_retry(sess, "http://x", {}, max_retries=3,
                           base_backoff=0.5, max_backoff=8.0)
    assert r.status_code == 429              # returned, not raised
    assert len(sess.calls) == 4              # 1 + 3 retries, all attempted
    assert slept == [8.0, 8.0, 8.0]


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
    """Patch the SDK client; returns the list of constructor kwargs seen."""
    import anthropic
    built = []

    class FakeAnthropic:
        def __init__(self, api_key=None, **kw):
            self._n = 0
            built.append(kw)
            self.messages = types.SimpleNamespace(create=self._create)

        def _create(self, **kw):
            self._n += 1
            return behavior(self._n)

    monkeypatch.setattr(anthropic, "Anthropic", FakeAnthropic)
    return built


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
    with pytest.raises(run.NonRetryableProviderError) as caught:
        complete("prompt")
    assert isinstance(caught.value.__cause__, _FakeAPIError)
    # 401 is a misconfigured key, NOT an unpayable account.
    assert not isinstance(caught.value, run.ProviderCreditExhausted)


# --------------------------------------------------------------------------
# make_completer -- bounded socket timeout (defect 2)
# --------------------------------------------------------------------------
def test_completer_client_has_finite_timeout(monkeypatch):
    """No timeout => an SSL read on a silently dropped connection blocks
    forever, and the retry loop never runs because nothing is ever raised."""
    built = _install_fake_anthropic(monkeypatch, lambda n: _msg())
    run.make_completer("model-x", api_key="k")
    assert len(built) == 1
    timeout = built[0].get("timeout")
    assert isinstance(timeout, (int, float))
    assert 0 < timeout < float("inf")


def test_completer_timeout_is_overridable(monkeypatch):
    built = _install_fake_anthropic(monkeypatch, lambda n: _msg())
    run.make_completer("model-x", api_key="k", timeout=5.0)
    assert built[0]["timeout"] == 5.0


def test_completer_does_not_add_sdk_retries(monkeypatch):
    """This module owns exactly one retry loop; the SDK must not gain a
    second one on top of it."""
    built = _install_fake_anthropic(monkeypatch, lambda n: _msg())
    run.make_completer("model-x", api_key="k")
    assert "max_retries" not in built[0]


def _timeout_error():
    import httpx
    import anthropic
    return anthropic.APITimeoutError(request=httpx.Request("POST", "http://x"))


def test_completer_retries_timeout_then_succeeds(monkeypatch):
    slept = []
    monkeypatch.setattr(run.time, "sleep", lambda s: slept.append(s))

    def behavior(n):
        if n < 3:
            raise _timeout_error()
        return _msg(_text_block("ok"))

    _install_fake_anthropic(monkeypatch, behavior)
    complete = run.make_completer("model-x", api_key="k", base_backoff=0.0)
    assert complete("prompt") == "ok"
    assert len(slept) == 2                   # two backoff sleeps, no exception


def test_completer_timeout_every_attempt_returns_blank(monkeypatch):
    """A timeout must stay 'uncertain' -> human review, never a silent clear
    and never a crash that drops the reference from the population."""
    _install_fake_anthropic(
        monkeypatch, lambda n: (_ for _ in ()).throw(_timeout_error()))
    complete = run.make_completer("model-x", api_key="k", base_backoff=0.0)
    assert complete("prompt") == ""


# --------------------------------------------------------------------------
# make_completer -- credit exhaustion is its own type (defect 3)
# --------------------------------------------------------------------------
_CREDIT_MESSAGE = ("Your credit balance is too low to access the Anthropic "
                   "API. Please go to Plans & Billing to upgrade or purchase "
                   "credits.")


class _FakeBodyAPIError(Exception):
    """An SDK-shaped error: status code plus the provider's structured body."""
    def __init__(self, status_code, err_type, message):
        super().__init__(message)
        self.status_code = status_code
        self.body = {"type": "error",
                     "error": {"type": err_type, "message": message}}


def _raising(exc):
    return lambda n: (_ for _ in ()).throw(exc)


def test_completer_credit_exhaustion_raises_dedicated_type(monkeypatch):
    exc = _FakeBodyAPIError(400, "invalid_request_error", _CREDIT_MESSAGE)
    _install_fake_anthropic(monkeypatch, _raising(exc))
    complete = run.make_completer("model-x", api_key="k")
    with pytest.raises(run.ProviderCreditExhausted) as caught:
        complete("prompt")
    # subclass => every existing `except NonRetryableProviderError` still works
    assert isinstance(caught.value, run.NonRetryableProviderError)
    assert caught.value.__cause__ is exc


def test_completer_auth_error_is_not_credit_exhaustion(monkeypatch):
    exc = _FakeBodyAPIError(401, "authentication_error",
                            "invalid x-api-key")
    _install_fake_anthropic(monkeypatch, _raising(exc))
    complete = run.make_completer("model-x", api_key="k")
    with pytest.raises(run.NonRetryableProviderError) as caught:
        complete("prompt")
    assert not isinstance(caught.value, run.ProviderCreditExhausted)


def test_completer_ordinary_bad_request_is_not_credit_exhaustion(monkeypatch):
    exc = _FakeBodyAPIError(400, "invalid_request_error",
                            "max_tokens: must be greater than 0")
    _install_fake_anthropic(monkeypatch, _raising(exc))
    complete = run.make_completer("model-x", api_key="k")
    with pytest.raises(run.NonRetryableProviderError) as caught:
        complete("prompt")
    assert not isinstance(caught.value, run.ProviderCreditExhausted)


def test_credit_exhaustion_detected_from_structured_type():
    """Structured fields are the preferred discriminator, not the message."""
    exc = _FakeBodyAPIError(400, "billing_error", "see your account")
    assert run._is_credit_exhausted(exc) is True


def test_credit_exhaustion_detected_from_payment_required_status():
    exc = _FakeBodyAPIError(402, "invalid_request_error", "nope")
    assert run._is_credit_exhausted(exc) is True


def test_credit_exhaustion_message_fallback_without_structured_body():
    """Last-resort path: no structured body at all, so str(exc) is matched."""
    assert run._is_credit_exhausted(RuntimeError(_CREDIT_MESSAGE)) is True
    assert run._is_credit_exhausted(RuntimeError("overloaded")) is False


def test_structured_message_wins_over_stringified_exception():
    """A structured message that is NOT about credit must not be overridden by
    whatever str(exc) happens to contain."""
    class _Noisy(_FakeBodyAPIError):
        def __str__(self):
            return "credit balance is too low"

    exc = _Noisy(400, "invalid_request_error", "temperature out of range")
    assert run._is_credit_exhausted(exc) is False


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


def test_live_path_flags_claimed_first_author_only_found_as_coauthor():
    ref = Reference(
        "coauthor", "", ClaimedRef(
            title="Neural semantic networks in older adults",
            authors=["Alice"], year=2020, journal="J Cognition",
            claimed_pmid="1"))
    ref.retrieved = RetrievedRecord(
        resolved=True,
        title="Neural semantic processing in younger adults",
        authors=["Bob", "Alice"], year=2020, journal="J Cognition", pmid="1")
    assert compare_and_flag(ref, 85.0, author_tripwire=True) is True
    assert ref.log.author_match is True
    assert ref.log.first_author_match is False
    assert ref.log.author_tripwire is True
    assert "appears later" in ref.log.notes
    assert ref.log.mismatch_flagged is True


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


def test_decide_partial_error_holds_not_f1():
    # REPLACES test_decide_partial_error_still_decides_f1, which asserted the
    # defect: one healthy-but-empty search licensed the accusation while another
    # search had errored. ZD, 2026-08-16: F1 requires EVERY search to answer.
    ref = Reference("x", "", ClaimedRef(title="t", claimed_pmid="1"))
    ref.log.pmid_present = True
    ref.log.pmid_resolved = True
    out = decide(ref, True, S.V_FABRICATION,
                 {"pubmed": 0.0, "crossref": None, "openalex": None})
    assert out.label == S.HUMAN_REVIEW
    assert out.log.decided_by == "confirm_incomplete_evidence"
    # The rationale must name what was missing, not assert a complete sweep.
    assert "crossref" in out.rationale and "openalex" in out.rationale


def test_decide_partial_error_still_reaches_f2():
    # A POSITIVE finding needs no complete sweep: one database returned the
    # work, so an outage elsewhere is irrelevant. Guards F2 recall against the
    # completeness gate above.
    ref = Reference("x", "", ClaimedRef(title="t", claimed_pmid="1"))
    ref.log.pmid_present = True
    ref.log.pmid_resolved = True
    out = decide(ref, True, S.V_FABRICATION,
                 {"pubmed": 99.0, "crossref": None, "openalex": None})
    assert out.label == S.F2


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
