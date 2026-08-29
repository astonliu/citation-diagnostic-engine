"""A withheld full text must not be reported as a parser failure.

PMC7977842 (VERTOS, AJNR 2007) is the case these guard. Its publisher does not
license the full text for XML download, so EFetch ``db=pmc`` answers with front
matter and a comment saying exactly that. The bench read the resulting emptiness
three ways, and all three named the wrong layer:

* ``citing_pmid`` came off the first parsed REFERENCE, so a document with no
  bibliography reported "no PMID in the XML" about an XML whose front matter
  prints ``<article-id pub-id-type="pmid">17353335</article-id>``;
* the page's ``with_citance == 0`` branch blamed ``link_citances`` for resolving
  no marker, when no reference had been delivered for it to resolve;
* a bare number is a valid PMCID and a valid PMID, so ``17353335`` was read as
  ``PMC17353335`` and its absence blamed on Open Access licensing.

None of the three was a wrong verdict -- the engine invented nothing either time.
They were diagnostics pointing at the parser, which is why they cost a reader a
session hunting a parser bug that did not exist. The parser was never called.

``load_pubmeta`` is guarded here too: F5 retrieval runs a claim stream and a MeSH
stream, and ``build_mesh_query`` over no terms yields an empty query, so a packet
with no ``cited_mesh_terms`` half-retrieves silently.

Offline. No network and no model.
"""
from __future__ import annotations

import pytest

from cde.runtime import sandbox_pmc as spmc

WITHHELD_XML = """<?xml version="1.0" ?><pmc-articleset><article article-type="research-article">
<!--The publisher of this article does not allow downloading of the full text in XML form.-->
<front><journal-meta><journal-id journal-id-type="nlm-ta">AJNR Am J Neuroradiol</journal-id></journal-meta>
<article-meta><article-id pub-id-type="pmcid">PMC7977842</article-id>
<article-id pub-id-type="pmid">17353335</article-id>
<title-group><article-title>Percutaneous Vertebroplasty Compared with Optimal Pain
Medication Treatment. The VERTOS Study</article-title></title-group>
<abstract><p>PURPOSE: To prospectively assess the short-term clinical outcome.</p></abstract>
</article-meta></front></article></pmc-articleset>"""

# The article's own title is the FIRST one; the second belongs to a reference.
# Scanning the whole document for <article-title> would report a paper titled
# after the first entry in its own bibliography.
WITH_REFS_XML = """<?xml version="1.0" ?><pmc-articleset><article>
<front><article-meta><article-id pub-id-type="pmid">111</article-id>
<title-group><article-title>The citing paper</article-title></title-group>
</article-meta></front>
<back><ref-list><ref id="R1"><element-citation>
<article-title>A cited paper, whose title is not the article's</article-title>
</element-citation></ref></ref-list></back></article></pmc-articleset>"""


def test_front_identity_reads_the_articles_own_pmid_and_title():
    front = spmc._front_identity(WITHHELD_XML)
    assert front["pmid"] == "17353335"
    # Whitespace inside the element is folded; the title is one line, not two.
    assert front["title"] == ("Percutaneous Vertebroplasty Compared with Optimal "
                             "Pain Medication Treatment. The VERTOS Study")


def test_front_identity_never_takes_a_references_title_for_the_articles():
    assert spmc._front_identity(WITH_REFS_XML)["title"] == "The citing paper"


def test_a_withheld_full_text_still_reports_its_identity(monkeypatch):
    """The fix. Zero references, but the paper is not anonymous."""
    monkeypatch.setattr(spmc, "fetch_article_xml",
                        lambda *a, **k: WITHHELD_XML)
    out = spmc.load_article("PMC7977842")
    assert out["counts"]["references_parsed"] == 0
    assert out["citing_pmid"] == "17353335"
    assert "VERTOS" in out["citing_title"]
    # The flag the page needs to say WHY it is empty, rather than guessing.
    assert out["full_text_withheld"] is True


def test_a_bare_number_is_named_as_an_identifier_mistake(monkeypatch):
    """``17353335`` read as ``PMC17353335`` must not be blamed on licensing."""
    class Resp:
        status_code = 200
        text = "<pmc-articleset><error>The following PMCID is not available: 17353335</error></pmc-articleset>"

    monkeypatch.setattr(spmc, "request_with_retry", lambda *a, **k: Resp())
    with pytest.raises(spmc.PmcError) as exc:
        spmc.fetch_article_xml("PMC17353335", raw_input="17353335")
    message = str(exc.value)
    assert "no PMC prefix" in message
    assert "if that was a PMID" in message
    # The licensing sentence is the WRONG explanation here and must not appear.
    assert "Open Access subset" not in message


def test_a_real_pmcid_absence_still_reads_as_licensing(monkeypatch):
    """The old message is right when the input really was a PMCID."""
    class Resp:
        status_code = 200
        text = "<pmc-articleset></pmc-articleset>"

    monkeypatch.setattr(spmc, "request_with_retry", lambda *a, **k: Resp())
    with pytest.raises(spmc.PmcError) as exc:
        spmc.fetch_article_xml("PMC4661126", raw_input="PMC4661126")
    assert "Open Access subset" in str(exc.value)


def _stub_finder(monkeypatch, result=None, raiser=None):
    from cde.diagnose import candidate_finder as fcf

    class Fake:
        def __init__(self, **kw):
            pass

        def fetch_metadata(self, pmid):
            if raiser:
                raise raiser
            return result

    monkeypatch.setattr(fcf, "PubMedCandidateFinder", Fake)


def test_pubmeta_returns_the_terms_the_live_finder_would_have_used(monkeypatch):
    _stub_finder(monkeypatch, result={
        "id": "17353335", "title": "VERTOS",
        "mesh_terms": ["Spinal Fractures", "Osteoporosis"],
        "mesh_major_terms": ["Orthopedic Procedures"],
        "publication_types": ["Randomized Controlled Trial"],
        "pub_date": "2007-03-01", "authors": ["Voormolen MH"],
    })
    out = spmc.load_pubmeta("17353335")
    assert out["found"] is True
    assert out["mesh_terms"] == ["Spinal Fractures", "Osteoporosis"]
    assert out["pub_date"] == "2007-03-01"
    assert "PubMedCandidateFinder" in out["source"]


def test_pubmeta_reports_an_answered_empty_record_as_absence(monkeypatch):
    _stub_finder(monkeypatch, result=None)
    out = spmc.load_pubmeta("17353335")
    assert out["found"] is False
    assert out["mesh_terms"] == []


def test_pubmeta_raises_on_an_outage_rather_than_reporting_no_terms(monkeypatch):
    """DEC-032's rule. An outage that returned [] would license F5 to retrieve
    on one stream and call the other one empty."""
    from cde.diagnose import candidate_finder as fcf

    _stub_finder(monkeypatch, raiser=fcf.CandidateFinderError("EFetch timed out"))
    with pytest.raises(spmc.PmcError) as exc:
        spmc.load_pubmeta("17353335")
    assert "outage, not an empty record" in str(exc.value)


def test_pubmeta_refuses_a_non_pmid():
    with pytest.raises(spmc.PmcError):
        spmc.load_pubmeta("PMC7977842")
