"""UNSCOREABLE gate -- exclude non-title / container inputs from F2 scoring.

The F2 screen compares a claimed title against a resolved title. When one side is
NOT a usable title -- a journal name parked in the title slot, a regulatory-code
string, a "[Not Available]" placeholder, or a book/container record cited as a
chapter -- the comparison carries ZERO evidence about whether the identifier
points to the wrong paper. Flagging such a pair as a (potential) F2 is a category
error, and these inputs DOMINATE the flagged pool, crowding out the genuine F2s.

This module classifies those pairs so the pipeline can route them to a named,
COUNTED ``UNSCOREABLE`` bucket -- excluded from both the flagged pool and the F2
numerator, but reported, never silently dropped.

Design rules (all load-bearing):
  * RECALL-SAFE BY CONSTRUCTION. Every signal here keys on *content shape*, never
    on score or field agreement. The genuine-F2 written titles are all real
    article titles, so a shape-keyed gate cannot capture them. When a signal is
    not certain, we return ``None`` (leave the ref in the pool): a false positive
    downstream is cheap; dropping a real wrong-reference is permanent.
  * NO BIDIRECTIONAL CONTAINMENT for ``journal_as_title``. A real article title
    can legitimately *contain* its journal name as a substring (e.g. an F2 whose
    journal "Genetics" is a substring of its title), so containment would drop
    genuine F2s. We use exact normalized equality, a curated masthead authority
    list, and the bilingual self-transliteration ("X = X") masthead pattern only.
  * Only HARD, unambiguous signals exclude. Committee/instrument-name/garbled
    fragments have no recall-safe deterministic signature, so they are left in
    the pool as cheap false positives (a human auditor catches them).
"""
from __future__ import annotations

import re
from typing import Optional

from .schema import ClaimedRef, RetrievedRecord
from .biblio_match import normalize_title
from .journal_identity import resolve_journal_id, JOURNAL_AUTHORITY


def _looks_like_a_title_not_a_journal(s: str) -> bool:
    """Authority-gated signal for title-shaped text in a non-title slot."""
    if len(s.split()) < 6 or JOURNAL_AUTHORITY.is_empty():
        return False
    return resolve_journal_id(s) is None

# Resolved-side titles that are placeholders, not titles (PubMed emits these).
_PLACEHOLDER_TITLES = {
    "", "not available", "no title available", "no title", "in process",
    "untitled", "title not available",
}

# Curated full journal-masthead strings (NORMALIZED) that appear, verbatim, in a
# title slot. Matched by EQUALITY only (never containment), so no entry can
# misfire on a real article title. A trailing parenthetical (e.g. "(PNAS)") is
# stripped before the compare. Extend conservatively; this is the §7.3 knob --
# every entry must be a *full* masthead that is implausible as an article title.
_JOURNAL_MASTHEAD_AUTHORITY = {
    normalize_title("Proceedings of the National Academy of Sciences"),
    normalize_title("Proceedings of the National Academy of Sciences of the "
                    "United States of America"),
    normalize_title("Proc. of the National Academy of Sciences of the United "
                    "States of America"),
    normalize_title("Journal of the American Medical Association"),
    normalize_title("New England Journal of Medicine"),
    normalize_title("Cochrane Database of Systematic Reviews"),
}

# A regulatory / legal-code string sitting in the title slot. Anchored TIGHTLY so
# a real article title cannot match: a CFR "Title NN" must be followed by a
# section separator (':', '.', '-'); "NN CFR"; or a periods-bearing "U.S.C." cite
# (bare "USC" is excluded -- it is a university, not a legal code).
_REGULATORY_RE = re.compile(
    r"^\s*title\s+\d+\s*[:.–—-]"   # "TITLE 45: PUBLIC WELFARE ..." (not "Title 1 diabetes")
    r"|\b\d+\s+cfr\b"                          # "45 CFR 46 ..."
    r"|\bu\.\s*s\.\s*c\.?\b",                  # "U.S.C." with periods (not "USC")
    re.I,
)
_AUTHOR_RESIDUE_RE = re.compile(r"^\s*(?:et\s+al\.?|and\s+colleagues)\s*$", re.I)
# A run of >=2 ASCII letters -- a distinctive lexical (word-shaped) token. Its
# ABSENCE from a normalized title marks a numeric/year/locator-only slot.
_DISTINCTIVE_ALPHA_RE = re.compile(r"[a-z]{2,}")


def _numeric_or_year_only_title(raw: str, normalized: str) -> bool:
    """A year / volume / issue / page-locator number parked in the title slot
    ("2001", "130", "3", "1999-2001"). Such a slot carries ZERO title evidence,
    the same category error as ``journal_as_title`` / ``regulatory_code``, so it
    is not a scoreable title comparison.

    RECALL-SAFE BY CONSTRUCTION (keys only on content shape): fires ONLY when the
    normalized title has no run of >=2 ASCII letters, the raw title carries no
    non-ASCII letter (a real, possibly non-Latin or transliterated title always
    has letters -- e.g. Cyrillic, a bracketed translation), AND at least one digit
    is present (so a stray single letter is never bucketed here). Any title that
    keeps a distinctive word -- "COVID-19 outcomes", "p53 signaling", "IL-6",
    "The 2019 revision..." -- retains an alphabetic token and stays scoreable.
    """
    if _DISTINCTIVE_ALPHA_RE.search(normalized):
        return False
    if any(ch.isalpha() and ord(ch) > 127 for ch in (raw or "")):
        return False
    return any(ch.isdigit() for ch in normalized)


def _despace(s: str) -> str:
    return s.replace(" ", "")


def _strip_trailing_paren(s: str) -> str:
    """Drop a single trailing '(...)' (e.g. a '(PNAS)' abbreviation gloss)."""
    return re.sub(r"\s*\([^)]*\)\s*$", "", s).strip()


def _is_bilingual_masthead(title: str) -> bool:
    """A bilingual journal masthead of the form 'Vernacular = Romanization'
    (e.g. 'Zhongguo Zhong yao za zhi = Zhongguo zhongyao zazhi'), where the two
    sides are the SAME name. Detected by splitting on '=' and finding two parts
    that are EQUAL once spacing is removed.

    Equality only -- NEVER substring containment. A real article title can
    legitimately gloss a term with '=' where one side is a substring of the
    other ('Genetics = Genetics of cancer susceptibility'); only space-insensitive
    EQUALITY of two halves marks a transliterated masthead, and a genuine title
    does not carry two identical halves around '='."""
    if "=" not in title:
        return False
    parts = [_despace(normalize_title(p)) for p in title.split("=")]
    parts = [p for p in parts if len(p) >= 6]
    for i in range(len(parts)):
        for j in range(i + 1, len(parts)):
            if parts[i] == parts[j]:
                return True
    return False


def _ordered_journal_abbreviation(left: str, right: str) -> bool:
    """Tight token-order match for a full masthead versus its abbreviation."""
    drop = {"the", "of", "and"}
    a = [t for t in normalize_title(left).split() if t not in drop]
    b = [t for t in normalize_title(right).split() if t not in drop]
    if len(a) < 3 or len(a) != len(b):
        return False
    return all(x == y or (x.startswith(y) or y.startswith(x))
               for x, y in zip(a, b))


def _is_journal_author_residue(title: str,
                               resolved: Optional[RetrievedRecord]) -> bool:
    """A masthead plus author surname accidentally parsed as an article title.

    Example: ``Royal Soc Open Science Titelboim``. The rule requires both the
    resolved journal's ordered token signature and the resolved first author at
    the end, so a real title that merely mentions a journal is not excluded.
    """
    if resolved is None or not resolved.authors or not resolved.journal:
        return False
    ntitle = normalize_title(title)
    author = normalize_title(resolved.authors[0])
    if not ntitle or not author:
        return False
    aliases = sorted({author, author.split()[0], author.split()[-1]},
                     key=len, reverse=True)
    for alias in aliases:
        suffix = " " + alias
        if not ntitle.endswith(suffix):
            continue
        masthead = ntitle[:-len(suffix)].strip()
        if _ordered_journal_abbreviation(masthead, resolved.journal):
            return True
    return False


#: JATS `publication-type` values naming something that is not a research
#: article. A claim attributed to a database, a website, a report or a book is
#: outside the F1-F8 taxonomy's scope. Calling such a reference `unverifiable`
#: MISREPORTS it: `unverifiable` implies a paper-identity check was attempted and
#: could not be completed, when in fact no paper identity was ever in question.
#:
#: `confproc` is in this set for the EMPTY-IDENTITY case ONLY (see
#: :func:`is_non_article_reference`). A conference paper with a DOI or a title is
#: a real work making a real claim and keeps its normal path -- 18 of the natural
#: run's `confproc` references have an identity, and one of them resolves cleanly
#: to 10.15607/rss.2021.xvii.089.
NON_ARTICLE_PUBLICATION_TYPES = frozenset({
    "book", "webpage", "other", "confproc"})

#: The bucket name. Registered in `reason_registry.UNSCOREABLE_BUCKETS`.
NON_ARTICLE_REFERENCE = "non_article_reference"

#: The F8 timing boundary could not be resolved after a bounded retry -- a date
#: PubMed did not return, not a judgment anyone withheld. A named, counted
#: exclusion rather than a human-queue item, because no adjudicator can supply
#: the missing boundary either.
F8_TIMING_BOUNDARY_UNRESOLVED = "f8_timing_boundary_unresolved"

#: The claimed title was searched and matched nothing strong enough to settle the
#: identity. A genuine semantic uncertainty ABOUT IDENTITY -- a trade-proceedings
#: abstract can be real and indexed nowhere -- and not a question a human
#: adjudicator can answer from a source the pipeline did not already query.
IDENTITY_UNRESOLVED_AFTER_TITLE_SEARCH = "identity_unresolved_after_title_search"


def is_non_article_reference(claimed: ClaimedRef) -> bool:
    """True for a non-article reference carrying NO identity of any kind.

    BOTH conditions, and the conjunction is the whole safety of the rule. The
    publication type alone would exclude every book chapter and conference paper
    the pipeline can actually resolve; the empty identity alone would exclude a
    badly-parsed journal article that deserves a real lookup. Together they name
    exactly the reference that is neither a research article NOR resolvable:
    a cancer-statistics database, a CDC tool, an industry blog, a think-tank
    report, a book.

    Deterministic and offline -- it reads the publisher's own attribute and three
    empty strings, so it runs BEFORE any network lookup and costs nothing.
    """
    if (claimed.publication_type or "").strip().casefold() \
            not in NON_ARTICLE_PUBLICATION_TYPES:
        return False
    return not any((
        (claimed.claimed_pmid or "").strip(),
        (claimed.claimed_doi or "").strip(),
        (claimed.title or "").strip(),
    ))


def classify_unscoreable(claimed: ClaimedRef,
                         resolved: Optional[RetrievedRecord] = None
                         ) -> tuple[Optional[str], str]:
    """Return ``(bucket, reason)`` if this pair is not a scoreable title
    comparison, else ``(None, "")``.

    Resolved-side signals only apply to a resolved record; claimed-side signals
    always apply. Checks are ordered most- to least- structural and return on the
    first hit. Conservative: any uncertainty yields ``(None, "")``.
    """
    # --- scope, before identity ---------------------------------------------
    # Checked first because it is the most structural fact available: this is not
    # a research article and carries nothing to look one up by, so no title
    # comparison is even defined for it.
    if is_non_article_reference(claimed):
        return (NON_ARTICLE_REFERENCE,
                f"reference is a {claimed.publication_type or 'non-article'} "
                "citation with no PMID, DOI or title; outside the taxonomy's "
                "scope (not a research article), not a failed identity check.")

    ct = (claimed.title or "")
    nct = normalize_title(ct)

    # --- resolved side (only when we actually resolved a record) -------------
    if resolved is not None and getattr(resolved, "resolved", False):
        if resolved.is_container:
            return ("resolved_book_container",
                    "claimed PMID resolves to a book/container record, not the "
                    "cited chapter; title comparison is not meaningful.")
        if normalize_title(resolved.title or "") in _PLACEHOLDER_TITLES:
            return ("resolved_no_title",
                    f"resolved record has no usable title "
                    f"({resolved.title!r}); nothing to compare against.")

    # --- claimed side --------------------------------------------------------
    if not nct:
        if _looks_like_a_title_not_a_journal(claimed.journal or ""):
            return ("field_transposition_journal_holds_title",
                    "claimed title is empty and written_journal holds title-shaped "
                    "text that resolves to no known serial; re-parse and re-score.")
        a0 = claimed.authors[0] if claimed.authors else ""
        if _looks_like_a_title_not_a_journal(a0):
            return ("field_transposition_authors_hold_title",
                    "claimed title is empty and written_authors[0] holds "
                    "title-shaped text; re-parse and re-score.")
        # No claimed title at all -> nothing to score (caller may also handle
        # this, but naming it keeps the bucket honest).
        return ("no_claimed_title", "claimed reference has no title to compare.")

    if _AUTHOR_RESIDUE_RE.fullmatch(ct):
        return ("author_residue_as_title",
                "claimed title contains only an author-list residue, not an "
                "article title.")

    # journal name parked in the title slot
    nj = normalize_title(claimed.journal or "")
    if nj and nct == nj:
        return ("journal_as_title",
                "claimed title is identical to the claimed journal name; the "
                "title slot holds a journal name, not an article title.")
    if (nct in _JOURNAL_MASTHEAD_AUTHORITY
            or normalize_title(_strip_trailing_paren(ct)) in _JOURNAL_MASTHEAD_AUTHORITY):
        return ("journal_as_title",
                "claimed title is a known journal masthead, not an article title.")
    if _is_bilingual_masthead(ct):
        return ("journal_as_title",
                "claimed title is a bilingual journal masthead "
                "('Vernacular = Romanization'), not an article title.")
    if _is_journal_author_residue(ct, resolved):
        return ("journal_author_residue_as_title",
                "claimed title contains only a journal masthead plus an author "
                "surname, not an article title.")

    if (nct and " " not in nct and len(nct) >= 8
            and not any(ch.isdigit() for ch in nct)
            and _DISTINCTIVE_ALPHA_RE.search(nct)):
        return ("single_word_title",
                "claimed title is a single long word (a bare container / field / "
                "genre name), not a searchable article title; not judgeable "
                "offline.")

    # a bare number (year / volume / issue / locator) parked in the title slot
    if _numeric_or_year_only_title(ct, nct):
        return ("numeric_or_year_only_title",
                "claimed title has no distinctive lexical content -- only a "
                "number (year / volume / issue / page locator) sits in the title "
                "slot, so it bears zero title evidence.")

    # regulatory / legal code in the title slot
    if _REGULATORY_RE.search(ct):
        return ("regulatory_code",
                "claimed title is a regulatory/legal-code string, not an "
                "article title.")

    return (None, "")
