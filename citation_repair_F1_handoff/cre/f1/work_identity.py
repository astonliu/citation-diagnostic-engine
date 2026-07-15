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
# A 4-digit publication/edition year embedded in a title -- used to detect serial
# annual editions (e.g. "...Statistics-2017 Update" vs "...-2019 Update").
_TITLE_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
_STOP = {
    "a", "an", "and", "as", "at", "by", "for", "from", "in", "of", "on",
    "or", "the", "to", "with", "paper", "study", "analysis", "medical",
}
_GENERIC_TITLES = {
    "introduction", "editorial", "preface", "foreword", "conclusion",
    "discussion", "letter",
}

# =====================================================================
# F2 wrong-paper-precision redesign (2026-07-14): version-family same-work
# signatures that were mis-banding as review_wrong_paper on seed 29. Each is a
# GENERAL, auditable evidence rule (no PMID/title memorization). All route to
# review_same_work_variant (human review), never to an auto-clear outcome.
# =====================================================================
# A supplement / poster locator: PubMed pages for meeting abstracts start with
# "S" (supplement, e.g. S39, S39-S40) or a poster/abstract letter+number
# (e.g. P1025). An "e"-locator ("e171-232") is an ordinary electronic article
# and is deliberately NOT matched.
_SUPPLEMENT_PAGE_RE = re.compile(r"^\s*[SP]\d", re.I)
# Editorial reprint/republication prefixes a journal prepends to a re-run of an
# earlier work (e.g. J Immunol "Pillars Article:"; "Classic Article",
# "Reprinted from"). General series markers, not one journal's brand.
_REPRINT_TITLE_RE = re.compile(
    r"^\s*(?:pillars?|classic(?:al)?|landmark|seminal)\s+(?:article|paper)\b"
    r"|^\s*reprint(?:ed)?\b|\breprinted\s+from\b|\brepublished\b", re.I)
# MEDLINE publication types that mark a record as a reprint of an earlier work.
_REPRINT_PUBTYPES = {"republished article", "reprint", "classical article"}

# Title-similarity floors for the new same-work rules (kept SEPARATE from and
# never below the 0.92 near-identical-title gate in biblio_match).
DOI_SAME_WORK_TITLE_MIN = 0.92          # exact DOI+author only overrides the block at near-identical titles
CONFERENCE_ABSTRACT_TITLE_MIN = 0.87    # abstract -> full publication of same study
TRANSLATION_TRANSLIT_TITLE_MIN = 0.85   # translation whose metadata is transliterated
# Fraction of the abstract's author roster that must reappear on the resolved full
# paper for RULE B (an abstract->full is one team; two different trials share only
# some serial co-authors).
CONFERENCE_ROSTER_CONTAINMENT_MIN = 0.75
# Fraction of the abstract title's distinctive tokens that must reappear in the
# resolved full-paper title for RULE B. A same-study abstract->full carries nearly
# all of the abstract's content; sibling trials sharing a drug/disease template
# diverge on the key population/endpoint qualifier ("mildly preserved" vs
# "reduced"), dropping coverage. Second guard beyond roster containment, needed
# because serial trialists (e.g. the dapagliflozin CV program) put the SAME core
# team on genuinely different trials.
CONFERENCE_ABSTRACT_CONTENT_COVERAGE_MIN = 0.77
# Minimum distinctive-token count for the claimed (abstract) title in RULE B. A
# short generic title ("Dapagliflozin in heart failure") cannot uniquely identify a
# study inside a drug's trial family -- its few tokens are trivially covered by ANY
# sibling trial's full title, so it must not be read as an abstract->full match.
CONFERENCE_ABSTRACT_MIN_DISTINCTIVE_TOKENS = 6
# Fraction of the resolved title's distinctive tokens that must be reconstructable
# from the claimed author+title fields for RULE E (a true shifted-field artifact
# splits ONE title across the two slots; a consortium author whose name merely
# appears in a different paper's title does not).
SHIFTED_TITLE_COVERAGE_MIN = 0.85


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
    if a and b and a != b:
        return True
    # Serial annual/periodic editions differ only by an embedded 4-digit year
    # ("...Statistics-2017 Update" vs "...-2019 Update"; "Standards of Care-2019"
    # vs "-2021"). Distinct papers in the same series -- NOT the same work -- even
    # when title_sim ~1.0, the first author is identical, and a DOI was mis-attached
    # across editions. Fires only when both titles carry a year and they share NONE.
    ya = set(_TITLE_YEAR_RE.findall(claimed_title or ""))
    yb = set(_TITLE_YEAR_RE.findall(resolved_title or ""))
    return bool(ya and yb and not (ya & yb))


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


def _volume_agrees(claimed: ClaimedRef, resolved: RetrievedRecord) -> bool:
    """Both volumes present and equal after stripping to digits."""
    cv, rv = re.sub(r"\D", "", claimed.volume or ""), re.sub(r"\D", "", resolved.volume or "")
    return bool(cv and rv and cv == rv)


def _year_within_one(claimed: ClaimedRef, resolved: RetrievedRecord) -> bool:
    if not (claimed.year and resolved.year):
        return False
    return abs(int(claimed.year) - int(resolved.year)) <= 1


def _pubmed_bracketed(title: str) -> bool:
    """PubMed brackets a translated (non-English original) title: '[...]'."""
    t = (title or "").strip()
    return t.startswith("[") and t.rstrip(".").endswith("]")


def _is_supplement_locator(pages: str) -> bool:
    return bool(_SUPPLEMENT_PAGE_RE.match(pages or ""))


def _is_reprint_record(resolved: RetrievedRecord) -> bool:
    """The resolved record is an editorial reprint/republication of an earlier
    work: a reprint title prefix OR a MEDLINE reprint publication type."""
    if _REPRINT_TITLE_RE.search(resolved.title or ""):
        return True
    return bool({p.lower() for p in (resolved.publication_types or [])}
                & _REPRINT_PUBTYPES)


def _author_field_holds_title(claimed: ClaimedRef, resolved: RetrievedRecord) -> bool:
    """Shifted-field parser artifact: the claimed AUTHOR slot actually holds
    article-title text (long, multi-word, non-corporate) that is contained in the
    resolved title -- the citation's title was split across the author and title
    fields. A real surname is short and does not appear inside the resolved title.
    """
    if not claimed.authors:
        return False
    a0 = canonical_title(claimed.authors[0])
    if not a0 or _CORPORATE_RE.search(a0):
        return False
    if len(a0) < 20 or len(a0.split()) < 4:
        return False
    return a0 in canonical_title(resolved.title)


def _shifted_title_coverage(claimed: ClaimedRef, resolved: RetrievedRecord) -> float:
    """Fraction of the resolved title's DISTINCTIVE tokens reconstructable from the
    union of the claimed author-slot and title-slot text. A genuine shifted-field
    artifact splits ONE title across the two slots, so coverage is ~1.0. A
    consortium author whose group name merely appears in a DIFFERENT paper's title
    (ADNI/MESA/TCGA cohort papers) covers only the group-name tokens, not the rest
    of the resolved title -- coverage stays well below 1.0."""
    rt_tokens = {t for t in canonical_title(resolved.title).split()
                 if len(t) >= 4 and t not in _STOP}
    if not rt_tokens:
        return 0.0
    have = set(canonical_title(
        (claimed.authors[0] if claimed.authors else "") + " "
        + (claimed.title or "")).split())
    return sum(1 for t in rt_tokens if t in have) / len(rt_tokens)


def _surname_set(authors: list[str]) -> set[str]:
    """Surname-proxy tokens for an author roster (drops given-name initials)."""
    out: set[str] = set()
    for a in authors or []:
        for t in canonical_title(a).split():
            if len(t) >= 4 and not re.fullmatch(r"[a-z]{1,3}", t):
                out.add(t)
    return out


def _roster_containment(claimed: ClaimedRef, resolved: RetrievedRecord) -> float:
    """Fraction of the claimed author roster present on the resolved roster. A
    conference abstract and its later full publication are one team, so the
    abstract's authors nearly all reappear (containment ~1.0); two DIFFERENT
    trials that happen to share serial co-authors overlap only partially."""
    ca, ra = _surname_set(claimed.authors), _surname_set(resolved.authors)
    if not ca or not ra:
        return 0.0
    return len(ca & ra) / len(ca)


def _distinctive_title_tokens(title: str) -> set[str]:
    return {t for t in canonical_title(title).split() if len(t) >= 4 and t not in _STOP}


def _abstract_content_coverage(claimed: ClaimedRef,
                               resolved: RetrievedRecord) -> float:
    """Fraction of the claimed (abstract) title's DISTINCTIVE tokens present in the
    resolved (full-paper) title. A conference abstract and its later full
    publication describe the SAME study, so the full title carries nearly all of
    the abstract's content; two different trials sharing a drug/disease template
    diverge on the key population/endpoint qualifier, dropping coverage."""
    a = _distinctive_title_tokens(claimed.title)
    if not a:
        return 0.0
    r = set(canonical_title(resolved.title).split())
    return sum(1 for t in a if t in r) / len(a)


def _reprint_recites_original(claimed: ClaimedRef, resolved: RetrievedRecord) -> bool:
    """The resolved (reprint) title recites the CLAIMED citation's own original
    year and volume/first-page -- i.e. it reproduces the original publication's
    citation string, proving it is a re-run of that same work. A different paper
    that merely carries a reprint marker does not embed the claimed year+locator.
    """
    title = resolved.title or ""
    if not claimed.year or str(claimed.year) not in title:
        return False
    vol = re.sub(r"\D", "", claimed.volume or "")
    fp = re.sub(r"\D", "", _first_page(claimed.pages))
    has_vol = bool(vol and re.search(rf"\b{re.escape(vol)}\b", title))
    has_pg = bool(fp and re.search(rf"\b{re.escape(fp)}\b", title))
    return has_vol or has_pg


def assess_same_work(claimed: ClaimedRef, resolved: RetrievedRecord, *,
                     title_similarity: float) -> WorkIdentityEvidence:
    """Return a proof-backed same-work/ambiguous-family reason, if one exists."""
    ct, rt = canonical_title(claimed.title), canonical_title(resolved.title)
    if not ct or not rt:
        return WorkIdentityEvidence(False)

    # RULE A (exact shared DOI). A DOI is a globally unique work identifier: an
    # exact match on it, with the first-author POSITION agreeing and a
    # NEAR-IDENTICAL title, lets a DOI-proven same-work override the
    # derivative-review block (seed 29: 33624016 -- both titles say "meta-analysis"
    # but the shared DOI proves it is the SAME meta-analysis). Two hardening guards
    # (found by adversarial review): the near-identical-title floor keeps a
    # common-surname run-on collision out ("Wang L" sepsis vs "Wang Y" AKI sharing
    # a mis-attached DOI, title_sim 0.90), and it must NOT override a research-SERIES
    # ordinal conflict (Part I vs Part II sharing a run-on DOI) -- those stay
    # wrong-paper. Report-vs-article / run-on DOI carriers (14741909, 34249371,
    # 33036834) fail the first-author check.
    if (doi_equivalent(claimed.claimed_doi, resolved.doi)
            and first_author_equivalent(claimed, resolved)
            and title_similarity >= DOI_SAME_WORK_TITLE_MIN
            and not _series_conflict(claimed.title, resolved.title)):
        return WorkIdentityEvidence(True, "shared_doi_same_work",
                                    ("exact_doi", "first_author",
                                     "title_sim>=%.2f" % DOI_SAME_WORK_TITLE_MIN))

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

    # RULE D (translation whose corroborating metadata is itself transliterated).
    # A PubMed-BRACKETED resolved title (the authoritative translated-title marker),
    # same year, high title similarity, corroborated by a transliterated
    # first-author surname AND a matching journal volume. The standard
    # journal_equivalent / first_author checks miss this because the venue and
    # surname differ only by transliteration (seed 29: 12500577 --
    # Biophysics/Biofizika, Yurkevich/Iurkevich, vol 47). The bracket requirement is
    # load-bearing (adversarial review): a bare non-English tag would fire on two
    # DIFFERENT same-journal/same-volume Russian papers with transliteration-similar
    # surnames (Grigorev/Grigorov, Nikolaev/Nikolaenko) -- those stay wrong-paper.
    if (_pubmed_bracketed(resolved.title)
            and same_year and title_similarity >= TRANSLATION_TRANSLIT_TITLE_MIN
            and _first_author_typo(claimed, resolved)
            and _volume_agrees(claimed, resolved)):
        return WorkIdentityEvidence(True, "translated_title_transliterated_author",
                                    ("pubmed_translated_title", "year",
                                     "transliterated_first_author", "volume"))

    # RULE C (historical republication / reprint). The resolved record is an
    # editorial re-run of an earlier work (reprint title prefix such as "Pillars
    # Article:" or a MEDLINE reprint publication type); the claimed original title
    # is contained within it and the first author agrees (seed 29: 26297790). The
    # reprint marker is load-bearing -- containment + author alone is a real
    # different-paper F2 (Zimet 2280326), so it is NOT sufficient on its own. Also
    # load-bearing (adversarial review): the resolved title must RECITE the claimed
    # citation's original year + volume/first-page, proving it re-runs THAT work.
    # Without this, a longer different paper whose title merely contains the claimed
    # title and carries a reprint word ("Classic Article: X in human cancer",
    # "Reprinted from Nature: X in chronic kidney disease") would be swallowed.
    if (_is_reprint_record(resolved) and len(ct) >= 18 and ct in rt
            and len(rt) - len(ct) >= 5 and first_author_equivalent(claimed, resolved)
            and _reprint_recites_original(claimed, resolved)):
        return WorkIdentityEvidence(True, "historical_republication",
                                    ("reprint_marker", "title_containment",
                                     "first_author", "recites_original_citation"))

    # RULE B (conference/supplement abstract -> full publication of the same
    # study). The claimed citation is a meeting abstract (supplement/poster page
    # locator, e.g. S39 / P1025), MOST of its author roster reappears on the
    # resolved full paper, and the abstract title is highly similar to the resolved
    # full-paper title (seed 29: 33551622, 33244148, 17261567). Two guards keep
    # DIFFERENT trials out: the 0.87 floor excludes a different endpoint (33148016,
    # title_sim 0.85), and ROSTER CONTAINMENT (>=0.75, not mere overlap) excludes
    # sibling trials that share only serial co-authors -- adversarial review showed
    # bare overlap swallows DAPA-HF vs DAPA-CKD/DELIVER and the rivaroxaban trials
    # (author overlap 0.25-0.60). Round-2 review then showed serial trialists put
    # the SAME core team on genuinely different trials (DELIVER vs DAPA-HF cited
    # "first-3 et al." -> roster containment 1.0), so a SECOND guard requires the
    # resolved full title to carry most of the abstract's distinctive content
    # (>=0.77); sibling trials diverge on the population/endpoint qualifier. The
    # page-locator never fires on a numeric/e-page. A minimum distinctive-token
    # count keeps a SHORT generic abstract title ("Dapagliflozin in heart failure")
    # out -- its few tokens are trivially covered by any sibling trial's full title
    # (the coverage guard is necessarily asymmetric, since a genuine full paper adds
    # a subtitle), so specificity is required to disambiguate a drug's trial family.
    if (_is_supplement_locator(claimed.pages)
            and len(_distinctive_title_tokens(claimed.title)) >= CONFERENCE_ABSTRACT_MIN_DISTINCTIVE_TOKENS
            and _roster_containment(claimed, resolved) >= CONFERENCE_ROSTER_CONTAINMENT_MIN
            and _abstract_content_coverage(claimed, resolved) >= CONFERENCE_ABSTRACT_CONTENT_COVERAGE_MIN
            and title_similarity >= CONFERENCE_ABSTRACT_TITLE_MIN):
        return WorkIdentityEvidence(True, "conference_abstract_publication",
                                    ("supplement_locator", "specific_title",
                                     "roster_containment", "abstract_content_coverage",
                                     "title_sim>=%.2f" % CONFERENCE_ABSTRACT_TITLE_MIN))

    # RULE E (shifted-field parser artifact). The claimed AUTHOR slot holds the
    # article title text (contained in the resolved title) while the claimed title
    # holds the REST of that same title, so the resolved title is nearly fully
    # reconstructed from the two claimed slots (coverage >= 0.85), and the year plus
    # journal/volume corroborate the resolved work (seed 29: 15129193). The coverage
    # guard is load-bearing (adversarial review): a consortium/cohort author whose
    # group name appears in a DIFFERENT paper's title (ADNI/MESA/TCGA) covers only
    # the group-name tokens, not the rest of the resolved title -- those stay
    # wrong-paper. This is a parsing defect on the SAME work, not a wrong reference.
    if (_author_field_holds_title(claimed, resolved)
            and _shifted_title_coverage(claimed, resolved) >= SHIFTED_TITLE_COVERAGE_MIN
            and _year_within_one(claimed, resolved)
            and (journal_equivalent(claimed.journal, resolved.journal)
                 or _volume_agrees(claimed, resolved))):
        return WorkIdentityEvidence(True, "shifted_author_title_artifact",
                                    ("author_field_holds_title", "year",
                                     "journal_or_volume"))

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
