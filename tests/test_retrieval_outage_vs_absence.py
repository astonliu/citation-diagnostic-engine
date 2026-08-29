"""Absence and outage are different answers, and every taxonomy can tell them apart.

Each NCBI helper here fails closed to a falsy value, which reads identically
whether the source ANSWERED "this article has no such thing" or never answered at
all. The first is a fact about the article and is a legitimate hold; the second
is a fact about the network, and counting it as the first turns an outage into
evidence about the corpus. ``fulltext_reader`` already drew this line for F6 with
``no_pmcid`` vs ``resolver_error``; ``strict`` is the same line for the seams
F3/F5/F7 use, raised rather than returned.
"""
from __future__ import annotations

import json

import pytest
import requests

from cde.claims import abstracts as er
from cde.diagnose import pipeline as jr
from cde.refs import ncbi_meta as nm
from cde.refs.ncbi_meta import RetrievalUnavailable, ResolverError
from .test_judgment_run import (disc_llm, f4_json, judge_established,
                                extractor_of, run_wired)


class _Resp:
    def __init__(self, status=200, text="ok"):
        self.status_code, self.text = status, text


def _boom(*_a, **_k):
    raise requests.RequestException("connection reset")


# ------------------------------------------------------------- the exception

def test_resolver_error_is_a_retrieval_unavailable():
    """One type every taxonomy can catch, without losing the resolver's own."""
    assert issubclass(ResolverError, RetrievalUnavailable)


# ------------------------------------------------------------------ reflist

def test_reflist_blank_pmcid_is_absence_even_in_strict_mode():
    """The caller named nothing to fetch. That is never an outage."""
    assert nm.ncbi_pmc_reflist("", strict=True) == (None, None)


def test_reflist_transport_failure_is_an_outage_only_in_strict_mode(monkeypatch):
    monkeypatch.setattr(nm, "request_with_retry", _boom)
    assert nm.ncbi_pmc_reflist("PMC1", strict=False) == (None, None)  # old contract
    with pytest.raises(RetrievalUnavailable, match="transport failure"):
        nm.ncbi_pmc_reflist("PMC1", strict=True)


def test_reflist_non_200_is_an_outage_in_strict_mode(monkeypatch):
    monkeypatch.setattr(nm, "request_with_retry",
                        lambda *a, **k: _Resp(status=503, text="down"))
    assert nm.ncbi_pmc_reflist("PMC1", strict=False) == (None, None)
    with pytest.raises(RetrievalUnavailable, match="did not answer"):
        nm.ncbi_pmc_reflist("PMC1", strict=True)


# ------------------------------------------------------------ pmid -> pmcid

def test_pmid_to_pmcid_distinguishes_no_full_text_from_no_answer(monkeypatch):
    """THE conflation named in ncbi_pmid_to_pmcid's own docstring."""
    monkeypatch.setattr(nm, "ncbi_pmids_to_pmcids", lambda *a, **k: {})
    assert nm.ncbi_pmid_to_pmcid("1", strict=True) == ""      # ANSWERED: none

    def _raise(*_a, **_k):
        raise ResolverError("resolver down")
    monkeypatch.setattr(nm, "ncbi_pmids_to_pmcids", _raise)
    assert nm.ncbi_pmid_to_pmcid("1", strict=False) == ""     # old contract
    with pytest.raises(ResolverError, match="resolver failed"):
        nm.ncbi_pmid_to_pmcid("1", strict=True)


# --------------------------------------------------------------- abstracts

def test_abstract_absence_and_outage_are_different_in_strict_mode(monkeypatch):
    monkeypatch.setattr(er, "request_with_retry",
                        lambda *a, **k: _Resp(text="<x/>"))
    assert er.fetch_abstract("1", strict=True) is None        # ANSWERED: none

    monkeypatch.setattr(er, "request_with_retry", _boom)
    assert er.fetch_abstract("1", strict=False) is None       # old contract
    with pytest.raises(RetrievalUnavailable, match="transport failure"):
        er.fetch_abstract("1", strict=True)


# ------------------------------------------- the run books it, per taxonomy

def test_an_f3_outage_is_booked_as_a_stage_failure_not_a_silent_hold(
        tmp_path, monkeypatch):
    """An outage must be visible and subtractable. A quiet UNJUDGEABLE would be
    indistinguishable from the assessor looking and being unsure."""
    def _outage(_pmid):
        raise RetrievalUnavailable("NCBI was down")

    rows, _m = run_wired(
        tmp_path, monkeypatch, coverage=judge_established(True),
        call=disc_llm(f4=f4_json()),
        f3_fetch_reflist=lambda _p: ([{"title": "P", "claimed_pmid": "2",
                                       "year": 2010}], True),
        f3_resolve_pmcid=_outage)

    failures = rows[0]["stage_failures"]
    assert {f["stage"] for f in failures} == {"F3"}
    # The type is what a reader subtracts by: a malformed model reply is a
    # ValueError, a lookup that never answered is not.
    assert failures[0]["error_type"] == "RetrievalUnavailable"
    assert "F3 stage failed" in rows[0]["hold_reasons"]
    assert rows[0]["label"] == []
