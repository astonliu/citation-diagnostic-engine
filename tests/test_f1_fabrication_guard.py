"""F1 fabrication guard -- transport failure must never become an accusation.

One test per row of the acceptance matrix in
``docs/F1_FABRICATION_GUARD_SPEC.md`` (ZD, 2026-08-16), plus the true-positive
guards that prove the fix did not simply disable F1.

THE DEFECT THESE COVER. A partial NCBI outage labelled a real, PubMed-indexed
paper ``F1`` at ``HIGH`` confidence -- the only defect in the taxonomy that
produces a false PUBLIC ACCUSATION, that a real paper does not exist. It had
three independent causes, and each one is pinned below:

  * ``resolved=False`` meant both "the PMID is dead" and "NCBI did not answer";
  * one healthy-but-empty search licensed the accusation while others errored;
  * a skipped search scored ``0.0``, so three searches that were never issued
    were presented as evidence of non-existence.

No network is touched: every response is replayed through a fake session.
"""
from __future__ import annotations

import pytest
import requests

from cde.refs import confirm
from cde.refs import lookup
from cde.runtime import ratelimit
from cde.refs import run
from cde.refs import schema as S
from cde.refs.confirm import search_crossref, search_openalex, search_pubmed
from cde.refs.decide import decide
from cde.refs.lookup import compare_and_flag, fetch_pubmed
from cde.refs.schema import ClaimedRef, Reference

EFETCH = lookup.EFETCH
ESEARCH = confirm.PUBMED_ESEARCH
ESUMMARY = confirm.PUBMED_ESUMMARY
CROSSREF = confirm.CROSSREF_URL
OPENALEX = confirm.OPENALEX_URL

#: A real MEDLINE record, trimmed. Resolves, but to an UNRELATED paper.
MEDLINE_OK = """
PMID- 31665581
DP  - 2019 Oct 31
TI  - Purple Urine after Catheterization.
AU  - Chen L
TA  - N Engl J Med
"""

#: Resolves to exactly what ``_ref()`` claims -> the reference CLEARS.
MEDLINE_MATCHING = """
PMID- 99999999
DP  - 2020 Jan 15
TI  - A plausible sounding study of nothing.
AU  - Smith J
TA  - J Test Med
"""


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
    """Routes GETs through handler(url, params) -> FakeResponse | raises."""

    def __init__(self, handler):
        self.handler = handler
        self.urls: list[str] = []

    def get(self, url, params=None, timeout=None):
        self.urls.append(url)
        return self.handler(url, params)


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    # Exercise the throttle/backoff logic without spending wall-clock on it.
    monkeypatch.setattr(ratelimit.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(run.time, "sleep", lambda *a, **k: None)


def _fabrication_llm(_prompt: str) -> str:
    return '{"verdict":"fabrication","reason":"invented"}'


def _ref(title="A plausible sounding study of nothing", pmid="99999999"):
    return Reference("c1", "citance", ClaimedRef(title=title, claimed_pmid=pmid,
                                                 authors=["Smith J"], year=2020))


def _empty_search(url, _params):
    """All three databases healthy, and none of them has the work."""
    if url == ESEARCH:
        return FakeResponse(json_data={"esearchresult": {"idlist": []}})
    if url == CROSSREF:
        return FakeResponse(json_data={"status": "ok",
                                       "message": {"items": []}})
    if url == OPENALEX:
        return FakeResponse(json_data={"results": []})
    raise AssertionError(f"unexpected url {url}")


def _crossref_finds_it(title):
    def handler(url, params):
        if url == CROSSREF:
            return FakeResponse(json_data={
                "status": "ok",
                "message": {"items": [{"title": [title]}]}})
        return _empty_search(url, params)
    return handler


# =====================================================================
# Defect 1 -- transport failure is not an absence
# =====================================================================
def test_efetch_429_exhausted_is_resolver_error_not_absence():
    """A 429 that survived every retry is returned as the final response by
    ratelimit.request_with_retry -- it must not read as a dead PMID."""
    sess = FakeSession(lambda url, p: FakeResponse(status_code=429))
    rec = fetch_pubmed("31665581", session=sess)
    assert rec.resolved is False
    assert rec.transport_status == S.FETCH_RESOLVER_ERROR
    assert S.fetch_answered(rec.transport_status) is False


def test_efetch_connection_error_is_resolver_error():
    def boom(url, params):
        raise requests.ConnectionError("network unreachable")
    rec = fetch_pubmed("31665581", session=FakeSession(boom))
    assert rec.transport_status == S.FETCH_RESOLVER_ERROR


def test_efetch_200_empty_body_is_answered_absent():
    """VERIFIED AGAINST LIVE NCBI 2026-08-16: a nonexistent PMID returns HTTP
    200 with an empty body. This is the ONLY shape that is evidence."""
    sess = FakeSession(lambda url, p: FakeResponse(status_code=200, text=""))
    rec = fetch_pubmed("99999999", session=sess)
    assert rec.resolved is False
    assert rec.transport_status == S.FETCH_ANSWERED_ABSENT
    assert S.fetch_answered(rec.transport_status) is True


def test_efetch_200_record_is_answered_record():
    sess = FakeSession(lambda url, p: FakeResponse(text=MEDLINE_OK))
    rec = fetch_pubmed("31665581", session=sess)
    assert rec.resolved is True
    assert rec.transport_status == S.FETCH_ANSWERED_RECORD


def test_no_pmid_is_not_attempted():
    assert fetch_pubmed("").transport_status == S.FETCH_NOT_ATTEMPTED


def test_transport_status_reaches_the_durable_record():
    """StageLog carries it, so a dead PMID and a failed fetch are no longer
    byte-identical in the log -- the whole point of the field."""
    ref = _ref()
    sess = FakeSession(lambda url, p: FakeResponse(status_code=503))
    ref.retrieved = fetch_pubmed(ref.claimed.claimed_pmid, session=sess)
    compare_and_flag(ref, 85.0, session=sess)
    assert ref.log.pmid_transport_status == S.FETCH_RESOLVER_ERROR
    assert "did not answer" in ref.log.notes
    assert "did not resolve" not in ref.log.notes
    # And it ships on the prediction record next to the boolean it qualifies.
    assert ref.to_prediction().evidence["pmid_transport_status"] == \
        S.FETCH_RESOLVER_ERROR


def test_old_cached_record_without_status_reads_as_answered():
    """Replaying a historical log must not reclassify every row as an outage."""
    assert S.fetch_answered("") is True


# --- acceptance matrix rows 1 and 2 ---------------------------------------
def test_matrix_efetch_429_title_found_in_crossref_is_not_f1():
    """Row 1: EFetch 429 exhausted, title found in Crossref -> held, not F1."""
    ref = _ref()
    title = ref.claimed.title

    def handler(url, params):
        if url == EFETCH:
            return FakeResponse(status_code=429)
        return _crossref_finds_it(title)(url, params)

    sess = FakeSession(handler)
    out = run.process_reference(ref, _fabrication_llm, session=sess)
    assert out.label not in (S.F1, S.F2)
    assert out.label == S.HUMAN_REVIEW
    assert out.log.decided_by == "pmid_fetch_no_answer"
    # the transport failure is NAMED, not merely implied
    assert S.FETCH_RESOLVER_ERROR in out.rationale


def test_matrix_efetch_connection_error_partial_searches_is_not_f1():
    """Row 2: EFetch connection error, one search errored, one empty -> held."""
    ref = _ref()

    def handler(url, params):
        if url == EFETCH:
            raise requests.ConnectionError("down")
        if url == ESEARCH:
            return FakeResponse(status_code=500)          # errored
        if url == CROSSREF:
            return FakeResponse(json_data={"status": "ok",
                                           "message": {"items": []}})
        return FakeResponse(json_data={"results": []})

    out = run.process_reference(ref, _fabrication_llm,
                                session=FakeSession(handler))
    assert out.label == S.HUMAN_REVIEW
    assert out.label not in (S.F1, S.F2)


def test_transport_failure_short_circuits_before_paying_for_evidence():
    """An unanswered EFetch decides the row, so the LLM and the three searches
    must not be bought. During an outage this is the hot path."""
    ref = _ref()
    calls = {"llm": 0}

    def counting_llm(_p):
        calls["llm"] += 1
        return _fabrication_llm(_p)

    def handler(url, params):
        if url == EFETCH:
            return FakeResponse(status_code=502)
        raise AssertionError("no confirmation search may be issued")

    sess = FakeSession(handler)
    run.process_reference(ref, counting_llm, session=sess)
    assert calls["llm"] == 0
    # EFetch alone -- retried, since 502 is retryable, but nothing else is hit.
    assert set(sess.urls) == {EFETCH}


# --- acceptance matrix row 3: complete empty evidence reaches the end -------
def test_matrix_genuine_dead_pmid_all_searches_empty_is_held_not_accused():
    """Row 3, as rewritten 2026-08-25.

    200 empty body + three healthy empty searches used to be the F1 true
    positive, and this row existed to prove the retrieval guards had not simply
    disabled F1. That route no longer accuses at all: a dead PMID plus a title
    our three title searches could not match supports neither non-existence (F1)
    nor a wrong-reference finding (F2, which has no work to name here), so the
    row is HELD (see ``decide.py``).

    What the row still defends is that the evidence is COMPLETE when it gets
    there -- the fetch ANSWERED-ABSENT, all three searches answered -- so the
    hold is a judgement about what that evidence supports, not a guard firing.
    Distinguishing it from the incomplete-sweep holds below is the point of
    asserting ``decided_by``.
    """
    ref = _ref()

    def handler(url, params):
        if url == EFETCH:
            return FakeResponse(status_code=200, text="")   # genuinely dead
        return _empty_search(url, params)

    out = run.process_reference(ref, _fabrication_llm,
                                session=FakeSession(handler))
    assert out.label == S.HUMAN_REVIEW
    assert out.log.decided_by == "confirm_not_found_human_review"
    # ...and it got there on COMPLETE evidence, not on a guard.
    assert out.log.db_hits == {"pubmed": 0.0, "crossref": 0.0, "openalex": 0.0}
    assert out.log.pmid_transport_status == S.FETCH_ANSWERED_ABSENT


# =====================================================================
# Defect 2 -- the confirmation guard (ZD: every search must answer)
# =====================================================================
@pytest.mark.parametrize("hits", [
    {"pubmed": 0.0, "crossref": None, "openalex": None},
    {"pubmed": 0.0, "crossref": 0.0, "openalex": None},
    {"pubmed": None, "crossref": 0.0, "openalex": 0.0},
])
def test_incomplete_search_evidence_holds(hits):
    ref = _ref()
    ref.log.pmid_present = True
    ref.log.pmid_resolved = True
    out = decide(ref, True, S.V_FABRICATION, hits)
    assert out.label == S.HUMAN_REVIEW
    assert out.log.decided_by == "confirm_incomplete_evidence"


def test_all_errored_keeps_its_established_reason_code():
    ref = _ref()
    ref.log.pmid_present = True
    ref.log.pmid_resolved = True
    out = decide(ref, True, S.V_FABRICATION,
                 {"pubmed": None, "crossref": None, "openalex": None})
    assert out.label == S.HUMAN_REVIEW
    assert out.log.decided_by == "confirm_all_errored"


def test_complete_empty_search_evidence_reaches_the_hold_not_a_guard():
    """Complete evidence is distinguishable from incomplete evidence even
    though both now end in human_review -- the reason code separates them."""
    ref = _ref()
    ref.log.pmid_present = True
    ref.log.pmid_resolved = True
    out = decide(ref, True, S.V_FABRICATION,
                 {"pubmed": 0.0, "crossref": 0.0, "openalex": 0.0})
    assert out.label == S.HUMAN_REVIEW
    assert out.log.decided_by == "confirm_not_found_human_review"


def test_fully_answered_predicate():
    assert confirm.fully_answered({"a": 0.0, "b": 0.0}) is True
    assert confirm.fully_answered({"a": 0.0, "b": None}) is False
    assert confirm.fully_answered({}) is False
    assert confirm.unanswered({"b": None, "a": 0.0, "c": None}) == ["b", "c"]


def test_completeness_gate_does_not_cost_f2_recall():
    """A POSITIVE finding needs no complete sweep. F2 recall is non-negotiable:
    if a database returned the work, an outage elsewhere is irrelevant."""
    ref = _ref()
    ref.log.pmid_present = True
    ref.log.pmid_resolved = True
    out = decide(ref, True, S.V_REFERENCE_ERROR,
                 {"pubmed": 99.0, "crossref": None, "openalex": None})
    assert out.label == S.F2


# =====================================================================
# Defect 3 -- a skipped search is None, and UNSCOREABLE is reachable
# =====================================================================
def test_matrix_skipped_search_scores_none_never_zero():
    """Row 5: no claimed title -> None per skipped search, and NO request."""
    sess = FakeSession(lambda url, p:
                       pytest.fail(f"no request may be issued: {url}"))
    assert search_pubmed("", s=sess) is None
    assert search_crossref("", s=sess) is None
    assert search_openalex("", s=sess) is None
    assert sess.urls == []


def test_confirm_reports_none_for_a_titleless_reference():
    ref = Reference("c1", "", ClaimedRef(title="", claimed_pmid="1"))
    hits = confirm.confirm(ref, s=FakeSession(lambda url, p: FakeResponse()))
    assert hits == {"pubmed": None, "crossref": None, "openalex": None}
    # ...and such evidence can never reach a finding.
    assert confirm.fully_answered(hits) is False


def test_matrix_no_title_dead_pmid_is_unscoreable_not_f1():
    """Row 4: no claimed title + dead PMID -> UNSCOREABLE.

    The unscoreable gate used to sit AFTER the unresolved-PMID early return, so
    this reference reached F1 on db_hits={0.0, 0.0, 0.0} with zero searches
    issued -- three fabricated zeros presented as evidence.
    """
    ref = Reference("c1", "citance", ClaimedRef(title="", claimed_pmid="99999999"))

    def handler(url, params):
        if url == EFETCH:
            return FakeResponse(status_code=200, text="")
        raise AssertionError(f"no search may be issued: {url}")

    sess = FakeSession(handler)
    out = run.process_reference(ref, _fabrication_llm, session=sess)
    assert out.label == S.UNSCOREABLE
    assert out.label not in (S.F1, S.F2)
    assert out.log.unscoreable_reason == "no_claimed_title"
    assert sess.urls == [EFETCH]


def test_control_no_title_resolving_pmid_still_unscoreable():
    """The spec's control: same reference, PMID resolves -> unscoreable too."""
    ref = Reference("c1", "citance", ClaimedRef(title="", claimed_pmid="31665581"))
    sess = FakeSession(lambda url, p: FakeResponse(text=MEDLINE_OK))
    out = run.process_reference(ref, _fabrication_llm, session=sess)
    assert out.label == S.UNSCOREABLE


# =====================================================================
# Defect 4 -- HTTP-200 error payloads are not "found nothing"
# =====================================================================
def test_matrix_entrez_200_error_body_scores_none():
    sess = FakeSession(lambda url, p: FakeResponse(
        json_data={"esearchresult": {"ERROR": "Invalid db name"}}))
    assert search_pubmed("a title", s=sess) is None


def test_entrez_200_top_level_error_scores_none():
    sess = FakeSession(lambda url, p: FakeResponse(
        json_data={"error": "Search Backend failed"}))
    assert search_pubmed("a title", s=sess) is None


def test_entrez_200_unexpected_shape_scores_none():
    sess = FakeSession(lambda url, p: FakeResponse(json_data={"unexpected": 1}))
    assert search_pubmed("a title", s=sess) is None


def test_matrix_crossref_200_status_error_scores_none():
    sess = FakeSession(lambda url, p: FakeResponse(
        json_data={"status": "error", "message": "upstream failure"}))
    assert search_crossref("a title", s=sess) is None


def test_openalex_200_error_body_scores_none():
    sess = FakeSession(lambda url, p: FakeResponse(
        json_data={"error": "Invalid query parameters"}))
    assert search_openalex("a title", s=sess) is None


def test_healthy_empty_results_still_score_zero():
    """The mirror of the defect: an honest 'found nothing' must NOT become
    'could not look', or F1 becomes unreachable and the guard is a disabling."""
    sess = FakeSession(_empty_search)
    assert search_pubmed("a title", s=sess) == 0.0
    assert search_crossref("a title", s=sess) == 0.0
    assert search_openalex("a title", s=sess) == 0.0


def test_entrez_benign_nested_errorlist_is_not_a_fault():
    """Entrez nests a benign errorlist (phrasesnotfound) in a SUCCESSFUL
    zero-hit search. Treating it as a fault would suppress true positives."""
    sess = FakeSession(lambda url, p: FakeResponse(json_data={
        "esearchresult": {"idlist": [],
                          "errorlist": {"phrasesnotfound": ["zzz"],
                                        "fieldsnotfound": []}}}))
    assert search_pubmed("a title", s=sess) == 0.0


def test_matrix_bracketed_translated_title_is_deliberately_not_quoted():
    """Row 8 -- LIVE-NCBI FINDING, 2026-08-16. Reported to ZD; see
    docs/F1_ESEARCH_TERM_FINDING_2026-08-16.md.

    The audit's suspicion was that a leading '[' makes the ESearch term
    malformed. Live NCBI says it does NOT: Entrez tolerates the bracket, warns
    ``outputmessages: ['[', ']']``, and still answers. The bracketed real title
    '[Myalgia and statins: Separating the true from the false].' returned
    exactly its own PMID (31473026).

    The spec's proposed remedy -- quoting -- was measured and is CATASTROPHIC:
    full article titles are not in PubMed's phrase index, so quoting returns 0
    hits for titles that the unquoted form finds exactly
    ('"Purple Urine after Catheterization"[Title]' -> 0, unquoted -> 1). Quoting
    every title would score nearly every reference 0.0 and manufacture
    fabrication evidence corpus-wide.

    So the term is left unquoted ON PURPOSE. This test pins that decision so a
    future reader does not "fix" it back.
    """
    seen = {}

    def handler(url, params):
        seen.update(params or {})
        return FakeResponse(json_data={"esearchresult": {"idlist": []}})

    search_pubmed("[Effect of statins on lipid levels].",
                  s=FakeSession(handler))
    assert seen["term"] == "[Effect of statins on lipid levels]."
    assert seen["field"] == "title"
    assert '"' not in seen["term"]


# =====================================================================
# Defect 5 -- the mirror-image error must not produce a false F2
# =====================================================================
def test_matrix_efetch_dead_title_found_holds_without_asserting_resolution():
    """Row 9: EFetch transport-dead while the search FINDS the title used to
    yield F2 'claimed PMID resolves to a different paper'. Nothing resolved."""
    ref = _ref()
    title = ref.claimed.title

    def handler(url, params):
        if url == EFETCH:
            raise requests.ConnectionError("down")
        return _crossref_finds_it(title)(url, params)

    out = run.process_reference(ref, _fabrication_llm,
                                session=FakeSession(handler))
    assert out.label != S.F2
    assert out.label == S.HUMAN_REVIEW
    assert "resolves to a different paper" not in out.rationale


def test_answered_absent_plus_found_is_f2_with_an_honest_rationale():
    """A genuinely dead PMID whose work exists IS a wrong reference -- but the
    rationale must not claim a resolution that never happened."""
    ref = _ref()
    ref.log.pmid_present = True
    ref.log.pmid_resolved = False
    ref.log.pmid_transport_status = S.FETCH_ANSWERED_ABSENT
    out = decide(ref, True, S.V_REFERENCE_ERROR,
                 {"pubmed": 99.0, "crossref": 0.0, "openalex": 0.0})
    assert out.label == S.F2
    assert "has no PubMed record" in out.rationale
    assert "resolves to a different paper" not in out.rationale


def test_resolved_wrong_paper_f2_rationale_unchanged():
    ref = _ref()
    ref.log.pmid_present = True
    ref.log.pmid_resolved = True
    out = decide(ref, True, S.V_REFERENCE_ERROR,
                 {"pubmed": 99.0, "crossref": 0.0, "openalex": 0.0})
    assert out.label == S.F2
    assert "resolves to a different paper" in out.rationale


# =====================================================================
# Defect 6 -- one bad row must not kill the batch
# =====================================================================
def _run_batch(tmp_path, refs, handler, monkeypatch):
    monkeypatch.setattr(run, "make_completer", lambda *a, **k: _fabrication_llm)
    monkeypatch.setattr(run.requests, "Session", lambda: FakeSession(handler))
    return run.run("", str(tmp_path / "ds.jsonl"), str(tmp_path / "logs.jsonl"),
                   model="m", refs=refs)


def test_crossref_string_message_no_longer_kills_the_batch(tmp_path,
                                                           monkeypatch):
    """Row 10. A Crossref 200 whose `message` is a STRING raised AttributeError
    out of confirm() and took the whole run with it.

    It is now handled one level earlier than the spec anticipated: the malformed
    payload is classified as an ERRORED SEARCH (None) rather than escaping as an
    exception, which is strictly better -- the row is held on incomplete
    evidence instead of quarantined. Either way the batch completes, which is
    what the row requires.
    """
    def handler(url, params):
        if url == EFETCH:
            return FakeResponse(status_code=200, text="")
        if url == CROSSREF:
            return FakeResponse(json_data={"status": "ok", "message": "oops"})
        return _empty_search(url, params)

    refs = [_ref(), _ref(), _ref()]
    for i, r in enumerate(refs):
        r.citation_id = f"c{i}"
    counts = _run_batch(tmp_path, refs, handler, monkeypatch)

    assert sum(counts.values()) == 3                 # the batch COMPLETED
    assert all(r.label == S.HUMAN_REVIEW for r in refs)
    assert all(r.log.decided_by == "confirm_incomplete_evidence" for r in refs)
    assert search_crossref("t", s=FakeSession(handler)) is None


def test_unexpected_exception_quarantines_the_row_and_the_batch_completes(
        tmp_path, monkeypatch):
    """The per-reference guard itself, proven with an injected fault so it does
    not depend on any particular upstream payload staying broken."""
    boom = _ref()
    boom.citation_id = "bad"
    good_before, good_after = _ref(), _ref()
    good_before.citation_id, good_after.citation_id = "ok1", "ok2"

    real = run.compare_and_flag

    def exploding(ref, *a, **k):
        if ref.citation_id == "bad":
            raise AttributeError("'str' object has no attribute 'get'")
        return real(ref, *a, **k)

    monkeypatch.setattr(run, "compare_and_flag", exploding)

    def handler(url, params):
        if url == EFETCH:
            return FakeResponse(status_code=200, text="")
        return _empty_search(url, params)

    counts = _run_batch(tmp_path, [good_before, boom, good_after], handler,
                        monkeypatch)

    assert sum(counts.values()) == 3                 # the batch COMPLETED
    assert boom.label == S.HUMAN_REVIEW              # unjudged is never a finding
    assert boom.log.decided_by == "quarantine_exception"
    assert "AttributeError" in boom.log.notes
    # The neighbours were JUDGED (they reach the end of decide()), which is what
    # separates them from the quarantined row -- not merely 'also human_review'.
    for ok in (good_before, good_after):
        assert ok.log.decided_by == "confirm_not_found_human_review"


# =====================================================================
# Defect 7 -- zero F1 must be distinguishable from "could not check"
# =====================================================================
def _summarize_run(tmp_path, refs, handler, monkeypatch):
    from cde.refs import eval_report
    _run_batch(tmp_path, refs, handler, monkeypatch)
    logs = [r.to_log_record() for r in refs]
    return eval_report.summarize(logs)


def test_matrix_zero_f1_run_reports_every_instrumentation_key(tmp_path,
                                                              monkeypatch):
    """Row 11: a run with zero F1 carries attempted/answered/transport-failed/
    fired -- a zero, not an absent key."""
    ref = _ref()

    def handler(url, params):
        if url == EFETCH:
            return FakeResponse(text=MEDLINE_MATCHING)   # resolves and matches
        return _empty_search(url, params)

    rep = _summarize_run(tmp_path, [ref], handler, monkeypatch)
    f1s = rep["f1_status"]
    for key in ("attempted", "answered", "transport_failed", "fired"):
        assert key in f1s, key
    assert f1s["fired"] == 0
    assert rep["counts"]["f1_count"] == 0            # present, not missing
    assert f1s["attempted"] == 1 and f1s["answered"] == 1
    assert f1s["transport_failed"] == 0


def test_run_returns_a_zero_f1_count_rather_than_an_absent_key(tmp_path,
                                                               monkeypatch):
    ref = _ref()

    def handler(url, params):
        if url == EFETCH:
            return FakeResponse(text=MEDLINE_MATCHING)
        return _empty_search(url, params)

    counts = _run_batch(tmp_path, [ref], handler, monkeypatch)
    assert ref.label == S.CLEARED
    assert counts[S.F1] == 0             # the KEY exists on a zero-F1 run


def test_matrix_a_run_that_could_not_check_is_distinguishable_from_zero(
        tmp_path, monkeypatch):
    """Row 12. Both runs report fired=0. Only the instrumentation separates
    'no fabrications' from 'the check never ran'."""
    ref = _ref()

    def dead(url, params):
        if url == EFETCH:
            return FakeResponse(status_code=429)       # NCBI down throughout
        raise AssertionError("no search should be reached")

    rep = _summarize_run(tmp_path, [ref], dead, monkeypatch)
    f1s = rep["f1_status"]
    assert f1s["fired"] == 0
    assert f1s["attempted"] == 1
    assert f1s["answered"] == 0
    assert f1s["transport_failed"] == 1               # <- the distinguisher

    from cde.refs import eval_report
    text = eval_report.format_report(rep)
    assert "does not mean zero fabrications" in text


def test_incomplete_confirmation_is_counted(tmp_path, monkeypatch):
    ref = _ref()

    def handler(url, params):
        if url == EFETCH:
            return FakeResponse(status_code=200, text="")
        if url == CROSSREF:
            return FakeResponse(status_code=503)       # one provider down
        return _empty_search(url, params)

    rep = _summarize_run(tmp_path, [ref], handler, monkeypatch)
    assert rep["f1_status"]["confirm_incomplete"] == 1
    assert rep["f1_status"]["confirm_complete"] == 0
    assert rep["f1_status"]["fired"] == 0
    assert ref.label == S.HUMAN_REVIEW


# =====================================================================
# Regression guard -- the fix must not disable F1
# =====================================================================
def test_full_evidence_reaches_the_end_of_decide_after_every_guard():
    """Dead PMID that ANSWERED, a claimed title that WAS searched for, three
    databases that ALL answered and found nothing: every guard added by this
    spec is satisfied and the row reaches the terminal branch on its merits.

    Since 2026-08-25 that branch holds rather than accuses (see ``decide.py``),
    so what this asserts is the ROUTE, plus the three searches actually being
    bought -- a guard that short-circuited early would fail both."""
    ref = _ref()

    def handler(url, params):
        if url == EFETCH:
            return FakeResponse(status_code=200, text="")
        return _empty_search(url, params)

    sess = FakeSession(handler)
    out = run.process_reference(ref, _fabrication_llm, session=sess)
    assert out.label == S.HUMAN_REVIEW
    assert out.log.decided_by == "confirm_not_found_human_review"
    assert out.log.db_hits == {"pubmed": 0.0, "crossref": 0.0, "openalex": 0.0}
    assert ESEARCH in sess.urls and CROSSREF in sess.urls and OPENALEX in sess.urls
