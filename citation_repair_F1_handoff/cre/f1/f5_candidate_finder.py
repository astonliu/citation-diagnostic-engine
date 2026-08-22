"""Live PubMed candidate discovery for F5 (stale / superseded citations).

This module supplies the production ``search_candidates`` callable expected by
``f5_seams.build_f5_seams``.  It finds *possible* later papers; it never decides
that a paper contradicts or supersedes another paper.  That judgment remains in
``f5_supersession``.

Three independent PubMed streams are unioned:

* claim words in Title/Abstract, sorted by PubMed relevance;
* the cited work's MeSH headings;
* PubMed's ``pubmed_pubmed_citedin`` forward-citation graph.

All HTTP is injected and uses the shared NCBI limiter.  Successful searches and
metadata records may be cached on disk.  Failures are never cached, and partial
success is named in :class:`CandidateSearchResult` rather than being presented
as a complete search.
"""
from __future__ import annotations

import calendar
import datetime as _dt
import hashlib
import json
import os
import re
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass

import requests

from .lookup import EFETCH
from .ncbi_meta import DEFAULT_EMAIL, ELINK, TOOL
from .ratelimit import NCBI, request_with_retry


ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
FORWARD_LINKNAME = "pubmed_pubmed_citedin"
FINDER_VERSION = "f5_pubmed_candidate_finder_v1"
_PMID_RE = re.compile(r"[0-9]+")
_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'’-]{2,}")
_MONTHS = {
    name.casefold(): i for i, name in enumerate(calendar.month_abbr) if name
}
_MONTHS.update({
    name.casefold(): i for i, name in enumerate(calendar.month_name) if name
})
_STOPWORDS = frozenset({
    "about", "after", "among", "and", "are", "because", "been", "before",
    "between", "both", "but", "can", "compared", "could", "did", "does",
    "during", "effect", "effects", "for", "from", "had", "has", "have",
    "into", "its", "may", "more", "not", "our", "patients", "reported",
    "showed", "shows", "study", "than", "that", "the", "their", "there",
    "these", "this", "those", "through", "using", "was", "were", "which",
    "with", "within", "would",
})


class CandidateFinderError(RuntimeError):
    """A candidate-discovery provider did not return a usable answer."""


@dataclass(frozen=True)
class CandidateSearchResult:
    """One auditable search result consumed by ``make_retrieve_*``.

    ``status`` is ``ok`` only when every planned stream and every retained
    metadata lookup answered. A skipped stream is incomplete retrieval, not a
    smaller successful protocol. ``partial`` preserves useful hits while
    ensuring the downstream F5 layer cannot turn incomplete retrieval into a
    confident negative. ``failure`` means no stream produced a usable answer.
    """

    hits: tuple[dict, ...]
    status: str
    query_hash: str
    rationale: str
    streams: tuple[dict, ...]
    truncated: bool = False
    exclusions: tuple[dict, ...] = ()

    def __post_init__(self) -> None:
        if self.status not in {"ok", "partial", "failure"}:
            raise ValueError("CandidateSearchResult.status must be ok/partial/failure")
        if not isinstance(self.hits, tuple):
            raise ValueError("CandidateSearchResult.hits must be a tuple")
        if not re.fullmatch(r"[0-9a-f]{64}", self.query_hash or ""):
            raise ValueError("CandidateSearchResult.query_hash must be 64 lowercase hex")


def _canonical_sha256(value) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"),
                     ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _iso_date(value: str, name: str) -> _dt.date:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be an ISO date string")
    try:
        return _dt.date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be YYYY-MM-DD") from exc


def _quoted(term: str, field: str) -> str:
    # PubMed syntax is built from metadata/paper text, not trusted operators.
    clean = re.sub(r"[\"\\\[\](){}:]", " ", str(term or ""))
    clean = " ".join(clean.split())
    return f'"{clean}"[{field}]' if clean else ""


def build_claim_query(claim: str, *, max_terms: int = 12) -> str:
    """A deterministic high-recall Title/Abstract query from an atomic claim.

    Terms are ORed and PubMed's relevance sort supplies the ranking.  The F5
    contradiction judge, not this lexical query, owns semantic precision.
    """
    if not isinstance(claim, str) or not claim.strip():
        return ""
    seen: set[str] = set()
    terms: list[str] = []
    for match in _TOKEN_RE.finditer(claim):
        token = match.group(0).strip("-'’")
        key = token.casefold()
        if len(key) < 4 or key in _STOPWORDS or key in seen:
            continue
        seen.add(key)
        terms.append(token)
        if len(terms) >= max_terms:
            break
    rendered = [_quoted(t, "Title/Abstract") for t in terms]
    return "(" + " OR ".join(x for x in rendered if x) + ")" if rendered else ""


def build_mesh_query(mesh_terms, *, max_terms: int = 12) -> str:
    seen: set[str] = set()
    rendered: list[str] = []
    for value in mesh_terms or ():
        term = " ".join(str(value or "").split())
        key = term.casefold()
        if not term or key in seen:
            continue
        seen.add(key)
        q = _quoted(term, "MeSH Terms")
        if q:
            rendered.append(q)
        if len(rendered) >= max_terms:
            break
    return "(" + " OR ".join(rendered) + ")" if rendered else ""


def _text(node) -> str:
    return " ".join("".join(node.itertext()).split()) if node is not None else ""


def _month(value: str) -> "int | None":
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.isdigit() and 1 <= int(raw) <= 12:
        return int(raw)
    return _MONTHS.get(raw.casefold())


def _date_interval(article) -> "tuple[str, str, str, str] | None":
    """Return earliest/latest ISO dates plus precision/raw publication date."""
    date_nodes = list(article.findall(".//Article/ArticleDate"))
    date_nodes += list(article.findall(".//Article/Journal/JournalIssue/PubDate"))
    for node in date_nodes:
        year_text = (node.findtext("Year") or "").strip()
        month_text = (node.findtext("Month") or "").strip()
        day_text = (node.findtext("Day") or "").strip()
        if not year_text:
            medline = (node.findtext("MedlineDate") or "").strip()
            match = re.search(r"(?<!\d)(18|19|20)\d{2}(?!\d)", medline)
            if not match:
                continue
            year_text = match.group(0)
            month_match = re.search(
                r"\b(" + "|".join(re.escape(k) for k in sorted(
                    _MONTHS, key=len, reverse=True)) + r")\b",
                medline.casefold())
            month_text = month_match.group(1) if month_match else ""
            day_text = ""
        try:
            year = int(year_text)
        except ValueError:
            continue
        month = _month(month_text)
        try:
            day = int(day_text) if day_text else None
        except ValueError:
            day = None
        raw = "-".join(x for x in (year_text, month_text, day_text) if x)
        try:
            if month is None:
                lo, hi, precision = _dt.date(year, 1, 1), _dt.date(year, 12, 31), "year"
            elif day is None:
                lo = _dt.date(year, month, 1)
                hi = _dt.date(year, month, calendar.monthrange(year, month)[1])
                precision = "month"
            else:
                lo = hi = _dt.date(year, month, day)
                precision = "day"
        except ValueError:
            continue
        return lo.isoformat(), hi.isoformat(), precision, raw
    return None


def _parse_pubmed_xml(xml_text: str) -> dict[str, dict]:
    try:
        root = ET.fromstring(xml_text)
    except (ET.ParseError, ValueError) as exc:
        raise CandidateFinderError(f"PubMed metadata XML is not parseable: {exc}") from exc
    out: dict[str, dict] = {}
    for article in root.findall(".//PubmedArticle"):
        pmid = (article.findtext(".//MedlineCitation/PMID") or "").strip()
        if not _PMID_RE.fullmatch(pmid):
            continue
        interval = _date_interval(article)
        if interval is None:
            continue
        date_lo, date_hi, precision, raw_date = interval
        title = _text(article.find(".//Article/ArticleTitle"))
        abstract_parts = []
        for node in article.findall(".//Article/Abstract/AbstractText"):
            body = _text(node)
            if not body:
                continue
            label = (node.get("Label") or "").strip()
            abstract_parts.append(f"{label}: {body}" if label else body)
        authors, authors_full = [], []
        for node in article.findall(".//Article/AuthorList/Author"):
            collective = (node.findtext("CollectiveName") or "").strip()
            last = (node.findtext("LastName") or "").strip()
            fore = (node.findtext("ForeName") or "").strip()
            short = collective or last
            full = collective or " ".join(x for x in (last, fore) if x)
            if short:
                # F5 independence compares the cited and candidate author sets.
                # The rest of CRE stores PubMed author surnames, so storing a
                # candidate as "Jones A" would fail to match cited "Jones" and
                # falsely call an overlapping team independent.
                authors.append(short)
                authors_full.append(full)
        mesh_terms, mesh_major = [], []
        for node in article.findall(".//MedlineCitation/MeshHeadingList/MeshHeading/DescriptorName"):
            value = _text(node)
            if value:
                mesh_terms.append(value)
                if (node.get("MajorTopicYN") or "").upper() == "Y":
                    mesh_major.append(value)
        publication_types = [
            _text(node) for node in
            article.findall(".//Article/PublicationTypeList/PublicationType")
            if _text(node)
        ]
        comments_corrections = []
        for node in article.findall(
                ".//MedlineCitation/CommentsCorrectionsList/CommentsCorrections"):
            ref_type = str(node.get("RefType") or "").strip()
            linked_pmid = (node.findtext("PMID") or "").strip()
            if ref_type:
                comments_corrections.append({
                    "ref_type": ref_type,
                    "pmid": linked_pmid,
                    "note": _text(node.find("Note")),
                })
        doi = ""
        for node in article.findall(".//PubmedData/ArticleIdList/ArticleId"):
            if str(node.get("IdType") or "").strip().casefold() == "doi":
                doi = _text(node)
                break
        registry_ids, dataset_ids = [], []
        for bank in article.findall(".//MedlineCitation/DataBankList/DataBank"):
            bank_name = str(bank.findtext("DataBankName") or "").casefold()
            is_registry = any(token in bank_name for token in (
                "clinicaltrials", "isrctn", "eudract", "chictr", "trial",
            ))
            target = registry_ids if is_registry else dataset_ids
            namespace = re.sub(r"[^a-z0-9]", "", bank_name)
            for node in bank.findall(".//AccessionNumber"):
                value = _text(node)
                if value:
                    target.append(
                        f"{namespace}:{value}" if is_registry and namespace
                        else value)
        for node in article.findall(".//MedlineCitation/OtherID"):
            source = str(node.get("Source") or "").casefold()
            value = _text(node)
            if value and any(token in source for token in (
                    "clinicaltrials", "isrctn", "eudract", "trial")):
                namespace = re.sub(r"[^a-z0-9]", "", source)
                registry_ids.append(
                    f"{namespace}:{value}" if namespace else value)
        version_relations = {
            "republishedin", "republishedfrom", "updatein", "updateof",
            "correctedandrepublishedin", "correctedandrepublishedfrom",
            "preprintof", "haspreprint"}
        version_work_ids = [
            row["pmid"] for row in comments_corrections
            if re.sub(r"[^a-z]", "", row["ref_type"].casefold())
            in version_relations
        ]
        out[pmid] = {
            "id": pmid,
            "title": title,
            "abstract": "\n".join(abstract_parts),
            "pub_date": date_lo,
            "pub_date_latest": date_hi,
            "pub_date_precision": precision,
            "pub_date_raw": raw_date,
            "authors": authors,
            "authors_full": authors_full,
            "mesh": mesh_terms,
            "mesh_terms": mesh_terms,
            "mesh_major_terms": mesh_major,
            "publication_types": publication_types,
            "comments_corrections": comments_corrections,
            "doi": doi,
            "registry_ids": sorted(set(registry_ids)),
            "version_work_ids": sorted(set(version_work_ids)),
            "cohort_ids": [],
            "dataset_ids": sorted(set(dataset_ids)),
            "demonstrably_distinct_from": [],
        }
    return out


def _valid_metadata_record(record, pmid: str) -> bool:
    if not isinstance(record, dict) or record.get("id") != pmid:
        return False
    try:
        lo = _iso_date(record.get("pub_date"), "cached pub_date")
        hi = _iso_date(record.get("pub_date_latest"), "cached pub_date_latest")
    except ValueError:
        return False
    if lo > hi:
        return False
    for key in ("authors", "mesh_terms", "publication_types"):
        value = record.get(key)
        if not isinstance(value, list) or any(not isinstance(x, str) for x in value):
            return False
    if not isinstance(record.get("doi"), str):
        return False
    for key in (
            "registry_ids", "version_work_ids", "cohort_ids", "dataset_ids",
            "demonstrably_distinct_from"):
        value = record.get(key)
        if not isinstance(value, list) or any(not isinstance(x, str) for x in value):
            return False
    links = record.get("comments_corrections")
    if not isinstance(links, list):
        return False
    for link in links:
        if (not isinstance(link, dict)
                or set(link) != {"ref_type", "pmid", "note"}
                or not isinstance(link["ref_type"], str)
                or not isinstance(link["pmid"], str)
                or not isinstance(link["note"], str)):
            return False
    return True


#: How the three streams are fused into one candidate order.
#:
#: ``multi_stream_first`` is the shipped behaviour: every candidate appearing in
#: two or more streams is emitted first, ordered by STREAM COUNT before rank,
#: then the three channels round-robin. It has one measured defect -- the size of
#: that leading block scales with ``pool_limit``, which is derived from the cap,
#: so a candidate's position is not stable as the cap changes. Measured on live
#: streams: WHI 2002 moves from #37 at cap 50 to #174 at cap 200, and
#: PROWESS-SHOCK from #49 to #138. A budget that buys the top N therefore buys a
#: different N at every cap, which is the opposite of what a budget is for.
#:
#: ``rrf`` is reciprocal-rank fusion, sum(1/(k+rank)) over the streams a
#: candidate appears in. It does NOT stop agreement from beating rank -- at k=60
#: two appearances at rank 31 (2/91) still outscore one at rank 1 (1/61), and
#: that is intended: agreement across independent streams is real evidence. What
#: it removes is the two structural defects above -- the LEXICOGRAPHIC precedence
#: of stream count over rank, and the leading block whose size scales with
#: pool_limit. Rank and agreement trade off smoothly instead.
#:
#: That is what buys cap-invariance. Measured on the same live streams:
#: PROWESS-SHOCK #3 at cap 50 and #3 at cap 200 (absent / #189 today), WHI 2002
#: #8 / #9 (#16 / #143 today), ARISE #2 / #2 (absent / absent today).
#:
#: Both are recorded in the retrieval plan, so ``query_hash`` separates runs made
#: under different fusions and no record can silently claim the other one.
RANKING_MULTI_STREAM_FIRST = "multi_stream_first"
RANKING_RRF = "rrf"
RANKINGS = frozenset({RANKING_MULTI_STREAM_FIRST, RANKING_RRF})
RANKING_DEFAULT = RANKING_MULTI_STREAM_FIRST

#: The RRF smoothing constant. 60 is the value from Cormack et al. 2009, where
#: reciprocal-rank fusion was introduced and shown to beat the individual
#: rankers it combines; it is not tuned here, because tuning it on three
#: hand-picked fixtures would be fitting to the fixtures.
RRF_K = 60


def fuse_reciprocal_rank(source_ids: dict, *, exclude: str = "",
                         limit: int, k: int = RRF_K) -> list:
    """Reciprocal-rank fusion over ``{stream_name: [pmid, ...]}``. PURE.

    Ties break on the numeric PMID so the order is total and reproducible -- a
    retrieval order that depended on dict iteration would make ``query_hash``
    a promise the finder could not keep.
    """
    scores: dict[str, float] = {}
    for ids in source_ids.values():
        for rank, pmid in enumerate(ids):
            if pmid == exclude:
                continue
            scores[pmid] = scores.get(pmid, 0.0) + 1.0 / (k + rank + 1)
    return [pmid for pmid, _score in sorted(
        scores.items(), key=lambda kv: (-kv[1], int(kv[0])))][:limit]


class PubMedCandidateFinder:
    """Injected, cached production candidate finder for ``build_f5_seams``."""

    def __init__(self, *, api_key: str = "", email: str = DEFAULT_EMAIL,
                 session=None, cache_dir: "str | None" = None,
                 timeout: float = 30.0, max_retries: int = 4,
                 ranking: str = RANKING_DEFAULT):
        if not isinstance(email, str) or not email.strip():
            raise ValueError("email must be a nonblank string")
        if ranking not in RANKINGS:
            raise ValueError(f"ranking must be one of {sorted(RANKINGS)}")
        self.ranking = ranking
        self.api_key = str(api_key or "")
        self.email = email.strip()
        self.session = session
        self.cache_dir = os.path.realpath(cache_dir) if cache_dir else None
        self.timeout = timeout
        self.max_retries = max_retries

    def _params(self, base: dict) -> dict:
        params = dict(base)
        params.update({"tool": TOOL, "email": self.email})
        if self.api_key:
            params["api_key"] = self.api_key
        return params

    def _cache_path(self, namespace: str, key: str) -> "str | None":
        if self.cache_dir is None:
            return None
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return os.path.join(self.cache_dir, namespace, digest + ".json")

    def _read_cache(self, namespace: str, key: str):
        path = self._cache_path(namespace, key)
        if path is None or not os.path.exists(path):
            return None
        try:
            with open(path, encoding="utf-8") as fh:
                payload = json.load(fh)
        except (OSError, ValueError, TypeError):
            return None
        return payload if isinstance(payload, dict) else None

    def _write_cache(self, namespace: str, key: str, payload: dict) -> None:
        path = self._cache_path(namespace, key)
        if path is None:
            return
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".tmp-f5-", dir=os.path.dirname(path))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, sort_keys=True, ensure_ascii=False)
                fh.write("\n")
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def _request(self, url: str, params: dict):
        try:
            response = request_with_retry(
                self.session, url, self._params(params), limiter=NCBI,
                timeout=self.timeout, max_retries=self.max_retries)
        except requests.RequestException as exc:
            raise CandidateFinderError(f"NCBI transport failure: {exc}") from exc
        if response is None or response.status_code != 200:
            code = "no response" if response is None else response.status_code
            raise CandidateFinderError(f"NCBI returned {code} for {url}")
        return response

    def _esearch(self, query: str, after: _dt.date, as_of: _dt.date,
                 retmax: int) -> tuple[list[str], int]:
        key_obj = {
            "v": FINDER_VERSION, "endpoint": "esearch", "query": query,
            "after": after.isoformat(), "as_of": as_of.isoformat(),
            "retmax": retmax,
        }
        key = _canonical_sha256(key_obj)
        cached = self._read_cache("search", key)
        if cached is not None and isinstance(cached.get("ids"), list):
            ids = [str(x) for x in cached["ids"]
                   if _PMID_RE.fullmatch(str(x))]
            try:
                count = int(cached.get("count") or 0)
            except (TypeError, ValueError):
                count = -1
            if count >= len(ids):
                return ids, count
        response = self._request(ESEARCH, {
            "db": "pubmed", "term": query, "retmode": "json",
            "retmax": retmax, "sort": "relevance", "datetype": "pdat",
            "mindate": (after + _dt.timedelta(days=1)).strftime("%Y/%m/%d"),
            "maxdate": as_of.strftime("%Y/%m/%d"),
        })
        try:
            data = response.json()
            result = data["esearchresult"]
            ids = [str(x) for x in result.get("idlist") or []
                   if _PMID_RE.fullmatch(str(x))]
            count = int(result.get("count") or 0)
        except (ValueError, TypeError, KeyError) as exc:
            raise CandidateFinderError(f"malformed ESearch JSON: {exc}") from exc
        self._write_cache("search", key, {"ids": ids, "count": count})
        return ids, count

    def _forward_citations(self, pmid: str) -> list[str]:
        key_obj = {"v": FINDER_VERSION, "endpoint": "elink",
                   "linkname": FORWARD_LINKNAME, "pmid": pmid}
        key = _canonical_sha256(key_obj)
        cached = self._read_cache("search", key)
        if cached is not None and isinstance(cached.get("ids"), list):
            ids = [str(x) for x in cached["ids"]
                   if _PMID_RE.fullmatch(str(x))]
            if len(ids) == len(cached["ids"]):
                return ids
        response = self._request(ELINK, {
            "dbfrom": "pubmed", "db": "pubmed", "id": pmid,
            "linkname": FORWARD_LINKNAME, "retmode": "json",
        })
        try:
            data = response.json()
        except ValueError as exc:
            raise CandidateFinderError(f"malformed ELink JSON: {exc}") from exc
        ids: list[str] = []
        try:
            for linkset in data.get("linksets") or []:
                for group in linkset.get("linksetdbs") or []:
                    if group.get("linkname") != FORWARD_LINKNAME:
                        continue
                    ids.extend(str(x) for x in (group.get("links") or [])
                               if _PMID_RE.fullmatch(str(x)))
        except (AttributeError, TypeError) as exc:
            raise CandidateFinderError(f"malformed ELink link set: {exc}") from exc
        ids = list(dict.fromkeys(ids))
        self._write_cache("search", key, {"ids": ids})
        return ids

    def _fetch_metadata(self, pmids: list[str]) -> tuple[dict[str, dict], list[str]]:
        records: dict[str, dict] = {}
        missing: list[str] = []
        uncached: list[str] = []
        for pmid in pmids:
            cached = self._read_cache("metadata", pmid)
            record = cached.get("record") if cached else None
            if _valid_metadata_record(record, pmid):
                records[pmid] = record
            else:
                uncached.append(pmid)
        for start in range(0, len(uncached), 200):
            batch = uncached[start:start + 200]
            response = self._request(EFETCH, {
                "db": "pubmed", "id": ",".join(batch),
                "rettype": "abstract", "retmode": "xml",
            })
            parsed = _parse_pubmed_xml(response.text)
            for pmid in batch:
                record = parsed.get(pmid)
                if record is None:
                    missing.append(pmid)
                    continue
                records[pmid] = record
                self._write_cache("metadata", pmid, {"record": record})
        return records, missing

    def fetch_metadata(self, pmid: str) -> "dict | None":
        """Fetch one PubMed record for the live F5 seam/evidence builder.

        Transport and parse failures raise :class:`CandidateFinderError`; an
        answered lookup with no usable record returns ``None``. Keeping those
        outcomes distinct prevents an outage from becoming evidence of absence.
        """
        value = str(pmid or "").strip()
        if not _PMID_RE.fullmatch(value):
            raise ValueError("pmid must contain decimal digits only")
        records, _missing = self._fetch_metadata([value])
        record = records.get(value)
        return dict(record) if record is not None else None

    def search_candidates(self, cited_meta: dict, claim: str, *,
                          after_date: str, as_of_date: str,
                          cap: int = 50) -> CandidateSearchResult:
        """Find later PubMed candidates without judging contradiction.

        Date intervals are filtered conservatively.  A year-only candidate is
        retained only when its *earliest* possible date is after ``after_date``
        and its *latest* possible date is on/before ``as_of_date``.  An uncertain
        boundary is excluded and makes the search partial, so it cannot support
        a downstream confident negative.
        """
        if not isinstance(cited_meta, dict):
            raise ValueError("cited_meta must be a dict")
        if isinstance(cap, bool) or not isinstance(cap, int) or cap < 1:
            raise ValueError("cap must be a positive int")
        after = _iso_date(after_date, "after_date")
        as_of = _iso_date(as_of_date, "as_of_date")
        if after >= as_of:
            raise ValueError("after_date must be strictly before as_of_date")
        cited_pmid = str(cited_meta.get("pmid") or cited_meta.get("work_id")
                         or cited_meta.get("cited_work_id") or "").strip()
        if cited_pmid and not _PMID_RE.fullmatch(cited_pmid):
            cited_pmid = ""
        claim_query = build_claim_query(claim)
        mesh_query = build_mesh_query(cited_meta.get("mesh_major_terms")
                                      or cited_meta.get("mesh_terms") or ())
        pool_limit = min(1000, max(cap * 4, cap))
        plan = {
            "version": FINDER_VERSION, "cited_pmid": cited_pmid,
            "claim_query": claim_query, "mesh_query": mesh_query,
            "after_date": after_date, "as_of_date": as_of_date,
            "candidate_cap": cap, "pool_limit": pool_limit,
            "sources": ["pubmed_esearch_claim", "pubmed_esearch_mesh",
                        FORWARD_LINKNAME],
            "ranking": self.ranking,
        }
        query_hash = _canonical_sha256(plan)
        streams: list[dict] = []
        source_ids: dict[str, list[str]] = {}
        errors: list[str] = []
        truncated = False

        def esearch_stream(name: str, query: str) -> None:
            nonlocal truncated
            if not query:
                streams.append({"name": name, "status": "skipped_no_input",
                                "count": 0, "returned": 0})
                return
            try:
                ids, count = self._esearch(query, after, as_of, pool_limit)
            except CandidateFinderError as exc:
                streams.append({"name": name, "status": "failure",
                                "error": str(exc), "count": None, "returned": 0})
                errors.append(name)
                return
            source_ids[name] = ids
            was_truncated = count > len(ids)
            truncated = truncated or was_truncated
            streams.append({"name": name, "status": "ok", "count": count,
                            "returned": len(ids), "truncated": was_truncated})

        esearch_stream("pubmed_esearch_claim", claim_query)
        esearch_stream("pubmed_esearch_mesh", mesh_query)
        if cited_pmid:
            try:
                ids = self._forward_citations(cited_pmid)
                source_ids[FORWARD_LINKNAME] = ids
                streams.append({"name": FORWARD_LINKNAME, "status": "ok",
                                "count": len(ids), "returned": len(ids),
                                "truncated": False})
            except CandidateFinderError as exc:
                streams.append({"name": FORWARD_LINKNAME, "status": "failure",
                                "error": str(exc), "count": None, "returned": 0})
                errors.append(FORWARD_LINKNAME)
        else:
            streams.append({"name": FORWARD_LINKNAME,
                            "status": "skipped_no_pmid", "count": 0,
                            "returned": 0})

        attempted = [s for s in streams if not s["status"].startswith("skipped")]
        succeeded = [s for s in attempted if s["status"] == "ok"]
        if not succeeded:
            return CandidateSearchResult(
                (), "failure", query_hash,
                "no PubMed candidate stream returned a usable answer",
                tuple(streams), truncated=truncated)

        appearances: dict[str, dict[str, int]] = {}
        for source, ids in source_ids.items():
            for rank, pmid in enumerate(ids):
                if pmid == cited_pmid:
                    continue
                appearances.setdefault(pmid, {})[source] = rank
        ranked = sorted(
            appearances,
            key=lambda pmid: (
                -len(appearances[pmid]),
                0 if FORWARD_LINKNAME in appearances[pmid] else 1,
                min(appearances[pmid].values()),
                sum(appearances[pmid].values()),
                int(pmid),
            ))
        # Preserve the strongest multi-stream candidates first, then round-robin
        # the three channels.  A very long forward-citation list must not crowd
        # every claim-query or MeSH-only candidate out of the metadata pool.
        if self.ranking == RANKING_RRF:
            # RECIPROCAL-RANK FUSION. Rank and cross-stream agreement trade off
            # smoothly here instead of agreement winning outright, and nothing
            # scales with pool_limit -- so a candidate's position no longer moves
            # when the cap moves. That is the property a deep-comparison budget
            # needs: a budget that buys the top N is only meaningful if the top N
            # does not change every time the cap does.
            ordered = fuse_reciprocal_rank(
                source_ids, exclude=cited_pmid, limit=pool_limit)
        else:
            ordered = [pmid for pmid in ranked if len(appearances[pmid]) > 1]
            already = set(ordered)
            stream_order = (FORWARD_LINKNAME, "pubmed_esearch_claim",
                            "pubmed_esearch_mesh")
            positions = {name: 0 for name in stream_order}
            while len(ordered) < min(pool_limit, len(appearances)):
                added = False
                for source in stream_order:
                    ids = source_ids.get(source) or []
                    position = positions[source]
                    while position < len(ids) and (ids[position] == cited_pmid
                                                    or ids[position] in already):
                        position += 1
                    positions[source] = position + 1
                    if position >= len(ids):
                        continue
                    pmid = ids[position]
                    already.add(pmid)
                    ordered.append(pmid)
                    added = True
                    if len(ordered) >= pool_limit:
                        break
                if not added:
                    break
        if len(ordered) > pool_limit:
            ordered = ordered[:pool_limit]
            truncated = True
        if len(appearances) > len(ordered):
            truncated = True

        exclusions: list[dict] = []
        try:
            metadata, missing = self._fetch_metadata(ordered)
        except CandidateFinderError as exc:
            return CandidateSearchResult(
                (), "failure" if not errors else "partial", query_hash,
                f"candidate streams answered but metadata retrieval failed: {exc}",
                tuple(streams), truncated=truncated,
                exclusions=({"reason": "metadata_failure", "detail": str(exc)},))
        for pmid in missing:
            exclusions.append({"id": pmid, "reason": "metadata_missing"})

        hits: list[dict] = []
        for pmid in ordered:
            record = metadata.get(pmid)
            if record is None:
                continue
            lo = _iso_date(record["pub_date"], "candidate pub_date")
            hi = _iso_date(record["pub_date_latest"], "candidate pub_date_latest")
            if lo <= after:
                # If the interval straddles the boundary, later publication is
                # possible but not established.  Preserve that distinction.
                reason = "not_strictly_after" if hi <= after else "date_boundary_uncertain"
                exclusions.append({"id": pmid, "reason": reason,
                                   "earliest": lo.isoformat(),
                                   "latest": hi.isoformat()})
                continue
            if hi > as_of:
                reason = "after_as_of_date" if lo > as_of else "date_boundary_uncertain"
                exclusions.append({"id": pmid, "reason": reason,
                                   "earliest": lo.isoformat(),
                                   "latest": hi.isoformat()})
                continue
            hit = dict(record)
            hit["candidate_sources"] = sorted(appearances[pmid])
            hit["candidate_source_ranks"] = dict(sorted(appearances[pmid].items()))
            hits.append(hit)

        # ``ordered`` already carries the multi-stream-first, then balanced
        # round-robin ranking.  Re-sorting here by forward-citation membership
        # would undo that balance and let a large cited-in list starve every
        # claim/MeSH-only candidate at the final cap.
        if len(hits) > cap:
            hits = hits[:cap]
            truncated = True

        skipped = [s for s in streams if s["status"].startswith("skipped")]
        partial = bool(errors or skipped or missing or any(
            e["reason"] == "date_boundary_uncertain" for e in exclusions))
        status = "partial" if partial else "ok"
        rationale = (
            f"{len(hits)} admissible later candidate(s) from "
            f"{len(succeeded)}/{len(streams)} planned PubMed streams answered; "
            f"excluded={len(exclusions)}; truncated={str(truncated).lower()}"
        )
        return CandidateSearchResult(
            tuple(hits), status, query_hash, rationale, tuple(streams),
            truncated=truncated, exclusions=tuple(exclusions))

    __call__ = search_candidates
