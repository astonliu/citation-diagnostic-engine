"""cre/f1/f2_samework_rule.py -- the same-work rule (language / container / subset).

Three distinct defects make the WRITTEN title an unusable comparison against the
resolved record, and all three land in ``review_wrong_paper`` as false positives:

  language   the citing author wrote the title in another language
  container  the title slot holds a book container (publisher / city / series)
  subset     the author quoted only a portion of the title

The rule reclassifies such a row to SAME_WORK only when the ADDRESS -- the fields
translation, truncation and re-issue cannot change -- independently corroborates it.

THERE IS NO ABSTAIN STATE. A row is either reclassified or left exactly where it
was, so coverage stays 1.000. An earlier build abstained when a fired entry could
not be corroborated; that abstained 17 genuine F2 detections and is the reason the
subset entry looked net-harmful. It is net-POSITIVE when it must be corroborated
rather than allowed to replace the address.

ADDRESS-ONLY ACCEPTANCE IS FORBIDDEN, at any threshold, with or without a DOI
requirement. 11 genuine F2 rows in seed 45 share DOI, volume, first page AND
journal with the resolved record and are still different papers -- a glaucoma
compliance paper against an intravitreal triamcinolone trial, both on DOI
10.4103/0301-4738.77008, volume 59, page 93. No address threshold separates those
from the false positives, and the seed-43 DOI-anchored relaxation already lost 4
TRUE_F2 to catch 1 false positive.

Every threshold here is a named module-level constant so it can be found, cited and
changed deliberately rather than discovered inside a function.

MEASURED ON SEED 45 (development only -- every constant above was set while looking
at these rows, so no figure here is reportable; seed 47 must be drawn first):

    input: f2_seed45_audited87_full.jsonl
           sha256 8b1207987922082063b43014119f13a3a417af78824a266f51c749e65ca033a9
           87 rows, FULL author rosters

    entries 1-3 : 69/87 = 0.7931 -> 67/71 = 0.9437, 14 of 18 FP reclassified
    + entry 4   : 69/87 = 0.7931 -> 67/69 = 0.9710, 16 of 18 FP reclassified
    coverage 1.000 throughout; 2 TRUE_F2 (PMC9940543:R13, PMC8845446:B49),
    unchanged by entry 4; entry mix language 8, subset 6, container 2,
    corporate 2 (PMC10011273:CR14, PMC9730128:bib13)

    The two false positives entry 4 does NOT reach are the spec's own out-of-scope
    rows: PMC9508853:B81 (stemming) and PMC10641984:gh2489-bib-0065 (thin address,
    reaching it costs skaf392-B47). PMC9533381:ref10 is a label question.

CORRECTION, recorded because the numbers in commit 77c4cb7's message are wrong and
that commit is pushed. Those were measured against the blind adjudication
workbook, which carries only ``first_author`` -- the ANNOTATOR'S STRIPPED ARTIFACT,
not the rule's input. ``author_any`` therefore compared "Moune" against "Ellong"
and scored False on a row whose full rosters share two surnames:

    written  ['Moune', 'Bella-Hiag']
    resolved ['Ellong', 'Ebana Mvogo', 'Nyouma Moune', 'Bella-Hiag']

R13 recovers on the real input (address 4, language entry). The 14/18 and the
coverage were unaffected -- none of those 14 turned on ``author_any`` -- but the
TRUE_F2 count and both precision figures were, so 68/72 = 0.9444 must not be
quoted. The remaining gap to the spec's 3 TRUE_F2 is PMC9533381:ref10 alone, whose
label ZD is reviewing; the rule treats the label as authoritative either way.

THE GENERAL HAZARD: the blind workbook is the convenient artifact and it looks
complete until you check the field. Any future rule using ``author_any`` must read
the reband file, not the workbook.
"""
from __future__ import annotations

import re
import unicodedata

from rapidfuzz import fuzz

# =====================================================================
# Thresholds -- every one named, none inline
# =====================================================================
#: Agreeing address fields required to reclassify a language/subset row.
#: >= 3 was rejected ON EVIDENCE: it buys one false positive and costs
#: PMC12664572:skaf392-B47, "Evaluation of SOYABEAN meal as a protein source in
#: canine foods" against "Evaluation of LOW-ASH POULTRY meal ...", an unmistakable
#: F2 that only this threshold stops.
ADDRESS_AGREEMENT_MIN = 4

#: Surname similarity. 85 admits one substituted character in a seven-letter
#: surname ("Nagendra babu" / "Nagendrababu") and still refuses Chen/Chan.
AUTHOR_FUZZ_MIN = 85

#: Journal similarity, token-set so word order and abbreviation do not matter.
JOURNAL_FUZZ_MIN = 80

#: The shorter title must carry at least this many CONTENT words before a subset
#: can identify a paper at all ("Diabetes mellitus" cannot).
SUBSET_MIN_CONTENT_WORDS = 4

#: A shared run is distinctive if it carries a word this long, OR runs this many
#: content words.
SUBSET_DISTINCTIVE_WORD_LEN = 7
SUBSET_DISTINCTIVE_RUN_WORDS = 6

#: One dropped word is tolerated once the shorter title is at least this long.
SUBSET_GAP_TOLERANCE_MIN_WORDS = 6

#: Minimum non-English function words (and total tokens) for the stopword profile.
LANG_MIN_STOPWORDS = 2
LANG_MIN_TOKENS = 5


# =====================================================================
# Vocabularies
# =====================================================================
_ENGLISH_STOPWORDS = frozenset("""
a an the of in on for and or to with from by at as is are was were be been being
this that these those its it their his her our your not no than then when where
which who whom whose how why what
""".split())

#: Known blind spot, stated: a language outside this list silently fails to
#: trigger. Covers French, Spanish, Portuguese, German, Italian, Dutch, Polish,
#: Scandinavian and Turkish at minimum.
_NON_ENGLISH_STOPWORDS = frozenset("""
le la les des du de un une et ou dans sur pour avec chez par au aux ce cette ces
el los las un una y o en para con por del al lo su sus como es son
o os as um uma e ou em para com por do da dos das no na nos nas ao aos que se
der die das ein eine und oder in an auf fur mit von bei zur zum des dem den als
il lo gli un una e o di in su per con da del della dei delle al alla nel nella
de het een en of in op voor met van bij aan door dat die zijn wordt werd
i w z na do od po za oraz jest sa dla nie tego tym ktory ktora
och eller i pa for med av till fran som ar det den de ett en
ve ile bir bu icin olan ya da nin nin den dan
""".split())

_CONTAINER_TOKENS = frozenset({
    "statpearls", "elsevier", "springer", "wiley", "mcgraw", "lippincott",
    "saunders", "churchill", "thieme", "humana", "blackwell", "karger",
    "bookshelf",
    # The citing side TRUNCATES the publisher's city: seed-45 rows carry
    # "Ewing Sarcoma. StatPearls. Treasure" with "Island" cut off, so the phrase
    # marker alone never matches and the cleaned titles never compare equal.
    "treasure", "island",
})

_CONTAINER_PHRASES = (
    "treasure island", "university press", "academic press", "crc press",
    "wolters kluwer", "nova science",
)

_CONTAINER_PUBTYPES = frozenset({"book", "book chapter", "books", "chapter"})

#: A subset match is REFUSED when the extra text carries one of these: they name a
#: SEPARATE record with its own PMID. Blocking them leaves the work/version
#: question to TAXONOMY.md instead of answering it by implementation.
_SUBSET_BLOCKLIST = frozenset("""
commentary comment comments reply replies response rebuttal discussion erratum
errata corrigendum correction corrections retraction retracted withdrawn
editorial letter preface foreword addendum supplement supplementary expression
concern part chapter volume update updated author authors abstract poster
protocol preprint summary translation reprint republished
""".split())

#: An author entry naming a body rather than a person. Such a name cannot bear a
#: surname comparison, so it makes the field NON-DISCRIMINATIVE, not disagreeing.
_CORPORATE_TOKENS = frozenset("""
group consortium society committee collaborative association panel institute
department network investigators task force authority agency registry foundation
working college academy council board organization organisation ministry centre
center trial study initiative program programme
""".split())
_CORPORATE_MAX_WORDS = 4


# =====================================================================
# Normalization
# =====================================================================
def _fold(text: str) -> str:
    """Lowercase, strip accents, drop punctuation, collapse whitespace."""
    t = unicodedata.normalize("NFKD", str(text or ""))
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[^\w\s]", " ", t.lower())
    return re.sub(r"\s+", " ", t).strip()


def _tokens(text: str) -> "list[str]":
    return _fold(text).split()


def _content_words(text: str) -> "list[str]":
    return [t for t in _tokens(text) if t not in _ENGLISH_STOPWORDS]


def _has_non_latin(text: str) -> bool:
    for ch in str(text or ""):
        if ch.isalpha() and not ("LATIN" in unicodedata.name(ch, "")):
            return True
    return False


# =====================================================================
# 1. entry_language
# =====================================================================
def entry_language(written_title: str, resolved_title: str) -> bool:
    """Whether the written title is a TRANSLATION of the resolved one.

    Computed from the TITLE STRINGS ONLY. ``resolved_language`` and MEDLINE ``LA``
    are deliberately not read: a citing author can translate a title that PubMed
    holds only in English, and no record-side field shows that. Measured on seed
    45, 4 of the 10 cross-language rows have ``LA - eng`` records."""
    w, r = str(written_title or ""), str(resolved_title or "")
    if not w or not r:
        return False

    # (a) script -- non-Latin on one side and not the other.
    if _has_non_latin(w) != _has_non_latin(r):
        return True

    # (b) bracket -- NLM's marker for its own English translation.
    rs = r.strip()
    if rs.startswith("[") and "]" in rs[-3:]:
        return True

    # (c) stopword profile.
    wt = _tokens(w)
    if len(wt) < LANG_MIN_TOKENS:
        return False
    w_non = sum(1 for t in wt if t in _NON_ENGLISH_STOPWORDS and t not in _ENGLISH_STOPWORDS)
    w_eng = sum(1 for t in wt if t in _ENGLISH_STOPWORDS)
    if not (w_non >= LANG_MIN_STOPWORDS and w_non > w_eng):
        return False
    rt = _tokens(r)
    r_non = sum(1 for t in rt if t in _NON_ENGLISH_STOPWORDS and t not in _ENGLISH_STOPWORDS)
    r_eng = sum(1 for t in rt if t in _ENGLISH_STOPWORDS)
    return r_eng > r_non


# =====================================================================
# 2. entry_container
# =====================================================================
def _container_hit(title: str) -> bool:
    """Container markers on WHOLE WORDS, never substrings.

    ``press`` as a bare substring fired on 5 of 8 container hits in seed 45 --
    inside ``expressed``, ``expression``, and ``press de banca`` (Spanish for bench
    press). That is the whole bug, so single tokens are matched against the
    TOKENIZED title and only multi-word markers are matched as phrases."""
    toks = set(_tokens(title))
    if toks & _CONTAINER_TOKENS:
        return True
    folded = _fold(title)
    return any(re.search(rf"\b{re.escape(p)}\b", folded) for p in _CONTAINER_PHRASES)


def entry_container(written_title: str, resolved_title: str, *,
                    resolved_is_container: bool = False,
                    publication_types=()) -> bool:
    if resolved_is_container:
        return True
    if any(str(p or "").strip().lower() in _CONTAINER_PUBTYPES for p in (publication_types or ())):
        return True
    return _container_hit(written_title) or _container_hit(resolved_title)


def _strip_container(title: str) -> str:
    """The title with container text removed, for the exact comparison."""
    folded = _fold(title)
    for phrase in _CONTAINER_PHRASES:
        folded = re.sub(rf"\b{re.escape(phrase)}\b", " ", folded)
    kept = [t for t in folded.split() if t not in _CONTAINER_TOKENS]
    return " ".join(kept).strip()


def container_same_work(written_title: str, resolved_title: str,
                        author: "bool | None") -> bool:
    """A book title is not unusable, it is DIRTY: clean it and compare.

    EXACT comparison, never fuzzy -- that is what keeps ``clavicle fractures`` and
    ``clavicle fracture`` flagged. Year is not consulted: a living chapter is
    re-dated on every revision, so a year difference is not a disagreement."""
    if author is False:
        return False
    left, right = _strip_container(written_title), _strip_container(resolved_title)
    return bool(left) and left == right


# =====================================================================
# 3. entry_subset
# =====================================================================
def _ordered_containment(short: "list[str]", long: "list[str]") -> "tuple[bool, int]":
    """``(matched_in_order, matched_count)`` -- gaps in ``long`` allowed.

    Tolerates COMPOUNDING in either direction: "post obturation" and
    "postobturation" are the same content word spelled with and without a space,
    and treating them as different words leaves an otherwise identical title pair
    unmatched (seed 45, PMC11849606:bib29). This is a normalization of one word,
    not a relaxation of what counts as a match."""
    i = j = matched = 0
    while i < len(short) and j < len(long):
        if short[i] == long[j]:
            i += 1
            j += 1
            matched += 1
            continue
        # one short token == two consecutive long tokens, or the reverse
        if j + 1 < len(long) and short[i] == long[j] + long[j + 1]:
            i += 1
            j += 2
            matched += 1
            continue
        if i + 1 < len(short) and short[i] + short[i + 1] == long[j]:
            i += 2
            j += 1
            matched += 1
            continue
        # Advance whichever side cannot match: if this short word appears later in
        # long, the gap is in LONG; otherwise the short word is dropped/swapped and
        # the gap is in SHORT.
        if short[i] in long[j + 1:]:
            j += 1
        else:
            i += 1
    return matched == len(short), matched


def entry_subset(written_title: str, resolved_title: str) -> bool:
    """Whether one title is a CONTENT-WORD subset of the other, in order.

    Character-level prefix/suffix matching is insufficient: authors quote the
    INTERIOR of a title ("Applications of the generalized gradient approximation"
    out of "Atoms, molecules, solids, and surfaces: Applications of ...") and drop
    words mid-title."""
    a, b = _content_words(written_title), _content_words(resolved_title)
    if not a or not b:
        return False
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    if len(short) < SUBSET_MIN_CONTENT_WORDS:
        return False
    if len(short) == len(long):
        return False                     # not a subset, the same length

    ok, matched = _ordered_containment(short, long)
    if not ok:
        # One dropped word is tolerated once the shorter title is long enough.
        if not (len(short) >= SUBSET_GAP_TOLERANCE_MIN_WORDS
                and matched >= len(short) - 1):
            return False

    # The shared run must be distinctive.
    shared = [w for w in short if w in set(long)]
    if not (any(len(w) >= SUBSET_DISTINCTIVE_WORD_LEN for w in shared)
            or len(shared) >= SUBSET_DISTINCTIVE_RUN_WORDS):
        return False

    # The extra text must not name a SEPARATE record.
    extra = [w for w in long if w not in set(short)]
    return not (set(extra) & _SUBSET_BLOCKLIST)


# =====================================================================
# 4. author_evidence -- tri-state
# =====================================================================
def _as_names(value) -> "list[str]":
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    return [str(v) for v in value if str(v or "").strip()]


def _is_corporate(name: str) -> bool:
    toks = _tokens(name)
    return bool(set(toks) & _CORPORATE_TOKENS) or len(toks) >= _CORPORATE_MAX_WORDS


def author_evidence_detail(written_authors, resolved_authors) -> "tuple[bool | None, str]":
    """``True`` / ``False`` / ``None``, where None means NOT DISCRIMINATIVE.

    None drops the field from the address count rather than counting it as a
    disagreement. The tri-state exists precisely because corporate names and parser
    corruption make author disagreement uninformative. Any author against any
    author -- never first-author only."""
    left, right = _as_names(written_authors), _as_names(resolved_authors)
    if not left or not right:
        return None, "missing"
    if all(_is_corporate(n) for n in left) or all(_is_corporate(n) for n in right):
        return None, "corporate"

    left_tokens = [set(_tokens(n)) for n in left]
    right_tokens = [set(_tokens(n)) for n in right]
    for lt in left_tokens:
        for rt in right_tokens:
            if not lt or not rt:
                continue
            if lt & rt:                                   # a shared surname token
                return True, "shared_token"
            if lt <= rt or rt <= lt:                      # compound surnames
                return True, "containment"
    for ln in left:
        for rn in right:
            a, b = _fold(ln), _fold(rn)
            if not a or not b:
                continue
            # Compared as written AND space-stripped: "Nagendra babu" vs
            # "Nagendrababu" is one space, which any token-sorted form destroys.
            if (fuzz.ratio(a, b) >= AUTHOR_FUZZ_MIN
                    or fuzz.ratio(a.replace(" ", ""), b.replace(" ", "")) >= AUTHOR_FUZZ_MIN):
                return True, "fuzz"
    return False, "disagree"


def author_evidence(written_authors, resolved_authors) -> "bool | None":
    """Tri-state only. :func:`author_evidence_detail` carries WHY, which entry 4
    needs: ``None`` for ``corporate`` is evidence the field cannot discriminate,
    ``None`` for ``missing`` is only a thin record."""
    return author_evidence_detail(written_authors, resolved_authors)[0]


# =====================================================================
# 5. address_agreement
# =====================================================================
def _norm_doi(value: str) -> str:
    d = str(value or "").strip().lower()
    d = re.sub(r"^https?://(dx\.)?doi\.org/", "", d)
    return d


def _leading_int(value: str) -> "str | None":
    m = re.match(r"\s*(\d+)", str(value or ""))
    return m.group(1) if m else None


def _first_page(value: str) -> "str | None":
    """Leading letter prefix and leading zeros stripped, so 6040 and e06040 agree
    and both 471-479 and 471-5 reduce to 471."""
    m = re.match(r"\s*[A-Za-z]*0*(\d+)", str(value or ""))
    return m.group(1) if m else None


def address_agreement(written: dict, resolved: dict) -> int:
    """How many address fields AGREE. A field is scored only when present on BOTH
    sides, so absence never counts as agreement or as disagreement."""
    n = 0
    wd, rd = _norm_doi(written.get("doi")), _norm_doi(resolved.get("doi"))
    if wd and rd and wd == rd:
        n += 1
    for key, extract in (("year", _leading_int), ("volume", _leading_int),
                         ("pages", _first_page)):
        # Volume is NEVER fuzzed: 114 vs 144 and 8 vs 298 are real parser errors in
        # seed 45 and must stay disagreements.
        a, b = extract(written.get(key)), extract(resolved.get(key))
        if a is not None and b is not None and a == b:
            n += 1
    wj, rj = _fold(written.get("journal")), _fold(resolved.get("journal"))
    if wj and rj and fuzz.token_set_ratio(wj, rj) >= JOURNAL_FUZZ_MIN:
        n += 1
    if author_evidence(written.get("authors") or written.get("first_author"),
                       resolved.get("authors") or resolved.get("first_author")) is True:
        n += 1
    return n



# =====================================================================
# 4. entry_corporate -- the MIRROR of entries 1-3
# =====================================================================
def title_equivalent(written_title: str, resolved_title: str) -> "tuple[bool, str]":
    """``(equivalent, path)`` -- ``exact`` or ``token_set``, else ``(False, "")``.

    TITLE_SIM IS DELIBERATELY NOT A PATH. A similarity score above 0.95 survives a
    swapped CONTENT word -- "adults" vs "children", "smoking" vs "alcohol" -- and
    this entry has no title-shape evidence to fall back on the way entries 1-3 do:
    there, something about the title itself (a translation, a container, a quoted
    portion) explains the mismatch. Here the title IS the whole case, so it has to
    be equal, not similar."""
    w, r = _fold(written_title), _fold(resolved_title)
    if not w or not r:
        return False, ""
    if w == r:
        return True, "exact"
    if set(_content_words(written_title)) == set(_content_words(resolved_title)):
        return True, "token_set"
    return False, ""


def entry_corporate(written: dict, resolved: dict) -> "tuple[bool, dict]":
    """The mirror image of entries 1-3: the TITLE is strong and the AUTHOR is
    non-discriminative.

    Entries 1-3 each describe a way the title became unusable. These rows have
    perfect titles and sit in the wrong-paper band purely because two renderings of
    one organization's name differ -- "Practice Committee of the American Society
    for reproductive medicine" against the same string with different casing, or a
    group name the citing article compressed ("National Institute of Neurological D
    and Stroke rt PASSG").

    Requires author_evidence to be None FOR REASON ``corporate``. Reason
    ``missing`` must not fire it: an absent author list is a thin record, not
    evidence that the author is uninformative, and accepting it would open the
    entry to anything with a blank author slot. One-sided corporate qualifies -- a
    corporate name against a person roster is still non-discriminative, and it is
    the common shape."""
    ok, path = title_equivalent(written.get("title"), resolved.get("title"))
    if not ok:
        return False, {"title_path": None, "author_reason": None}
    value, reason = author_evidence_detail(
        written.get("authors") or written.get("first_author"),
        resolved.get("authors") or resolved.get("first_author"))
    fires = value is None and reason == "corporate"
    return fires, {"title_path": path, "author_reason": reason}


# =====================================================================
# 6. The decision
# =====================================================================
def entry_for(written: dict, resolved: dict) -> "str | None":
    """Which entry fires -- language, container, subset -- FIRST MATCH WINS."""
    wt, rt = written.get("title"), resolved.get("title")
    if entry_language(wt, rt):
        return "language"
    if entry_container(wt, rt,
                       resolved_is_container=bool(resolved.get("is_container")),
                       publication_types=resolved.get("publication_types") or ()):
        return "container"
    if entry_subset(wt, rt):
        return "subset"
    # LAST on purpose: entries 1-3 each explain why the title is unusable, and this
    # one requires the title to be perfect, so it can only ever claim rows the
    # others left behind. Ordering is what keeps the 24 earlier fixtures fixed.
    if entry_corporate(written, resolved)[0]:
        return "corporate"
    return None


def classify_samework(written: dict, resolved: dict) -> "tuple[str, str | None, int]":
    """``(verdict, entry, address_n)`` -- verdict is ``SAME_WORK`` or ``FLAG``.

    No abstain, no UNRESOLVED, no coverage loss: a row is either reclassified or
    left exactly where it was."""
    entry = entry_for(written, resolved)
    if entry is None:
        return "FLAG", None, 0
    if entry == "container":
        author = author_evidence(
            written.get("authors") or written.get("first_author"),
            resolved.get("authors") or resolved.get("first_author"))
        ok = container_same_work(written.get("title"), resolved.get("title"), author)
        return ("SAME_WORK" if ok else "FLAG"), entry, 0
    n = address_agreement(written, resolved)
    return ("SAME_WORK" if n >= ADDRESS_AGREEMENT_MIN else "FLAG"), entry, n
