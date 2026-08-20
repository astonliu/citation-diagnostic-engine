"""Deterministic study identity and conservative F5 independence decisions."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


STUDY_CLUSTER_VERSION = "f5_study_cluster_v1"
_VERSION_RELATIONS = frozenset({
    "republishedin", "republishedfrom", "updatein", "updateof",
    "correctedandrepublishedin", "correctedandrepublishedfrom",
    "preprintof", "haspreprint",
})
_ASCII_PMID = re.compile(r"[0-9]+")
_DOI = re.compile(r"10\.[0-9]{4,9}/\S+")
_PLACEHOLDERS = frozenset({
    "unknown", "none", "na", "n/a", "not available", "not applicable",
    "unavailable", "missing", "null", "tbd", "pending", "unspecified",
})


def _values(raw: Any, *, name: str) -> tuple[str, ...]:
    if raw is None:
        return ()
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
        raise ValueError(f"{name} must be a sequence of strings")
    out = []
    for value in raw:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must contain only nonblank strings")
        normalized = re.sub(r"\s+", " ", value.strip()).casefold()
        if normalized not in out:
            out.append(normalized)
    return tuple(sorted(out))


def _registry_values(raw: Any) -> tuple[str, ...]:
    values = _values(raw, name="registry_ids")
    normalized = set()
    for value in values:
        if value in _PLACEHOLDERS:
            continue
        namespace = ""
        identifier = value
        if ":" in value:
            namespace, identifier = value.split(":", 1)
            namespace = re.sub(r"[^a-z0-9]", "", namespace)
        compact = re.sub(r"[^a-z0-9]", "", identifier)
        eudract = re.sub(r"\s+", "", identifier)
        is_eudract = bool(re.fullmatch(
            r"[0-9]{4}-[0-9]{6}-[0-9]{2}", eudract))
        valid = bool(
            re.fullmatch(r"nct[0-9]{8}", compact)
            or re.fullmatch(r"isrctn[0-9]{8}", compact)
            or re.fullmatch(r"actrn[0-9]{14}", compact)
            or re.fullmatch(r"chictr[a-z0-9]{8,}", compact)
            or re.fullmatch(r"ctri[0-9]{4}[0-9]{2}[0-9]{6}", compact)
            or re.fullmatch(r"drks[0-9]{8}", compact)
            or re.fullmatch(r"irct[0-9]{14,24}n[0-9]+", compact)
            or re.fullmatch(r"pactr[0-9]{12,18}", compact)
            or re.fullmatch(r"rbr[a-z0-9]{5,}", compact)
            or re.fullmatch(r"rpcec[0-9]{4,}", compact)
            or re.fullmatch(r"tctr[0-9]{8,}", compact)
            or re.fullmatch(r"(?:jprn)?umin[0-9]{8,}", compact)
            or is_eudract
        )
        if not valid:
            continue
        canonical = eudract if is_eudract else compact
        # These registry prefixes are globally unique. Keeping the provider's
        # optional namespace would make `NCT...` and
        # `clinicaltrialsgov:NCT...` look like different trials.
        normalized.add(canonical)
    return tuple(sorted(normalized))


def _doi(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("doi must be a string or None")
    normalized = value.strip().casefold()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix):].strip()
    if not normalized or normalized in _PLACEHOLDERS:
        return None
    return normalized if _DOI.fullmatch(normalized) else None


def _established_values(raw: Any, *, name: str) -> tuple[str, ...]:
    return tuple(value for value in _values(raw, name=name)
                 if value not in _PLACEHOLDERS)


def _version_values(raw: Any) -> tuple[str, ...]:
    return tuple(value for value in _values(raw, name="version_work_ids")
                 if _ASCII_PMID.fullmatch(value))


def _relation_key(value: Any) -> str:
    return re.sub(r"[^a-z]", "", str(value or "").casefold())


_NAMED_DATA_SOURCE = re.compile(
    r"\b(?:[Cc]ohort|[Ss]tudy|[Tt]rial|[Dd]ataset)\s+"
    r"([A-Z][A-Za-z0-9_-]{2,})\b")
_REGISTRY_IN_TEXT = re.compile(
    r"\b(?:NCT[0-9]{8}|ISRCTN[0-9]{8}|DRKS[0-9]{8}|"
    r"IRCT[0-9]{14,24}N[0-9]+|PACTR[0-9]{12,18})\b", re.IGNORECASE)


def source_bound_distinct_data(
        cited_text: str, candidate_text: str) -> tuple[bool, str | None]:
    """Recognize only an explicit, cited-source-bound no-overlap statement.

    Different locations, dates, authors, or registrations alone are deliberately
    insufficient. The candidate must name an identity present in the cited source,
    explicitly say its participants/records/data do not come from or overlap that
    identity, and expose a different named cohort or registration of its own.
    """
    if not isinstance(cited_text, str) or not isinstance(candidate_text, str):
        return False, None
    cited_tokens = {
        match.group(1).casefold() for match in _NAMED_DATA_SOURCE.finditer(cited_text)}
    cited_tokens.update(
        match.group(0).casefold() for match in _REGISTRY_IN_TEXT.finditer(cited_text))
    candidate_tokens = {
        match.group(1).casefold()
        for match in _NAMED_DATA_SOURCE.finditer(candidate_text)}
    candidate_tokens.update(
        match.group(0).casefold()
        for match in _REGISTRY_IN_TEXT.finditer(candidate_text))
    if not cited_tokens or not (candidate_tokens - cited_tokens):
        return False, None
    candidate_lowered = candidate_text.casefold()
    for token in cited_tokens:
        if re.search(
                rf"\b(?:subgroup|subset)\s+of\s+(?:cohort\s+)?{re.escape(token)}\b"
                rf"|\bnested\s+(?:within|in)\s+(?:cohort\s+)?{re.escape(token)}\b",
                candidate_lowered):
            return False, None
    sentences = re.split(r"(?<=[.!?])\s+|[\r\n]+", candidate_text)
    for sentence in sentences:
        lowered = sentence.casefold()
        bound_tokens = [token for token in cited_tokens if token in lowered]
        if not bound_tokens:
            continue
        explicit_no_overlap = re.search(
            r"\bno\s+(?:participants?|patients?|subjects?|records?|data)\b"
            r"\s+(?:came\s+from|were\s+(?:drawn|recruited)\s+from|"
            r"originated\s+from|overlapped(?:\s+with)?|were\s+(?:shared|reused))\b",
            lowered,
        ) or re.search(
            r"\b(?:participants?|patients?|subjects?|records?|data)\b"
            r"\s+(?:did\s+not|were\s+not|was\s+not)\s+"
            r"(?:overlap|share|reuse|come\s+from|originate\s+from|"
            r"(?:be\s+)?drawn\s+from|(?:be\s+)?recruited\s+from)\b",
            lowered,
        )
        if explicit_no_overlap:
            return True, sentence.strip()
    return False, None


@dataclass(frozen=True)
class StudyIdentity:
    work_id: str
    registry_ids: tuple[str, ...] = ()
    doi: str | None = None
    version_work_ids: tuple[str, ...] = ()
    cohort_ids: tuple[str, ...] = ()
    dataset_ids: tuple[str, ...] = ()
    demonstrably_distinct_from: tuple[str, ...] = ()
    primary_study: bool | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.work_id, str) or not self.work_id.strip():
            raise ValueError("StudyIdentity.work_id must be nonblank")
        for name in (
            "registry_ids", "version_work_ids", "cohort_ids", "dataset_ids",
            "demonstrably_distinct_from",
        ):
            value = getattr(self, name)
            if not isinstance(value, tuple) or any(
                    not isinstance(item, str) or not item for item in value):
                raise ValueError(f"StudyIdentity.{name} must be normalized strings")
        if self.primary_study is not None and type(self.primary_study) is not bool:
            raise ValueError("StudyIdentity.primary_study must be bool or None")


@dataclass(frozen=True)
class StudyRelation:
    independence: str
    basis: str
    cited_cluster_id: str
    candidate_cluster_id: str
    cluster_uncertain: bool


@dataclass(frozen=True)
class StudyCluster:
    cluster_id: str
    work_ids: tuple[str, ...]
    identity_evidence_ids: tuple[str, ...]
    basis: str
    cluster_uncertain: bool


def identity_from_mapping(meta: Mapping[str, Any], *, work_id: str) -> StudyIdentity:
    if not isinstance(meta, Mapping):
        raise ValueError("study metadata must be a mapping")
    links = meta.get("comments_corrections") or ()
    if isinstance(links, (str, bytes)) or not isinstance(links, Sequence):
        raise ValueError("comments_corrections must be a sequence")
    version_ids = list(meta.get("version_work_ids") or ())
    for row in links:
        if not isinstance(row, Mapping):
            raise ValueError("comments_corrections entries must be mappings")
        if _relation_key(row.get("ref_type")) in _VERSION_RELATIONS:
            linked = str(row.get("pmid") or "").strip()
            if _ASCII_PMID.fullmatch(linked):
                version_ids.append(linked)
    primary_study = meta.get("primary_study")
    if primary_study is None:
        tier = str(meta.get("tier_hint") or meta.get("cited_tier") or "").strip()
        if tier:
            primary_study = tier in {
                "rct", "prospective_cohort", "retrospective_cohort",
                "case_control", "cross_sectional",
            }
    return StudyIdentity(
        work_id=work_id,
        registry_ids=_registry_values(
            meta.get("registry_ids") or meta.get("trial_registration_numbers") or ()),
        doi=_doi(meta.get("doi")),
        version_work_ids=_version_values(version_ids),
        cohort_ids=_established_values(
            meta.get("cohort_ids") or (), name="cohort_ids"),
        dataset_ids=_established_values(
            meta.get("dataset_ids") or (), name="dataset_ids"),
        demonstrably_distinct_from=_established_values(
            meta.get("demonstrably_distinct_from") or (),
            name="demonstrably_distinct_from"),
        primary_study=primary_study,
    )


def _cluster_id(prefix: str, values: Sequence[str]) -> str:
    payload = "\x1f".join(sorted(values)).encode("utf-8")
    return f"{prefix}:{hashlib.sha256(payload).hexdigest()[:20]}"


def _fallback(identity: StudyIdentity) -> str:
    return _cluster_id("pmid", (identity.work_id,))


def compare_studies(cited: StudyIdentity, candidate: StudyIdentity) -> StudyRelation:
    """Compare two papers without using title/PMID difference as independence."""
    if cited.work_id.casefold() == candidate.work_id.casefold():
        cluster = _fallback(cited)
        return StudyRelation(
            "not_independent", "same_work_id", cluster, cluster, False)
    shared_registry = set(cited.registry_ids) & set(candidate.registry_ids)
    if (shared_registry and cited.primary_study is not False
            and candidate.primary_study is not False):
        cluster = _cluster_id("registry", tuple(shared_registry))
        return StudyRelation(
            "not_independent", "shared_registry_identifier", cluster, cluster, False)

    version_linked = (
        candidate.work_id.casefold() in cited.version_work_ids
        or cited.work_id.casefold() in candidate.version_work_ids
        or bool(set(cited.version_work_ids) & set(candidate.version_work_ids))
        or (cited.doi is not None and cited.doi == candidate.doi)
    )
    if version_linked:
        if cited.doi is not None and cited.doi == candidate.doi:
            keys = (f"doi:{cited.doi}",)
        else:
            keys = tuple(sorted({
                cited.work_id.casefold(), candidate.work_id.casefold(),
                *cited.version_work_ids, *candidate.version_work_ids,
            }))
        cluster = _cluster_id("version", keys)
        return StudyRelation(
            "not_independent", "established_version_relationship",
            cluster, cluster, False)

    shared_cohort = set(cited.cohort_ids) & set(candidate.cohort_ids)
    shared_dataset = set(cited.dataset_ids) & set(candidate.dataset_ids)
    if shared_cohort or shared_dataset:
        prefix = "cohort" if shared_cohort else "dataset"
        shared = tuple(shared_cohort or shared_dataset)
        cluster = _cluster_id(prefix, shared)
        return StudyRelation(
            "not_independent", f"shared_{prefix}_identity", cluster, cluster, False)

    explicit_distinct = (
        candidate.work_id.casefold() in cited.demonstrably_distinct_from
        or cited.work_id.casefold() in candidate.demonstrably_distinct_from
    )
    if explicit_distinct:
        return StudyRelation(
            "independent", "explicit_distinct_data",
            _cluster_id("registry", cited.registry_ids) if cited.registry_ids
            else _fallback(cited),
            _cluster_id("registry", candidate.registry_ids) if candidate.registry_ids
            else _fallback(candidate),
            False,
        )

    return StudyRelation(
        "unknown", "cluster_identity_insufficient",
        _fallback(cited), _fallback(candidate), True)


def cluster_studies(identities: Sequence[StudyIdentity]) -> tuple[StudyCluster, ...]:
    """Group papers by established shared-study evidence, never by title/PMID."""
    if any(not isinstance(item, StudyIdentity) for item in identities):
        raise ValueError("identities must contain only StudyIdentity values")
    by_id = {item.work_id: item for item in identities}
    if len(by_id) != len(identities):
        raise ValueError("StudyIdentity work_id values must be unique")
    work_ids = sorted(by_id)
    parent = {work_id: work_id for work_id in work_ids}
    links: dict[frozenset[str], StudyRelation] = {}

    def find(work_id: str) -> str:
        while parent[work_id] != work_id:
            parent[work_id] = parent[parent[work_id]]
            work_id = parent[work_id]
        return work_id

    def union(left: str, right: str) -> None:
        a, b = find(left), find(right)
        if a != b:
            parent[max(a, b)] = min(a, b)

    for index, left_id in enumerate(work_ids):
        for right_id in work_ids[index + 1:]:
            relation = compare_studies(by_id[left_id], by_id[right_id])
            if relation.independence == "not_independent":
                union(left_id, right_id)
                links[frozenset((left_id, right_id))] = relation

    components: dict[str, list[str]] = {}
    for work_id in work_ids:
        components.setdefault(find(work_id), []).append(work_id)

    clusters = []
    for members in components.values():
        member_set = set(members)
        related = [
            relation for pair, relation in links.items()
            if pair <= member_set
        ]
        if related:
            priority = {
                "shared_registry_identifier": 0,
                "established_version_relationship": 1,
                "shared_cohort_identity": 2,
                "shared_dataset_identity": 2,
            }
            chosen = min(related, key=lambda item: (
                priority.get(item.basis, 9), item.cited_cluster_id))
            basis = chosen.basis
            member_items = [by_id[work_id] for work_id in members]
            if basis == "shared_registry_identifier":
                evidence_ids = tuple(sorted({
                    f"registry:{value}" for item in member_items
                    for value in item.registry_ids
                }))
                cluster_id = _cluster_id("registry", evidence_ids)
            elif basis == "established_version_relationship":
                evidence_ids = tuple(sorted({
                    *(f"pmid:{work_id.casefold()}" for work_id in members),
                    *(f"pmid:{value}" for item in member_items
                      for value in item.version_work_ids),
                    *(f"doi:{item.doi}" for item in member_items if item.doi),
                }))
                cluster_id = _cluster_id("version", evidence_ids)
            else:
                field = "cohort_ids" if basis == "shared_cohort_identity" \
                    else "dataset_ids"
                prefix = "cohort" if field == "cohort_ids" else "dataset"
                evidence_ids = tuple(sorted({
                    f"{prefix}:{value}" for item in member_items
                    for value in getattr(item, field)
                }))
                cluster_id = _cluster_id(prefix, evidence_ids)
            uncertain = False
        else:
            item = by_id[members[0]]
            if item.registry_ids:
                evidence_ids = tuple(
                    (f"registry:{value}" if item.primary_study is not False
                     else f"context-registry:{item.work_id.casefold()}:{value}")
                    for value in item.registry_ids)
                cluster_id = _cluster_id("registry", evidence_ids)
                basis, uncertain = (
                    ("registry_identifier", False)
                    if item.primary_study is not False
                    else ("nonprimary_registry_context", True))
            elif item.doi:
                evidence_ids = (f"doi:{item.doi}",)
                cluster_id = _cluster_id("version", evidence_ids)
                basis, uncertain = "doi_identity", False
            elif item.version_work_ids:
                evidence_ids = tuple(sorted({
                    f"pmid:{item.work_id.casefold()}",
                    *(f"pmid:{value}" for value in item.version_work_ids),
                }))
                cluster_id = _cluster_id("version", evidence_ids)
                basis, uncertain = "established_version_relationship", False
            elif item.cohort_ids:
                evidence_ids = tuple(f"cohort:{value}" for value in item.cohort_ids)
                cluster_id = _cluster_id("cohort", evidence_ids)
                basis, uncertain = "cohort_identity", False
            elif item.dataset_ids:
                evidence_ids = tuple(f"dataset:{value}" for value in item.dataset_ids)
                cluster_id = _cluster_id("dataset", evidence_ids)
                basis, uncertain = "dataset_identity", False
            elif item.demonstrably_distinct_from:
                evidence_ids = tuple(sorted({
                    f"pmid:{item.work_id.casefold()}",
                    *(f"distinct:{value}" for value
                      in item.demonstrably_distinct_from),
                }))
                cluster_id = _cluster_id(
                    "explicit_data", evidence_ids)
                basis, uncertain = "explicit_distinct_data", False
            else:
                evidence_ids = (f"pmid:{item.work_id.casefold()}",)
                cluster_id = _fallback(item)
                basis, uncertain = "pmid_fallback", True
        clusters.append(StudyCluster(
            cluster_id=cluster_id, work_ids=tuple(sorted(members)),
            identity_evidence_ids=evidence_ids,
            basis=basis, cluster_uncertain=uncertain))
    return tuple(sorted(clusters, key=lambda cluster: cluster.cluster_id))
