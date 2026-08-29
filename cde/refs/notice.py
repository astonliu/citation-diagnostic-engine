"""Cutoff-aware PubMed formal-notice resolution for F5."""
from __future__ import annotations

import re
from typing import Any, Callable, Mapping

from ..diagnose.supersession import NoticeStatus, _parse_date


NOTICE_RESOLVER_VERSION = "f5_notice_resolver_v1"

_SUBJECT_RELATIONS = {
    "retractionin": ("retraction", "retracted_article"),
    "erratumin": ("correction", "corrected_article"),
    "correctedandrepublishedin": ("correction", "corrected_article"),
    "expressionofconcernin": ("eoc", "eoc_subject"),
}
_NOTICE_RELATIONS = {
    "retractionof": "retraction_notice",
    "erratumfor": "correction_notice",
    "correctedandrepublishedfrom": "corrected_republication",
    "expressionofconcernfor": "eoc_notice",
}
_SEVERITY = {"retraction": 0, "eoc": 1, "correction": 2}
_ASCII_PMID = re.compile(r"[0-9]+")

#: A DOI anywhere inside a free-text ``RefSource``.
_DOI_IN_TEXT = re.compile(r"10\.\d{4,9}/[^\s;,)\]]+")
_DOI_PREFIX = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)")

#: The EXACT normalized titles PubMed gives a retraction stub -- a record whose
#: own title has been replaced because the record IS the notice.
#:
#: THIS IS AN EQUALITY TEST AND MUST STAY ONE. A containment test
#: (``"retract" in title.lower()``) also fires on every ``RETRACTED ARTICLE:
#: ...`` record that KEPT its original title, and on any paper whose title
#: merely discusses retraction ("Citation of retracted articles in ..."). Those
#: records would then be dated from their OWN publication date, which is the
#: article's date, not a notice date -- a false-positive F8 against every
#: citation after it. Measured 2026-08-23: 24 of 300 sampled retracted records
#: carry a no-PMID ``RetractionIn`` with the original title kept; a containment
#: test converts all 24 into fabricated accusations.
_RETRACTION_STUB_TITLES = frozenset(
    {"retraction", "retraction notice", "retraction statement"})

_MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
           "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
_MONTH_DAYS = (31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)

#: A date inside a ``RefSource`` citation string ("Bioengineered. 2024 Dec;...").
_REFSOURCE_DATE = re.compile(
    r"\b(?P<year>(?:19|20)\d{2})\b"
    r"(?:\s+(?P<month>jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"
    r"(?:\s+(?P<day>\d{1,2})\b)?)?",
    re.IGNORECASE)


def _relation_key(value: Any) -> str:
    return re.sub(r"[^a-z]", "", str(value or "").casefold())


def _pubtype_role(pubtypes: Any) -> tuple[str, str]:
    lowered = {str(value).strip().casefold() for value in (pubtypes or [])}
    if "retracted publication" in lowered:
        return "retraction", "retracted_article"
    if lowered & {"retraction of publication", "retraction notice"}:
        return "none", "retraction_notice"
    # These publication types describe the NOTICE article. The corrected or
    # concerned subject is identified by an *...In* relationship on that work.
    if lowered & {"published erratum", "erratum"}:
        return "none", "correction_notice"
    if "expression of concern" in lowered:
        return "none", "eoc_notice"
    return "none", "no_notice_type"


def _fetch(fetch_meta: Callable, work_id: str) -> tuple[str, Any]:
    try:
        value = fetch_meta(work_id)
    except Exception:
        return "failure", None
    return ("ok", value) if isinstance(value, Mapping) and value \
        else ("no_record", None)


def _metadata_identity(meta: Mapping[str, Any], expected: str) -> bool:
    actual = str(meta.get("id") or meta.get("pmid") or "").strip()
    return actual == expected


def _linked_date_state(meta: Mapping[str, Any], cutoff) -> tuple[str, str | None, str | None]:
    earliest_raw = meta.get("pub_date")
    latest_raw = meta.get("pub_date_latest") or earliest_raw
    if not earliest_raw or not latest_raw:
        return "absent", None, None
    try:
        earliest = _parse_date(str(earliest_raw), "linked notice earliest date")
        latest = _parse_date(str(latest_raw), "linked notice latest date")
    except ValueError:
        return "unparseable", None, f"{earliest_raw}/{latest_raw}"
    if earliest > latest:
        return "unparseable", None, f"{earliest_raw}/{latest_raw}"
    if latest <= cutoff:
        return "compared", latest.isoformat(), f"{earliest_raw}/{latest_raw}"
    if earliest > cutoff:
        return "after_cutoff", earliest.isoformat(), f"{earliest_raw}/{latest_raw}"
    return "boundary_uncertain", None, f"{earliest_raw}/{latest_raw}"


def _normalize_doi(value: Any) -> str:
    text = _DOI_PREFIX.sub("", str(value or "").strip().casefold())
    return text.strip().rstrip(".").strip()


def _refsource_doi(ref_source: Any) -> str:
    """The DOI a ``RefSource`` carries, normalized, or ``""``."""
    match = _DOI_IN_TEXT.search(str(ref_source or ""))
    return _normalize_doi(match.group(0)) if match else ""


def _stub_title(title: Any) -> str:
    """``title`` folded case-, punctuation- and whitespace-insensitively."""
    lowered = re.sub(r"[^a-z0-9]+", " ", str(title or "").casefold())
    return lowered.strip()


def _refsource_interval(ref_source: Any) -> "tuple[str, str] | None":
    """The publication interval a ``RefSource`` CITATION STRING states.

    Returns ``(earliest_iso, latest_iso)`` for the coarsest date present, or
    ``None``. A year-only or month-only date becomes the WHOLE interval; the
    caller narrows it with :func:`_linked_date_state`, which takes the LATEST
    day as the notice date, so the notice-to-citation gap is the smallest value
    the metadata allows and a coarse date can never manufacture an F8.

    Every DOI is stripped BEFORE the scan. A bare-DOI ``RefSource`` such as
    ``10.1016/j.cell.2019.05.003`` contains a four-digit year-shaped token that
    is part of an identifier, not a date; reading it as one would date a notice
    from a suffix. A bare DOI is therefore undatable here BY CONSTRUCTION and
    falls through to the DOI route.
    """
    text = _DOI_IN_TEXT.sub(" ", str(ref_source or ""))
    match = _REFSOURCE_DATE.search(text)
    if match is None:
        return None
    year = int(match.group("year"))
    month_token = (match.group("month") or "").casefold()
    if not month_token:
        return f"{year:04d}-01-01", f"{year:04d}-12-31"
    month = _MONTHS[month_token[:3]]
    last = _MONTH_DAYS[month - 1]
    if month == 2 and not (year % 4 == 0 and (year % 100 or year % 400 == 0)):
        last = 28
    day_token = match.group("day")
    if day_token is None:
        return f"{year:04d}-{month:02d}-01", f"{year:04d}-{month:02d}-{last:02d}"
    day = int(day_token)
    if not 1 <= day <= last:
        return f"{year:04d}-{month:02d}-01", f"{year:04d}-{month:02d}-{last:02d}"
    return f"{year:04d}-{month:02d}-{day:02d}", f"{year:04d}-{month:02d}-{day:02d}"


def _is_self_notice(kind: str, row: Mapping[str, Any],
                    raw_meta: Mapping[str, Any]) -> bool:
    """Is THIS record its own retraction notice?

    All four conditions of the self-notice route, and all four are required:

    (a) the relationship links NO notice PMID -- the caller is already in that
        branch;
    (b) the row's ``RefSource`` DOI EQUALS this record's own DOI, which is what
        establishes that the PMID's current record IS the notice rather than a
        pointer to one;
    (c) the record carries the ``Retracted Publication`` publication type;
    (d) the record's title, normalized, is EQUAL to a retraction stub.

    (d) alone would be wrong in both directions and (a)-(c) alone are nowhere
    near sufficient: 24 of 300 sampled records satisfy (a)-(c) while KEEPING
    their original ``RETRACTED ARTICLE: ...`` title, and for those PubMed holds
    no datable notice at all. See :data:`_RETRACTION_STUB_TITLES`.
    """
    if kind != "retraction":
        return False
    own_doi = _normalize_doi(raw_meta.get("doi"))
    if not own_doi or _refsource_doi(row.get("ref_source")) != own_doi:
        return False
    if _pubtype_role(raw_meta.get("publication_types"))[0] != "retraction":
        return False
    return _stub_title(raw_meta.get("title")) in _RETRACTION_STUB_TITLES


def _unlinked_subject_state(kind: str, row: Mapping[str, Any], work_id: str,
                            raw_meta: Mapping[str, Any], cutoff,
                            fetch_meta: Callable,
                            resolve_doi_to_pmid: "Callable | None"
                            ) -> tuple:
    """Date a subject relationship that links NO notice PMID.

    PubMed emits ``RetractionIn`` with a ``RefSource`` and no ``PMID`` for a
    whole publisher class, and dating a notice ONLY by fetching a linked record
    left every one of them ``unresolved`` -- F8 could not fire on any of them.
    Three routes are tried in order, each conservative, NONE of which may return
    a date it did not read out of PubMed:

    1. the record IS the notice (:func:`_is_self_notice`) -- date it from the
       record's own JOURNAL ISSUE interval (see the comment below for why the
       issue date and not ``pub_date``);
    2. the ``RefSource`` is a citation string carrying a date;
    3. the ``RefSource`` is a DOI that is NOT this record's own -- resolve it to
       a PMID and date that record exactly as the linked-PMID path does.

    Returns the ``(linked_id, lookup_status, date_status, date, date_raw)``
    tail of a ``relevant`` row. When all three routes are exhausted the tail is
    ``("", "no_record", "notice_pmid_absent", None, ...)``, which the caller
    turns into an ``unresolved`` hold -- the CORRECT verdict for a work PubMed
    holds no datable boundary for, and the one this function must never
    promote to a fire.
    """
    ref_source = str(row.get("ref_source") or "").strip()
    if _is_self_notice(kind, row, raw_meta):
        # THE ISSUE DATE, NOT ``pub_date``. When PubMed converts a record into
        # its own notice it moves the JournalIssue PubDate to the retraction's
        # issue but LEAVES ArticleDate at the original article's e-publication
        # date, and ``pub_date`` prefers ArticleDate. Measured 2026-08-23:
        # 31758846 is ArticleDate 2019-11-23, PubDate 2023 Jan. Dating the
        # notice from ``pub_date`` backdates the retraction by four years and
        # accuses a 2021 citation that is CLEAR. A record with no issue date
        # falls through to the routes below rather than borrowing the article's.
        date_status, date, raw_date = _linked_date_state(
            {"pub_date": raw_meta.get("issue_pub_date"),
             "pub_date_latest": raw_meta.get("issue_pub_date_latest")}, cutoff)
        if date_status != "absent":
            return (work_id, "ok", date_status, date, raw_date)

    interval = _refsource_interval(ref_source)
    if interval is not None:
        date_status, date, _raw = _linked_date_state(
            {"pub_date": interval[0], "pub_date_latest": interval[1]}, cutoff)
        return ("", "ok", date_status, date, ref_source)

    ref_doi = _refsource_doi(ref_source)
    own_doi = _normalize_doi(raw_meta.get("doi"))
    if ref_doi and ref_doi != own_doi and callable(resolve_doi_to_pmid):
        try:
            resolved = str(resolve_doi_to_pmid(ref_doi) or "").strip()
        except Exception:
            # The resolver DID NOT ANSWER. That is a transport outcome, not a
            # finding that the notice is undatable, so it holds under the
            # retryable "absent" reason rather than the structural one.
            return ("", "failure", "absent", None, ref_source)
        if _ASCII_PMID.fullmatch(resolved):
            linked_lookup, linked_meta = _fetch(fetch_meta, resolved)
            if linked_meta is None or not _metadata_identity(
                    linked_meta or {}, resolved):
                return (resolved, linked_lookup, "absent", None, ref_source)
            date_status, date, raw_date = _linked_date_state(linked_meta, cutoff)
            return (resolved, "ok", date_status, date, raw_date)

    return ("", "no_record", "notice_pmid_absent", None, ref_source or None)


def resolve_formal_notice(
    work_id: str, *, as_of_date: str, fetch_meta: Callable,
    resolve_doi_to_pmid: "Callable | None" = None,
) -> NoticeStatus:
    """Resolve notice relationships as they stood at ``as_of_date``.

    A linked notice is applied only when its publication interval is wholly on
    or before the cutoff. Missing identity/date/lookup facts return unresolved.

    ``resolve_doi_to_pmid`` is the OPTIONAL DOI -> PMID seam used by the third
    route of :func:`_unlinked_subject_state`. Left unset -- as every offline
    caller and every test leaves it -- that route is simply not available and
    its cases hold, which is the same fail-closed direction as everything else
    here: no route may return ``flagged`` without a date it actually read.
    """
    if not isinstance(work_id, str) or not work_id.strip():
        raise ValueError("work_id must be a nonblank string")
    try:
        cutoff = _parse_date(as_of_date, "as_of_date")
    except ValueError:
        return NoticeStatus(
            notice_kind="none", notice_resolution="unresolved",
            lookup_status="not_performed", date_status="as_of_unavailable",
            source_role="unknown")
    lookup, raw_meta = _fetch(fetch_meta, work_id)
    if raw_meta is None:
        return NoticeStatus(
            notice_kind="none", notice_resolution="unresolved",
            lookup_status=lookup, source_role="unknown")
    if not _metadata_identity(raw_meta, work_id):
        return NoticeStatus(
            notice_kind="none", notice_resolution="unresolved",
            lookup_status="no_record", source_role="unknown")

    direct_kind, direct_role = _pubtype_role(raw_meta.get("publication_types"))
    links = raw_meta.get("comments_corrections") or []
    if not isinstance(links, list):
        raise ValueError("comments_corrections must be a list")

    relevant = []
    notice_roles = []
    for row in links:
        if not isinstance(row, Mapping):
            raise ValueError("comments_corrections entries must be mappings")
        relationship = str(row.get("ref_type") or "").strip()
        linked_id = str(row.get("pmid") or "").strip()
        key = _relation_key(relationship)
        if key in _NOTICE_RELATIONS:
            notice_roles.append(_NOTICE_RELATIONS[key])
            continue
        if key not in _SUBJECT_RELATIONS:
            continue
        kind, role = _SUBJECT_RELATIONS[key]
        if not _ASCII_PMID.fullmatch(linked_id):
            relevant.append((kind, role, relationship) + _unlinked_subject_state(
                kind, row, work_id, raw_meta, cutoff, fetch_meta,
                resolve_doi_to_pmid))
            continue
        linked_lookup, linked_meta = _fetch(fetch_meta, linked_id)
        if linked_meta is None or not _metadata_identity(linked_meta or {}, linked_id):
            relevant.append((kind, role, relationship, linked_id,
                             linked_lookup, "absent", None, None))
            continue
        date_status, date, raw_date = _linked_date_state(linked_meta, cutoff)
        relevant.append((kind, role, relationship, linked_id, "ok",
                         date_status, date, raw_date))

    actionable = []
    for row in relevant:
        if row[4] == "ok" and row[5] == "compared":
            actionable.append((row, "flagged"))
        elif row[4] != "ok" or row[5] in {
                "absent", "unparseable", "boundary_uncertain",
                "notice_pmid_absent"}:
            actionable.append((row, "unresolved"))
    if actionable:
        chosen, resolution = min(
            actionable,
            key=lambda item: (
                _SEVERITY[item[0][0]],
                0 if item[1] == "flagged" else 1,
                item[0][3],
            ),
        )
        kind, role, relationship, linked_id, lookup_status, date_status, date, raw = chosen
        return NoticeStatus(
            notice_kind=kind, notice_resolution=resolution,
            date=date, lookup_status=lookup_status, date_status=date_status,
            date_raw=raw, source_role=role,
            linked_notice_work_id=(
                linked_id if _ASCII_PMID.fullmatch(linked_id) else None),
            relationship=relationship or None)

    # Every relevant linked notice is after the cutoff: it did not yet apply.
    if relevant:
        first = sorted(relevant, key=lambda row: (_SEVERITY[row[0]], row[3]))[0]
        return NoticeStatus(
            notice_kind="none", notice_resolution="resolved_clear",
            date=first[6], lookup_status="ok", date_status="after_cutoff",
            date_raw=first[7], source_role=first[1],
            linked_notice_work_id=first[3] or None, relationship=first[2])

    if notice_roles:
        if direct_kind != "none":
            # The same work cannot simultaneously be a retracted subject and a
            # notice/corrected-republication article. Conflicting role metadata
            # is not evidence that either interpretation is clear.
            return NoticeStatus(
                notice_kind=direct_kind, notice_resolution="unresolved",
                lookup_status="ok", date_status="absent",
                source_role="unknown")
        # This work IS a notice about another work, not the affected subject.
        role = sorted(notice_roles)[0]
        return NoticeStatus(
            notice_kind="none", notice_resolution="resolved_clear",
            lookup_status="ok", source_role=role)

    # Compatibility fallback for metadata adapters that already resolved a
    # direct retraction and supplied its notice date but no relationship list.
    if direct_kind == "retraction":
        raw_date = raw_meta.get("notice_date")
        if raw_date is None:
            return NoticeStatus(
                notice_kind="retraction", notice_resolution="unresolved",
                lookup_status="ok", date_status="absent",
                source_role=direct_role)
        try:
            notice_day = _parse_date(str(raw_date), "notice_date")
        except ValueError:
            return NoticeStatus(
                notice_kind="retraction", notice_resolution="unresolved",
                lookup_status="ok", date_status="unparseable",
                date_raw=str(raw_date), source_role=direct_role)
        if notice_day > cutoff:
            return NoticeStatus(
                notice_kind="none", notice_resolution="resolved_clear",
                lookup_status="ok", date_status="after_cutoff",
                date=notice_day.isoformat(), date_raw=str(raw_date),
                source_role=direct_role)
        return NoticeStatus(
            notice_kind="retraction", notice_resolution="flagged",
            lookup_status="ok", date_status="compared",
            date=notice_day.isoformat(), date_raw=str(raw_date),
            source_role=direct_role)

    return NoticeStatus(
        notice_kind="none", notice_resolution="resolved_clear",
        lookup_status="ok", source_role=direct_role)


def make_notice_resolver(fetch_meta: Callable, *, resolve_doi_to_pmid=None):
    if not callable(fetch_meta):
        raise ValueError("fetch_meta must be callable")

    def check(work_id: str, *, as_of_date: str) -> NoticeStatus:
        return resolve_formal_notice(
            work_id, as_of_date=as_of_date, fetch_meta=fetch_meta,
            resolve_doi_to_pmid=resolve_doi_to_pmid)

    check.resolver_version = NOTICE_RESOLVER_VERSION
    return check
