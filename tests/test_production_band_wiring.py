"""Straightforward cases for the corpus-run F5/F7 wiring.

The one that matters is ``validate_production_f5_configuration`` passing: the
bench builder deliberately does not call it, so before this module no F5 wiring
in the repo was gated by it. The rest guard the refusals.

Offline. Construction makes no network call and no model call, so every test here
is free; the Anthropic client is constructed but never invoked.
"""
from __future__ import annotations

import pytest

from cde.runtime import production_band_wiring as pbw


@pytest.fixture(autouse=True)
def _dummy_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-used")


def test_production_f5_passes_the_production_validator():
    out = pbw.build_production_f5(
        as_of_date="2018-05-25", model="claude-opus-5",
        email="a@b.com", cap=50, max_deep_comparisons=12)
    assert out["provenance"]["production_validator_called"] is True
    assert out["provenance"]["f5_candidate_source"] == "live_pubmed_candidate_finder"
    assert set(out["f5_seams"]) == {
        "check_formal_notice", "classify_evidence_tier",
        "fetch_comparability_source", "retrieve_superseding_candidates",
        "find_supersession_attestation", "judge_contradiction",
        "verify_contradiction", "screen_candidates"}
    assert out["f5_policy"].mode == "deployment"
    assert out["f5_policy"].deploy_path_a is False
    assert getattr(out["f5_evidence_builder"],
                   "production_f5_evidence_builder") is True
    # Generator, verifier and screen, each with its own ledger.
    assert len(out["token_ledgers"]) == 3


def test_no_as_of_date_is_refused():
    """'Superseded by when' has no default."""
    with pytest.raises(pbw.WiringError, match="AT A POINT IN TIME"):
        pbw.build_production_f5(as_of_date="", model="claude-opus-5",
                                email="a@b.com")


def test_a_nonpositive_cap_is_refused():
    with pytest.raises(pbw.WiringError):
        pbw.build_production_f5(as_of_date="2018-05-25", model="claude-opus-5",
                                email="a@b.com", cap=0)


def test_turning_the_screen_off_drops_its_transport_and_says_so():
    out = pbw.build_production_f5(
        as_of_date="2018-05-25", model="claude-opus-5", email="a@b.com",
        screen=False)
    # The flag is an ASSERTION about what was wired, not a request -- the
    # detector's constructor refuses a mismatch.
    assert out["f5_policy"].candidate_screen_enabled is False
    assert out["provenance"]["f5_candidate_screen"] == {"enabled": False}
    assert len(out["token_ledgers"]) == 2


def test_a_blank_email_is_refused():
    with pytest.raises(pbw.WiringError):
        pbw.make_live_pubmed_readers(email="   ")


class _Finder:
    """Counts fetches so memoization is observable."""

    def __init__(self, record=None, raises=None):
        self.record, self.raises, self.calls = record, raises, 0

    def fetch_metadata(self, pmid):
        self.calls += 1
        if self.raises:
            raise self.raises
        return self.record


def _readers(monkeypatch, finder):
    from cde.diagnose import candidate_finder as fcf
    monkeypatch.setattr(fcf, "PubMedCandidateFinder",
                        lambda **kw: finder)
    return pbw.make_live_pubmed_readers(email="a@b.com")


def test_one_record_is_fetched_once_per_run(monkeypatch):
    finder = _Finder(record={"id": "1", "abstract": "text", "mesh_terms": []})
    fetch_meta, fetch_abstract, _ = _readers(monkeypatch, finder)
    assert fetch_meta("1") is fetch_meta("1")
    # fetch_abstract reads the SAME record, so it costs no extra round trip.
    assert fetch_abstract("1") == "text"
    assert finder.calls == 1


def test_an_answered_empty_record_is_not_refetched(monkeypatch):
    finder = _Finder(record=None)
    fetch_meta, fetch_abstract, _ = _readers(monkeypatch, finder)
    assert fetch_meta("1") is None
    assert fetch_meta("1") is None
    assert fetch_abstract("1") is None
    assert finder.calls == 1


def test_an_outage_is_never_cached_as_an_absence(monkeypatch):
    """DEC-032. Caching a timeout would make one outage a permanent absence."""
    from cde.diagnose.candidate_finder import CandidateFinderError

    finder = _Finder(raises=CandidateFinderError("EFetch timed out"))
    fetch_meta, _fa, _f = _readers(monkeypatch, finder)
    for _ in range(2):
        with pytest.raises(CandidateFinderError):
            fetch_meta("1")
    assert finder.calls == 2          # retried, not remembered as None


def test_a_blank_pmid_never_reaches_the_network(monkeypatch):
    finder = _Finder(record={"id": "1"})
    fetch_meta, _fa, _f = _readers(monkeypatch, finder)
    assert fetch_meta("") is None
    assert fetch_meta(None) is None
    assert finder.calls == 0


def test_band_seams_wires_f5_only_by_default():
    b = pbw.build_band_seams(model="claude-opus-5", email="a@b.com",
                             as_of_date="2018-05-25")
    assert sorted(b["run_kwargs"]) == [
        "f5_evidence_builder", "f5_policy", "f5_seams"]
    assert b["provenance"]["f5_wired"] is True
    assert b["provenance"]["f7_wired"] is False


def test_f5_can_be_left_out_entirely():
    b = pbw.build_band_seams(model="claude-opus-5", email="a@b.com", f5=False)
    assert b["run_kwargs"] == {}
    assert b["token_ledgers"] == []


def test_f7_without_a_receipt_refuses_rather_than_skipping():
    """A silently skipped F7 and a wired F7 that found nothing must not be the
    same output -- that is what the reachability attestation exists to catch."""
    with pytest.raises(pbw.WiringError, match="AdapterReceipt"):
        pbw.build_band_seams(model="claude-opus-5", email="a@b.com", f5=False,
                             f7_authorities_root="/no/such/dir", receipt=None)


def test_merged_usage_keeps_the_three_f5_stages_apart():
    b = pbw.build_band_seams(model="claude-opus-5", email="a@b.com",
                             as_of_date="2018-05-25")
    usage = pbw.merge_run_usage(b["token_ledgers"])
    assert sorted(usage["by_stage"]) == [
        "f5_candidate_screen", "f5_generator", "f5_verifier"]
    # Snapshotted before any call: zero, and honestly zero.
    assert usage["total"]["calls"] == 0
