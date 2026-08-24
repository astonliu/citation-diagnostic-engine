"""The OpenAlex API key, end to end, and the 409 that must never read as absence.

WHY THIS SUITE EXISTS. OpenAlex metered its API in 2026: an anonymous caller
gets $0.10 of usage per day and then a flat 409. On 2026-08-24 two
``provider_probe`` records from the SAME run, same probe title, reported
``openalex: 100.0`` and then ``openalex: None`` while PubMed (36.22) and
Crossref (74.07) returned byte-identical scores in both. Since
``confirm.fully_answered`` requires all three providers to have answered before
F1 is reachable, the run's F1 = 0 over 8,009 references was an artifact of API
ACCESS, not a base rate of the corpus.

Two things therefore have to hold at once, and both are tested here:

  1. Every OpenAlex leg can carry a key, so the allowance lasts a corpus run.
  2. A spent allowance still reads as "could not look", never "looked and found
     nothing". A 409 must reach ``decide()`` as an unanswered search, and F1
     must stay unreachable on it.

And with NO key configured every request must be byte-identical to the keyless
engine, because the key is an authentication and billing parameter and is not
allowed to move a single judgment.

Run:  PYTHONPATH=<repo> python -m pytest cre/f1/test_openalex_api_key.py -q
"""
from __future__ import annotations
import hashlib
import importlib
import json

import pytest

from cre.f1 import biblio_match as bm
from cre.f1 import confirm as cf
from cre.f1 import decide as dc
from cre.f1 import doi_lookup as dl
from cre.f1 import lookup as lk
from cre.f1 import openalex_telemetry as tele
from cre.f1 import production_launcher as pl
from cre.f1 import ratelimit, run as run_mod
from cre.f1 import schema as S
from cre.f1.recording_adapter import AdapterReceipt
from cre.f1.schema import ClaimedRef, Reference

TITLE = "The Hallmarks of Cancer"
KEY = "K"


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, headers=None):
        self.status_code = status_code
        self._json = json_data
        self.headers = headers or {}

    def json(self):
        if self._json is None:
            raise ValueError("no json body")
        return self._json


class FakeSession:
    """Records every (url, params) pair so a request can be asserted on."""

    def __init__(self, handler):
        self.handler = handler
        self.calls: list[tuple[str, dict]] = []

    def get(self, url, params=None, timeout=None):
        resp = self.handler(url, params, len(self.calls))
        self.calls.append((url, dict(params or {})))
        return resp

    def openalex_params(self) -> list[dict]:
        return [params for url, params in self.calls if "openalex" in url]


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    monkeypatch.setattr(ratelimit.time, "sleep", lambda *a, **k: None)


@pytest.fixture(autouse=True)
def _clean_counters():
    tele.reset()
    yield
    tele.reset()


#: One healthy OpenAlex work whose title is an EXACT match, so a keyed and a
#: keyless call are comparable on the same recorded body.
_OA_200 = {"results": [{"id": "W1", "title": TITLE,
                        "doi": "https://doi.org/10.1/x"}]}
_OA_EMPTY = {"results": []}
#: The spent-allowance response. OpenAlex sends 409 with a body; the body is
#: irrelevant, because a non-200 never reaches parsing.
_OA_409 = FakeResponse(409, {"error": "insufficient balance"})


def _oa(json_data=None, status=200):
    return lambda url, params, i: FakeResponse(status, json_data)


def _oa_409_session():
    return FakeSession(lambda url, params, i: _OA_409)


# ---------------------------------------------------------------------------
# Leg A -- confirm.search_openalex, the F1 evidence gate
# ---------------------------------------------------------------------------

def test_no_key_sends_no_api_key_parameter():
    """A keyless call must be byte-identical to the pre-change engine: the
    parameter is ABSENT, not present and empty. `api_key=` in a query string is
    a different request, and an empty one is a request OpenAlex may reject."""
    s = FakeSession(_oa(_OA_200))
    cf.search_openalex(TITLE, mailto="m", s=s, api_key="")
    params = s.openalex_params()[0]
    assert "api_key" not in params
    assert params == {"filter": f"title.search:{TITLE}", "per-page": 3,
                      "mailto": "m"}


def test_key_and_mailto_both_travel_and_neither_displaces_the_other():
    s = FakeSession(_oa(_OA_200))
    cf.search_openalex(TITLE, mailto="m", s=s, api_key=KEY)
    params = s.openalex_params()[0]
    assert params["api_key"] == KEY
    assert params["mailto"] == "m"
    # The filter tier, not the search tier: this leg is priced at $0.0001.
    assert params["filter"] == f"title.search:{TITLE}"


def test_the_key_changes_the_billing_not_the_score():
    """Same recorded 200 body, keyed and keyless -> the same float. The key is
    not allowed to move a judgment, and this is the assertion that says so."""
    keyless = cf.search_openalex(TITLE, mailto="m",
                                 s=FakeSession(_oa(_OA_200)), api_key="")
    keyed = cf.search_openalex(TITLE, mailto="m",
                               s=FakeSession(_oa(_OA_200)), api_key=KEY)
    assert keyed == keyless == 100.0


def test_a_spent_allowance_is_none_not_zero():
    """THE CENTRAL RULE (confirm.py's module docstring): 0.0 is 'searched,
    answered, found nothing' and is evidence; None is 'no answer' and is not.
    A 409 is a billing state and must never be scoreable."""
    assert cf.search_openalex(TITLE, mailto="m", s=_oa_409_session()) is None


def test_an_honest_empty_result_is_still_zero_with_a_key_set():
    """The mirror of the test above -- the fix must not turn a genuine
    'found nothing' into 'could not look'."""
    assert cf.search_openalex(TITLE, mailto="m",
                              s=FakeSession(_oa(_OA_EMPTY)),
                              api_key=KEY) == 0.0


def _confirm_with_openalex_409():
    """confirm() where PubMed and Crossref answer healthily and OpenAlex 409s.

    Mirrors the measured 2026-08-24 probe: the other two providers are fine, so
    OpenAlex is the only moving part.
    """
    def handler(url, params, i):
        if "openalex" in url:
            return _OA_409
        if "esearch" in url:
            return FakeResponse(200, {"esearchresult": {"idlist": ["1"]}})
        if "esummary" in url:
            return FakeResponse(200, {"result": {"1": {"title": TITLE}}})
        return FakeResponse(200, {"status": "ok", "message": {
            "items": [{"title": [TITLE]}]}})

    ref = Reference("c1", "", ClaimedRef(title=TITLE, authors=["A"], year=2000))
    hits = cf.confirm(ref, "", "m", "m", s=FakeSession(handler),
                      openalex_api_key=KEY)
    return ref, hits


def test_a_409_leaves_the_confirmation_not_fully_answered():
    _ref, hits = _confirm_with_openalex_409()
    assert hits["openalex"] is None
    assert hits["pubmed"] is not None and hits["crossref"] is not None
    assert cf.fully_answered(hits) is False
    # Strictly stronger than "somebody answered" -- that is the whole point of
    # the all-three bar, and it is not relaxed to two-of-three to dodge a bill.
    assert cf.all_errored(hits) is False
    assert cf.unanswered(hits) == ["openalex"]


def _f1_candidate(db_hits: dict) -> Reference:
    """A reference on the PMID path that is one search-result away from F1.

    The claimed PMID was fetched, the fetch ANSWERED, and it did not resolve --
    which is the strongest F1 posture the engine has. Everything except the
    confirmation searches is therefore already in place, so whether F1 is
    reached is decided purely by ``db_hits``.
    """
    ref = Reference("c1", "", ClaimedRef(title=TITLE, authors=["A"], year=2000,
                                         claimed_pmid="99999999"))
    ref.log.pmid_present = True
    ref.log.pmid_resolved = False
    ref.log.pmid_transport_status = "ok"
    ref.log.db_hits = db_hits
    return ref


def test_f1_is_reachable_when_all_three_providers_answer():
    """THE POSITIVE CONTROL for the test below. Without it, "409 never yields
    F1" could pass because this fixture never yields F1 under any conditions,
    and the acceptance row would be proving nothing."""
    answered_nothing = {"pubmed": 0.0, "crossref": 0.0, "openalex": 0.0}
    ref = _f1_candidate(answered_nothing)
    dc.decide(ref, True, "fabrication", answered_nothing)
    assert ref.label == S.F1
    assert ref.log.decided_by == "confirm_not_found_f1"


def test_a_409_can_never_license_f1():
    """The acceptance row that matters most: with OpenAlex unpaid, no
    combination of flag and LLM verdict may reach the F1 label.

    Note the fixture: PubMed and Crossref both ANSWERED and found the title, so
    the only thing standing between this reference and an accusation is the
    provider that could not be paid."""
    _ref, hits = _confirm_with_openalex_409()
    for verdict in (None, "fabrication", "wrong_reference", "uncertain",
                    "formatting_discrepancy"):
        fresh = _f1_candidate(hits)
        dc.decide(fresh, True, verdict, hits)
        assert fresh.label != S.F1, f"F1 licensed on a 409 with {verdict!r}"
    # And the same is true when the two paid providers found NOTHING -- which is
    # the case that looks most like fabrication and is exactly where a missing
    # third answer is most dangerous. This is the one that would otherwise slip
    # through: it is the F1 fixture above with a single score replaced by None.
    two_of_three = {"pubmed": 0.0, "crossref": 0.0, "openalex": None}
    fresh = _f1_candidate(two_of_three)
    dc.decide(fresh, True, "fabrication", two_of_three)
    assert fresh.label != S.F1
    assert fresh.log.decided_by == "confirm_incomplete_evidence"


def test_409_is_not_retried_because_a_spent_quota_is_not_transient():
    """Retrying a spent daily allowance cannot succeed and burns the run's
    remaining wall clock. 409 stays out of the retry set deliberately."""
    assert 409 not in ratelimit._RETRY_STATUS
    s = _oa_409_session()
    cf.search_openalex(TITLE, s=s)
    assert len(s.calls) == 1


# ---------------------------------------------------------------------------
# Leg B -- biblio_match._openalex_candidates, the expensive leg
# ---------------------------------------------------------------------------

def test_candidates_leg_carries_the_key_and_stays_on_the_search_tier():
    """`search=` costs 10x `filter=` and is the leg that empties an allowance,
    but switching tiers changes WHICH candidates come back, which changes F2 and
    F1 outcomes. It is a judgment change, so it is out of scope here and this
    test pins the tier."""
    claimed = ClaimedRef(title=TITLE, authors=["A"], year=2000)
    s = FakeSession(_oa(_OA_200))
    bm._openalex_candidates(claimed, 5, s, api_key=KEY)
    params = s.openalex_params()[0]
    assert params["api_key"] == KEY
    assert params["search"] == TITLE
    assert "filter" not in params


def test_candidates_leg_keyless_request_is_unchanged():
    claimed = ClaimedRef(title=TITLE, authors=["A"], year=2000)
    s = FakeSession(_oa(_OA_200))
    bm._openalex_candidates(claimed, 5, s)
    assert s.openalex_params()[0] == {"search": TITLE, "per-page": 5}


def test_a_409_on_the_candidates_leg_is_recorded_as_an_error_not_a_miss():
    claimed = ClaimedRef(title=TITLE, authors=["A"], year=2000)
    errors: list[str] = []
    out = bm._openalex_candidates(claimed, 5, _oa_409_session(), errors)
    assert out == []
    assert errors == ["openalex_candidates"]


def test_biblio_match_retrieve_candidates_threads_the_key():
    claimed = ClaimedRef(title=TITLE, authors=["A"], year=2000)
    s = FakeSession(_oa(_OA_EMPTY))
    bm.retrieve_candidates(claimed, session=s, openalex_api_key=KEY)
    assert all(p["api_key"] == KEY for p in s.openalex_params())


# ---------------------------------------------------------------------------
# THE UNFIXED DEFECT on the Band-1 no-PMID path
# ---------------------------------------------------------------------------

def test_fuzzy_biblio_lookup_LOSES_the_openalex_error_tag_KNOWN_DEFECT():
    """A quota-blocked OpenAlex is INDISTINGUISHABLE from a healthy empty
    result on the Band-1 no-PMID path, and this test pins that fact rather than
    hiding it.

    ``biblio_match.retrieve_candidates`` accepts an ``errors`` list and
    ``resolve_a`` passes one, so the F2-F cascade can route a thrown request to
    ``undetermined`` (retrieval_incomplete, spec §14.6). ``lookup``'s
    same-named seam -- the one ``fuzzy_biblio_lookup`` and therefore
    ``compare_and_flag`` actually call -- has no ``errors`` parameter at all, so
    the marker ``_openalex_candidates`` faithfully appends is dropped on the
    floor and the reference's log never learns that a provider could not be
    paid.

    IT IS NOT FIXED HERE, and not because it does not matter. Closing it changes
    which references reach ``undetermined``, which is an F2 disposition change
    and needs its own spec and its own before/after diff. Threading the API key
    makes the trigger far rarer; it does not close the hole.

    Blast radius, stated precisely so the next reader does not have to re-derive
    it: this cannot manufacture an F1. An empty candidate list yields
    ``resolved=False``, which routes through ``confirm()``, whose own OpenAlex
    leg 409s too and whose ``fully_answered`` bar then refuses F1 (see
    ``test_a_409_can_never_license_f1``). What it can do is silently shrink the
    F2 scoreable population -- a flaky or unpaid provider narrowing candidate
    retrieval while every artifact reports a clean miss.
    """
    claimed = ClaimedRef(title=TITLE, authors=["A"], year=2000)

    quota_blocked = Reference("n1", "", claimed)
    assert lk.fuzzy_biblio_lookup(
        quota_blocked, session=_oa_409_session()).resolved is False

    healthy_but_empty = Reference("n1", "", claimed)
    assert lk.fuzzy_biblio_lookup(
        healthy_but_empty,
        session=FakeSession(_oa(_OA_EMPTY))).resolved is False

    # The defect, stated as an equality: the two logs are byte-identical, so
    # nothing downstream -- decide() included -- can tell them apart.
    assert (quota_blocked.to_log_record()
            == healthy_but_empty.to_log_record())
    assert "openalex_candidates" not in json.dumps(
        quota_blocked.to_log_record()), (
        "If this now FAILS the defect has been fixed -- rewrite this test as a "
        "positive assertion and record the F2 disposition change in its own "
        "spec, because references that used to read as a clean miss will now "
        "route to undetermined/retrieval_incomplete.")


def test_the_lookup_seam_has_no_errors_parameter_while_biblio_match_does():
    """The structural half of the defect above, asserted directly so a future
    refactor cannot make the two seams silently diverge further."""
    import inspect
    assert "errors" in inspect.signature(bm.retrieve_candidates).parameters
    assert "errors" not in inspect.signature(lk.retrieve_candidates).parameters


# ---------------------------------------------------------------------------
# Leg C -- doi_lookup
# ---------------------------------------------------------------------------

def test_exact_doi_openalex_provider_carries_the_key():
    s = FakeSession(_oa({"results": []}))
    dl._openalex("10.1/x", s, KEY)
    assert s.openalex_params()[0]["api_key"] == KEY


def test_exact_doi_openalex_provider_keyless_request_is_unchanged():
    s = FakeSession(_oa({"results": []}))
    dl._openalex("10.1/x", s)
    assert s.openalex_params()[0] == {"filter": "doi:10.1/x", "per-page": 1}


def test_a_409_on_the_doi_leg_is_provider_error_not_provider_absent():
    """A provider that could not be paid has not testified that a DOI is
    missing. PROVIDER_ABSENT here would be evidence manufactured from a bill."""
    status, rec = dl._openalex("10.1/x", _oa_409_session(), KEY)
    assert status == dl.PROVIDER_ERROR
    assert rec is None


def test_lookup_exact_doi_gives_the_key_to_openalex_only():
    """Each provider is authenticated separately; no provider may be handed
    another's credential."""
    s = FakeSession(lambda url, params, i: FakeResponse(404, None))
    dl.lookup_exact_doi("10.1/x", s=s, openalex_api_key=KEY)
    assert s.openalex_params(), "OpenAlex was never called"
    assert len(s.calls) > len(s.openalex_params()), "no other provider was called"
    for url, params in s.calls:
        assert (params.get("api_key") == KEY) is ("openalex" in url), url


def test_abstract_seam_carries_the_key_alongside_the_mailto():
    s = FakeSession(_oa({"results": []}))
    dl.fetch_openalex_abstract("10.1/x", s=s, mailto="m", api_key=KEY)
    params = s.openalex_params()[0]
    assert params["api_key"] == KEY and params["mailto"] == "m"
    assert params["select"] == "id,doi,abstract_inverted_index"


def test_abstract_seam_keyless_request_is_unchanged():
    s = FakeSession(_oa({"results": []}))
    dl.fetch_openalex_abstract("10.1/x", s=s, mailto="m")
    assert s.openalex_params()[0] == {
        "filter": "doi:10.1/x", "per-page": 1, "mailto": "m",
        "select": "id,doi,abstract_inverted_index"}


# ---------------------------------------------------------------------------
# Leg D -- run.process_reference / run.run
# ---------------------------------------------------------------------------

def _noid_ref(cid="PMC1:R1"):
    return Reference(cid, "PMC1", ClaimedRef(
        title=TITLE, authors=["Hanahan"], year=2000, journal="Cell"))


def test_process_reference_puts_the_key_on_every_openalex_request():
    """The whole point of the change: one parameter at the entrypoint, and no
    OpenAlex call anywhere below it goes out anonymous."""
    s = FakeSession(_oa(_OA_EMPTY))
    ref = _noid_ref()
    run_mod.process_reference(ref, lambda _p: '{"verdict": "uncertain"}',
                              openalex_api_key=KEY, session=s)
    oa = s.openalex_params()
    assert oa, "no OpenAlex call was made; this test proves nothing"
    assert all(p.get("api_key") == KEY for p in oa), oa


def test_process_reference_with_no_key_sends_no_api_key_anywhere():
    s = FakeSession(_oa(_OA_EMPTY))
    run_mod.process_reference(_noid_ref(), lambda _p: '{"verdict": "uncertain"}',
                              session=s)
    oa = s.openalex_params()
    assert oa
    assert all("api_key" not in p for p in oa), oa


def test_the_key_does_not_move_the_verdict():
    """The regression guard in miniature: same fixtures, keyed and keyless, and
    the per-reference decision must be identical."""
    def decide_with(api_key):
        ref = _noid_ref()
        run_mod.process_reference(ref, lambda _p: '{"verdict": "uncertain"}',
                                  openalex_api_key=api_key,
                                  session=FakeSession(_oa(_OA_EMPTY)))
        return ref.label, ref.confidence, ref.log.db_hits
    assert decide_with(KEY) == decide_with("")


def test_run_threads_the_key_and_reports_per_leg_call_counts(tmp_path, capsys):
    s = FakeSession(_oa(_OA_EMPTY))
    monkey_refs = [_noid_ref("PMC1:R1"), _noid_ref("PMC1:R2")]
    # requests.Session is not used: `refs` and `complete` are both injected, so
    # the run is fully offline apart from the FakeSession handed to it.
    import requests as _rq
    real_session = _rq.Session
    try:
        _rq.Session = lambda: s               # run() builds its own session
        run_mod.run(str(tmp_path), str(tmp_path / "d.jsonl"),
                    str(tmp_path / "l.jsonl"), model="m",
                    openalex_api_key=KEY, refs=iter(monkey_refs),
                    complete=lambda _p: '{"verdict": "uncertain"}')
    finally:
        _rq.Session = real_session
    oa = s.openalex_params()
    assert oa and all(p.get("api_key") == KEY for p in oa)
    out = capsys.readouterr().out
    assert "[openalex-calls]" in out
    assert "keyed=True" in out
    assert "quota_exhausted=0" in out


def test_a_run_that_runs_out_of_allowance_says_so(tmp_path, capsys):
    """The telemetry requirement. A run whose OpenAlex account went dry used to
    be indistinguishable, in every artifact it produced, from a run over a
    corpus OpenAlex does not index."""
    s = _oa_409_session()
    import requests as _rq
    real_session = _rq.Session
    try:
        _rq.Session = lambda: s
        run_mod.run(str(tmp_path), str(tmp_path / "d.jsonl"),
                    str(tmp_path / "l.jsonl"), model="m",
                    refs=iter([_noid_ref()]),
                    complete=lambda _p: '{"verdict": "uncertain"}')
    finally:
        _rq.Session = real_session
    out = capsys.readouterr().out
    assert "[openalex-quota]" in out
    assert "keyed=False" in out


# ---------------------------------------------------------------------------
# Telemetry
# ---------------------------------------------------------------------------

def test_every_leg_is_named_in_a_delta_even_when_it_made_no_call():
    """A leg that made no call and a leg that was never wired must not look the
    same in a manifest."""
    before = tele.snapshot()
    tele.record(tele.LEG_CONFIRM, FakeResponse(200))
    tele.record(tele.LEG_CONFIRM, FakeResponse(409))
    tele.record(tele.LEG_CANDIDATES, error=True)
    d = tele.delta(before)
    assert set(d["legs"]) == set(tele.LEGS)
    assert d["legs"][tele.LEG_CONFIRM] == {"200": 1, "409": 1}
    assert d["legs"][tele.LEG_CANDIDATES] == {tele.TRANSPORT_ERROR: 1}
    assert d["legs"][tele.LEG_DOI] == {}
    assert d["leg_totals"] == {tele.LEG_CONFIRM: 2, tele.LEG_CANDIDATES: 1,
                               tele.LEG_DOI: 0, tele.LEG_ABSTRACT: 0}
    assert d["total"] == 3
    assert d["quota_exhausted"] == 1


def test_a_delta_charges_a_window_not_the_whole_process():
    tele.record(tele.LEG_DOI, FakeResponse(200))
    before = tele.snapshot()
    tele.record(tele.LEG_DOI, FakeResponse(200))
    assert tele.delta(before)["total"] == 1


def test_the_counters_never_hold_the_key():
    s = FakeSession(_oa(_OA_200))
    cf.search_openalex(TITLE, s=s, api_key="SECRET-KEY")
    assert "SECRET-KEY" not in json.dumps(tele.snapshot())


def test_each_leg_counts_under_its_own_name():
    before = tele.snapshot()
    cf.search_openalex(TITLE, s=FakeSession(_oa(_OA_200)))
    bm._openalex_candidates(ClaimedRef(title=TITLE), 5,
                            FakeSession(_oa(_OA_200)))
    dl._openalex("10.1/x", FakeSession(_oa({"results": []})))
    dl.fetch_openalex_abstract("10.1/x", s=FakeSession(_oa({"results": []})))
    assert tele.delta(before)["leg_totals"] == {
        tele.LEG_CONFIRM: 1, tele.LEG_CANDIDATES: 1,
        tele.LEG_DOI: 1, tele.LEG_ABSTRACT: 1}


# ---------------------------------------------------------------------------
# The launcher
# ---------------------------------------------------------------------------

def test_the_abstract_seam_is_bound_to_the_key_by_the_launcher():
    """Bound by the launcher, not left to the caller, because a run that forgets
    does not fail -- it reports 'no abstract' for every DOI-only reference and
    terminates them UNJUDGEABLE."""
    s = FakeSession(_oa({"results": []}))
    import requests as _rq
    real_session = _rq.Session
    try:
        # The seam binds its own session at construction time, so the swap has
        # to be in place before it is built.
        _rq.Session = lambda: s
        pl._openalex_abstract_seam("m", KEY)("10.1/x")
    finally:
        _rq.Session = real_session
    params = s.openalex_params()[0]
    assert params["api_key"] == KEY and params["mailto"] == "m"


def _corpus(tmp_path):
    xml_dir = tmp_path / "xml"
    xml_dir.mkdir()
    raw = b"<article/>"
    (xml_dir / "PMC1.xml").write_bytes(raw)
    manifest = tmp_path / "corpus.json"
    manifest.write_text(json.dumps({
        "documents": {"PMC1.xml": hashlib.sha256(raw).hexdigest()}}),
        encoding="utf-8")
    return xml_dir, manifest


def _run_seams():
    fn = lambda *args, **kwargs: None
    return {"extractor": fn, "coverage_judge": fn, "coverage_judge_v3": fn,
            "fetch_abstract": fn, "fetch_fulltext": fn,
            "discriminator_call_llm": fn, "f3_fetch_reflist": fn,
            "f3_resolve_pmcid": fn, "pubtypes_lookup": fn}


def test_launch_full_threads_the_key_and_puts_the_tallies_in_the_manifest(
        tmp_path, monkeypatch):
    """The manifest acceptance row: a completed run's durable manifest carries
    per-leg OpenAlex call counts, and says whether the run was authenticated --
    so the daily allowance is OBSERVABLE rather than inferred after a run dies.
    """
    xml_dir, corpus_manifest = _corpus(tmp_path)
    out_dir = tmp_path / "out"
    receipt = AdapterReceipt(model="model", temperature=0)
    captured = {}

    monkeypatch.setattr(pl, "verify_tree", lambda *_a, **_k: {
        "code_commit": "a" * 40, "runtime_module_sha256": {}})
    monkeypatch.setattr(pl, "verify_judge_governance", lambda **_k: {})
    f5s = importlib.import_module(pl.__package__ + ".f5_seams")
    f7s = importlib.import_module(pl.__package__ + ".f7_seams")
    monkeypatch.setattr(f5s, "validate_production_f5_configuration",
                        lambda **_k: None)
    monkeypatch.setattr(f7s, "validate_production_f7_configuration",
                        lambda **_k: None)

    band1 = importlib.import_module(pl.__package__ + ".run")

    def fake_band1(_xml, dataset, logs, **kwargs):
        captured["band1"] = kwargs
        kwargs["complete"]("prompt")
        # Spend some allowance so the manifest has something to report.
        tele.record(tele.LEG_CANDIDATES, FakeResponse(200))
        tele.record(tele.LEG_CANDIDATES, FakeResponse(409))
        with open(dataset, "w", encoding="utf-8") as fh:
            fh.write("{}\n")
        with open(logs, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "citation_id": "PMC1:R1", "label": "F2",
                "claimed": {"claimed_pmid": "", "claimed_doi": "10.1/x"},
                "retrieved": {"resolved": True, "pmid": "123"},
                "log": {"decided_by": "exact_doi_metadata_mismatch_f2",
                        "retracted": False, "f8_timing_status": "clear"}}) + "\n")
        return {"F2": 1}

    monkeypatch.setattr(band1, "run", fake_band1)

    def fake_launch(**kwargs):
        captured["launch"] = kwargs
        manifest_path = out_dir / "judgment" / "judgment_run_manifest.json"
        manifest_path.parent.mkdir(parents=True)
        return {"manifest_path": str(manifest_path),
                "predictions_path": str(out_dir / "judgment" /
                                        "predictions.jsonl")}

    monkeypatch.setattr(pl, "launch", fake_launch)
    marker = object()
    manifest = pl.launch_full(
        repo_dir="/repo", pkg_dir="/pkg", xml_dir=str(xml_dir),
        out_dir=str(out_dir), corpus_manifest_path=str(corpus_manifest),
        model="model", authorized_models=["model"],
        adapter_receipt=receipt, band1_snapshot_date="2026-08-20",
        judge_model="other-model", temperature=0,
        f1_complete=lambda _p: "{}", openalex_mailto="m",
        openalex_api_key=KEY,
        f5_seams=marker, f5_evidence_builder=marker, f5_policy=marker,
        f7_seams=marker, f7_evidence_builder=marker, f7_policy=marker,
        **_run_seams())

    # The key reaches Band 1 ...
    assert captured["band1"]["openalex_api_key"] == KEY
    # ... and Band 2, via the abstract seam the launcher builds itself.
    assert captured["launch"]["openalex_api_key"] == KEY
    assert callable(captured["launch"]["fetch_openalex_abstract"])

    full = manifest["full_launch"]
    assert full["openalex_authenticated"] is True
    calls = full["band1_openalex_calls"]
    assert calls["legs"][tele.LEG_CANDIDATES] == {"200": 1, "409": 1}
    assert calls["quota_exhausted"] == 1
    assert set(calls["legs"]) == set(tele.LEGS)
    # And it is DURABLE -- a manifest read after the run says the same thing.
    durable = json.loads((out_dir / "judgment" /
                          "judgment_run_manifest.json").read_text())
    assert durable["full_launch"]["band1_openalex_calls"] == calls
    assert KEY not in json.dumps(durable), "the key must never be persisted"
