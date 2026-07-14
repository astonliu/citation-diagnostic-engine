"""Orthogonal evidence that two noisy citations represent one work.

This module deliberately does *not* produce an F2 label.  It recognizes a
small set of auditable transformations (alternate-language title, malformed
title wrapper, correction notice, living chapter revision, etc.) and returns a
reason.  The caller may quarantine the pair for human review.  Stable-ID or
metadata agreement alone never proves identity because a recombined citation
can carry the wrong paper's PMID, DOI, volume and pages together.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from rapidfuzz.distance import JaroWinkler

from .schema import ClaimedRef, RetrievedRecord
from .textnorm import fold_bibliographic_text, fold_chemical_charges


@dataclass(frozen=True)
class WorkIdentityEvidence:
    same_work: bool
    reason: str = ""
    signals: tuple[str, ...] = ()
    blocked_by: str = ""


_CORRECTION_RE = re.compile(r"^\s*(corrigendum|erratum|correction)\b", re.I)
_DERIVATIVE_RE = re.compile(
    r"(?:^|[.:;]\s+)\s*(?:(?:a|an|the|editorial|invited)\s+){0,2}"
    r"(?:(?:systematic|scoping|narrative|updated|umbrella|rapid|integrative|"
    r"critical|literature)\s+){0,2}"
    r"(?:commentary|comment|reply|review|perspective|editorial|retraction|"
    r"protocol|meta[ -]?analysis)"
    r"\b(?:\s*[:.\-–—]|\s+(?:on|to|of|for)\b)", re.I)
_DOCUMENT_RE = re.compile(
    r"\b(declaration|position\s+paper|guidelines?|guidance|consensus(?:\s+statement)?|"
    r"recommendations?|standard)\b", re.I)
_CORPORATE_RE = re.compile(
    r"\b(association|society|organization|organisation|college|academy|world|"
    r"national|international|committee|council)\b", re.I)
_LIVING_SOURCES = ("statpearls", "ncbi bookshelf", "bookshelf")
_AUTHOR_SUFFIX_ONLY = {"jr", "junior", "sr", "senior", "filho", "neto"}
_ROMAN_RE = re.compile(r"\b(?:i|ii|iii|iv|v|vi|vii|viii|ix|x)\b", re.I)
_STOP = {
    "a", "an", "and", "as", "at", "by", "for", "from", "in", "of", "on",
    "or", "the", "to", "with", "paper", "study", "analysis", "medical",
}
_GENERIC_TITLES = {
    "introduction", "editorial", "preface", "foreword", "conclusion",
    "discussion", "letter",
}


def canonical_title(text: str) -> str:
    """Title-only canonical form; keeps semantic words and series ordinals."""
    text = (text or "").strip()
    if text.startswith("[") and text.rstrip(".").endswith("]"):
        text = text[1:text.rfind("]")]
    text = fold_bibliographic_text(text)
    text = fold_chemical_charges(text)
    text = text.lower()
    text = re.sub(r"(?<=\w)-(?=\w)", "", text)
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def is_distinctive_title(text: str) -> bool:
    """Enough lexical identity to support a same-work inference."""
    value = canonical_title(text)
    tokens = [t for t in value.split() if len(t) >= 3 and t not in _STOP]
    return value not in _GENERIC_TITLES and len(value) >= 18 and len(tokens) >= 2


def _norm_doi(value: str) -> str:
    value = (value or "").strip().lower()
    value = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", value)
    return value.rstrip(". ,;)")


def doi_equivalent(left: str, right: str) -> bool:
    left, right = _norm_doi(left), _norm_doi(right)
    return bool(left and right and left == right)


def _locator(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def _first_page(value: str) -> str:
    return re.split(r"[-‐-―]", (value or "").strip(), maxsplit=1)[0].lower()


def _name(value: str) -> str:
    return canonical_title(value)


def _first_author_value(authors: list[str]) -> str:
    if not authors:
        return ""
    value = authors[0]
    if "," in value:
        value = value.split(",", 1)[0]
    value = _name(value)
    tokens = value.split()
    if len(tokens) > 1 and re.fullmatch(r"[a-z]{1,3}", tokens[0]):
        value = " ".join(tokens[1:])
    return value


_NAME_PARTICLES = {"al", "da", "de", "del", "della", "der", "di", "dos",
                   "du", "la", "le", "van", "von"}


def _first_author_aliases(authors: list[str]) -> set[str]:
    """Surname aliases for author position zero, without roster-wide matching.

    Providers represent the same first author as ``Smith``, ``John Smith``,
    ``Smith J`` or ``Smith, John``.  Comparing the full strings turned those
    formatting differences into a positional disagreement.  This helper stays
    strict about *position* while normalizing only the representation of the
    person in position zero.  Corporate names remain exact full-name matches.
    """
    if not authors or not (authors[0] or "").strip():
        return set()
    raw = authors[0].strip()
    if "," in raw:
        raw = raw.split(",", 1)[0]
    full = _name(raw)
    if not full:
        return set()
    if _CORPORATE_RE.search(full):
        return {full}

    tokens = full.split()
    while (len(tokens) > 1 and re.fullmatch(r"[a-z]{1,3}", tokens[0])
           and tokens[0] not in _NAME_PARTICLES):
        tokens.pop(0)
    while len(tokens) > 1 and (re.fullmatch(r"[a-z]{1,3}", tokens[-1])
                               or tokens[-1] in _AUTHOR_SUFFIX_ONLY):
        tokens.pop()
    if not tokens:
        return set()

    cleaned = " ".join(tokens)
    # The first token is needed for compound family names that JATS truncates
    # (``Romeo`` vs ``Romeo Casabona``; ``Van`` vs ``Van der Weyden``). This
    # remains position-zero-to-position-zero evidence; it never searches later
    # coauthors.
    aliases = {cleaned, tokens[0], tokens[-1]}
    # Preserve multi-token surname particles (``de la Cruz``, ``van der Berg``)
    # as an additional alias when a provider supplies a leading given name.
    i = len(tokens) - 2
    while i >= 0 and tokens[i] in _NAME_PARTICLES:
        i -= 1
    if i < len(tokens) - 2:
        aliases.add(" ".join(tokens[i + 1:]))
    return aliases


def first_author_equivalent(claimed: ClaimedRef, resolved: RetrievedRecord) -> bool:
    left = _first_author_aliases(claimed.authors)
    right = _first_author_aliases(resolved.authors)
    return bool(left and right and left & right)


def _first_author_typo(claimed: ClaimedRef, resolved: RetrievedRecord) -> bool:
    """Narrow personal-surname typo signal; never a global author match."""
    left = _first_author_value(claimed.authors)
    right = _first_author_value(resolved.authors)
    if (not left or not right or " " in left or " " in right
            or _CORPORATE_RE.search(left) or _CORPORATE_RE.search(right)):
        return False
    return (min(len(left), len(right)) >= 5
            and JaroWinkler.similarity(left, right) >= 0.91)


def _author_overlap(claimed: ClaimedRef, resolved: RetrievedRecord) -> bool:
    ca = {_name(a) for a in claimed.authors if _name(a)}
    ra = {_name(a) for a in resolved.authors if _name(a)}
    if ca & ra:
        return True
    return first_author_equivalent(claimed, resolved)


def journal_equivalent(left: str, right: str) -> bool:
    """Conservative ISO-abbreviation/transliteration approximation."""
    left, right = canonical_title(left), canonical_title(right)
    if not left or not right:
        return False
    if left == right:
        return True
    if (min(len(left), len(right)) >= 6 and len(left.split()) >= 2
            and len(right.split()) >= 2 and (left in right or right in left)):
        return True
    drop = {"j", "the", "of", "and", "suppl", "supplement"}
    a = [t for t in left.split() if t not in drop]
    b = [t for t in right.split() if t not in drop]
    if not a or not b:
        return False

    def token_match(x: str, y: str) -> bool:
        if x == y:
            return True
        if min(len(x), len(y)) >= 4 and (x.startswith(y) or y.startswith(x)):
            return True
        return min(len(x), len(y)) >= 5 and JaroWinkler.similarity(x, y) >= 0.88

    smaller, larger = (a, b) if len(a) <= len(b) else (b, a)
    return all(any(token_match(x, y) for y in larger) for x in smaller)


def _near_transliteration(left: str, right: str) -> bool:
    """Fuzzy spelling evidence used only inside an explicit translation rule."""
    left, right = canonical_title(left), canonical_title(right)
    return (min(len(left), len(right)) >= 8
            and JaroWinkler.similarity(left, right) >= 0.84)


def _doi_match(claimed: ClaimedRef, resolved: RetrievedRecord) -> bool:
    return doi_equivalent(claimed.claimed_doi, resolved.doi)


def _locator_match(claimed: ClaimedRef, resolved: RetrievedRecord) -> bool:
    volumes = _locator(claimed.volume), _locator(resolved.volume)
    if all(volumes) and volumes[0] != volumes[1]:
        return False
    pages = _first_page(claimed.pages), _first_page(resolved.pages)
    return bool(all(pages) and pages[0] == pages[1])


def _series_conflict(claimed_title: str, resolved_title: str) -> bool:
    a = {x.lower() for x in _ROMAN_RE.findall(claimed_title or "")}
    b = {x.lower() for x in _ROMAN_RE.findall(resolved_title or "")}
    return bool(a and b and a != b)


def _derivative_block(claimed: ClaimedRef, resolved: RetrievedRecord) -> str:
    ct, rt = canonical_title(claimed.title), canonical_title(resolved.title)
    if ct != rt and _DERIVATIVE_RE.search(resolved.title or ""):
        return "derivative_publication"
    if ct != rt and _DERIVATIVE_RE.search(claimed.title or ""):
        return "derivative_publication"
    if _series_conflict(claimed.title, resolved.title):
        return "series_ordinal_conflict"
    left_author = _first_author_value(claimed.authors)
    right_author = _first_author_value(resolved.authors)
    if (left_author and right_author and left_author != right_author
            and left_author not in right_author and right_author not in left_author
            and _CORPORATE_RE.search(left_author)
            and _CORPORATE_RE.search(right_author)):
        return "corporate_author_conflict"
    ct, rt = canonical_title(claimed.title), canonical_title(resolved.title)
    return ""


def _outside_embedded(container: str, embedded: str) -> str:
    i = container.find(embedded)
    return "" if i < 0 else (container[:i] + " " + container[i + len(embedded):]).strip()


def _wrapper_grammar(text: str) -> bool:
    return bool(re.search(r"\bet\s+al\b|\b(?:19|20)\d{2}\b\s*[;,:]\s*\d+|"
                          r"\bdoi\b|\bpmid\b", text or "", re.I))


def _section_heading_wrapper(outside: str) -> bool:
    """Known JATS section-heading leakage, not generic subtitle containment."""
    value = canonical_title(outside)
    return "special focus" in value and "background" in value


def _embedded_metadata_count(claimed: ClaimedRef,
                             resolved: RetrievedRecord) -> int:
    """Independent resolved-record fields visibly embedded in a title wrapper."""
    haystack = canonical_title((claimed.title or "") + " " + (claimed.raw or ""))
    if not haystack:
        return 0
    signals = 0
    first = _first_author_value(resolved.authors)
    if first and first not in _AUTHOR_SUFFIX_ONLY and first in haystack:
        signals += 1
    if resolved.year and str(resolved.year) in (claimed.title or "") + " " + (claimed.raw or ""):
        signals += 1
    journal = canonical_title(resolved.journal)
    if len(journal) >= 8 and journal in haystack:
        signals += 1
    if resolved.volume and re.search(rf"\b{re.escape(str(resolved.volume))}\b", haystack):
        signals += 1
    first_page = _first_page(resolved.pages)
    if first_page and re.search(rf"\b{re.escape(first_page)}\b", haystack):
        signals += 1
    return signals


def _distinctive_shared_tokens(left: str, right: str) -> set[str]:
    return ({t for t in canonical_title(left).split() if len(t) >= 4 and t not in _STOP}
            & {t for t in canonical_title(right).split() if len(t) >= 4 and t not in _STOP})


def _one_token_typo(left: str, right: str) -> bool:
    a, b = canonical_title(left).split(), canonical_title(right).split()
    if len(a) != len(b):
        return False
    diffs = [(x, y) for x, y in zip(a, b) if x != y]
    return (len(diffs) == 1 and min(len(diffs[0][0]), len(diffs[0][1])) >= 5
            and JaroWinkler.similarity(*diffs[0]) >= 0.75)


def _adjacent_year_transposition(left: int | None, right: int | None) -> bool:
    if not left or not right:
        return False
    a, b = str(left), str(right)
    if len(a) != 4 or len(b) != 4:
        return False
    diff = [i for i, (x, y) in enumerate(zip(a, b)) if x != y]
    return (len(diff) == 2 and diff[1] == diff[0] + 1
            and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]])


def assess_same_work(claimed: ClaimedRef, resolved: RetrievedRecord, *,
                     title_similarity: float) -> WorkIdentityEvidence:
    """Return a proof-backed same-work/ambiguous-family reason, if one exists."""
    ct, rt = canonical_title(claimed.title), canonical_title(resolved.title)
    if not ct or not rt:
        return WorkIdentityEvidence(False)

    blocked = _derivative_block(claimed, resolved)
    if blocked:
        return WorkIdentityEvidence(False, blocked_by=blocked)
    first_author = first_author_equivalent(claimed, resolved)
    first_author_typo = _first_author_typo(claimed, resolved)
    author_overlap = _author_overlap(claimed, resolved)
    journal = journal_equivalent(claimed.journal, resolved.journal)
    doi = _doi_match(claimed, resolved)
    locator = _locator_match(claimed, resolved)
    same_year = bool(claimed.year and resolved.year and claimed.year == resolved.year)

    # Authoritative alternate/vernacular title retained from MEDLINE TT.
    for alternate in resolved.alternate_titles:
        if ct == canonical_title(alternate):
            return WorkIdentityEvidence(True, "authoritative_title_alias",
                                        ("medline_alternate_title",))

    # Exact canonical title (including Greek-letter and chemical typography).
    if (ct == rt and ct not in _GENERIC_TITLES
            and (first_author or doi or locator or (same_year and journal))):
        return WorkIdentityEvidence(True, "canonical_title_exact",
                                    tuple(s for s, ok in (("first_author", first_author),
                                                         ("doi", doi), ("locator", locator),
                                                         ("year", same_year)) if ok))

    # Resolved title embedded in a malformed/wrapped claimed title.  Direction is
    # load-bearing: reverse containment includes original->commentary F2s.
    if len(rt) >= 28 and rt in ct and len(ct) - len(rt) >= 5:
        outside = _outside_embedded(ct, rt)
        embedded_metadata = _embedded_metadata_count(claimed, resolved)
        citation_wrapper = (_wrapper_grammar(claimed.title)
                            and embedded_metadata >= 2)
        section_wrapper = (_section_heading_wrapper(outside)
                           and first_author and journal)
        if (not _DERIVATIVE_RE.search(outside)
                and (citation_wrapper or section_wrapper)):
            return WorkIdentityEvidence(True, "malformed_title_wrapper",
                                        tuple(s for s, ok in (
                                            ("citation_wrapper", citation_wrapper),
                                            ("section_heading_wrapper", section_wrapper),
                                            ("embedded_metadata>=2", embedded_metadata >= 2),
                                            ("first_author", first_author),
                                            ("journal", journal)) if ok))
        if _wrapper_grammar(claimed.title):
            return WorkIdentityEvidence(False, blocked_by="uncorroborated_title_wrapper")

    # PubMed's brackets mark an English translation.  The lexical floor is not a
    # replacement gate: exact year plus venue/author corroboration is mandatory.
    raw_resolved = (resolved.title or "").strip()
    if (raw_resolved.startswith("[") and raw_resolved.rstrip(".").endswith("]")
            and title_similarity >= 0.80 and same_year
            and (journal or first_author
                 or (first_author_typo
                     and _near_transliteration(claimed.journal, resolved.journal)))):
        transliterated_metadata = first_author_typo and not (journal or first_author)
        return WorkIdentityEvidence(True, "translated_title_metadata",
                                    ("pubmed_translated_title", "year",
                                     "transliterated_author_and_venue"
                                     if transliterated_metadata
                                     else ("journal" if journal else "first_author")))

    # Some JATS producers remove PubMed's square brackets from a translated
    # title.  Preserve a narrow fallback for visibly non-English citations:
    # same year and venue plus several distinctive, language-stable anchors
    # (usually an institution or place name).  This is intentionally not a
    # generic same-year/same-journal similarity override.
    if (any(ord(ch) > 127 for ch in (claimed.title or "")) and same_year and journal
            and title_similarity >= 0.82
            and len(_distinctive_shared_tokens(claimed.title, resolved.title)) >= 3):
        return WorkIdentityEvidence(True, "translated_title_shared_anchors",
                                    ("non_ascii_claimed_title", "year", "journal",
                                     "three_title_anchors"))

    # Explicit correction notices are related records, not autonomous F2 calls.
    if (_CORRECTION_RE.match(resolved.title or "") and title_similarity >= 0.80
            and author_overlap):
        return WorkIdentityEvidence(True, "correction_notice",
                                    ("correction_prefix", "author_overlap"))

    # A genuine title stem/numbered part with exact year and strong metadata.
    if (len(ct) >= 24 and ct in rt and (first_author or first_author_typo)
            and journal and same_year):
        return WorkIdentityEvidence(True, "title_stem_same_issue",
                                    ("first_author" if first_author else "first_author_typo",
                                     "journal", "year"))

    # Omitted corporate prefix on an institutional position/guideline title.
    if (len(ct) >= 28 and ct in rt and _DOCUMENT_RE.search(claimed.title or "")
            and _CORPORATE_RE.search(_outside_embedded(rt, ct))
            and (doi or title_similarity >= 0.65)):
        return WorkIdentityEvidence(True, "corporate_title_prefix",
                                    ("institutional_document", "corporate_prefix"))

    # Living NCBI chapters are revised in place and may be renamed between dates.
    source = canonical_title(claimed.journal + " " + resolved.journal)
    gap = abs((claimed.year or 0) - (resolved.year or 0)) if claimed.year and resolved.year else 99
    if (any(token in source for token in _LIVING_SOURCES) and gap <= 3
            and ((first_author and len(ct.split()) >= 2 and ct in rt) or ct == rt)):
        return WorkIdentityEvidence(True, "living_chapter_revision",
                                    ("living_source",
                                     "first_author" if first_author else "exact_title",
                                     "title_containment"))

    # Institutional declarations/guidelines with the same venue and several
    # distinctive anchors are edition-family ambiguity, not HIGH-confidence F2.
    shared = _distinctive_shared_tokens(claimed.title, resolved.title)
    resolved_corporate = any(_CORPORATE_RE.search(_name(a))
                             for a in resolved.authors)
    if (re.search(r"\bdeclaration\b", claimed.title or "", re.I)
            and re.search(r"\bdeclaration\b", resolved.title or "", re.I)
            and resolved_corporate and journal and title_similarity >= 0.70
            and len(shared) >= 4):
        return WorkIdentityEvidence(True, "corporate_declaration_edition",
                                    ("declaration", "resolved_corporate_author",
                                     "journal", "four_title_anchors"))
    if (_DOCUMENT_RE.search(claimed.title or "") and _DOCUMENT_RE.search(resolved.title or "")
            and journal and title_similarity >= 0.70 and len(shared) >= 3
            and (doi or locator or author_overlap or same_year)):
        return WorkIdentityEvidence(True, "institutional_document_revision",
                                    ("document_type", "journal", "title_anchors"))

    # A single likely transcription error, gated by first-author and venue.  This
    # quarantines for review; it does not auto-clear the citation.
    if (_one_token_typo(claimed.title, resolved.title) and first_author and journal
            and (locator or _adjacent_year_transposition(claimed.year, resolved.year))):
        return WorkIdentityEvidence(True, "single_token_metadata_typo",
                                    ("first_author", "journal",
                                     "locator" if locator else "year_transposition"))

    return WorkIdentityEvidence(False)
