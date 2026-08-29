"""F2-E: excise leading bibliographic furniture from a parsed written title.

Written titles can carry furniture glued to the FRONT that suppresses title_sim
and destroys the handle needed to search for the intended work A:

  * author run   -- ``Kitagawa, K.; Higashida, T.; ...; Inagaki, C. Cl-ATPase ...``
  * chapter label -- ``Chapter Nine - Sculpting the Transcriptome ...``
  * article-type label -- ``Alzheimer's Association Report. Alzheimer's disease ...``

Excising the leading run recovers the exact title (verified: PMC11244905:R55,
PMC9340374:B8 -> title_sim 1.0; PMC10227918:r48/r98/r138). Discipline (all
load-bearing, from F2_MATCHER_REVISION_SPEC):

  * FRONT ONLY. Never excise from the end -- 4,086 rows end with a capitalized
    proper noun that is NOT an author (species binomials, gene/compound names),
    against 2 rows ending with an author, so a trailing-name rule fires ~2,000:1
    against legitimate title text.
  * The author run must terminate with ``. `` (period + space) followed by a
    real title word (Capital + lowercase), and its ``;``-separated items must all
    read as names -- NOT anchored to written_authors, which is itself corrupted
    on exactly these rows (the leaked names are the ones missing from the list).
  * At least 4 content words must survive; otherwise the excision would leave a
    stub, so the title is left UNTOUCHED (the caller routes it to unscoreable --
    not-yet-judgeable, never non-F2).
"""
from __future__ import annotations

import re

# A single ";"-separated author item: a capitalized surname, optionally followed
# by ", Initials." (each initial an upper-case letter + period, possibly
# hyphenated). Deliberately permissive on the surname (accents, hyphens,
# apostrophes, particles like "ul"/"ur" inside "Zaheer-ul-Haq"/"Atta-ur-Rahman").
_AUTHOR_ITEM = r"[A-ZÀ-ɏ][\w'’.\-]*(?:,\s*(?:[A-Z]\.-?)+)?"

# The author run terminates at the FIRST period-space that is followed by a real
# title word (Capital + lower-case letter). A single initial ("K.") is Capital +
# non-letter, so it never triggers the boundary; the title's first word does.
_RUN_BOUNDARY_RE = re.compile(r"\.\s+(?=[A-Z][a-z])")

# Chapter / section label: "Chapter Nine - ", "Chapter 5 - ", "Section II: ".
_CHAPTER_RE = re.compile(
    r"^\s*(?:chapters?|sections?|parts?|appendix|unit)\s+"
    r"(?:[ivxlcdm]+|\d{1,3}|one|two|three|four|five|six|seven|eight|nine|ten|"
    r"eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|"
    r"nineteen|twenty)\b\s*[-–—:.]\s+(?=[A-Za-z])", re.I)

# Article-type / society-report label: a short (<=6 word) leading phrase ending
# in a genre noun, then ". " + a real title word. Anchored tightly so an ordinary
# title ending mid-phrase in one of these words is not caught.
_ARTICLE_TYPE_RE = re.compile(
    r"^\s*(?:[A-Z][\w'’&.\-]+(?:\s+[\w'’&.\-]+){0,5}?\s+"
    r"(?:report|statement|guidelines?|guidance|recommendations?|"
    r"consensus(?:\s+statement)?))\.\s+(?=[A-Z][a-z])", re.I)

_CONTENT_STOP = {
    "a", "an", "and", "as", "at", "by", "for", "from", "in", "of", "on",
    "or", "the", "to", "with", "de", "la", "le",
}


def _content_word_count(title: str) -> int:
    """Number of content words: alphabetic tokens of length >= 3 that are not
    stopwords. Numbers, punctuation, initials, and stopwords do not count."""
    toks = re.findall(r"[^\W\d_]+", (title or "").lower())
    return sum(1 for t in toks if len(t) >= 3 and t not in _CONTENT_STOP)


def _looks_like_author_run(run: str) -> bool:
    """The leading ``run`` (text before the boundary period) reads as an author
    list: it contains a ``;`` separator and every ``;``-separated item starts with
    a capital letter (a name), with no item long enough to be a sentence clause.
    Requiring the ``;`` keeps this to the STRICT signature the spec measured (9
    rows), not the comma-only generalization it flags as unverified."""
    if ";" not in run:
        return False
    items = [it.strip() for it in run.split(";") if it.strip()]
    if len(items) < 2:
        return False
    for it in items:
        if not it[:1].isupper():
            return False
        # A genuine author item is short (surname + initials); a leaked sentence
        # clause is not. 6 words is generous for "Zaheer-ul-Haq" style names.
        if len(it.split()) > 6:
            return False
    return True


def excise_leading_furniture(title: str) -> tuple[str, str]:
    """Return ``(clean_title, excised_prefix)``.

    When a leading furniture run is found AND at least 4 content words survive,
    ``clean_title`` is the title with the run removed and ``excised_prefix`` is the
    removed substring (kept for audit). Otherwise the title is returned unchanged
    with an empty ``excised_prefix`` -- including the case where excision would
    leave fewer than 4 content words (a stub), so the caller can route the row to
    unscoreable rather than scoring a fragment."""
    if not title:
        return (title, "")
    t = title.strip()

    # 1. Chapter / section label (own terminator: dash or colon).
    m = _CHAPTER_RE.match(t)
    if m:
        cut = m.end()
        clean = t[cut:].strip()
        if _content_word_count(clean) >= 4:
            return (clean, t[:cut])
        return (title, "")

    # 2. Article-type / society-report label.
    m = _ARTICLE_TYPE_RE.match(t)
    if m:
        cut = m.end()
        clean = t[cut:].strip()
        if _content_word_count(clean) >= 4:
            return (clean, t[:cut])
        return (title, "")

    # 3. Author run: split at the first "period + space + real title word", and
    # only excise when the leading part reads as a "; "-separated author list.
    bm = _RUN_BOUNDARY_RE.search(t)
    if bm:
        run = t[:bm.start()]
        clean = t[bm.end():].strip()
        if _looks_like_author_run(run) and _content_word_count(clean) >= 4:
            # Include the boundary period in the excised prefix for a faithful audit.
            return (clean, t[:bm.start() + 1])

    return (title, "")
