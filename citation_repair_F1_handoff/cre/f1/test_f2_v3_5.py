"""F2 v3.5 tests -- preprint-source same-work quarantine.

A citation whose CLAIMED venue is a preprint server (arXiv, bioRxiv, ...) and
whose identifier resolves to the published version is the SAME work under a
revised title, NOT a wrong paper. Before v3.5 such a retitle scored just under
the 0.92 same-work title gate (dev row 35264587 = 0.9014) and landed in
VERDICT_WRONG_PAPER (HIGH) as a false positive.

v3.5 adds ``is_preprint_source(claimed)`` and a quarantine branch in
``flag_verdict``, placed AFTER the 0.92 same-work check and BEFORE the
wrong-paper branch. The branch is keyed on the preprint signal (orthogonal to
title_sim, so SAME_WORK_TITLE_SIM_MIN stays 0.92) and gated on
``author_match is True`` -- a genuinely wrong preprint cite by a DIFFERENT (or
unparsed) author still lands in WRONG_PAPER, so recall on real F2 is preserved.

Explicitly NOT addressed: the title-containment / subtitle-expansion class
(row 2280326). A containment fixture is included as a control proving that class
is deliberately untouched.

Run:  PYTHONPATH=<repo> python -m pytest cre/f1/test_f2_v3_5.py -q
"""
from __future__ import annotations

import json
import os

import pytest

from cre.f1.biblio_match import (flag_verdict, is_preprint_source,
                                 SAME_WORK_TITLE_SIM_MIN, VERDICT_MATCH,
                                 VERDICT_WRONG_PAPER, VERDICT_SAME_WORK_VARIANT)
from cre.f1.schema import ClaimedRef, RetrievedRecord


# --------------------------------------------------------------------------
# is_preprint_source -- venue string OR preprint-registrant DOI prefix
# --------------------------------------------------------------------------
@pytest.mark.parametrize("journal", [
    "arXiv:2007.15367",
    "bioRxiv",
    "medRxiv preprint",
    "Research Square",
    "SSRN Electronic Journal",
    "chemRxiv",
    "Preprints.org",
])
def test_is_preprint_source_true_on_preprint_venues(journal):
    assert is_preprint_source(ClaimedRef(journal=journal)) is True


@pytest.mark.parametrize("doi", [
    "10.1101/2020.01.01.123456",     # bioRxiv/medRxiv
    "10.48550/arXiv.2007.15367",     # arXiv (post-2022 DataCite)
    "10.21203/rs.3.rs-12345/v1",     # Research Square
    "10.26434/chemrxiv-2021-abcde",  # chemRxiv
])
def test_is_preprint_source_true_on_preprint_doi_prefixes(doi):
    assert is_preprint_source(ClaimedRef(claimed_doi=doi)) is True


@pytest.mark.parametrize("journal,doi", [
    ("N Engl J Med", ""),
    ("Nature", "10.1038/s41586-020-2649-2"),
    ("Bioinformatics", "10.1093/bioinformatics/btz123"),
    ("", ""),
])
def test_is_preprint_source_false_on_ordinary_journals(journal, doi):
    assert is_preprint_source(ClaimedRef(journal=journal, claimed_doi=doi)) is False


# --------------------------------------------------------------------------
# flag_verdict -- the quarantine branch and its recall guard
# --------------------------------------------------------------------------
# A preprint->published retitle: claimed venue is arXiv, first author agrees,
# year drifts (preprint 2020 -> print 2022), title is revised so title_sim is
# well BELOW the 0.92 same-work gate. Models dev row 35264587.
_PRE_TITLE = "Deep learning for genomic variant calling in cancer"
_PUB_TITLE = "A convolutional network approach to variant detection"


def test_preprint_venue_author_match_true_quarantines():
    c = ClaimedRef(title=_PRE_TITLE, authors=["Aguilar"], year=2020,
                   journal="arXiv:2007.15367")
    r = RetrievedRecord(resolved=True, title=_PUB_TITLE, authors=["Aguilar"],
                        year=2022, journal="Bioinformatics")
    v, m = flag_verdict(c, r)
    # The 0.92 branch is NOT what fired: title_sim is far below the gate, proving
    # the quarantine is keyed on the preprint signal, not title similarity.
    assert m.title_sim < SAME_WORK_TITLE_SIM_MIN
    assert m.fields.author_match is True
    assert v == VERDICT_SAME_WORK_VARIANT


def test_preprint_doi_prefix_author_match_true_quarantines():
    """The DOI-prefix path also drives the quarantine (venue string ordinary)."""
    c = ClaimedRef(title=_PRE_TITLE, authors=["Aguilar"], year=2020,
                   journal="", claimed_doi="10.1101/2020.07.15.204305")
    r = RetrievedRecord(resolved=True, title=_PUB_TITLE, authors=["Aguilar"],
                        year=2022, journal="Bioinformatics")
    v, m = flag_verdict(c, r)
    assert m.title_sim < SAME_WORK_TITLE_SIM_MIN
    assert v == VERDICT_SAME_WORK_VARIANT


def test_preprint_venue_clean_match_stays_match():
    """The ``and disagree`` guard: a CORRECTLY-cited preprint that resolves and
    matches cleanly (same title, same first author, same year) is a true
    negative, not a same-work variant. It must stay VERDICT_MATCH so the ~79
    clean preprint->published matches in seed 7 remain in the denominator --
    quarantining them would shrink the frame and dump true negatives into the
    audited same-work queue."""
    title = "Deep learning for genomic variant calling in cancer"
    c = ClaimedRef(title=title, authors=["Aguilar"], year=2022,
                   journal="arXiv:2007.15367")
    r = RetrievedRecord(resolved=True, title=title, authors=["Aguilar"],
                        year=2022, journal="Bioinformatics")
    v, m = flag_verdict(c, r)
    assert is_preprint_source(c) is True
    assert m.fields.author_match is True
    # No confident disagreement -> the preprint branch does NOT fire.
    assert m.fields.author_match is not False and m.fields.year_match is not False
    assert m.score >= 0.85
    assert v == VERDICT_MATCH


def test_preprint_venue_author_match_false_stays_wrong_paper():
    """Recall guard: a wrong preprint cite by a DIFFERENT author is real F2."""
    c = ClaimedRef(title=_PRE_TITLE, authors=["Aguilar"], year=2020,
                   journal="arXiv:2007.15367")
    r = RetrievedRecord(resolved=True, title=_PUB_TITLE, authors=["Nakamura"],
                        year=2022, journal="Bioinformatics")
    v, m = flag_verdict(c, r)
    assert m.fields.author_match is False
    assert v == VERDICT_WRONG_PAPER


def test_preprint_venue_author_match_none_stays_wrong_paper():
    """Tri-state guard: an UNPARSED author (None, not True) does not divert.
    The gate is ``author_match is True``, never a falsy check."""
    c = ClaimedRef(title=_PRE_TITLE, authors=[], year=2020,
                   journal="arXiv:2007.15367")
    r = RetrievedRecord(resolved=True, title=_PUB_TITLE, authors=["Nakamura"],
                        year=2022, journal="Bioinformatics")
    v, m = flag_verdict(c, r)
    assert m.fields.author_match is None
    assert v == VERDICT_WRONG_PAPER


def test_non_preprint_containment_stays_wrong_paper():
    """Containment control (modeled on confirmed F2 row 2280326, Zimet): claimed
    title is contained in the resolved title, author agrees, year drifts, venue
    is NOT a preprint. The containment class is deliberately untouched -- this
    must stay WRONG_PAPER, so a containment rule aggressive enough to quarantine
    it (and drop a ground-truth F2) is never introduced."""
    ct = "The Multidimensional Scale of Perceived Social Support"
    rt = ("Psychometric characteristics of the Multidimensional Scale of "
          "Perceived Social Support")
    c = ClaimedRef(title=ct, authors=["Zimet"], year=1988, journal="J Pers Assess")
    r = RetrievedRecord(resolved=True, title=rt, authors=["Zimet"], year=1990,
                        journal="J Pers Assess")
    v, m = flag_verdict(c, r)
    assert is_preprint_source(c) is False
    assert m.title_sim < SAME_WORK_TITLE_SIM_MIN
    assert m.fields.author_match is True
    assert v == VERDICT_WRONG_PAPER


# --------------------------------------------------------------------------
# Guardrail: the 0.92 same-work gate is a SEPARATE, untouched signal
# --------------------------------------------------------------------------
def test_same_work_title_gate_unchanged():
    assert SAME_WORK_TITLE_SIM_MIN == 0.92


# --------------------------------------------------------------------------
# End-to-end: the branch propagates through reband_from_cache automatically
# (build_f2_record calls flag_verdict), no schema change needed.
# --------------------------------------------------------------------------
def _write_xml(dirpath, pmcid, refs):
    """refs: list of (ref_id, title, author, year, pmid, source). ``source``
    becomes the claimed journal string (a preprint venue makes the row a
    preprint-source cite)."""
    body = []
    for rid, title, author, year, pmid, source in refs:
        title_el = f"<article-title>{title}</article-title>" if title else ""
        body.append(
            f'<ref id="{rid}"><element-citation>'
            f'<person-group person-group-type="author"><name><surname>{author}'
            f'</surname></name></person-group>{title_el}<source>{source}</source>'
            f'<year>{year}</year><pub-id pub-id-type="pmid">{pmid}</pub-id>'
            f'</element-citation></ref>')
    xml = ('<article><back><ref-list>' + "".join(body)
           + '</ref-list></back></article>')
    with open(os.path.join(dirpath, f"{pmcid}.xml"), "w", encoding="utf-8") as f:
        f.write(xml)


def _write_cache(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def test_reband_flips_preprint_row_and_keeps_wrong_paper_control(tmp_path):
    from cre.f1.f2_run_v3 import reband_from_cache
    xml_dir = tmp_path / "xml"; xml_dir.mkdir()
    # r1: preprint-source retitle (arXiv, author agrees) -> SAME_WORK_VARIANT.
    # r2: non-preprint wrong paper (different author)     -> WRONG_PAPER.
    _write_xml(str(xml_dir), "PMC0001", [
        ("r1", _PRE_TITLE, "Aguilar", 2020, "35264587", "arXiv"),
        ("r2", "Disseminated varicella infection", "Pannu", 2019, "31665581", "J"),
    ])
    cache = tmp_path / "resolved.jsonl"
    _write_cache(str(cache), [
        {"src_pmcid": "PMC0001", "pmid": "35264587", "resolved": True,
         "title": _PUB_TITLE, "authors": ["Aguilar"], "year": 2022,
         "journal": "Bioinformatics"},
        {"src_pmcid": "PMC0001", "pmid": "31665581", "resolved": True,
         "title": "Purple Urine after Catheterization", "authors": ["Sabanis"],
         "year": 2019, "journal": "J Urol"},
    ])
    summary = reband_from_cache(str(xml_dir), str(cache), out_dir=str(tmp_path),
                                version="v3_5", seed=7)
    recs = {json.loads(l)["pmid"]: json.loads(l)
            for l in open(summary["records_path"])}
    assert recs["35264587"]["verdict"] == VERDICT_SAME_WORK_VARIANT
    assert recs["31665581"]["verdict"] == VERDICT_WRONG_PAPER
    # counts: preprint row quarantined out, non-preprint wrong paper stays HIGH.
    assert summary["flagged_f2_high"] == 1
    assert summary["same_work_variant_excluded"] == 1
    assert summary["denominator_scoreable"] == 1
