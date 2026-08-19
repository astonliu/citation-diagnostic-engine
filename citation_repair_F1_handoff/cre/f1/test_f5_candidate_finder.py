"""Offline tests for the live F5 PubMed candidate-finding mechanism."""
from __future__ import annotations

import json

import pytest

from . import f5_candidate_finder as finder
from . import f5_seams


class Response:
    def __init__(self, *, status=200, payload=None, text=""):
        self.status_code = status
        self._payload = payload
        self.text = text or (json.dumps(payload) if payload is not None else "")

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class Session:
    def __init__(self, responder):
        self.responder = responder
        self.calls = []

    def get(self, url, *, params, timeout):
        self.calls.append((url, dict(params), timeout))
        return self.responder(url, params)


def direct_requests(monkeypatch):
    """Bypass sleeping/retry mechanics; ratelimit has its own tests."""
    monkeypatch.setattr(
        finder, "request_with_retry",
        lambda session, url, params, **_kw: session.get(
            url, params=params, timeout=_kw.get("timeout")))


def article(pmid, *, title="Later trial", year="2022", month="Jan", day="15",
            authors=("Jones", "Lee"), mesh=("Heart Diseases",),
            pubtypes=("Randomized Controlled Trial",), abstract="Later result"):
    date = f"<Year>{year}</Year>"
    if month:
        date += f"<Month>{month}</Month>"
    if day:
        date += f"<Day>{day}</Day>"
    author_xml = "".join(
        f"<Author><LastName>{name}</LastName><ForeName>A</ForeName></Author>"
        for name in authors)
    mesh_xml = "".join(
        f'<MeshHeading><DescriptorName MajorTopicYN="Y">{term}</DescriptorName>'
        "</MeshHeading>" for term in mesh)
    pt_xml = "".join(f"<PublicationType>{value}</PublicationType>"
                     for value in pubtypes)
    return f"""
      <PubmedArticle>
        <MedlineCitation>
          <PMID>{pmid}</PMID>
          <Article>
            <ArticleTitle>{title}</ArticleTitle>
            <Abstract><AbstractText Label="RESULTS">{abstract}</AbstractText></Abstract>
            <AuthorList>{author_xml}</AuthorList>
            <Journal><JournalIssue><PubDate>{date}</PubDate></JournalIssue></Journal>
            <PublicationTypeList>{pt_xml}</PublicationTypeList>
          </Article>
          <MeshHeadingList>{mesh_xml}</MeshHeadingList>
        </MedlineCitation>
      </PubmedArticle>"""


def pubmed_xml(*articles):
    return "<PubmedArticleSet>" + "".join(articles) + "</PubmedArticleSet>"


def esearch(ids, *, count=None):
    return Response(payload={"esearchresult": {
        "count": str(len(ids) if count is None else count),
        "idlist": list(ids),
    }})


def elink(ids, *, wrong_group=()):
    groups = [{"linkname": finder.FORWARD_LINKNAME, "links": list(ids)}]
    if wrong_group:
        groups.append({"linkname": "pubmed_pubmed_refs",
                       "links": list(wrong_group)})
    return Response(payload={"linksets": [{"linksetdbs": groups}]})


def test_query_builders_are_deterministic_and_do_not_admit_pubmed_operators():
    claim = 'Drug X reduces mortality [pt] OR retracted publication'
    query = finder.build_claim_query(claim)
    assert "[pt]" not in query
    assert '"mortality"[Title/Abstract]' in query
    assert query == finder.build_claim_query(claim)

    mesh = finder.build_mesh_query(["Heart Diseases", "heart diseases", "Stroke"])
    assert mesh.count("Heart Diseases") == 1
    assert '"Stroke"[MeSH Terms]' in mesh


def test_pubmed_metadata_keeps_the_fields_f5_needs():
    parsed = finder._parse_pubmed_xml(pubmed_xml(article("104")))
    row = parsed["104"]
    assert row["title"] == "Later trial"
    assert row["abstract"] == "RESULTS: Later result"
    assert row["pub_date"] == "2022-01-15"
    assert row["pub_date_latest"] == "2022-01-15"
    assert row["authors"] == ["Jones", "Lee"]
    assert row["authors_full"] == ["Jones A", "Lee A"]
    assert row["mesh_terms"] == ["Heart Diseases"]
    assert row["publication_types"] == ["Randomized Controlled Trial"]


def test_three_stream_union_deduplicates_filters_dates_and_preserves_sources(
        tmp_path, monkeypatch):
    direct_requests(monkeypatch)

    def responder(url, params):
        if url == finder.ESEARCH:
            if "MeSH Terms" in params["term"]:
                return esearch(["103", "104"])
            return esearch(["102", "103"])
        if url == finder.ELINK:
            return elink(["104", "105"], wrong_group=["999"])
        if url == finder.EFETCH:
            return Response(text=pubmed_xml(
                article("102", year="2021"),
                article("103", year="2019"),
                article("104", year="2022", title="Two-source candidate"),
                article("105", year="2025"),
            ))
        raise AssertionError(url)

    session = Session(responder)
    search = finder.PubMedCandidateFinder(
        session=session, cache_dir=str(tmp_path), max_retries=0)
    result = search.search_candidates(
        {"pmid": "100", "mesh_terms": ["Heart Diseases"]},
        "Drug therapy reduces cardiovascular mortality",
        after_date="2020-06-01", as_of_date="2024-12-31", cap=50)

    assert result.status == "ok"
    assert [h["id"] for h in result.hits] == ["104", "102"]
    assert result.hits[0]["candidate_sources"] == [
        "pubmed_esearch_mesh", finder.FORWARD_LINKNAME]
    assert "999" not in {h["id"] for h in result.hits}
    assert {e["id"]: e["reason"] for e in result.exclusions} == {
        "103": "not_strictly_after", "105": "after_as_of_date"}
    assert len(result.query_hash) == 64
    esearch_calls = [params for url, params, _ in session.calls
                     if url == finder.ESEARCH]
    assert all(p["mindate"] == "2020/06/02" for p in esearch_calls)
    assert all(p["maxdate"] == "2024/12/31" for p in esearch_calls)


def test_one_failed_stream_preserves_hits_but_marks_the_search_partial(
        tmp_path, monkeypatch):
    direct_requests(monkeypatch)

    def responder(url, params):
        if url == finder.ESEARCH and "MeSH Terms" in params["term"]:
            return Response(status=503)
        if url == finder.ESEARCH:
            return esearch(["102"])
        if url == finder.ELINK:
            return Response(status=503)
        if url == finder.EFETCH:
            return Response(text=pubmed_xml(article("102", year="2022")))
        raise AssertionError(url)

    result = finder.PubMedCandidateFinder(
        session=Session(responder), cache_dir=str(tmp_path),
        max_retries=0).search_candidates(
            {"pmid": "100", "mesh_terms": ["Heart Diseases"]},
            "Drug therapy reduces mortality", after_date="2020-01-01",
            as_of_date="2024-01-01", cap=50)
    assert result.status == "partial"
    assert [h["id"] for h in result.hits] == ["102"]
    assert sum(s["status"] == "failure" for s in result.streams) == 2


def test_missing_cited_pmid_cannot_become_an_adequate_confident_negative(
        tmp_path, monkeypatch):
    direct_requests(monkeypatch)

    def responder(url, _params):
        if url == finder.ESEARCH:
            return esearch(["102"])
        if url == finder.EFETCH:
            return Response(text=pubmed_xml(article("102", year="2022")))
        raise AssertionError(f"forward-citation lookup must be skipped: {url}")

    searched = finder.PubMedCandidateFinder(
        session=Session(responder), cache_dir=str(tmp_path),
        max_retries=0).search_candidates(
            {"mesh_terms": ["Heart Diseases"]},
            "Drug therapy reduces mortality", after_date="2020-01-01",
            as_of_date="2024-01-01", cap=50)

    assert searched.status == "partial"
    assert any(s["status"] == "skipped_no_pmid" for s in searched.streams)
    retrieve = f5_seams.make_retrieve_superseding_candidates(
        lambda *_args, **_kwargs: searched)
    result = retrieve(
        {"mesh_terms": ["Heart Diseases"]},
        "Drug therapy reduces mortality", after_date="2020-01-01",
        as_of_date="2024-01-01")
    assert (result.status, result.adequacy) == ("partial", "inadequate")


def test_large_forward_list_cannot_starve_claim_and_mesh_candidates(
        tmp_path, monkeypatch):
    direct_requests(monkeypatch)
    forward = [str(x) for x in range(1000, 1020)]

    def responder(url, params):
        if url == finder.ESEARCH and "MeSH Terms" in params["term"]:
            return esearch(["3000"])
        if url == finder.ESEARCH:
            return esearch(["2000"])
        if url == finder.ELINK:
            return elink(forward)
        if url == finder.EFETCH:
            ids = params["id"].split(",")
            return Response(text=pubmed_xml(*[
                article(pmid, year="2022") for pmid in ids]))
        raise AssertionError(url)

    result = finder.PubMedCandidateFinder(
        session=Session(responder), cache_dir=str(tmp_path),
        max_retries=0).search_candidates(
            {"pmid": "100", "mesh_terms": ["Heart Diseases"]},
            "Drug therapy reduces mortality", after_date="2020-01-01",
            as_of_date="2024-01-01", cap=3)
    assert {h["id"] for h in result.hits} == {"1000", "2000", "3000"}
    assert result.truncated is True


def test_every_failed_stream_is_failure_not_an_empty_clean_search(
        tmp_path, monkeypatch):
    direct_requests(monkeypatch)
    session = Session(lambda _url, _params: Response(status=503))
    result = finder.PubMedCandidateFinder(
        session=session, cache_dir=str(tmp_path),
        max_retries=0).search_candidates(
            {"pmid": "100", "mesh_terms": ["Heart Diseases"]},
            "Drug therapy reduces mortality", after_date="2020-01-01",
            as_of_date="2024-01-01", cap=50)
    assert result.status == "failure"
    assert result.hits == ()
    assert "no PubMed candidate stream" in result.rationale


def test_year_only_date_that_straddles_the_cutoff_is_partial_not_silently_dropped(
        tmp_path, monkeypatch):
    direct_requests(monkeypatch)

    def responder(url, _params):
        if url == finder.ESEARCH:
            return esearch(["102"])
        if url == finder.EFETCH:
            return Response(text=pubmed_xml(
                article("102", year="2020", month="", day="")))
        raise AssertionError(url)

    result = finder.PubMedCandidateFinder(
        session=Session(responder), cache_dir=str(tmp_path),
        max_retries=0).search_candidates(
            {}, "Drug therapy reduces mortality",
            after_date="2020-06-01", as_of_date="2024-01-01", cap=50)
    assert result.status == "partial"
    assert result.hits == ()
    assert result.exclusions[0]["reason"] == "date_boundary_uncertain"


def test_successful_search_and_metadata_are_cached_not_recalled(
        tmp_path, monkeypatch):
    direct_requests(monkeypatch)

    def responder(url, _params):
        if url == finder.ESEARCH:
            return esearch(["102"])
        if url == finder.EFETCH:
            return Response(text=pubmed_xml(article("102", year="2022")))
        raise AssertionError(url)

    session = Session(responder)
    search = finder.PubMedCandidateFinder(
        session=session, cache_dir=str(tmp_path), max_retries=0)
    kwargs = dict(after_date="2020-01-01", as_of_date="2024-01-01", cap=50)
    first = search.search_candidates({}, "Drug therapy reduces mortality", **kwargs)
    call_count = len(session.calls)
    second = search.search_candidates({}, "Drug therapy reduces mortality", **kwargs)
    assert first.hits == second.hits
    assert len(session.calls) == call_count


def test_f5_retrieval_adapter_keeps_metadata_and_partial_status():
    hit = {
        "id": "102", "title": "Later RCT", "abstract": "Drug did not help",
        "pub_date": "2022-01-15", "authors": ["Jones", "Lee"],
        "mesh_terms": ["Heart Diseases"],
        "publication_types": ["Randomized Controlled Trial"],
    }
    searched = finder.CandidateSearchResult(
        (hit,), "partial", "a" * 64, "forward-citation stream failed",
        ({"name": "pubmed_esearch_claim", "status": "ok"},),
        truncated=False)
    protocols = []
    retrieve = f5_seams.make_retrieve_superseding_candidates(
        lambda *_args, **_kwargs: searched, cap=50, protocol_log=protocols)
    result = retrieve(
        {"mesh_terms": ["Heart Diseases"]}, "Drug therapy reduces mortality",
        after_date="2020-01-01", as_of_date="2024-01-01")

    assert result.status == "partial"
    assert result.adequacy == "inadequate"
    assert result.query_hash == "a" * 64
    candidate = result.candidates[0]
    assert candidate.title == "Later RCT"
    assert candidate.authors == ("Jones", "Lee")
    assert candidate.mesh == ("Heart Diseases",)
    assert candidate.tier_hint == "rct"
    assert protocols[0]["query_hash"] == "a" * 64
    assert protocols[0]["protocol_version"] == "f5_retrieval_v3"
    assert protocols[0]["candidate_cap"] == 50


def test_adapter_downgrades_an_ok_provider_that_skipped_a_planned_stream():
    hit = {"id": "102", "pub_date": "2022-01-15"}
    searched = finder.CandidateSearchResult(
        (hit,), "ok", "b" * 64, "legacy provider claimed complete",
        (
            {"name": "pubmed_esearch_claim", "status": "ok"},
            {"name": finder.FORWARD_LINKNAME, "status": "skipped_no_pmid"},
        ))
    protocols = []
    result = f5_seams.make_retrieve_superseding_candidates(
        lambda *_args, **_kwargs: searched, protocol_log=protocols)(
            {}, "Drug therapy reduces mortality", after_date="2020-01-01",
            as_of_date="2024-01-01")

    assert (result.status, result.adequacy) == ("partial", "inadequate")
    assert protocols[0]["sources_attempted"] == ["pubmed_esearch_claim"]
    assert protocols[0]["sources_succeeded"] == ["pubmed_esearch_claim"]
    assert protocols[0]["sources_skipped"] == [finder.FORWARD_LINKNAME]

    empty = finder.CandidateSearchResult(
        (), "ok", "c" * 64, "legacy provider claimed complete",
        ({"name": finder.FORWARD_LINKNAME, "status": "skipped_no_pmid"},))
    empty_result = f5_seams.make_retrieve_superseding_candidates(
        lambda *_args, **_kwargs: empty)(
            {}, "Drug therapy reduces mortality", after_date="2020-01-01",
            as_of_date="2024-01-01")
    assert (empty_result.status, empty_result.adequacy) == ("partial", "empty")


def test_pubmed_runtime_wires_finder_and_evidence_builder_end_to_end(
        tmp_path, monkeypatch):
    direct_requests(monkeypatch)

    def responder(url, params):
        if url == finder.ESEARCH:
            return esearch(["102"])
        if url == finder.ELINK:
            return elink(["103"])
        if url == finder.EFETCH:
            ids = params["id"].split(",")
            year = "2020" if ids == ["100"] else "2022"
            return Response(text=pubmed_xml(*[
                article(pmid, year=year) for pmid in ids]))
        raise AssertionError(url)

    runtime = f5_seams.build_pubmed_f5_runtime(
        complete=lambda _prompt: "{}", as_of_date="2024-01-01",
        session=Session(responder), cache_dir=str(tmp_path), max_retries=0)
    evidence = runtime["f5_evidence_builder"]({"cited_pmid": "100"})
    result = runtime["f5_seams"]["retrieve_superseding_candidates"](
        evidence["cited_meta"], "Drug therapy reduces mortality",
        after_date=evidence["cited_date"], as_of_date=evidence["as_of_date"])

    assert evidence["cited_work_id"] == "100"
    assert evidence["cited_meta"]["pmid"] == "100"
    assert result.status == "ok"
    assert result.adequacy == "adequate"
    assert {candidate.id for candidate in result.candidates} == {"102", "103"}


def test_f5_candidate_finder_is_in_both_runtime_provenance_boundaries():
    from . import judgment_run
    from . import production_launcher

    hashes = judgment_run._module_hashes(False, {}, None)
    assert "f5_candidate_finder.py" in hashes
    assert "f5_candidate_finder.py" in production_launcher.GOVERNING_MODULES
