"""Immutable, source-bound evidence packets for F5.

This module adapts the real ``fulltext_reader`` result shape (labelled
``sections[]``) and binds normalized text, source identity, cutoff, status and
missing facts into a canonical packet hash.  It performs no I/O itself.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable, Mapping, Optional, Sequence


SOURCE_PACKET_SCHEMA_VERSION = "f5_source_packet_v1"
SOURCE_STATUSES = frozenset({"complete", "partial", "failure"})
DATE_PRECISIONS = frozenset({"day", "month", "year"})
FACT_ASSESSMENT_NOT_PERFORMED = "fact_assessment_not_performed"


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalize_text(value: Any, name: str) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string or None")
    text = unicodedata.normalize("NFC", value).replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(line.rstrip() for line in text.split("\n")).strip()
    return text or None


def _iso_date(value: Any, name: str) -> dt.date:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be an ISO date string")
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO YYYY-MM-DD date") from exc


def _freeze_json(value: Any, name: str = "value") -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        out = {}
        for key, sub in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{name} mapping keys must be strings")
            out[key] = _freeze_json(sub, f"{name}.{key}")
        return MappingProxyType(dict(sorted(out.items())))
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(sub, f"{name}[]") for sub in value)
    raise ValueError(f"{name} must contain only JSON-compatible values")


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json(sub) for key, sub in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(sub) for sub in value]
    return value


def _strings(values: Any, name: str) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise ValueError(f"{name} must be a sequence of strings")
    out = []
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must contain only nonblank strings")
        out.append(value.strip())
    return tuple(out)


@dataclass(frozen=True)
class FullTextAdaptation:
    methods: Optional[str]
    results: Optional[str]
    other_sections: Optional[str]
    provenance: Mapping[str, Any]
    source_status: str
    missing_reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.source_status not in SOURCE_STATUSES:
            raise ValueError("invalid full-text source_status")
        object.__setattr__(self, "provenance", _freeze_json(
            self.provenance, "fulltext provenance"))


def adapt_fulltext_sections(fulltext: Any, *, work_id: str) -> FullTextAdaptation:
    """Map ``fulltext_reader.fetch_fulltext`` output into Methods/Results.

    Section labels, titles, normalized text, content hashes, PMCID,
    completeness and incomplete reasons remain in ``provenance``.  A failed or
    absent body is represented as partial/failure evidence, never as an empty
    verified source.
    """
    if not isinstance(work_id, str) or not re.fullmatch(r"[0-9]+", work_id):
        raise ValueError("work_id must be a decimal PMID")
    if not isinstance(fulltext, dict):
        return FullTextAdaptation(
            None, None, None,
            {"pmid": work_id, "pmcid": None, "retrieval_complete": False,
             "incomplete_reasons": ["fulltext_result_unavailable"],
             "sections": []},
            "failure", ("fulltext_result_unavailable",))
    pmid = str(fulltext.get("pmid") or "").strip()
    if pmid != work_id:
        raise ValueError(
            f"full-text PMID {pmid!r} does not match requested PMID {work_id!r}")
    sections = fulltext.get("sections")
    if not isinstance(sections, list):
        raise ValueError("fulltext['sections'] must be a list")

    preserved = []
    grouped = {"methods": [], "results": [], "other_sections": []}
    for index, section in enumerate(sections):
        if not isinstance(section, dict):
            raise ValueError(f"fulltext section {index} must be a dict")
        label = section.get("label")
        title = _normalize_text(section.get("title", ""),
                                f"sections[{index}].title") or ""
        source_text = section.get("text")
        content_hash = section.get("content_sha256")
        if (not isinstance(label, str) or not label.strip()
                or not isinstance(source_text, str) or not source_text.strip()):
            raise ValueError(f"fulltext section {index} has invalid label/text")
        # Validate the producer's binding BEFORE applying F5's canonicalization.
        # Comparing its source hash to normalized text rejected valid table rows
        # whenever XML rendering left trailing cell whitespace.
        if (not isinstance(content_hash, str)
                or content_hash != _sha256_text(source_text)):
            raise ValueError(
                f"fulltext section {index} content_sha256 does not match stored text")
        text = _normalize_text(source_text, f"sections[{index}].text")
        if text is None:  # guarded above; retained as an explicit invariant
            raise ValueError(f"fulltext section {index} has invalid label/text")
        # The adapted packet owns normalized text, so its hash must bind that
        # exact representation rather than reusing the source representation's
        # digest.
        row = {"label": label, "title": title, "text": text,
               "content_sha256": _sha256_text(text)}
        preserved.append(row)
        normalized_label = label.strip().casefold()
        heading = f"[{normalized_label}]" + (f" {title}" if title else "")
        target = normalized_label if normalized_label in {"methods", "results"} \
            else "other_sections"
        grouped[target].append(f"{heading}\n{text}")

    reasons = _strings(fulltext.get("incomplete_reasons") or (),
                       "fulltext.incomplete_reasons")
    complete = fulltext.get("retrieval_complete") is True
    if fulltext.get("retrieval_complete") not in {True, False}:
        raise ValueError("fulltext['retrieval_complete'] must be bool")
    status = "complete" if complete else ("partial" if preserved else "failure")
    provenance = {
        "pmid": pmid,
        "pmcid": fulltext.get("pmcid"),
        "retrieval_complete": complete,
        "incomplete_reasons": list(reasons),
        "sections_present": list(fulltext.get("sections_present") or []),
        "sections": preserved,
    }
    return FullTextAdaptation(
        methods="\n\n".join(grouped["methods"]) or None,
        results="\n\n".join(grouped["results"]) or None,
        other_sections="\n\n".join(grouped["other_sections"]) or None,
        provenance=provenance,
        source_status=status,
        missing_reasons=reasons,
    )


@dataclass(frozen=True)
class F5SourcePacket:
    schema_version: str
    work_id: str
    title: str
    publication_date_earliest: str
    publication_date_latest: str
    publication_date_precision: str
    authors: tuple[str, ...]
    authors_full: tuple[str, ...]
    mesh_terms: tuple[str, ...]
    mesh_major_terms: tuple[str, ...]
    publication_types: tuple[str, ...]
    evidence_tier: str
    evidence_tier_basis: str
    abstract: Optional[str]
    methods: Optional[str]
    results: Optional[str]
    other_sections: Optional[str]
    protocol: Optional[str]
    registry_record: Optional[str]
    notice: Mapping[str, Any]
    study_identifiers: Mapping[str, Any]
    fulltext: Mapping[str, Any]
    retrieved_at: str
    as_of_date: str
    source_status: str
    missing_facts: tuple[str, ...]
    source_hashes: Mapping[str, str]
    packet_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != SOURCE_PACKET_SCHEMA_VERSION:
            raise ValueError("unsupported source packet schema_version")
        if not isinstance(self.work_id, str) or not re.fullmatch(
                r"[0-9]+", self.work_id):
            raise ValueError("work_id must be a decimal PMID")
        earliest = _iso_date(self.publication_date_earliest,
                             "publication_date_earliest")
        latest = _iso_date(self.publication_date_latest,
                           "publication_date_latest")
        cutoff = _iso_date(self.as_of_date, "as_of_date")
        if earliest > latest:
            raise ValueError("publication date interval is inverted")
        if latest > cutoff:
            raise ValueError("publication date extends beyond as_of_date")
        if self.publication_date_precision not in DATE_PRECISIONS:
            raise ValueError("invalid publication_date_precision")
        if self.source_status not in SOURCE_STATUSES:
            raise ValueError("invalid source_status")
        if not isinstance(self.title, str):
            raise ValueError("title must be a string")
        for name in ("evidence_tier", "evidence_tier_basis", "retrieved_at"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a nonblank string")
        # Validate timestamp without replacing the caller's recorded value.
        try:
            dt.datetime.fromisoformat(self.retrieved_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("retrieved_at must be an ISO timestamp") from exc
        for name in ("abstract", "methods", "results", "other_sections", "protocol",
                     "registry_record"):
            value = getattr(self, name)
            if value is not None and _normalize_text(value, name) != value:
                raise ValueError(f"{name} must already be normalized")
        object.__setattr__(self, "notice", _freeze_json(self.notice, "notice"))
        object.__setattr__(self, "study_identifiers", _freeze_json(
            self.study_identifiers, "study_identifiers"))
        object.__setattr__(self, "fulltext", _freeze_json(self.fulltext, "fulltext"))
        object.__setattr__(self, "source_hashes", _freeze_json(
            self.source_hashes, "source_hashes"))
        for name in ("abstract", "methods", "results", "other_sections", "protocol",
                     "registry_record"):
            text = getattr(self, name)
            expected = self.source_hashes.get(name)
            if text is None:
                if expected is not None:
                    raise ValueError(f"source_hashes has {name} but packet text is absent")
            elif expected != _sha256_text(text):
                raise ValueError(f"source_hashes.{name} does not match stored text")
        if self.packet_sha256 != self.compute_sha256():
            raise ValueError("packet_sha256 does not match canonical packet")

    def to_dict(self, *, include_hash: bool = True) -> dict[str, Any]:
        out = {
            "schema_version": self.schema_version,
            "work_id": self.work_id,
            "title": self.title,
            "publication_date_earliest": self.publication_date_earliest,
            "publication_date_latest": self.publication_date_latest,
            "publication_date_precision": self.publication_date_precision,
            "authors": list(self.authors),
            "authors_full": list(self.authors_full),
            "mesh_terms": list(self.mesh_terms),
            "mesh_major_terms": list(self.mesh_major_terms),
            "publication_types": list(self.publication_types),
            "evidence_tier": self.evidence_tier,
            "evidence_tier_basis": self.evidence_tier_basis,
            "abstract": self.abstract,
            "methods": self.methods,
            "results": self.results,
            "other_sections": self.other_sections,
            "protocol": self.protocol,
            "registry_record": self.registry_record,
            "notice": _thaw_json(self.notice),
            "study_identifiers": _thaw_json(self.study_identifiers),
            "fulltext": _thaw_json(self.fulltext),
            "retrieved_at": self.retrieved_at,
            "as_of_date": self.as_of_date,
            "source_status": self.source_status,
            "missing_facts": list(self.missing_facts),
            "source_hashes": _thaw_json(self.source_hashes),
        }
        if include_hash:
            out["packet_sha256"] = self.packet_sha256
        return out

    def compute_sha256(self) -> str:
        return hashlib.sha256(_canonical_bytes(
            self.to_dict(include_hash=False))).hexdigest()


def source_packet_from_dict(value: Any) -> F5SourcePacket:
    """Strict replay parser for persisted packet dictionaries."""
    if not isinstance(value, dict):
        raise ValueError("source packet must be a dict")
    expected = {
        "schema_version", "work_id", "title", "publication_date_earliest",
        "publication_date_latest", "publication_date_precision", "authors",
        "authors_full", "mesh_terms", "mesh_major_terms", "publication_types",
        "evidence_tier", "evidence_tier_basis", "abstract", "methods",
        "results", "other_sections", "protocol", "registry_record", "notice",
        "study_identifiers", "fulltext", "retrieved_at", "as_of_date",
        "source_status", "missing_facts", "source_hashes", "packet_sha256",
    }
    if set(value) != expected:
        raise ValueError(
            "source packet keys must be exactly " + repr(sorted(expected)))
    kwargs = dict(value)
    for name in ("authors", "authors_full", "mesh_terms", "mesh_major_terms",
                 "publication_types", "missing_facts"):
        raw = kwargs[name]
        if not isinstance(raw, list):
            raise ValueError(f"source packet {name} must be a list")
        kwargs[name] = tuple(raw)
    return F5SourcePacket(**kwargs)


def build_source_packet(
    metadata: Mapping[str, Any], *, as_of_date: str, retrieved_at: str,
    evidence_tier: str, evidence_tier_basis: str,
    abstract: Any = None, fulltext: Any = None,
    missing_facts: Sequence[str] = (), notice: Optional[Mapping[str, Any]] = None,
    study_identifiers: Optional[Mapping[str, Any]] = None,
    protocol: Any = None, registry_record: Any = None,
    historical_content_verified: Optional[bool] = None,
) -> F5SourcePacket:
    """Build and validate one source packet from source-bound facts."""
    if not isinstance(metadata, Mapping):
        raise ValueError("metadata must be a mapping")
    work_id = str(metadata.get("id") or metadata.get("pmid") or "").strip()
    if not re.fullmatch(r"[0-9]+", work_id):
        raise ValueError("metadata must identify one decimal PMID")
    adaptation = adapt_fulltext_sections(fulltext, work_id=work_id) \
        if fulltext is not None else FullTextAdaptation(
            None, None, None,
            {"pmid": work_id, "pmcid": None, "retrieval_complete": False,
             "incomplete_reasons": ["fulltext_not_attempted"], "sections": []},
            "partial", ("fulltext_not_attempted",))
    normalized = {
        "abstract": _normalize_text(
            abstract if abstract is not None else metadata.get("abstract"), "abstract"),
        "methods": adaptation.methods,
        "results": adaptation.results,
        "other_sections": adaptation.other_sections,
        "protocol": _normalize_text(protocol, "protocol"),
        "registry_record": _normalize_text(registry_record, "registry_record"),
    }
    hashes = {name: _sha256_text(text) for name, text in normalized.items()
              if text is not None}
    facts_list = list(_strings(missing_facts, "missing_facts"))
    if not str(metadata.get("title") or "").strip() and "title_missing" not in facts_list:
        facts_list.append("title_missing")
    if historical_content_verified is not None and not isinstance(
            historical_content_verified, bool):
        raise ValueError("historical_content_verified must be bool or None")
    retrieved_day = dt.datetime.fromisoformat(
        retrieved_at.replace("Z", "+00:00")).date()
    cutoff_day = _iso_date(as_of_date, "as_of_date")
    if (retrieved_day > cutoff_day and historical_content_verified is not True
            and "historical_content_as_of_cutoff_unverified" not in facts_list):
        facts_list.append("historical_content_as_of_cutoff_unverified")
    facts = tuple(facts_list)
    if not any(normalized.values()):
        source_status = "failure"
    elif fulltext is not None and adaptation.source_status != "complete":
        # A fragment returned by the full-text transport remains partial even
        # if the semantic fact screen happens to find what it needs in it.
        source_status = "partial"
    elif facts:
        source_status = "partial"
    else:
        source_status = "complete"
    base = dict(
        schema_version=SOURCE_PACKET_SCHEMA_VERSION,
        work_id=work_id,
        title=str(metadata.get("title") or "").strip(),
        publication_date_earliest=str(metadata.get("pub_date") or ""),
        publication_date_latest=str(
            metadata.get("pub_date_latest") or metadata.get("pub_date") or ""),
        publication_date_precision=str(metadata.get("pub_date_precision") or ""),
        authors=_strings(metadata.get("authors") or (), "authors"),
        authors_full=_strings(metadata.get("authors_full") or (), "authors_full"),
        mesh_terms=_strings(metadata.get("mesh_terms") or metadata.get("mesh") or (),
                            "mesh_terms"),
        mesh_major_terms=_strings(metadata.get("mesh_major_terms") or (),
                                  "mesh_major_terms"),
        publication_types=_strings(metadata.get("publication_types") or (),
                                   "publication_types"),
        evidence_tier=evidence_tier,
        evidence_tier_basis=evidence_tier_basis,
        notice=notice or {}, study_identifiers=study_identifiers or {},
        fulltext=adaptation.provenance,
        retrieved_at=retrieved_at, as_of_date=as_of_date,
        source_status=source_status, missing_facts=facts,
        source_hashes=hashes, packet_sha256="",
        **normalized,
    )
    provisional = object.__new__(F5SourcePacket)
    for key, value in base.items():
        object.__setattr__(provisional, key, value)
    # Canonicalize mappings before deriving the hash, then construct normally so
    # every invariant is checked against the exact object that is returned.
    for key in ("notice", "study_identifiers", "fulltext", "source_hashes"):
        object.__setattr__(provisional, key, _freeze_json(getattr(provisional, key), key))
    base["packet_sha256"] = provisional.compute_sha256()
    return F5SourcePacket(**base)


class F5EvidenceStore:
    """Source-bound packet builder with content-addressed successful caching.

    All I/O and fact assessment are injected.  A changed metadata/text payload
    creates a different cache key.  Exceptions and failure packets are never
    cached as successful evidence.
    """

    def __init__(
        self, *, fetch_metadata: Callable[[str], Mapping[str, Any]],
        fetch_abstract: Callable[[str], Any],
        classify_evidence_tier: Callable[[Mapping[str, Any]], Any],
        fetch_fulltext: Optional[Callable[[str], Any]] = None,
        assess_missing_facts: Optional[Callable[..., Sequence[str]]] = None,
        fact_assessor_version: str = "none",
        fetch_notice: Optional[Callable[..., Mapping[str, Any]]] = None,
        retrieved_at: Callable[[], str],
    ) -> None:
        for name, fn in (
            ("fetch_metadata", fetch_metadata),
            ("fetch_abstract", fetch_abstract),
            ("classify_evidence_tier", classify_evidence_tier),
            ("retrieved_at", retrieved_at),
        ):
            if not callable(fn):
                raise ValueError(f"{name} must be callable")
        if fetch_fulltext is not None and not callable(fetch_fulltext):
            raise ValueError("fetch_fulltext must be callable or None")
        if assess_missing_facts is not None and not callable(assess_missing_facts):
            raise ValueError("assess_missing_facts must be callable or None")
        if fetch_notice is not None and not callable(fetch_notice):
            raise ValueError("fetch_notice must be callable or None")
        if not isinstance(fact_assessor_version, str) or not fact_assessor_version.strip():
            raise ValueError("fact_assessor_version must be nonblank")
        if assess_missing_facts is not None and fact_assessor_version == "none":
            raise ValueError("an injected fact assessor needs a version")
        self.fetch_metadata = fetch_metadata
        self.fetch_abstract = fetch_abstract
        self.classify_evidence_tier = classify_evidence_tier
        self.fetch_fulltext = fetch_fulltext
        self.assess_missing_facts = assess_missing_facts
        self.fact_assessor_version = fact_assessor_version
        self.fetch_notice = fetch_notice
        self.retrieved_at = retrieved_at
        self._cache: dict[str, F5SourcePacket] = {}
        self.counters = {
            "metadata_calls": 0,
            "abstract_calls": 0,
            "fulltext_attempts": 0,
            "fulltext_successes": 0,
            "fulltext_failures": 0,
            "cache_hits": 0,
        }

    def _missing(self, work_id: str, abstract: Optional[str],
                 adaptation: Optional[FullTextAdaptation]) -> tuple[str, ...]:
        if self.assess_missing_facts is None:
            # Unknown is not the same as verified-complete.  The sentinel makes
            # the richest available evidence get fetched and prevents a clean
            # negative from being asserted without a fact-completeness screen.
            return (FACT_ASSESSMENT_NOT_PERFORMED,)
        values = self.assess_missing_facts(
            work_id=work_id, abstract=abstract,
            methods=adaptation.methods if adaptation else None,
            results=adaptation.results if adaptation else None,
            other_sections=adaptation.other_sections if adaptation else None,
        )
        if values is None:
            raise ValueError("assess_missing_facts must return a sequence, not None")
        return _strings(values, "assess_missing_facts result")

    def get(self, work_id: str, *, as_of_date: str) -> F5SourcePacket:
        if not isinstance(work_id, str) or not re.fullmatch(r"[0-9]+", work_id):
            raise ValueError("work_id must be a decimal PMID")
        _iso_date(as_of_date, "as_of_date")
        self.counters["metadata_calls"] += 1
        metadata = self.fetch_metadata(work_id)
        if not isinstance(metadata, Mapping):
            raise ValueError(f"metadata unavailable for PMID {work_id}")
        metadata_id = str(metadata.get("id") or metadata.get("pmid") or "").strip()
        if metadata_id != work_id:
            raise ValueError(
                f"metadata PMID {metadata_id!r} does not match requested {work_id!r}")

        self.counters["abstract_calls"] += 1
        abstract = _normalize_text(self.fetch_abstract(work_id), "abstract")
        missing = self._missing(work_id, abstract, None)
        adaptation = None
        raw_fulltext = None
        fulltext_failed = False
        if missing and self.fetch_fulltext is not None:
            self.counters["fulltext_attempts"] += 1
            raw_fulltext = self.fetch_fulltext(work_id)
            adaptation = adapt_fulltext_sections(raw_fulltext, work_id=work_id)
            if adaptation.source_status == "failure":
                fulltext_failed = True
                self.counters["fulltext_failures"] += 1
            else:
                self.counters["fulltext_successes"] += 1
            missing = self._missing(work_id, abstract, adaptation)

        tier_value = self.classify_evidence_tier(metadata)
        tier = str(getattr(tier_value, "value", tier_value) or "").strip()
        if not tier:
            raise ValueError("classify_evidence_tier returned no tier")
        notice = self.fetch_notice(work_id, as_of_date=as_of_date) \
            if self.fetch_notice is not None else {
                "lookup_status": "not_performed", "as_of_date": as_of_date}
        if not isinstance(notice, Mapping):
            raise ValueError("fetch_notice must return a mapping")
        study_ids = metadata.get("study_identifiers") or {
            "registry_ids": list(metadata.get("registry_ids") or ()),
            "doi": metadata.get("doi") or None,
            "version_work_ids": list(metadata.get("version_work_ids") or ()),
            "cohort_ids": list(metadata.get("cohort_ids") or ()),
            "dataset_ids": list(metadata.get("dataset_ids") or ()),
        }
        retrieved_at = self.retrieved_at()

        content_key = hashlib.sha256(_canonical_bytes({
            "schema_version": SOURCE_PACKET_SCHEMA_VERSION,
            "work_id": work_id,
            "as_of_date": as_of_date,
            "metadata": _thaw_json(_freeze_json(metadata, "metadata")),
            "abstract": abstract,
            "fulltext": raw_fulltext,
            "missing_facts": list(missing),
            "fact_assessor_version": self.fact_assessor_version,
            "notice": _thaw_json(_freeze_json(notice, "notice")),
            "tier": tier,
        })).hexdigest()
        cached = self._cache.get(content_key)
        if cached is not None:
            self.counters["cache_hits"] += 1
            return cached

        packet = build_source_packet(
            metadata, as_of_date=as_of_date, retrieved_at=retrieved_at,
            evidence_tier=tier,
            evidence_tier_basis="classify_evidence_tier",
            abstract=abstract, fulltext=raw_fulltext,
            missing_facts=missing, notice=notice,
            study_identifiers=study_ids,
            protocol=metadata.get("protocol"),
            registry_record=metadata.get("registry_record"),
        )
        if packet.source_status != "failure" and not fulltext_failed:
            self._cache[content_key] = packet
        return packet
