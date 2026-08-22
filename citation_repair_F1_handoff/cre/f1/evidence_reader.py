"""cre/f1/evidence_reader.py -- the band's cited-paper abstract fetcher.

This is the ``evidence_reader`` trust-boundary role (one of
``bootstrap.TRUST_BOUNDARY_ROLES``): the single production callable that turns a
cited PMID into the abstract text the coverage judge reasons over. Every band
consumer (``judgment_band``, ``judgment_run``, ``f3_provenance``) takes
``fetch_abstract`` as an INJECTED callable; this module supplies the live one,
so a real band run has an evidence source without any consumer growing a network
dependency.

CONTRACT (the injected-callable shape the band expects):
    fetch_abstract(pmid, *, api_key="", email=DEFAULT_EMAIL, session=None,
                   cache_dir=None) -> str | None

  * Returns the cited paper's abstract text, or ``None`` -- NEVER ``""`` or a
    placeholder -- on ANY failure (empty PMID, HTTP error, empty record, no
    ``<AbstractText>``, or a sentinel abstract). ``None`` is the band's honest
    "no usable evidence" signal: it flows through ``evidence_is_usable`` ->
    ``established=None`` -> HELD_LOW_CONFIDENCE with NO coverage LLM call.
    Returning a string on failure would silently manufacture a coverage
    decision, so failure must be unambiguous.
  * NEVER emits a sentinel string. If the parsed abstract case-folds into
    ``band_prompts._MISSING_ABSTRACT_SENTINELS`` ("n/a", "none", ...), return
    ``None`` so the deterministic evidence-sufficiency gate agrees with this
    module rather than routing a sentinel to the model.

STRUCTURED-ABSTRACT JOIN CONVENTION (this changes what the model reads, and
therefore what the annotator must read):
  * PubMed splits a structured abstract into multiple ``<AbstractText>``
    elements, each optionally carrying a ``Label`` ("BACKGROUND", "METHODS",
    ...). Sections are joined IN DOCUMENT ORDER, separated by a blank line
    ("\\n\\n").
  * A labelled section is rendered ``"<LABEL>: <text>"``; an unlabelled section
    is rendered as its text verbatim. A single unlabelled abstract therefore
    round-trips to exactly its text (no prefix, no separators).

Uses NCBI EFetch (``db=pubmed``, ``rettype=abstract``, ``retmode=xml``) via the
shared ``ncbi_meta.request_with_retry`` (one retry loop, one per-IP NCBI
limiter) -- no second retry loop, no second host budget. ``session`` is injected
so the module is fully offline-testable with a stub; no network in tests.

DRIVE-FIRST CACHE: when ``cache_dir`` is given, a successful fetch is written as
one JSON file per PMID and read back before any HTTP request. Calibration re-runs
the same PMIDs repeatedly; the cache makes those runs reproducible and spends no
quota on a refetch. Failures (``None``) are deliberately NOT cached, so a
transient error is retried on the next run rather than frozen.
"""
from __future__ import annotations

import json
import os
import xml.etree.ElementTree as ET
from typing import Optional

import requests

from .band_prompts import _MISSING_ABSTRACT_SENTINELS
from .ncbi_meta import (EFETCH, TOOL, DEFAULT_EMAIL, NCBI,
                        RetrievalUnavailable, request_with_retry)


def _cache_path(cache_dir: str, pmid: str) -> str:
    return os.path.join(cache_dir, f"abstract_pmid_{pmid}.json")


def _read_cache(cache_dir: str, pmid: str) -> "str | None":
    """Return the cached abstract string, or None when there is no cache entry.

    Only successful (non-None) fetches are ever written, so a present entry is
    always a usable abstract string."""
    path = _cache_path(cache_dir, pmid)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f).get("abstract")
    except (OSError, ValueError):
        return None


def _write_cache(cache_dir: str, pmid: str, abstract: str) -> None:
    try:
        os.makedirs(cache_dir, exist_ok=True)
        with open(_cache_path(cache_dir, pmid), "w", encoding="utf-8") as f:
            json.dump({"pmid": pmid, "abstract": abstract}, f,
                      ensure_ascii=False)
    except OSError:
        pass                                # cache is best-effort, never fatal


def _parse_abstract(xml_text: str) -> "str | None":
    """Parse EFetch pubmed/abstract XML into joined abstract text, or None.

    Joins every ``<AbstractText>`` in document order under the structured-abstract
    convention documented in the module docstring; returns None when the record
    has no non-empty abstract text or the XML is unparseable."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None
    sections = []
    for elem in root.iter("AbstractText"):
        # itertext() flattens inline markup (<i>, <sup>, ...) inside a section.
        text = "".join(elem.itertext()).strip()
        if not text:
            continue
        label = (elem.get("Label") or "").strip()
        sections.append(f"{label}: {text}" if label else text)
    if not sections:
        return None
    return "\n\n".join(sections)


def fetch_abstract(pmid, *, api_key: str = "", email: str = DEFAULT_EMAIL,
                   session=None, cache_dir: "str | None" = None,
                   strict: bool = False) -> Optional[str]:
    """Fetch the cited paper's abstract text for ``pmid``, or None on any failure.

    ``strict`` splits that "any failure" in two. Without it the contract is
    unchanged: None means anything went wrong. With it, None means PubMed
    ANSWERED and the record carries no usable abstract, while a lookup that did
    not answer raises :class:`~.ncbi_meta.RetrievalUnavailable`. Callers whose
    verdict depends on the difference -- an F3 origin candidate holds either way,
    but only one of the two is evidence about the paper -- pass ``strict=True``.

    See the module docstring for the full contract, the sentinel guard, the
    structured-abstract join convention, and the drive-first cache."""
    pmid = str(pmid or "").strip()
    if not pmid:
        return None

    if cache_dir:
        cached = _read_cache(cache_dir, pmid)
        if cached is not None:
            return cached

    params = {"db": "pubmed", "id": pmid, "rettype": "abstract",
              "retmode": "xml", "tool": TOOL, "email": email}
    if api_key:
        params["api_key"] = api_key

    try:
        r = request_with_retry(session, EFETCH, params, limiter=NCBI, timeout=20)
    except requests.RequestException as exc:
        if strict:
            raise RetrievalUnavailable(
                f"abstract transport failure for {pmid}: {exc}") from exc
        return None
    if r is None or r.status_code != 200 or not r.text.strip():
        if strict:
            raise RetrievalUnavailable(
                f"abstract lookup did not answer for {pmid}: "
                f"status={getattr(r, 'status_code', None)}")
        return None

    abstract = _parse_abstract(r.text)
    if abstract is None:
        return None
    # Never route a sentinel to the model: fold it to the same "no usable
    # abstract" signal the deterministic gate uses.
    if abstract.strip().casefold() in _MISSING_ABSTRACT_SENTINELS:
        return None

    if cache_dir:
        _write_cache(cache_dir, pmid, abstract)
    return abstract
