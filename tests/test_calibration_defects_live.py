"""LIVE network rows for the resolver fix (ZD 2026-08-11 calibration item 2).

OPT-IN ONLY. Skipped unless ``CRE_LIVE_NCBI=1``, because the rest of ``cre/f1`` is
offline by construction and a network test in the default suite is a liability: it
turns an NCBI outage into a red build, which is the exact confusion this spec
exists to remove. These rows are run deliberately, and their output is pasted into
the handoff.

They are the three PMIDs whose PMCIDs were confirmed independently through the PMC
ID Converter after ELink reported ``no_pmcid`` for all 25 distinct cited PMIDs of
calibration run 1. They are CODE-PATH fixtures, not evaluation data: nothing here
is a gold label or an input to a reported number.

Run with:
    CRE_LIVE_NCBI=1 PYTHONPATH=. ../.venv_cre/bin/python -m pytest \\
        tests/test_calibration_defects_live.py -q -s
"""
from __future__ import annotations

import os

import pytest

from cde.claims import fulltext as fr
from cde.refs import ncbi_meta as nm

pytestmark = pytest.mark.skipif(
    os.environ.get("CRE_LIVE_NCBI") != "1",
    reason="live NCBI row; set CRE_LIVE_NCBI=1 to run")

API_KEY = os.environ.get("NCBI_API_KEY", "")

#: PMID -> PMCID, confirmed independently through the ID Converter on 2026-08-11.
CONFIRMED = {"30140736": "PMC6105232",
             "32382079": "PMC7206102",
             "26372954": "PMC4586821"}


@pytest.mark.parametrize("pmid,pmcid", sorted(CONFIRMED.items()))
def test_live_resolver_returns_the_confirmed_pmcid(pmid, pmcid):
    """Acceptance rows 10-12, resolver half. Each of these came back ``no_pmcid``
    in calibration run 1."""
    got = nm.ncbi_pmids_to_pmcids([pmid], api_key=API_KEY)
    print(f"\n  resolve {pmid} -> {got[pmid]!r} (expected {pmcid})")
    assert got[pmid] == pmcid


def test_live_one_request_resolves_all_three():
    """The batch is one request, not three -- the cost claim, live."""
    got = nm.ncbi_pmids_to_pmcids(sorted(CONFIRMED), api_key=API_KEY)
    print(f"\n  batch of {len(CONFIRMED)} in "
          f"{nm.idconv_request_count(len(CONFIRMED))} request(s) -> {got}")
    assert got == CONFIRMED
    assert nm.idconv_request_count(len(CONFIRMED)) == 1


def test_live_end_to_end_through_fetch_fulltext():
    """Acceptance row 10 in full: the spec's proven end-to-end result --
    ``pmcid='PMC6105232'``, ``resolved=True``, ``retrieval_complete=True``, 9
    sections, and the six section labels the reader emitted for it."""
    out = fr.fetch_fulltext("30140736", api_key=API_KEY)
    print(f"\n  pmcid={out['pmcid']!r} resolved={out['resolved']} "
          f"retrieval_complete={out['retrieval_complete']} "
          f"n_sections={len(out['sections'])}\n"
          f"  incomplete_reasons={out['incomplete_reasons']}\n"
          f"  sections_present={out['sections_present']}")
    assert out["pmcid"] == "PMC6105232"
    assert out["resolved"] is True
    assert out["retrieval_complete"] is True
    assert out["incomplete_reasons"] == []
    assert len(out["sections"]) == 9
    assert out["sections_present"] == [
        "discussion", "figure", "intro", "methods", "other", "results"]


def test_live_a_pmid_with_no_pmc_fulltext_is_no_pmcid_not_an_error():
    """The boundary, live: PMID 111 predates PMC, so the converter ANSWERS "not in
    PMC". That must be ``no_pmcid`` -- if this ever reports ``resolver_error``, the
    per-record/top-level status distinction has been broken and every non-OA
    article is about to be misreported as an outage."""
    out = fr.fetch_fulltext("111", api_key=API_KEY)
    print(f"\n  PMID 111 -> incomplete_reasons={out['incomplete_reasons']}")
    assert out["incomplete_reasons"] == [fr.REASON_NO_PMCID]
