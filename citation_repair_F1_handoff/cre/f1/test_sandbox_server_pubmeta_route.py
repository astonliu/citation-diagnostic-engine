"""The page and the route that close the silent half-retrieval.

``cited_mesh_terms`` had no input on the page and no route on the server, so the
only way to supply it was to hand-edit JSON -- and F5 run without it retrieves on
``pubmed_esearch_claim`` alone while ``pubmed_esearch_mesh`` queries nothing. The
run is thinner than it looks and the record cannot say so, because the stream was
never asked anything. These assert the field, the route and the warning exist.

The page assertions are text-level on purpose: the page ships as one static file
with no JS test harness, and a text guard that fails loudly when a branch is
deleted is worth more than no guard at all.

Offline. No network and no model.
"""
from __future__ import annotations

from pathlib import Path

from . import sandbox_server as ss

UI = (Path(__file__).resolve().parent / "sandbox_ui.html").read_text(encoding="utf-8")


def test_the_free_retrieval_routes_include_pubmeta_and_do_not_bill():
    assert "/api/pubmeta" in ss.Handler.ROUTES
    _handler, bills = ss.Handler.ROUTES["/api/pubmeta"]
    # It is an NCBI retrieval. Taking the model lock would serialize it behind a
    # paid run for no reason.
    assert bills is False


def test_the_page_offers_a_mesh_field_and_a_way_to_fill_it():
    assert 'id="f_mesh"' in UI
    assert 'id="getmeta"' in UI
    assert "/api/pubmeta" in UI
    # And emits it under the key the wiring reads.
    assert "cited_mesh_terms" in UI


def test_the_page_warns_when_f5_runs_with_no_mesh_terms():
    """The warning is the point: a thinner run that announces itself."""
    assert "pubmed_esearch_mesh" in UI
    assert "claim stream alone" in UI


def test_the_citance_diagnostic_separates_no_bibliography_from_a_parser_miss():
    """Three states, keyed on both numbers, not one.

    ``with_citance == 0`` is reached both by a parser that resolved no marker and
    by a document that delivered no references to resolve against. Branching on
    with_citance alone told a reader whose publisher withheld the body to go
    looking for a bug in ``link_citances``.
    """
    assert "c.references_parsed === 0" in UI
    assert "no bibliography to parse" in UI
    # The parser IS still blamed, but only where it is actually at fault.
    assert "All ' + c.references_parsed + ' references parsed" in UI
    assert "full_text_withheld" in UI


def test_editing_the_cited_pmid_drops_metadata_fetched_for_the_old_one():
    """Two of the fetched fields have no visible input, so a stale carry would
    put one paper's publication date under another paper's PMID."""
    assert 'if(t.id === "f_cpmid"' in UI
    assert "META = {}" in UI
