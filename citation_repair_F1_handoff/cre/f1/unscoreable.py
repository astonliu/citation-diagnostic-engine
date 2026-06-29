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


def classify_unscoreable(claimed: ClaimedRef,
                         resolved: Optional[RetrievedRecord] = None
                         ) -> tuple[Optional[str], str]:
    """Return ``(bucket, reason)`` if this pair is not a scoreable title
    comparison, else ``(None, "")``.

    Resolved-side signals only apply to a resolved record; claimed-side signals
    always apply. Checks are ordered most- to least- structural and return on the
    first hit. Conservative: any uncertainty yields ``(None, "")``.
    """
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
        # No claimed title at all -> nothing to score (caller may also handle
        # this, but naming it keeps the bucket honest).
        return ("no_claimed_title", "claimed reference has no title to compare.")

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

    # regulatory / legal code in the title slot
    if _REGULATORY_RE.search(ct):
        return ("regulatory_code",
                "claimed title is a regulatory/legal-code string, not an "
                "article title.")

    return (None, "")
