"""F2 RECALL GUARD -- the assertions that must stay green on every change to
parser.py / biblio_match.py / lookup.py.

The F2 screen is recall-first: a false positive is recovered by the downstream
audit, a false negative is permanent. The fixes in this branch (parser author
person-group scoping; boost-decoupled flagging; the UNSCOREABLE bucket) all carry
a recall risk that this file pins down:

  * The parser author fix flips a spurious author_match=False to True, which ADDS
    a +0.05 boost; on a genuine wrong-reference sitting just under accept that
    boost could cross the threshold and silently UN-flag it. The boost-decoupled
    flag rule (lookup._has_confident_disagreement) must keep it flagged.
  * The UNSCOREABLE gate must never bucket a genuine wrong-reference (every
    genuine-F2 written title is a real article title).

CONSTRUCTED-INPUT NOTE (spec C6): the cases below are built to exercise the code
MECHANISM (a high-title-similarity record with a confident field disagreement,
the 16639420 shape). They are NOT the real seed=7 records (unreachable here) and
prove no precision/recall NUMBER -- only that the screen still flags the shapes a
genuine F2 takes.

Run:  PYTHONPATH=<repo> python -m pytest cre/f1/test_f2_recall_guard.py -q
"""
from __future__ import annotations

from lxml import etree

from cre.f1.parser import _authors_from
from cre.f1.lookup import (compare_and_flag, _year_disagreement,
                           _override_quality, _flag_decision)
from cre.f1.biblio_match import match_score, field_agreement, FieldAgreement
from cre.f1.schema import Reference, ClaimedRef, RetrievedRecord
from cre.f1 import eval_report

ACCEPT = 0.85


def _pmid_ref(claimed: ClaimedRef, retrieved: RetrievedRecord) -> Reference:
    ref = Reference(claimed.claimed_pmid or "x", "", claimed)
    ref.retrieved = retrieved
    return ref


# ======================================================================
# 1. THE CRUX (D9): a genuine wrong-reference with a high title similarity and a
#    confident YEAR disagreement must STAY flagged even after the parser author
#    fix raises its composite over accept via the +0.05 author boost.
# ======================================================================
def test_high_ts_year_disagreement_stays_flagged_after_author_fix():
    # 16639420 shape: a same-author paper SERIES, parts VIII vs X. Titles are
    # very close (ts high); years differ (> 1); author now matches (fix applied).
    claimed = ClaimedRef(
        title="Evolution in closely adjacent plant populations VIII clinal patterns",
        authors=["Antonovics"], year=1971, journal="Heredity", claimed_pmid="16639420")
    retrieved = RetrievedRecord(
        resolved=True, pmid="16639420",
        title="Evolution in closely adjacent plant populations X long-term persistence",
        authors=["Antonovics"], year=1990, journal="Heredity")

    m = match_score(claimed, retrieved)
    assert m.fields.author_match is True          # author now matches (fix)
    assert m.fields.year_match is False           # years > 1 apart
    assert m.score >= ACCEPT                       # boosts lifted it OVER accept...

    ref = _pmid_ref(claimed, retrieved)
    flagged = compare_and_flag(ref, 85.0)
    assert flagged is True, (                      # ...yet it MUST stay flagged
        "RECALL BREAK: a genuine wrong-reference with a confident year "
        "disagreement was un-flagged because boosts lifted its score over accept")
    assert ref.log.year_match is False


def test_confident_author_disagreement_flags_regardless_of_score():
    # A wrong paper by a different author, similar title -> author_match False.
    claimed = ClaimedRef(title="Deep learning for protein folding",
                         authors=["Smith"], year=2020, journal="Cell",
                         claimed_pmid="x")
    retrieved = RetrievedRecord(resolved=True, pmid="x",
                                title="Deep learning for protein folding",
                                authors=["Jones"], year=2020, journal="Cell")
    ref = _pmid_ref(claimed, retrieved)
    assert compare_and_flag(ref, 85.0) is True
    assert ref.log.author_match is False


def test_low_title_similarity_f2_still_flags():
    # The easy genuine-F2 shape: titles are simply different (27665045 / 25750229).
    claimed = ClaimedRef(title="The chemical basis of morphogenesis",
                         authors=["Turing"], year=1952, journal="Phil Trans R Soc",
                         claimed_pmid="25750229")
    retrieved = RetrievedRecord(
        resolved=True, pmid="25750229",
        title="A commentary on Turing's 1952 paper on morphogenesis",
        authors=["Smith"], year=2015, journal="Interface Focus")
    ref = _pmid_ref(claimed, retrieved)
    assert compare_and_flag(ref, 85.0) is True


def test_sparse_field_f2_still_flags():
    # 31665581 shape: author UNPARSED (None), year+journal coincidentally agree
    # (CORROBORATED), but the title differs -> still flagged on low title score.
    claimed = ClaimedRef(title="Disseminated varicella infection",
                         authors=[], year=2019, journal="N Engl J Med",
                         claimed_pmid="31665581")
    retrieved = RetrievedRecord(resolved=True, pmid="31665581",
                                title="Purple urine after catheterization",
                                authors=["Lee", "Park"], year=2019,
                                journal="N Engl J Med")
    m = match_score(claimed, retrieved)
    assert m.fields.author_match is None           # sparse: can't judge
    assert m.score >= m.title_sim                   # corroborated (boosts, no penalty)
    ref = _pmid_ref(claimed, retrieved)
    assert compare_and_flag(ref, 85.0) is True      # still flagged (low title)


# ======================================================================
# 2. NO REGRESSION on Dr. Roberts' concern: a genuinely same paper must NOT be
#    force-flagged by the new disagreement rule (no confident disagreement ->
#    boosts/override legitimately clear it).
# ======================================================================
def test_clean_truncation_same_paper_not_flagged():
    claimed = ClaimedRef(
        title="Effects of aspirin on cardiovascular outcomes",
        authors=["Okafor"], year=2019, journal="Lancet", claimed_pmid="x")
    retrieved = RetrievedRecord(
        resolved=True, pmid="x",
        title=("Effects of aspirin on cardiovascular outcomes in elderly "
               "patients: a randomized controlled trial"),
        authors=["Okafor", "Lee"], year=2019, journal="Lancet")
    ref = _pmid_ref(claimed, retrieved)
    assert compare_and_flag(ref, 85.0) is False     # fields agree -> cleared
    assert _year_disagreement(field_agreement(claimed, retrieved)) is False


def test_cross_language_override_same_paper_not_flagged():
    # Override-rescued cross-language same paper (author+journal agree, no False).
    claimed = ClaimedRef(
        title="Ergebnisse der chirurgischen Behandlung des Magenkarzinoms",
        authors=["Müller"], year=1998, journal="Der Chirurg", claimed_pmid="x")
    retrieved = RetrievedRecord(
        resolved=True, pmid="x",
        title="Results of surgical treatment of gastric carcinoma",
        authors=["H Muller"], year=1998, journal="Der Chirurg")
    m = match_score(claimed, retrieved)
    assert m.score >= ACCEPT                         # override floored it
    ref = _pmid_ref(claimed, retrieved)
    assert compare_and_flag(ref, 85.0) is False      # not force-flagged


def test_year_disagreement_semantics():
    # Fix 6 owns YEAR disagreement only; author disagreement is the trip-wire's.
    assert _year_disagreement(FieldAgreement(year_match=False)) is True
    assert _year_disagreement(FieldAgreement(author_match=False)) is False  # trip-wire's job
    assert _year_disagreement(FieldAgreement()) is False                    # all None
    assert _year_disagreement(FieldAgreement(year_match=True)) is False


# ======================================================================
# 3. The parser author fix (D1) itself: editors/translators no longer leak in.
# ======================================================================
def test_parser_excludes_editor_first_chapter():
    xml = b'''<element-citation publication-type="book">
      <person-group person-group-type="editor">
        <name><surname>Editor</surname><given-names>E</given-names></name></person-group>
      <person-group person-group-type="author">
        <name><surname>Realauthor</surname><given-names>R</given-names></name></person-group>
      <chapter-title>Acute Liver Failure</chapter-title>
      <source>Some Book</source><year>2019</year></element-citation>'''
    authors = _authors_from(etree.fromstring(xml))
    assert authors[0] == "Realauthor"               # editor no longer leaks first
    assert "Editor" not in authors


def test_parser_excludes_translator():
    xml = b'''<element-citation>
      <person-group person-group-type="author"><name><surname>Author</surname></name></person-group>
      <person-group person-group-type="translator"><name><surname>Translator</surname></name></person-group>
      <article-title>T</article-title></element-citation>'''
    assert _authors_from(etree.fromstring(xml)) == ["Author"]


def test_parser_captures_collab():
    xml = b'''<element-citation><person-group person-group-type="author">
      <collab>WHO Collaborating Group</collab></person-group>
      <article-title>T</article-title></element-citation>'''
    assert _authors_from(etree.fromstring(xml)) == ["WHO Collaborating Group"]


def test_parser_falls_back_when_only_editors():
    # Edited book cited with editors only: surface them (recall-first) not nothing.
    xml = b'''<element-citation publication-type="book">
      <person-group person-group-type="editor"><name><surname>OnlyEditor</surname></name></person-group>
      <source>Handbook</source></element-citation>'''
    assert _authors_from(etree.fromstring(xml)) == ["OnlyEditor"]


def test_parser_no_person_group_still_extracts_names():
    # Legacy citations without a person-group: keep the old all-<name> behavior.
    xml = b'''<element-citation>
      <name><surname>Solo</surname></name>
      <article-title>T</article-title></element-citation>'''
    assert _authors_from(etree.fromstring(xml)) == ["Solo"]


# ======================================================================
# 4. Review Finding B (recall hole): the lone +0.05 author boost must NOT carry a
#    sub-accept title over accept on a SPARSE ref (year+journal unparsed).
# ======================================================================
def test_sparse_author_only_boost_does_not_unflag():
    # The parser author fix flips author None->True (+0.05); year+journal are
    # unparsed (None) so the override can't fire and the year rule can't trip.
    claimed = ClaimedRef(title="Genome-wide association study of type 2 diabetes",
                         authors=["Realauthor"], year=None, journal="", claimed_pmid="x")
    retrieved = RetrievedRecord(
        resolved=True, pmid="x",
        title="Genome-wide CRISPR screen of pancreatic beta cells",
        authors=["Realauthor"], year=None, journal="")
    m = match_score(claimed, retrieved)
    assert m.fields.author_match is True
    assert m.fields.year_match is None and m.fields.journal_match is None
    assert m.title_sim < ACCEPT                  # title alone is sub-accept
    assert m.score >= ACCEPT                      # boost lifted the composite over accept
    ref = _pmid_ref(claimed, retrieved)
    assert compare_and_flag(ref, 85.0) is True, (
        "RECALL BREAK: a sub-accept-title wrong-reference was cleared by the lone "
        "author boost without high-entropy corroboration")


def test_override_quality_gate_semantics():
    # author+journal agree, no disagreement -> override quality (clears sub-accept)
    assert _override_quality(FieldAgreement(author_match=True, journal_match=True)) is True
    # author-only is NOT enough (Finding B)
    assert _override_quality(FieldAgreement(author_match=True)) is False
    # author+year (no journal) is NOT enough (low entropy)
    assert _override_quality(FieldAgreement(author_match=True, year_match=True)) is False
    # any disagreement blocks it
    assert _override_quality(
        FieldAgreement(author_match=True, journal_match=True, year_match=False)) is False


def test_flag_decision_consistent_across_paths():
    # Finding D: the same predicate governs both paths. Identical title,
    # author+journal agree, year disagrees -> must flag (not clear).
    claimed = ClaimedRef(title="Identical title here", authors=["Smith"],
                         year=2010, journal="J Foo")
    cand = RetrievedRecord(resolved=True, title="Identical title here",
                           authors=["Smith"], year=2015, journal="J Foo")
    m = match_score(claimed, cand)
    assert m.fields.year_match is False
    assert _flag_decision(m, ACCEPT) is True


# ======================================================================
# 5. Fix 5: preprint/epub (DEP) year gap is demoted to None ONLY when tightly
#    gated; a large gap or a sparse ref stays a confident disagreement.
# ======================================================================
def test_preprint_dep_year_gap_demoted_to_none():
    claimed = ClaimedRef(title="Impact of urban structure on COVID-19 spread",
                         authors=["Smith"], year=2020, journal="eLife")
    cand = RetrievedRecord(  # published version, year from DEP (epub), 2 yrs later
        resolved=True, title="Impact of urban structure on COVID-19 spread",
        authors=["Smith"], year=2022, journal="eLife", year_from_dep=True)
    fa = field_agreement(claimed, cand)
    assert fa.author_match is True
    assert fa.year_match is None                  # preprint gap -> can't-judge


def test_large_year_gap_stays_false_even_if_corroborated():
    # 16639420 shape: 19-year gap, NOT dep-derived -> confident disagreement.
    claimed = ClaimedRef(title="Evolution VIII", authors=["Antonovics"],
                         year=1971, journal="Heredity")
    cand = RetrievedRecord(resolved=True, title="Evolution X",
                           authors=["Antonovics"], year=1990, journal="Heredity",
                           year_from_dep=False)
    assert field_agreement(claimed, cand).year_match is False


def test_dep_gap_not_demoted_without_author_corroboration():
    # Sparse ref (author can't be judged): a DEP 2-year gap stays False, so the
    # sparse-field F2 population is not relaxed.
    claimed = ClaimedRef(title="T", authors=[], year=2020, journal="X")
    cand = RetrievedRecord(resolved=True, title="T", authors=["Z"], year=2022,
                           journal="X", year_from_dep=True)
    assert field_agreement(claimed, cand).year_match is False


def test_dep_gap_not_demoted_without_journal_corroboration():
    # author matches but journal does NOT: the same-author/different-journal
    # wrong-paper case stays a confident disagreement (surfaced, not relaxed).
    claimed = ClaimedRef(title="T", authors=["Smith"], year=2020, journal="Journal A")
    cand = RetrievedRecord(resolved=True, title="T", authors=["Smith"], year=2022,
                           journal="Journal B", year_from_dep=True)
    fa = field_agreement(claimed, cand)
    assert fa.author_match is True and fa.journal_match is False
    assert fa.year_match is False           # not demoted -> stays a disagreement


def test_dep_gap_not_demoted_when_three_years_apart():
    claimed = ClaimedRef(title="T", authors=["Smith"], year=2019, journal="X")
    cand = RetrievedRecord(resolved=True, title="T", authors=["Smith"], year=2022,
                           journal="X", year_from_dep=True)
    assert field_agreement(claimed, cand).year_match is False   # gap 3 > 2


# ======================================================================
# 6. ANOMALY trio: identical title with a field disagreement (parser artifact OR
#    a genuinely different same-titled paper) is flagged for review either way.
# ======================================================================
def test_anomaly_identical_title_disagreement_flags():
    claimed = ClaimedRef(title="Hypothalamic dysfunction", authors=["Aname"],
                         year=2018, journal="J Neuro", claimed_pmid="x")
    cand = RetrievedRecord(resolved=True, pmid="x", title="Hypothalamic Dysfunction.",
                           authors=["Bname"], year=2010, journal="J Neuro")
    ref = _pmid_ref(claimed, cand)
    assert compare_and_flag(ref, 85.0) is True


# ======================================================================
# 7. Override residual is instrumented (logged + counted) for measurement.
# ======================================================================
def test_override_fired_logged_and_counted():
    claimed = ClaimedRef(title="Zur Pathogenese der Leberzirrhose",
                         authors=["Schmidt"], year=2001, journal="Z Gastroenterol",
                         claimed_pmid="x")
    cand = RetrievedRecord(resolved=True, pmid="x",
                           title="On the pathogenesis of liver cirrhosis",
                           authors=["K Schmidt"], year=2001, journal="Z Gastroenterol")
    m = match_score(claimed, cand)
    assert m.title_sim < ACCEPT and m.override_fired is True   # floored to accept
    ref = _pmid_ref(claimed, cand)
    assert compare_and_flag(ref, 85.0) is False                # cleared via override
    assert ref.log.override_fired is True
    rep = eval_report.summarize([ref.to_log_record()])
    assert rep["counts"]["override_cleared"] == 1
