"""Fixture-based tests for the bibliographic matcher (HANDOFF_BIBLIO_MATCH).

Covers Dr. Roberts' concern directly: a truncated-but-correct title whose
author/year/journal agree must score HIGH (not flagged), while a same-title-but-
different-paper (the embedding failure mode) must score LOW (flagged) on the
strength of confident author/year DISagreement.

Scale note: the matcher works on 0..1 (``title_sim`` / ``match_score``); the
default accept gate is 0.85 with a 0.05 ambiguity margin. The integration keeps
``log.title_similarity`` on the established 0..100 scale and records the 0..1
composite in ``log.match_score``.

No network is touched: recorded JSON in ./fixtures is replayed through the same
FakeSession the other suites use; the optional Stage-2 cross-encoder is mocked.

Run:  PYTHONPATH=<repo> python -m pytest cre/f1/test_biblio_match.py -q
"""
from __future__ import annotations
import json
import os

import pytest

from cre.f1 import biblio_match as bm
from cre.f1 import biblio_rerank, lookup, run, ratelimit, confirm
from cre.f1 import schema as S
from cre.f1.biblio_match import (match_score, best_match, field_agreement,
                                 title_sim, retrieve_candidates,
                                 is_scoreable_title, flag_verdict,
                                 VERDICT_MATCH, VERDICT_WRONG_PAPER,
                                 VERDICT_FORMATTING)
from cre.f1.lookup import compare_and_flag
from cre.f1.run import process_reference
from cre.f1.schema import Reference, ClaimedRef, RetrievedRecord

FIX = os.path.join(os.path.dirname(__file__), "fixtures")
ACCEPT = 0.85


def _make_claimed(title, authors, year, journal):
    return ClaimedRef(title=title, authors=authors, year=year, journal=journal,
                      volume="", pages="", claimed_pmid="")


def _make_record(title, authors, year, journal):
    return RetrievedRecord(resolved=True, title=title, authors=authors, year=year,
                           journal=journal, pmid="", doi="", volume="", pages="")


def _fx(name: str) -> dict:
    with open(os.path.join(FIX, name)) as f:
        return json.load(f)


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
        self.calls = []

    def get(self, url, params=None, timeout=None):
        r = self.handler(url, params, len(self.calls))
        self.calls.append((url, params))
        return r


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    monkeypatch.setattr(ratelimit.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(run.time, "sleep", lambda *a, **k: None)


def _boom(_prompt: str) -> str:                # LLM must not run on cleared paths
    raise AssertionError("LLM filter should not be called here")


def _formatting(_prompt: str) -> str:
    return '{"verdict": "formatting_discrepancy", "reason": "abbreviated title"}'


def _fabrication(_prompt: str) -> str:
    return '{"verdict": "fabrication", "reason": "no real paper found"}'


def _route_all_empty(url, params, n):
    if "esearch" in url:
        return FakeResponse(200, json_data=_fx("pubmed_esearch_empty.json"))
    if "esummary" in url:
        return FakeResponse(200, json_data={"result": {}})
    if "crossref" in url:
        return FakeResponse(200, json_data=_fx("crossref_empty.json"))
    if "openalex" in url:
        return FakeResponse(200, json_data=_fx("openalex_empty.json"))
    return FakeResponse(404)


# ==========================================================================
# 1. Truncated title, fields agree -> high match_score, NOT flagged.
#    (Dr. Roberts' case: authors truncate references; a bare lexical title
#    threshold would wrongly flag this. Field agreement rescues it.)
# ==========================================================================
def test_truncated_title_fields_agree_scores_high():
    claimed = ClaimedRef(
        title="Effects of aspirin on cardiovascular outcomes",
        authors=["Okafor"], year=2019, journal="Lancet")
    cand = RetrievedRecord(
        title=("Effects of aspirin on cardiovascular outcomes in elderly "
               "patients: a randomized controlled trial"),
        authors=["Okafor", "Lee"], year=2019, journal="Lancet")

    m = match_score(claimed, cand)
    assert m.title_sim >= 0.80                 # truncation-robust on its own
    assert m.score >= ACCEPT                    # field boosts seal it
    assert m.fields.author_match is True
    assert m.fields.year_match is True
    assert m.fields.journal_match is True
    assert (m.score < ACCEPT) is False          # i.e. NOT flagged


def test_truncated_title_not_flagged_on_pmid_path():
    # Same truncation, but routed through the real PMID compare path.
    ref = Reference("trunc", "", ClaimedRef(
        title="Effects of aspirin on cardiovascular outcomes",
        authors=["Okafor"], year=2019, journal="Lancet", claimed_pmid="42"))
    ref.retrieved = RetrievedRecord(
        resolved=True, pmid="42",
        title=("Effects of aspirin on cardiovascular outcomes in elderly "
               "patients: a randomized controlled trial"),
        authors=["Okafor", "Lee"], year=2019, journal="Lancet")

    flagged = compare_and_flag(ref, 85.0)
    assert flagged is False                     # never reaches the Opus filter
    assert ref.log.mismatch_flagged is False
    assert ref.log.match_score >= ACCEPT
    assert ref.log.title_similarity >= 80.0     # logged on 0..100


# ==========================================================================
# 2. Same title, different author + year -> low match_score, flagged.
#    (The embedding failure mode: a paper and its later update sit close in
#    title/topic space but are different records. Field disagreement catches it.)
# ==========================================================================
def test_same_title_different_author_year_is_flagged():
    claimed = ClaimedRef(title="Deep learning for protein structure prediction",
                         authors=["Smith"], year=2018)
    cand = RetrievedRecord(title="Deep learning for protein structure prediction",
                           authors=["Jones"], year=2022)

    m = match_score(claimed, cand)
    assert m.title_sim >= 0.99                  # titles are identical
    assert m.fields.author_match is False
    assert m.fields.year_match is False         # 2018 vs 2022 -> > 1 apart
    assert m.score < ACCEPT                      # penalties pull it under accept
    assert (m.score < ACCEPT) is True            # i.e. flagged


def test_year_within_one_still_agrees():
    # +/-1 tolerance: epub vs print year drift must NOT count as disagreement.
    fa = field_agreement(ClaimedRef(title="t", year=2020),
                         RetrievedRecord(title="t", year=2021))
    assert fa.year_match is True


def test_missing_field_is_none_not_false():
    # Precision-first: a field missing on either side is "can't judge", never a
    # disagreement that would penalize the score.
    fa = field_agreement(ClaimedRef(title="t"),            # no authors/year
                         RetrievedRecord(title="t", authors=["Jones"]))
    assert fa.author_match is None
    assert fa.year_match is None


# ==========================================================================
# 3. No-ID, confident Crossref match -> CLEARED, noid_lookup_attempted == True.
# ==========================================================================
def test_noid_confident_crossref_match_is_cleared():
    def handler(url, params, n):
        if "crossref" in url:
            return FakeResponse(200, json_data=_fx("crossref_noid_match.json"))
        if "openalex" in url:
            return FakeResponse(200, json_data=_fx("openalex_empty.json"))
        return FakeResponse(404)

    ref = Reference("b3", "", ClaimedRef(
        title="Bibliometric drift in retracted oncology literature",
        authors=["Okafor"], year=2021, journal="Journal of Scholarly Metrics"))

    out = process_reference(ref, _boom, session=FakeSession(handler))

    assert out.label == S.CLEARED
    assert ref.log.noid_lookup_attempted is True
    assert ref.log.mismatch_flagged is False
    assert ref.log.match_score >= ACCEPT
    assert ref.retrieved.resolved is True
    assert ref.retrieved.pmid == ""             # no PMID on this path
    assert out.log.decided_by == "noid_metadata_match"


def test_retrieve_candidates_dedups_and_parses_fields():
    # Crossref fixture: a strong match (with author/year/journal) + an unrelated
    # item. Verify field parsing and that best_match picks the strong one.
    def handler(url, params, n):
        if "crossref" in url:
            return FakeResponse(200, json_data=_fx("crossref_noid_match.json"))
        return FakeResponse(200, json_data=_fx("openalex_empty.json"))

    claimed = ClaimedRef(
        title="Bibliometric drift in retracted oncology literature",
        authors=["Okafor"], year=2021, journal="Journal of Scholarly Metrics")
    cands = retrieve_candidates(claimed, session=FakeSession(handler))
    assert len(cands) == 2
    top = cands[0]
    assert top.doi == "10.1000/biblio-drift"    # DOI parsed + lowercased
    assert top.authors == ["Okafor"]
    assert top.year == 2021 and isinstance(top.year, int)
    assert top.journal == "Journal of Scholarly Metrics"

    chosen = best_match(claimed, cands, accept=ACCEPT)
    assert chosen.confident is True and chosen.ambiguous is False


# ==========================================================================
# 4. No-ID, no confident match -> HUMAN_REVIEW, never F1.
# ==========================================================================
def test_noid_no_confident_match_is_human_review_never_f1(monkeypatch):
    # Pin the confirmation searches deterministically (a sibling protected test
    # reassigns these module globals at import time and never restores).
    for fn in ("search_pubmed", "search_crossref", "search_openalex"):
        monkeypatch.setattr(confirm, fn, lambda *a, **k: 0.0)

    ref = Reference("b4", "", ClaimedRef(
        title="Quantum entanglement therapy for refractory migraine"))

    out = process_reference(ref, _fabrication, session=FakeSession(_route_all_empty))

    assert ref.log.noid_lookup_attempted is True
    assert ref.log.noid_not_found is True
    assert out.label == S.HUMAN_REVIEW
    assert out.label != S.F1                     # precision-first
    assert out.label != S.F2                     # no PMID to be "wrong"
    assert out.log.decided_by == "noid_confirm_not_found_human_review"


# ==========================================================================
# 5. Ambiguous top-2 -> best_match.ambiguous == True; Stage 2 is invoked and
#    its verdict is used (mock the model).
# ==========================================================================
def test_ambiguous_top_two_sets_ambiguous_flag():
    claimed = ClaimedRef(title="Machine learning in clinical diagnosis",
                         authors=["Lee"], year=2020)
    cands = [
        RetrievedRecord(title="Machine learning in clinical diagnosis",
                        authors=["Lee"], year=2020, doi="10.1/a"),
        RetrievedRecord(title="Machine learning in clinical diagnostics",
                        authors=["Lee"], year=2020, doi="10.1/b"),
    ]
    chosen = best_match(claimed, cands, accept=ACCEPT, margin=0.05)
    assert chosen.ambiguous is True
    assert chosen.confident is False             # a near-tie is never confident


def test_stage2_rerank_invoked_on_ambiguous(monkeypatch):
    # When best_match is ambiguous, fuzzy_biblio_lookup must consult Stage 2.
    # Mock the cross-encoder to decisively pick the second candidate.
    claimed_title = "Machine learning in clinical diagnosis"
    a = RetrievedRecord(title="Machine learning in clinical diagnosis",
                        authors=["Lee"], year=2020, doi="10.1/a")
    b = RetrievedRecord(title="Machine learning in clinical diagnostics",
                        authors=["Lee"], year=2020, doi="10.1/b")

    monkeypatch.setattr(lookup, "retrieve_candidates",
                        lambda claimed, n=5, session=None: [a, b])

    called = {"n": 0}

    def fake_rerank(claimed, candidates, accept=0.85, margin=0.05):
        called["n"] += 1
        # Cross-encoder is confident the SECOND candidate is the match.
        top = bm.MatchResult(score=0.97, title_sim=1.0,
                             fields=bm.FieldAgreement(), record=b)
        runner = bm.MatchResult(score=0.10, title_sim=0.9,
                               fields=bm.FieldAgreement(), record=a)
        return bm.BestMatch(found=True, best=top, confident=True,
                            ambiguous=False, runners_up=[runner])

    monkeypatch.setattr(biblio_rerank, "rerank_stage2", fake_rerank)

    ref = Reference("amb", "", ClaimedRef(title=claimed_title,
                                          authors=["Lee"], year=2020))
    rec = lookup.fuzzy_biblio_lookup(ref, session=FakeSession(_route_all_empty))

    assert called["n"] == 1                      # Stage 2 was consulted
    assert rec.resolved is True
    assert rec.doi == "10.1/b"                   # cross-encoder's pick won


def test_stage2_absent_degrades_to_stage1(monkeypatch):
    # With no usable Stage-2 verdict, an ambiguous lookup stays unresolved
    # (degrades to Stage 1, which refuses to clear a near-tie).
    a = RetrievedRecord(title="Machine learning in clinical diagnosis",
                        authors=["Lee"], year=2020, doi="10.1/a")
    b = RetrievedRecord(title="Machine learning in clinical diagnostics",
                        authors=["Lee"], year=2020, doi="10.1/b")
    monkeypatch.setattr(lookup, "retrieve_candidates",
                        lambda claimed, n=5, session=None: [a, b])
    monkeypatch.setattr(biblio_rerank, "rerank_stage2",
                        lambda *a, **k: None)     # model unavailable

    ref = Reference("amb2", "", ClaimedRef(title="Machine learning in clinical "
                                                 "diagnosis", authors=["Lee"], year=2020))
    rec = lookup.fuzzy_biblio_lookup(ref, session=FakeSession(_route_all_empty))
    assert rec.resolved is False                 # ambiguous + no Stage 2 -> escalate


def test_rerank_stage2_degrades_when_model_unavailable():
    # The real entry point returns None when torch/transformers/weights are
    # absent (the default environment here) -- never raises.
    out = biblio_rerank.rerank_stage2(
        ClaimedRef(title="t"), [RetrievedRecord(title="t")])
    assert out is None


# ==========================================================================
# 6. Regression: the PMID path still works with match_score swapped in.
# ==========================================================================
def test_pmid_resolves_to_unrelated_paper_is_flagged():
    ref = Reference("reg1", "", ClaimedRef(
        title="A study of widget reliability", authors=["Smith"],
        year=2015, claimed_pmid="99"))
    ref.retrieved = RetrievedRecord(resolved=True, pmid="99",
                                    title="Totally unrelated paper on lizards",
                                    authors=["Kim"], year=2003)
    flagged = compare_and_flag(ref, 85.0)
    assert flagged is True
    assert ref.log.match_score < ACCEPT
    assert ref.log.mismatch_flagged is True


def test_pmid_exact_match_not_flagged():
    ref = Reference("reg2", "", ClaimedRef(
        title="A study of widget reliability", authors=["Smith"],
        year=2015, claimed_pmid="99"))
    ref.retrieved = RetrievedRecord(resolved=True, pmid="99",
                                    title="A study of widget reliability",
                                    authors=["Smith"], year=2015)
    flagged = compare_and_flag(ref, 85.0)
    assert flagged is False
    assert ref.log.match_score >= ACCEPT
    assert ref.log.author_match is True
    assert ref.log.year_match is True


def test_dead_pmid_still_flagged():
    # Regression: a dead PMID (unresolved) is a candidate regardless of scoring.
    ref = Reference("reg3", "", ClaimedRef(title="Anything", claimed_pmid="0"))
    ref.retrieved = RetrievedRecord(resolved=False, pmid="0")
    assert compare_and_flag(ref, 85.0) is True
    assert ref.log.notes == "claimed PMID did not resolve"


# ==========================================================================
# 7. Strong-corroboration override regression tests.
# ==========================================================================

def test_override_rescues_cross_language_same_paper():
    claimed = ClaimedRef(
        title="Ergebnisse der chirurgischen Behandlung des Magenkarzinoms",
        authors=["Müller"], year=1998, journal="Der Chirurg")
    cand = RetrievedRecord(
        resolved=False,
        title="Results of surgical treatment of gastric carcinoma",
        authors=["H Muller"], year=1998, journal="Der Chirurg",
        pmid="", doi="", volume="", pages="")
    r = match_score(claimed, cand, accept=ACCEPT)
    assert r.title_sim < 0.7, f"expected low cross-language title_sim, got {r.title_sim}"
    assert r.fields.author_match is True
    assert r.fields.journal_match is True
    assert r.score >= ACCEPT, f"override should fire, score={r.score}"


def test_override_does_not_fire_on_author_year_only_wrong_paper():
    claimed = ClaimedRef(
        title="A randomized trial of early surfactant in preterm infants",
        authors=["Okafor"], year=2015, journal="J Perinatology")
    cand = RetrievedRecord(
        resolved=False,
        title="Maternal vitamin D status and neonatal outcomes",
        authors=["A Okafor"], year=2015, journal="",
        pmid="", doi="", volume="", pages="")
    r = match_score(claimed, cand, accept=ACCEPT)
    assert r.fields.author_match is True
    assert r.fields.year_match is True
    assert r.fields.journal_match is None
    assert r.score < ACCEPT, (
        f"override must not fire on author+year only (journal missing), score={r.score}")


def test_override_does_not_fire_when_author_disagrees():
    claimed = ClaimedRef(
        title="Deep learning for medical image segmentation",
        authors=["Zhang"], year=2020, journal="Med Image Anal")
    cand = RetrievedRecord(
        resolved=False,
        title="Convolutional networks for biomedical image segmentation",
        authors=["Patel"], year=2020, journal="Med Image Anal",
        pmid="", doi="", volume="", pages="")
    r = match_score(claimed, cand, accept=ACCEPT)
    assert r.fields.author_match is False
    assert r.score < ACCEPT, f"author disagreement must block override, score={r.score}"


def test_override_threshold_tracks_accept():
    claimed = ClaimedRef(
        title="Zur Pathogenese der Leberzirrhose",
        authors=["Schmidt"], year=2001, journal="Z Gastroenterol")
    cand = RetrievedRecord(
        resolved=False,
        title="On the pathogenesis of liver cirrhosis",
        authors=["K Schmidt"], year=2001, journal="Z Gastroenterol",
        pmid="", doi="", volume="", pages="")
    assert match_score(claimed, cand, accept=0.90).score >= 0.90


# ── is_scoreable_title ────────────────────────────────────────────────────────

def test_is_scoreable_title_rejects_empty():
    assert is_scoreable_title("") is False

def test_is_scoreable_title_rejects_not_available():
    assert is_scoreable_title("[Not Available].", "J Foo") is False

def test_is_scoreable_title_rejects_journal_as_title():
    j = "Zhongguo Zhong yao za zhi = Zhongguo zhongyao zazhi"
    assert is_scoreable_title(j, j) is False

def test_is_scoreable_title_accepts_real_title():
    assert is_scoreable_title("Acute liver failure", "Hepatology") is True

# ── flag_verdict bands ────────────────────────────────────────────────────────

def test_flag_verdict_match_band():
    """Word-for-word same title + corroborating fields -> VERDICT_MATCH."""
    c = _make_claimed("Metamorphic proteins", ["Murzin"], 2008, "Biochemistry")
    r = _make_record("Metamorphic proteins", ["Murzin"], 2008, "Biochemistry")
    v, m = flag_verdict(c, r)
    assert v == VERDICT_MATCH

def test_flag_verdict_wrong_paper_band():
    """Different paper, author disagrees -> VERDICT_WRONG_PAPER."""
    c = _make_claimed("Disseminated varicella infection", ["Smith"], 2019, "")
    r = _make_record("Purple Urine after Catheterization", ["Placais"], 2019, "")
    v, m = flag_verdict(c, r)
    assert v == VERDICT_WRONG_PAPER

def test_flag_verdict_formatting_band():
    """Cross-language same paper, author+year agree, no journal -> VERDICT_FORMATTING."""
    c = _make_claimed("Haufigkeit und Verteilung von Schlafproblemen", ["Schlack"], 2013, "")
    r = _make_record("Frequency and distribution of sleep problems", ["Schlack"], 2013, "")
    v, m = flag_verdict(c, r)
    assert v == VERDICT_FORMATTING

# ── prefix-aware title_sim (de-prefixing can only RAISE the score) ─────────────

from cre.f1.biblio_match import _title_variants  # noqa: E402


def test_title_variants_deprefixes_section_prefix():
    # "Biochemistry. Metamorphic proteins." -> base + de-prefixed remainder.
    vs = _title_variants("Biochemistry. Metamorphic proteins.")
    assert "metamorphic proteins" in vs


def test_title_variants_keeps_short_remainder_whole():
    # A real short title must NOT be stripped to a fragment (remainder < 10 chars).
    assert _title_variants("Foo: bar") == ["foo bar"]


def test_title_sim_section_prefix_same_paper_scores_high():
    # 18583598 shape: written omits the "Biochemistry." section prefix PubMed adds.
    s = title_sim("Metamorphic proteins", "Biochemistry. Metamorphic proteins.")
    assert s >= 0.95


def test_title_sim_society_prefix_same_paper_scores_high():
    # 19204579 shape: ACSM position-stand prefix on the resolved side.
    s = title_sim(
        "Progression models in resistance training for healthy adults",
        "American College of Sports Medicine position stand. Progression models "
        "in resistance training for healthy adults")
    assert s >= 0.95


def test_title_sim_never_lowers_vs_bare_comparison():
    # Monotonicity: adding a prefix to one side cannot DROP similarity.
    bare = title_sim("Celiac disease", "Celiac disease")
    prefixed = title_sim("Celiac disease", "Clinical practice. Celiac disease.")
    assert prefixed >= 0.95 and prefixed >= bare - 1e-9


def test_title_sim_different_papers_not_rescued_by_deprefixing():
    # Two genuinely different works gain nothing decisive from de-prefixing: the
    # similarity stays well below the accept gate, so the pair still flags.
    s = title_sim("Disseminated varicella infection",
                  "Review. Purple urine after catheterization")
    assert s < 0.7


def test_prefixed_genuine_f2_still_flagged():
    # A genuine wrong-reference whose written title carries a leading prefix must
    # still flag: de-prefixing does not make two different titles match.
    c = _make_claimed("Background. Disseminated varicella infection", ["Smith"], 2019, "")
    r = _make_record("Purple urine after catheterization", ["Placais"], 2019, "")
    v, m = flag_verdict(c, r)
    assert m.score < ACCEPT and v == VERDICT_WRONG_PAPER
