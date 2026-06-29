"""Tests for the UNSCOREABLE gate (Fix 3 / D3-D4) and its routing + reporting.

Asserts: (a) each HARD non-title signal is detected; (b) NO genuine-F2 / HARD /
same-paper title is ever bucketed (the recall guard, C1); (c) a journal-as-title
with corroborating fields is routed to UNSCOREABLE rather than silently CLEARED
by the strong-corroboration override; (d) UNSCOREABLE maps to None (dropped from
the dataset, never ACCURATE); (e) the eval layer counts it as a separate bucket.

Run:  PYTHONPATH=<repo> python -m pytest cre/f1/test_unscoreable.py -q
"""
from __future__ import annotations

from cre.f1.unscoreable import classify_unscoreable
from cre.f1.lookup import compare_and_flag
from cre.f1.decide import decide
from cre.f1.schema import (Reference, ClaimedRef, RetrievedRecord, UNSCOREABLE,
                           ACCURATE, CLEARED, pipeline_state_to_taxonomy)
from cre.f1 import eval_report


def _resolved(title="A perfectly ordinary article title", authors=("Z",),
              year=2005, journal="J Foo", is_container=False):
    return RetrievedRecord(resolved=True, title=title, authors=list(authors),
                           year=year, journal=journal, is_container=is_container)


# ======================================================================
# 1. Each HARD signal fires.
# ======================================================================
def test_journal_as_title_exact_equality():
    c = ClaimedRef(title="Heredity", journal="Heredity", claimed_pmid="x")
    bucket, _ = classify_unscoreable(c, _resolved())
    assert bucket == "journal_as_title"


def test_journal_as_title_bilingual_masthead():
    # 34467707: 'Vernacular = Romanization' journal masthead in the title slot.
    c = ClaimedRef(title="Zhongguo Zhong yao za zhi = Zhongguo zhongyao zazhi",
                   journal="", claimed_pmid="x")
    bucket, _ = classify_unscoreable(c, _resolved(title="[R&D strategies]."))
    assert bucket == "journal_as_title"


def test_regulatory_code():
    # 11686173: a CFR title string.
    c = ClaimedRef(title="TITLE 45: PUBLIC WELFARE part 46 protection of human "
                         "subjects", claimed_pmid="x")
    bucket, _ = classify_unscoreable(c, _resolved(title="Protection of human subjects."))
    assert bucket == "regulatory_code"


def test_resolved_no_title_placeholder():
    # 30539090: claimed PMID resolves to "[Not Available]".
    c = ClaimedRef(title="Estimation of Relative Load From Bar Velocity",
                   claimed_pmid="x")
    bucket, _ = classify_unscoreable(c, _resolved(title="[Not Available]."))
    assert bucket == "resolved_no_title"


def test_resolved_book_container():
    # 32091673: chapter cite resolves to its parent book (BTI, no TI).
    c = ClaimedRef(title="Acute Graft-Versus-Host Disease", claimed_pmid="x")
    bucket, _ = classify_unscoreable(
        c, _resolved(title="The EBMT Handbook", is_container=True))
    assert bucket == "resolved_book_container"


# ======================================================================
# 2. RECALL GUARD: no genuine-F2 / HARD / same-paper title is bucketed.
# ======================================================================
GENUINE_F2_AND_HARD_TITLES = [
    # (written title, journal) -- all real article titles
    ("Disseminated varicella infection", "N Engl J Med"),         # 31665581
    ("Interactions between plant RRM-containing proteins", "Plant J"),  # 27665045
    ("The chemical basis of morphogenesis", "Phil Trans R Soc"),  # 25750229
    ("Evolution in closely adjacent plant populations VIII", "Genetics"),  # 16639420 (journal substring!)
    ("Validation of the physical activity questionnaire", "Pediatr Exerc Sci"),  # 9346166
    ("The multidimensional scale of perceived social support", "J Pers Assess"),  # 2280326
    ("Hypothalamic dysfunction", "Handb Clin Neurol"),            # 32809578 anomaly
    ("Metals, toxicity and oxidative stress", "Curr Med Chem"),   # 15892631 anomaly
]


def test_no_genuine_f2_is_unscoreable():
    for title, journal in GENUINE_F2_AND_HARD_TITLES:
        c = ClaimedRef(title=title, journal=journal, claimed_pmid="x")
        # resolved to a DIFFERENT real paper (worst case for the gate)
        bucket, _ = classify_unscoreable(
            c, _resolved(title="Some entirely different real paper", journal=journal))
        assert bucket is None, (
            f"RECALL BREAK: genuine-F2/HARD title {title!r} bucketed as {bucket}")


def test_journal_substring_of_title_is_not_unscoreable():
    # The exact trap the spec warns about: journal 'Genetics' is a substring of
    # the title. Exact-equality (not containment) must NOT fire here.
    c = ClaimedRef(title="Evolution in closely adjacent plant populations VIII",
                   journal="Genetics", claimed_pmid="16639420")
    assert classify_unscoreable(c, _resolved())[0] is None


# ======================================================================
# 3. Routing: UNSCOREABLE never becomes CLEARED/ACCURATE, and the override
#    cannot silently clear a journal-as-title.
# ======================================================================
def test_unscoreable_routes_to_unscoreable_label_not_cleared():
    ref = Reference("34467707", "", ClaimedRef(
        title="Heredity", journal="Heredity", claimed_pmid="34467707"))
    ref.retrieved = _resolved(title="A real different paper")
    flagged = compare_and_flag(ref, 85.0)
    assert flagged is False                          # not in the flagged pool
    out = decide(ref, flagged, None, None)
    assert out.label == UNSCOREABLE                  # NOT cleared / accurate
    assert out.label != CLEARED
    assert ref.log.unscoreable_reason == "journal_as_title"
    assert ref.log.decided_by == "unscoreable"


def test_override_cannot_clear_unscoreable_with_corroborating_fields():
    # journal-as-title whose author+journal corroborate: WITHOUT the gate the
    # strong-corroboration override would floor the score to accept and clear it.
    # The gate runs BEFORE scoring, so it is bucketed UNSCOREABLE instead.
    ref = Reference("x", "", ClaimedRef(
        title="J Foo", journal="J Foo", authors=["Smith"], year=2010,
        claimed_pmid="x"))
    ref.retrieved = _resolved(title="J Foo", authors=["Smith"], year=2010,
                              journal="J Foo")
    flagged = compare_and_flag(ref, 85.0)
    out = decide(ref, flagged, None, None)
    assert out.label == UNSCOREABLE
    # match_score was never used as the verdict (gated before scoring)
    assert ref.log.match_score is None


def test_unscoreable_maps_to_none_taxonomy():
    # Dropped from the dataset like UNVERIFIABLE -- never an ACCURATE control.
    assert pipeline_state_to_taxonomy(UNSCOREABLE) is None
    assert pipeline_state_to_taxonomy(CLEARED) == ACCURATE


# ======================================================================
# 4. The eval layer counts UNSCOREABLE separately and excludes it.
# ======================================================================
def test_eval_report_buckets_unscoreable_separately():
    uns = Reference("u", "", ClaimedRef(title="Heredity", journal="Heredity",
                                        claimed_pmid="u"))
    uns.retrieved = _resolved(title="Different real paper")
    decide(uns, compare_and_flag(uns, 85.0), None, None)

    f2ish = Reference("w", "", ClaimedRef(
        title="Deep learning for folding", authors=["Smith"], year=2020,
        journal="Cell", claimed_pmid="w"))
    f2ish.retrieved = RetrievedRecord(resolved=True, pmid="w",
                                      title="Deep learning for folding",
                                      authors=["Jones"], year=2020, journal="Cell")
    compare_and_flag(f2ish, 85.0)

    rep = eval_report.summarize([uns.to_log_record(), f2ish.to_log_record()])
    assert rep["unscoreable_by_reason"] == {"journal_as_title": 1}
    assert rep["counts"]["unscoreable_total"] == 1
    assert rep["counts"]["scoreable"] == 1           # the UNSCOREABLE one excluded
    assert rep["counts"]["flagged_total"] == 1       # only the f2ish one
    assert rep["flagged_band_counts"].get("STRONG_WRONG") == 1   # author disagrees


# ======================================================================
# 5. Review findings A & C: recall-safe correctness of the gate's matchers.
# ======================================================================
def test_bilingual_masthead_requires_equality_not_containment():
    # Review Finding A: a real title where one '='-half is a SUBSTRING of the
    # other must NOT bucket (the old `a in b` clause smuggled containment back in).
    for title in ("Genetics = Genetics of cancer susceptibility",
                  "Tumor growth = uncontrolled tumor growth in the absence of apoptosis",
                  "Gene regulation = post-transcriptional gene regulation networks"):
        c = ClaimedRef(title=title, journal="Some Journal", claimed_pmid="x")
        assert classify_unscoreable(c, _resolved())[0] is None, title
    # ...but a true transliterated masthead (space-insensitively EQUAL halves) does.
    c = ClaimedRef(title="Zhongguo Zhong yao za zhi = Zhongguo zhongyao zazhi",
                   journal="", claimed_pmid="x")
    assert classify_unscoreable(c, _resolved())[0] == "journal_as_title"


def test_safe_equals_titles_not_bucketed():
    for title in ("E = mc2 and the energetics of cellular metabolism",
                  "CD4 = T helper cell counts in early HIV infection",
                  "Setting p = 0.05 considered harmful: a re-analysis"):
        c = ClaimedRef(title=title, journal="J", claimed_pmid="x")
        assert classify_unscoreable(c, _resolved())[0] is None, title


def test_regulatory_regex_does_not_overmatch_title_n():
    # Review Finding C: "Title <digit>" without a section separator is a real
    # article title, not a CFR code -> must NOT bucket.
    for title in ("Title 1 diabetes and its complications in adolescents",
                  "Title 17 modifications in chromatin remodeling"):
        c = ClaimedRef(title=title, claimed_pmid="x")
        assert classify_unscoreable(c, _resolved())[0] is None, title
    # the genuine CFR string (separator after the number) still buckets
    c = ClaimedRef(title="TITLE 45: PUBLIC WELFARE part 46 protection of human "
                         "subjects", claimed_pmid="x")
    assert classify_unscoreable(c, _resolved())[0] == "regulatory_code"


def test_usc_requires_periods_not_university():
    c = ClaimedRef(title="A multicenter trial conducted at USC and UCLA centers",
                   claimed_pmid="x")
    assert classify_unscoreable(c, _resolved())[0] is None


def test_pnas_masthead_with_parenthetical_buckets():
    # 33846255: a full journal masthead with a '(PNAS)' gloss -> journal_as_title.
    c = ClaimedRef(title="Proc. of the National Academy of Sciences of the "
                         "United States of America (PNAS)", journal="", claimed_pmid="x")
    assert classify_unscoreable(c, _resolved())[0] == "journal_as_title"
