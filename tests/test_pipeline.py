"""Band 1 end to end: parse, decide, and the record that comes out.

WHY THESE ARE FUNCTIONS AND NOT A SCRIPT. This module used to run at COLLECTION
time and patch ``run`` and ``confirm`` at module scope. Those patches outlived
it: they silently disabled the transport fakes in test_f1_fabrication_guard, so
that file passed alone and failed in the suite. Nothing here touches a module
global except through ``monkeypatch``, which undoes itself.
"""
from __future__ import annotations

import pytest

from cde.refs import confirm as confmod
from cde.refs import lookup
from cde.refs import run as runmod
from cde.refs import schema as S
from cde.refs.decide import decide
from cde.refs.parser import parse_pmc_xml
from cde.refs.schema import ClaimedRef, Reference, RetrievedRecord

XML = (
    '<article><back><ref-list><ref id="r1"><element-citation>'
    '<person-group><name><surname>Smith</surname></name></person-group>'
    '<article-title>A real study</article-title><source>J</source>'
    '<year>2021</year><pub-id pub-id-type="pmid">123</pub-id>'
    "</element-citation></ref></ref-list></back></article>"
)


def test_the_parser_reads_a_title_and_a_claimed_pmid_off_a_reference(tmp_path):
    path = tmp_path / "d.xml"
    path.write_text(XML, encoding="utf-8")
    refs = parse_pmc_xml(str(path), source_pmcid="PMC1")
    assert refs[0].claimed.title == "A real study"
    assert refs[0].claimed.claimed_pmid == "123"


def _ref(cid, **log):
    r = Reference(cid, "", ClaimedRef(title="x", claimed_pmid=log.pop("pmid", "1")))
    for k, v in log.items():
        setattr(r.log, k, v)
    return r


def test_a_reference_with_no_pmid_is_unverifiable_not_a_finding():
    r = Reference("u", "", ClaimedRef(title="x"))
    r.log.pmid_present = False
    assert decide(r, False, None, None).label == S.UNVERIFIABLE


def test_a_pmid_that_resolves_to_the_claimed_title_clears():
    assert decide(_ref("c", pmid_present=True, title_similarity=99),
                  False, None, None).label == S.CLEARED


def test_a_dead_pmid_whose_title_nothing_matched_goes_to_a_human_not_to_f1():
    """The evidence supports neither finding, so it supports neither label.

    F1 says no such work exists; F2 says the reference names a different work.
    A mismatched PMID whose title none of the three databases matched says
    neither, and the model calling it a fabrication does not change that
    (decide.py, 2026-08-25).
    """
    r = _ref("fb", pmid_present=True, pmid_resolved=True)
    assert decide(r, True, S.V_FABRICATION,
                  {"pubmed": 10, "crossref": 0, "openalex": 0}
                  ).label == S.HUMAN_REVIEW


def test_an_unanswered_search_holds_it_too_because_absence_needs_every_reply():
    r = _ref("fbh", pmid_present=True, pmid_resolved=True)
    assert decide(r, True, S.V_FABRICATION,
                  {"pubmed": 10, "crossref": 0, "openalex": None}
                  ).label == S.HUMAN_REVIEW


def test_a_strong_title_match_to_a_different_work_is_f2():
    r = _ref("f2", pmid_present=True, pmid_resolved=True)
    assert decide(r, True, S.V_REFERENCE_ERROR,
                  {"pubmed": 97, "crossref": 0, "openalex": 0}).label == S.F2


def test_a_mocked_reference_travels_the_whole_band_and_emits_a_prediction(
        monkeypatch):
    monkeypatch.setattr(runmod, "fetch_pubmed",
                        lambda pmid, *a, **k: RetrievedRecord(
                            resolved=True, title="Unrelated real paper", pmid=pmid,
                            transport_status=S.FETCH_ANSWERED_RECORD))
    monkeypatch.setattr(confmod, "search_pubmed", lambda *a, **k: 5.0)
    monkeypatch.setattr(confmod, "search_crossref", lambda *a, **k: 0.0)
    monkeypatch.setattr(confmod, "search_openalex", lambda *a, **k: 0.0)

    r = Reference("e2e", "", ClaimedRef(title="Fabricated quantum neuro synthesis",
                                        claimed_pmid="123"))
    runmod.process_reference(
        r, lambda p: '{"verdict":"fabrication","reason":"invented"}',
        ncbi_key="", session=None)
    assert r.label == S.HUMAN_REVIEW
    assert r.to_prediction().evidence["decided_by"] == "confirm_not_found_human_review"


def test_the_package_surface_imports_without_shadowing():
    import cde.refs
    import cde.refs.confirm
    import cde.refs.decide
    import cde.refs.run
    assert callable(cde.refs.run_pipeline)
    assert callable(cde.refs.decide.decide)
