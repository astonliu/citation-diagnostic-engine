"""Cutoff-aware PubMed formal-notice resolution for F5."""
from __future__ import annotations

import re
from typing import Any, Callable, Mapping

from .f5_supersession import NoticeStatus, _parse_date


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


def resolve_formal_notice(
    work_id: str, *, as_of_date: str, fetch_meta: Callable,
) -> NoticeStatus:
    """Resolve notice relationships as they stood at ``as_of_date``.

    A linked notice is applied only when its publication interval is wholly on
    or before the cutoff. Missing identity/date/lookup facts return unresolved.
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
            relevant.append((kind, role, relationship, linked_id,
                             "no_record", "absent", None, None))
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
                "absent", "unparseable", "boundary_uncertain"}:
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
            linked_notice_work_id=first[3], relationship=first[2])

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


def make_notice_resolver(fetch_meta: Callable):
    if not callable(fetch_meta):
        raise ValueError("fetch_meta must be callable")

    def check(work_id: str, *, as_of_date: str) -> NoticeStatus:
        return resolve_formal_notice(
            work_id, as_of_date=as_of_date, fetch_meta=fetch_meta)

    check.resolver_version = NOTICE_RESOLVER_VERSION
    return check
