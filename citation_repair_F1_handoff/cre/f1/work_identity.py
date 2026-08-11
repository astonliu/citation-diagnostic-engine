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
    r"national|international|committee|council|institute|foundation|board)\b", re.I)
_LIVING_SOURCES = ("statpearls", "ncbi bookshelf", "bookshelf")
_AUTHOR_SUFFIX_ONLY = {"jr", "junior", "sr", "senior", "filho", "neto"}
_ROMAN_RE = re.compile(r"\b(?:i|ii|iii|iv|v|vi|vii|viii|ix|x)\b", re.I)
# Dotted ``i.v.`` / ``v.i.`` is a route abbreviation, not two series ordinals.
# Mask only that abbreviation before extracting Roman tokens: ``I.`` and ``II.``
# section labels (including the AM1-BCC guard) remain visible to the rule.
_DOTTED_IV_ABBREVIATION_RE = re.compile(r"\b(?:i\s*\.\s*v|v\s*\.\s*i)\s*\.", re.I)
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
# An ABSTRACT locator -- the page field of a non-final lineage node (§15.2). Wider
# than _SUPPLEMENT_PAGE_RE on purpose, and deliberately NOT the same predicate:
#   [SP]\d   supplement / poster page   S100, P1025
#   [E]\d    e-locator                  e46, e022455
#   \d{1,4}  a bare abstract NUMBER     207   (no end page -- an abstract book's
#            with an optional letter     207A   running number, not a page RANGE)
# The two are kept apart because _SUPPLEMENT_PAGE_RE is a page-PARITY predicate
# (_first_pages_agree: "S344" and "344" are different physical slots), and 2,725
# claimed rows in the seed-37 frame carry a bare-number page while 1,336 carry an
# e-page. Widening the parity predicate to cover them was measured to stop
# ``overwhelming_bibliographic_anchor`` firing on 4 frame rows -- a change to a
# rule the §24 register fences off (LR-1) -- for no gain, since only the abstract
# rules need the wider shapes. Verified: an ordinary page RANGE never matches.
_ABSTRACT_LOCATOR_RE = re.compile(
    r"^\s*(?:[SP]\d|E\d+[A-Za-z]?\s*$|\d{1,4}[A-Za-z]?\s*$)", re.I)
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
# Rule-local floor for a malformed author field whose DOI, publication year,
# venue, volume, and first page independently identify the work.  It is NOT a
# relaxation of RULE A: this rule requires all five bibliographic anchors.
DOI_BIBLIOGRAPHIC_ANCHOR_TITLE_MIN = 0.85
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
# RULE A2: minimum boundary-tolerant content agreement (best of the two
# directions) required before an exact shared DOI is read as same-work. Set from
# the adjudicated separation, with margin on both sides: the 21-case
# false-positive packet floors at 0.833 once boundaries are ignored, while the
# committed DOI-sharing negatives that are NOT already excluded by a series or
# organization conflict reach only 0.750 (an adjacent article) and 0.667 (the
# contaminated-DOI TRUE_F2, PMC8015328:ref011).
DOI_BOUNDARY_AGREEMENT_MIN = 0.80
# --- §15.2 version chain -------------------------------------------------------
# Two records that are nodes of ONE publication lineage (preprint <-> conference
# paper <-> extended journal version; conference abstract <-> full paper) are the
# SAME WORK. Identifiers legitimately change between nodes, so DOI/journal/volume/
# year inequality cannot by itself route such a pair to review_wrong_paper.
#
# These floors are LOWER than RULE B's because RULE B must survive the sibling-
# TRIAL problem (serial trialists publishing different trials with the same core
# team and a shared drug/disease title template), which it solves with roster
# containment >= 0.75 AND coverage >= 0.77 AND title >= 0.87. The version chain is
# a different question -- is the claimed side a non-final NODE of this same work --
# and it is gated first on an abstract locator or a preprint venue, which no
# sibling-trial pair carries. The destination is the AUDITED same-work band, never
# ``match``, so the bar is a routing bar, not an auto-clear bar.
VERSION_CHAIN_TITLE_MIN = 0.80
VERSION_CHAIN_ROSTER_MIN = 0.60
# Fraction of the resolved title's distinctive tokens that must be reconstructable
# from the claimed author+title fields for RULE E (a true shifted-field artifact
# splits ONE title across the two slots; a consortium author whose name merely
# appears in a different paper's title does not).
SHIFTED_TITLE_COVERAGE_MIN = 0.85
# Rule-local title floors for translated_title_missing_volume_anchors (RULE F,
# 2026-07-15; seed 31 is now burned development data for this rule). Kept SEPARATE
# from and never below the 0.92 near-identical-title gate in biblio_match, and
# never lowering any GLOBAL threshold. The high tier reuses the existing 0.85
# transliteration floor; the low tier (0.78) is reached ONLY under the full
# translation conjunction AND roster containment >= 0.60 as a backstop.
TRANSLATION_MISSING_VOLUME_TITLE_MIN = 0.78
TRANSLATION_MISSING_VOLUME_ROSTER_MIN = 0.60


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


def _corporate_author_format_key(value: str) -> str:
    """Formatting-only key for an institutional author name.

    Hyphens between words are separators here (``patient-and`` vs ``patient
    and``), unlike personal-name normalization where a hyphen can be part of a
    family name.  This deliberately does no token deletion, abbreviation
    expansion, or fuzzy matching: distinct organizations retain distinct keys.
    """
    value = fold_bibliographic_text(value or "").lower()
    # "&" is the typographic form of the conjunction "and" in institutional
    # names; folding it is a formatting equivalence, not a token change.
    value = re.sub(r"[&＆]", " and ", value)
    value = re.sub(r"[-‐-―/_]+", " ", value)
    value = re.sub(r"[^\w\s]", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _corporate_author_equivalent(claimed: ClaimedRef,
                                 resolved: RetrievedRecord) -> bool:
    if not claimed.authors or not resolved.authors:
        return False
    left, right = claimed.authors[0] or "", resolved.authors[0] or ""
    if not (_CORPORATE_RE.search(left) and _CORPORATE_RE.search(right)):
        return False
    a, b = _corporate_author_format_key(left), _corporate_author_format_key(right)
    return bool(a and b and a == b)


# A parenthetical acronym that merely GLOSSES the words beside it ("World Medical
# Association (WMA)", "National Cholesterol Education Program (NCEP) Expert
# Panel"). Bounded to a single unspaced token so a parenthetical QUALIFIER that
# names a sub-body ("(Adult Treatment Panel III)") can never match and is kept as
# real tokens.
_ACRONYM_GLOSS_RE = re.compile(r"\s*\(\s*([A-Za-z][A-Za-z.&\-]{1,15})\s*\)")


def _strip_acronym_gloss(value: str) -> str:
    """Remove a parenthetical acronym whose letters are the initials of the tokens
    immediately before (or after) it -- the expansion is present in the SAME
    string, so the acronym adds no information and is pure typography.

    This is deliberately NOT acronym EXPANSION: "AAP Committee on Nutrition" vs
    "American Academy of Pediatrics Committee on Nutrition" carries its expansion
    in the OTHER string, replacing tokens, and is left untouched (it stays a
    genuine token change -- see
    ``test_corporate_abbreviation_is_a_token_change_and_stays_high``).
    """
    text = value or ""

    def repl(match: "re.Match[str]") -> str:
        letters = re.sub(r"[^a-z]", "", match.group(1).lower())
        if len(letters) < 2:
            return match.group(0)
        before = re.sub(r"[^\w\s]", " ", text[:match.start()]).split()
        after = re.sub(r"[^\w\s]", " ", text[match.end():]).split()
        for words in (before[-len(letters):], after[:len(letters)]):
            if (len(words) == len(letters)
                    and "".join(w[0].lower() for w in words) == letters):
                return " "
        return match.group(0)

    return _ACRONYM_GLOSS_RE.sub(repl, text)


def _corporate_name_tokens(value: str) -> list[str]:
    return _corporate_author_format_key(_strip_acronym_gloss(value)).split()


def _corporate_token_equivalent(left: str, right: str, *, terminal: bool) -> bool:
    """Whether two institutional-name tokens are the SAME word."""
    if left == right:
        return True
    # A spelling/localization variant of one word ("anaesthesiologists" vs
    # "anesthesiologists", 0.985). Threshold RAISED 0.92 -> 0.95: at 0.92 it wrongly
    # equated the DISTINCT first tokens "international" / "interventional" (0.9227),
    # clearing two different societies as one; the genuine spelling variant (0.985)
    # is well above 0.95, and "national" / "international" (0.789) is far below.
    if (min(len(left), len(right)) >= 6
            and JaroWinkler.similarity(left, right) >= 0.95):
        return True
    # JATS truncates the FINAL token of a long institutional name ("...Committee
    # on Taxonomy of, V" for "...on Taxonomy of Viruses"). Allowed at the closing
    # token only, and ONLY when the longer token is a genuine truncated WORD
    # (length >= 5) -- so short alphanumeric group designators are NOT equated
    # ("Group A" vs "Group AB": "a"/"ab" are distinct groups, not a truncation).
    return (terminal and bool(left and right)
            and max(len(left), len(right)) >= 5
            and (left.startswith(right) or right.startswith(left)))


def _corporate_name_contained(inner: list[str], outer: list[str]) -> bool:
    """Whether ``inner`` occurs as a CONTIGUOUS run inside ``outer``."""
    if not inner or len(inner) > len(outer):
        return False
    last = len(inner) - 1
    return any(
        all(_corporate_token_equivalent(inner[i], outer[start + i],
                                        terminal=(i == last))
            for i in range(len(inner)))
        for start in range(len(outer) - len(inner) + 1))


def _corporate_names_conflict(claimed: ClaimedRef,
                              resolved: RetrievedRecord) -> bool:
    """AFFIRMATIVE evidence that two institutional authors are DIFFERENT bodies.

    Absence of a format-key match is not that evidence: a parenthetical acronym,
    a truncated trailing token, or periods where commas belong are one
    organization written two ways.

    Containment is NOT identity. Two names where neither contains the other
    conflict outright ("National" vs "International" Committee for Pediatric Care).
    Two names in a strict CONTAINMENT relation -- one carrying EXTRA distinctive
    tokens ("American Academy of Pediatrics" vs "...Committee on Nutrition") -- are
    the same body only when they cite the SAME document, which shows up as an
    IDENTICAL title; when the titles diverge, the extra tokens distinguish a parent
    from its subunit's different work, and that is a conflict. Equal-length mutual
    containment is identity ("World Medical Association (WMA)" vs "World Medical
    Association") and never conflicts.
    """
    left = _corporate_name_tokens(claimed.authors[0] if claimed.authors else "")
    right = _corporate_name_tokens(resolved.authors[0] if resolved.authors else "")
    if not left or not right:
        return True
    if not (_corporate_name_contained(left, right)
            or _corporate_name_contained(right, left)):
        return True                      # neither contains the other -> conflict
    if len(left) == len(right):
        return False                     # equal length + contained = identity
    # Strict containment: one name has EXTRA distinctive tokens. Same body only if
    # the titles are identical (canonical form); divergent titles -> conflict.
    ct, rt = canonical_title(claimed.title), canonical_title(resolved.title)
    return not (ct != "" and ct == rt)


def _distinct_organizations(claimed: ClaimedRef,
                            resolved: RetrievedRecord) -> bool:
    """Affirmative evidence that two INSTITUTIONAL authors are different bodies.

    Deliberately narrower than ``_corporate_names_conflict``, and used only on the
    DOI-anchored path. That predicate treats a strict containment with divergent
    titles as a conflict, which is right when the only evidence is the strings --
    a parent body's document is not its subunit's different document. When an exact
    DOI already pins ONE record, the remaining question is just "are these two
    different organizations", and the answer is: only if the SHORTER name carries a
    token the longer one cannot account for. Extra tokens on the longer name are the
    ordinary case (a truncated or partially-parsed institutional name), never
    evidence of a second body.

    False for a non-institutional pair, so the DOI path is unaffected by it.
    """
    left_raw = claimed.authors[0] if claimed.authors else ""
    right_raw = resolved.authors[0] if resolved.authors else ""
    if not (_CORPORATE_RE.search(left_raw) and _CORPORATE_RE.search(right_raw)):
        return False
    left = _corporate_name_tokens(left_raw)
    right = _corporate_name_tokens(right_raw)
    if not left or not right:
        return True
    short, long = (left, right) if len(left) <= len(right) else (right, left)
    return any(
        not any(_corporate_token_equivalent(s, l, terminal=False) for l in long)
        for s in short)


def _doi_anchored_same_work(claimed: ClaimedRef, resolved: RetrievedRecord, *,
                            title_similarity: float):
    """RULE A2 -- exact shared DOI read against the citation's slots as a WHOLE.

    RULE A requires the first-author POSITION to agree and a NEAR-IDENTICAL title,
    so it cannot see the dominant false-positive shape: a reference whose
    author/title/journal BOUNDARY was parsed in the wrong place. One misplaced
    boundary makes the author, the title and the journal each look wrong, and the
    matcher then counts three "independent" disagreements that are really one
    parsing fault -- a group author left in the title slot, or the journal name
    swallowed into it.

    An exact DOI is a globally-unique work identifier, so it is treated here as
    overwhelming. Five affirmative counter-signals still refute it, each
    load-bearing against a committed negative:
      * a series/edition conflict (annual editions sharing a run-on DOI);
      * two genuinely DIFFERENT organizations (National vs International Committee
        for Pediatric Care; "AAP" vs "American Academy of Pediatrics");
      * a roster the citation contradicts outright -- the report-vs-article
        carriers 14741909 and 34249371, whose CONTENT matches almost perfectly but
        whose author lists have nothing in common;
      * a supplement/article locator mismatch: a meeting abstract at S344 and the
        article at 344-352 are different physical slots of one volume, so a shared
        DOI is a carrier, not proof;
      * content that agrees in NEITHER direction, which is what a contaminated DOI
        looks like (PMC8015328:ref011, Paurodontella persica vs compostiocola,
        0.667; an adjacent article, 0.750).
    A version-chain node is left to §15.2's rule, which names the relation more
    precisely than "shared DOI" does.

    Returns evidence or None. The destination is the AUDITED same-work band, never
    ``match``: a shared DOI is strong enough to lift a row out of the wrong-paper
    band, not to clear it.
    """
    if not doi_equivalent(claimed.claimed_doi, resolved.doi):
        return None
    if _series_conflict(claimed.title, resolved.title):
        return None
    if _distinct_organizations(claimed, resolved):
        return None
    if _roster_contradicted(claimed, resolved):
        return None
    if _is_supplement_locator(claimed.pages) != _is_supplement_locator(resolved.pages):
        return None
    if version_chain_same_work(claimed, resolved, title_similarity=title_similarity,
                               preprint_source=False):
        return None
    if _boundary_tolerant_agreement(claimed, resolved) < DOI_BOUNDARY_AGREEMENT_MIN:
        return None
    return WorkIdentityEvidence(True, "shared_doi_same_work",
                                ("exact_doi", "boundary_tolerant_content",
                                 "agreement>=%.2f" % DOI_BOUNDARY_AGREEMENT_MIN))


def _roster_contradicted(claimed: ClaimedRef, resolved: RetrievedRecord) -> bool:
    """The resolved roster shares NO name with anything the citation says.

    This is the guard that keeps the DOI path off the report-vs-article carriers:
    a citation naming a DOCUMENT ("WHO", "NICE") whose DOI resolves to a journal
    piece ABOUT that document has near-identical CONTENT but a completely
    different roster (seed 29: 14741909 "WHO" -> a Letter by Guilbert; 34249371
    NICE -> a 15-author article). Content agreement cannot separate those from a
    boundary shift; the roster can.

    Names are looked for across the citation's author AND title slots, so a
    roster displaced into the title by the very boundary shift this path exists
    to tolerate still counts (PMC9374052: the author slot holds "BJ" while the
    title slot holds "Singh D, Madrigal A, ..."). Returns False when either side
    has no usable roster -- absence of evidence is not contradiction.
    """
    resolved_names = _slot_tokens(*(resolved.authors or []))
    if not resolved_names:
        return False
    claimed_side = _slot_tokens(*(list(claimed.authors or []) + [claimed.title or ""]))
    if not claimed_side:
        return False
    return not (resolved_names & claimed_side)


def _slot_tokens(*parts: str) -> set:
    """Distinctive tokens of a citation slot group, boundaries ignored."""
    return {t for t in canonical_title(" ".join(p or "" for p in parts)).split()
            if len(t) >= 4 and t not in _STOP}


def _boundary_tolerant_agreement(claimed: ClaimedRef,
                                 resolved: RetrievedRecord) -> float:
    """How well the two records agree on CONTENT once slot boundaries are ignored.

    Each title is scored against the UNION of the other record's author, title and
    journal text, so a title that leaked into the author or journal slot (or a
    group author left in the title) still matches -- one boundary shift stops
    producing several apparently independent field disagreements.

    The BEST of the two directions is returned, because the two failure shapes are
    asymmetric: a citing paper often abbreviates a long title down to a fragment
    (PMC13189598, "Late stent thrombosis" for a paper whose full title runs 20
    words -- forward coverage 0.231, reverse 1.000), and just as often pads a short
    title with the journal name. Requiring both directions would reject the very
    cases this is for; requiring either keeps a contaminated DOI out, because those
    disagree in BOTH directions.
    """
    w_title, r_title = _slot_tokens(claimed.title), _slot_tokens(resolved.title)
    w_union = _slot_tokens(claimed.title, claimed.journal, *(claimed.authors or []))
    r_union = _slot_tokens(resolved.title, resolved.journal, *(resolved.authors or []))
    forward = len(r_title & w_union) / len(r_title) if r_title else 0.0
    reverse = len(w_title & r_union) / len(w_title) if w_title else 0.0
    return max(forward, reverse)


def _corporate_physically_sufficient(claimed: ClaimedRef,
                                     resolved: RetrievedRecord) -> bool:
    """PHYSICAL proof that two records are the same work, independent of how the
    institutional author is spelled: an exact shared DOI, or agreement on the
    full physical slot (venue AND volume AND first page AND year).

    Format-key equality is a convenience, never the proof -- string shape alone
    must not be read as "same organization".
    """
    if doi_equivalent(claimed.claimed_doi, resolved.doi):
        return True
    return bool(claimed.year and resolved.year
                and int(claimed.year) == int(resolved.year)
                and journal_equivalent(claimed.journal, resolved.journal)
                and _volume_agrees(claimed, resolved)
                and _first_pages_agree(claimed, resolved))


def first_author_equivalent(claimed: ClaimedRef, resolved: RetrievedRecord) -> bool:
    if _corporate_author_equivalent(claimed, resolved):
        return True
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


_ORDINAL_WORDS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
                  "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10}
# A spelled-out or digit ordinal immediately qualifying an edition-family noun
# ("Second Edition", "3rd revision", "second revised edition"). Bound to the
# noun on purpose: a bare "first"/"second" ("first-line therapy") is ordinary
# title vocabulary and must not read as a series marker.
_EDITION_ORDINAL_RE = re.compile(
    r"\b(?:(first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)"
    r"|(\d{1,2})(?:st|nd|rd|th))[\s-]+(?:revised[\s-]+)?"
    r"(?:edition|update|revision|version|part|report)s?\b", re.I)


def _edition_ordinals(title: str) -> set[int]:
    out: set[int] = set()
    for word, digits in _EDITION_ORDINAL_RE.findall(title or ""):
        out.add(int(digits) if digits else _ORDINAL_WORDS[word.lower()])
    return out


def _roman_series_ordinals(title: str) -> set[str]:
    title_without_dotted_iv = _DOTTED_IV_ABBREVIATION_RE.sub(" ", title or "")
    return {token.lower() for token in _ROMAN_RE.findall(title_without_dotted_iv)}


def _series_conflict(claimed_title: str, resolved_title: str) -> bool:
    a = _roman_series_ordinals(claimed_title)
    b = _roman_series_ordinals(resolved_title)
    if a and b and a != b:
        return True
    # Serial annual/periodic editions differ only by an embedded 4-digit year
    # ("...Statistics-2017 Update" vs "...-2019 Update"; "Standards of Care-2019"
    # vs "-2021"). Distinct papers in the same series -- NOT the same work -- even
    # when title_sim ~1.0, the first author is identical, and a DOI was mis-attached
    # across editions. Fires only when both titles carry a year and they share NONE.
    ya = set(_TITLE_YEAR_RE.findall(claimed_title or ""))
    yb = set(_TITLE_YEAR_RE.findall(resolved_title or ""))
    if ya and yb and not (ya & yb):
        return True
    # Spelled-out/digit ordinal editions ("Second Edition" vs "Third Edition")
    # are the same serial-edition conflict without a roman numeral or an embedded
    # year (adversarial review, 2026-07-15: a shared run-on DOI plus title_sim
    # ~0.96 let a guideline edition family through RULE A). Same both-sides,
    # share-NONE contract as the year rule.
    ea = _edition_ordinals(claimed_title)
    eb = _edition_ordinals(resolved_title)
    return bool(ea and eb and not (ea & eb))


def _derivative_block(claimed: ClaimedRef, resolved: RetrievedRecord) -> str:
    ct, rt = canonical_title(claimed.title), canonical_title(resolved.title)
    if ct != rt and _DERIVATIVE_RE.search(resolved.title or ""):
        return "derivative_publication"
    if ct != rt and _DERIVATIVE_RE.search(claimed.title or ""):
        return "derivative_publication"
    if _series_conflict(claimed.title, resolved.title):
        return "series_ordinal_conflict"
    left_raw = claimed.authors[0] if claimed.authors else ""
    right_raw = resolved.authors[0] if resolved.authors else ""
    # A corporate-author conflict is AFFIRMATIVE two-organization evidence, so the
    # block is NOT raised merely because the format keys differ. It is lifted only
    # when BOTH hold: the two names are not in conflict (neither carries a
    # distinctive word the other cannot account for), AND the pair is physically
    # proven to be one work (exact shared DOI, or venue+volume+first-page+year).
    # Either condition alone is insufficient -- the National vs International
    # Committee for Pediatric Care pair shares a run-on DOI and stays blocked on
    # the name conflict, while a merely similar-looking name with no physical
    # anchor stays blocked for want of proof.
    if (_CORPORATE_RE.search(left_raw) and _CORPORATE_RE.search(right_raw)
            and not _corporate_author_equivalent(claimed, resolved)
            and not (not _corporate_names_conflict(claimed, resolved)
                     and _corporate_physically_sufficient(claimed, resolved))):
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


def _has_non_english_evidence(resolved: RetrievedRecord) -> bool:
    """Explicit MEDLINE non-English marker: a language other than English, OR an
    'English Abstract' publication type (PubMed's tag for the translated abstract
    of a non-English-language article). An independent signal from the bracketed
    title; empty/unknown language with no such pubtype is NOT read as non-English.
    """
    if (resolved.language or "").strip().lower() not in ("", "eng", "en"):
        return True
    return "english abstract" in {p.lower()
                                  for p in (resolved.publication_types or [])}


def _first_pages_agree(claimed: ClaimedRef, resolved: RetrievedRecord) -> bool:
    """Both first pages present and equal after stripping to digits. Used as the
    numeric anchor that REPLACES volume only when the resolved volume is absent.

    A supplement/poster locator ("S344", "P1025") and a plain article page
    ("344") are DIFFERENT physical locations in the same volume, so the digit
    comparison additionally requires supplement-status parity (adversarial
    review, 2026-07-15: without it, a meeting abstract's S-page anchored RULE G
    and the mixed-identity rule to the co-numbered full article's slot)."""
    if _is_supplement_locator(claimed.pages) != _is_supplement_locator(resolved.pages):
        return False
    a = re.sub(r"\D", "", _first_page(claimed.pages))
    b = re.sub(r"\D", "", _first_page(resolved.pages))
    return bool(a and b and a == b)


def _mixed_identity_citation(claimed: ClaimedRef, resolved: RetrievedRecord,
                             *, title_similarity: float, journal: bool) -> bool:
    """Strictly quarantine a citation assembled from two different works.

    The resolved-work anchors must be unusually complete (exact DOI, venue,
    volume, and first page), while the cited-work identity disagrees in both a
    substantive title and a non-trivial author roster.  A >=2-year gap keeps
    ordinary online-first/print-year drift out of this ambiguity-only rule.
    """
    if not (doi_equivalent(claimed.claimed_doi, resolved.doi)
            and journal and _volume_agrees(claimed, resolved)
            and _first_pages_agree(claimed, resolved)
            and claimed.year and resolved.year
            and abs(int(claimed.year) - int(resolved.year)) >= 2
            and is_distinctive_title(claimed.title)
            and is_distinctive_title(resolved.title)
            and 0.55 <= title_similarity < DOI_BIBLIOGRAPHIC_ANCHOR_TITLE_MIN):
        return False
    claimed_names, resolved_names = _surname_set(claimed.authors), _surname_set(resolved.authors)
    return len(claimed_names - resolved_names) >= 2


# Generic journal-name words that are NOT a distinctive family token. Private to
# _journal_family_transliteration (RULE F): its leading-token match must never key
# on one of these (e.g. two different "Journal of Clinical ..." titles).
_JOURNAL_FAMILY_GENERIC = frozenset({
    "journal", "international", "clinical", "medical", "the", "annals",
    "archives", "acta", "review", "reviews", "bulletin", "research",
    "national", "european", "american", "science", "sciences",
})


def _journal_family_transliteration(left: str, right: str) -> bool:
    """Narrow transliteration match on the LEADING DISTINCTIVE journal token, used
    ONLY inside RULE F (translated_title_missing_volume_anchors). A translated
    Russian/East-European masthead and its citing transliteration share a
    distinctive stem that journal_equivalent / _near_transliteration miss when the
    rest of the two mastheads diverge (e.g. 'Khirurgiya. Zhurnal im. N.I.
    Pirogova' vs 'Khirurgiia (Mosk)'; 'Anesteziologiya...' vs 'Anesteziol...').
    Takes the first token of length >= 6 that is not a generic journal word and
    requires Jaro-Winkler >= 0.90 on those two tokens. Looser than
    journal_equivalent, so it is confined to this rule's full conjunction."""
    def lead(value: str) -> str:
        for tok in canonical_title(value).split():
            if len(tok) >= 6 and tok not in _JOURNAL_FAMILY_GENERIC:
                return tok
        return ""
    a, b = lead(left), lead(right)
    if not a or not b:
        return False
    return JaroWinkler.similarity(a, b) >= 0.90


def _abbreviated_journal_anchor(left: str, right: str) -> bool:
    """Very narrow ``Med. J``-style evidence, private to RULE G.

    Short journal abbreviations are too ambiguous to relax global journal
    matching.  Here they are only one corroborator among an exact DOI, year,
    volume, first page, and substantive title, so accept a two-token form ending
    in ``J`` when its substantive token prefixes a word in a full ``... Journal``
    title.
    """
    a, b = canonical_title(left).split(), canonical_title(right).split()
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    if len(short) != 2 or short[-1] != "j" or len(short[0]) < 3:
        return False
    return ("journal" in long
            and any(tok.startswith(short[0]) for tok in long if len(tok) >= 4))


def _is_supplement_locator(pages: str) -> bool:
    return bool(_SUPPLEMENT_PAGE_RE.match(pages or ""))


def is_abstract_locator(pages: str) -> bool:
    """The page field reads as an ABSTRACT locator rather than an article page
    range -- a supplement/poster page, an e-locator, or a bare abstract number."""
    return bool(_ABSTRACT_LOCATOR_RE.match(pages or ""))


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


def _lineage_surname_set(authors: list[str]) -> set[str]:
    """Surname proxies for a lineage roster, WITHOUT ``_surname_set``'s >=4-char
    floor.

    That floor silently drops every short romanized surname -- Mao, Li, Xie, Lau,
    Liu, Sun, Hu, Ma -- so ``_roster_containment`` reads 0.00 for an all-short
    roster that in fact matches perfectly (PMC12733676:B29: Mao/Li/Xie/Lau against
    Mao/Li/Xie/Lau/Wang/Smolley). ``_surname_set`` is left alone because it feeds
    RULE B, whose thresholds were calibrated against its output; changing it would
    move that rule frame-wide. Recorded as a defect of ``_surname_set`` in its own
    right -- the blindness is systematic for CJK-romanized names.
    """
    out: set[str] = set()
    for a in authors or []:
        for t in canonical_title(a).split():
            if len(t) >= 2:
                out.add(t)
    return out


def _lineage_roster_containment(claimed: ClaimedRef,
                                resolved: RetrievedRecord) -> float:
    ca, ra = _lineage_surname_set(claimed.authors), _lineage_surname_set(resolved.authors)
    if not ca or not ra:
        return 0.0
    return len(ca & ra) / len(ca)


def version_chain_same_work(claimed: ClaimedRef, resolved: RetrievedRecord, *,
                            title_similarity: float,
                            preprint_source: bool) -> bool:
    """§15.2: whether the pair are two nodes of ONE publication lineage.

    Gated first on the claimed side reading as a NON-FINAL node -- an abstract
    locator or a preprint venue. That gate is what separates this rule from RULE
    B's hard case: the sibling-TRIAL family (serial trialists running different
    trials with one core team and a shared drug/disease title template). Then one
    of two routes must establish the lineage:

      ROUTE 1 (shared identifier across nodes). The claimed abstract record
      carries the FULL PAPER's DOI -- the venue, volume and pages all differ
      because they are different nodes, but the identifier is the same work's
      (PMC9829249:R20: an Atherosclerosis abstract at e46 carrying the JACC
      paper's DOI). An exact DOI agreement across a node boundary is direct
      lineage evidence and needs no content threshold beyond a title floor.

      ROUTE 2 (content lineage). No shared identifier, so the claim rests on
      content: this is RULE B's evidence with its TITLE and ROSTER floors relaxed
      (0.87 -> 0.80, 0.75 -> 0.60), because a lineage node is routinely retitled
      and re-rostered between the abstract and the paper, while RULE B's other two
      guards are kept AT FULL STRENGTH. Those two are what exclude the sibling
      trials, and relaxing them is what made the first draft of this rule swallow
      the whole adversarial-hardening negative set:
        * distinctive-token count >= 6 excludes a generic abstract title
          ("Empagliflozin in heart failure", 3 tokens) that ANY sibling's full
          title trivially covers;
        * content coverage >= 0.77 excludes a sibling whose population/endpoint
          qualifier diverges (DAPA-HF "Reduced" vs "Mildly Reduced or Preserved"
          scores 0.75).

    Identifier DISAGREEMENT is not a conjunct of either route: changing
    identifiers is the defining property of a lineage, not evidence against it.

    ``preprint_source`` is passed in rather than computed here: the preprint
    predicates live in biblio_match, which imports this module.
    """
    if not (is_abstract_locator(claimed.pages) or preprint_source):
        return False
    if title_similarity < VERSION_CHAIN_TITLE_MIN:
        return False
    if _lineage_roster_containment(claimed, resolved) < VERSION_CHAIN_ROSTER_MIN:
        return False
    if doi_equivalent(claimed.claimed_doi, resolved.doi):
        return True                                          # ROUTE 1
    return (len(_distinctive_title_tokens(claimed.title))
            >= CONFERENCE_ABSTRACT_MIN_DISTINCTIVE_TOKENS
            and _abstract_content_coverage(claimed, resolved)
            >= CONFERENCE_ABSTRACT_CONTENT_COVERAGE_MIN)     # ROUTE 2


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
        # A corporate-author conflict is not decisive against an EXACT shared DOI
        # when the two institutional names are not different bodies. RULE A2 is
        # offered the row HERE rather than letting it fall through to the ordinary
        # rules -- falling through would let the institutional-document rules clear
        # a parent body's work against its committee's DIFFERENT work (AAP
        # "Dietary guidance for infants" vs "...for children", one shared DOI).
        if blocked == "corporate_author_conflict":
            anchored = _doi_anchored_same_work(claimed, resolved,
                                               title_similarity=title_similarity)
            if anchored is not None:
                return anchored
        return WorkIdentityEvidence(False, blocked_by=blocked)

    # RULE A2 (exact shared DOI + boundary-tolerant content). RULE A above requires
    # the first-author POSITION to agree and a near-identical TITLE, so it cannot
    # see the dominant false-positive shape: a reference whose author/title/journal
    # BOUNDARY was parsed in the wrong place. One misplaced boundary makes the
    # author, the title and the journal each look wrong, and the matcher then counts
    # three "independent" disagreements that are really one parsing fault -- e.g. a
    # group author left in the title slot, or the journal name swallowed into it.
    #
    # An exact DOI is a globally-unique work identifier, so it is treated here as
    # overwhelming evidence, checked against the citation's slots as a WHOLE rather
    # than field by field (_boundary_tolerant_agreement). Three affirmative
    # counter-signals still refute it, and each is load-bearing against a committed
    # negative:
    #   * a series/edition conflict  (annual editions sharing a run-on DOI);
    #   * two genuinely DIFFERENT organizations (National vs International
    #     Committee for Pediatric Care; "AAP" vs "American Academy of Pediatrics");
    #   * content that does not agree in EITHER direction, which is what a
    #     contaminated DOI looks like (PMC8015328:ref011, Paurodontella persica vs
    #     compostiocola, scores 0.667; an adjacent article scores 0.750).
    # The destination is the AUDITED same-work band, never ``match``: a shared DOI
    # is strong enough to lift a row out of the wrong-paper band, not to auto-clear
    # it (§16.2).
    first_author = first_author_equivalent(claimed, resolved)
    first_author_typo = _first_author_typo(claimed, resolved)
    author_overlap = _author_overlap(claimed, resolved)
    journal = journal_equivalent(claimed.journal, resolved.journal)
    doi = _doi_match(claimed, resolved)
    locator = _locator_match(claimed, resolved)
    same_year = bool(claimed.year and resolved.year and claimed.year == resolved.year)

    # RULE G (overwhelming bibliographic anchor).  A malformed author field can
    # invert given names into the surname position.  Do not relax author matching
    # globally: quarantine only when an exact DOI is independently corroborated
    # by exact year, equivalent venue, matching volume AND first page, plus a
    # substantive title.  Derivative/series/corporate conflicts were rejected
    # above, so this cannot turn a related report or edition into a same work.
    if (_mixed_identity_citation(claimed, resolved, title_similarity=title_similarity,
                                 journal=journal)):
        return WorkIdentityEvidence(True, "mixed_identity_citation",
                                    ("exact_doi", "journal", "volume", "first_page",
                                     "year_gap>=2", "title_and_roster_conflict"))

    if (doi and same_year
            and (journal or _abbreviated_journal_anchor(claimed.journal, resolved.journal))
            and _volume_agrees(claimed, resolved)
            and _first_pages_agree(claimed, resolved)
            and title_similarity >= DOI_BIBLIOGRAPHIC_ANCHOR_TITLE_MIN
            and is_distinctive_title(claimed.title)
            and is_distinctive_title(resolved.title)):
        return WorkIdentityEvidence(True, "overwhelming_bibliographic_anchor",
                                    ("exact_doi", "year", "journal", "volume",
                                     "first_page",
                                     "title_sim>=%.2f" % DOI_BIBLIOGRAPHIC_ANCHOR_TITLE_MIN))

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

    # RULE F (translation whose only volume anchor is ABSENT in PubMed).  Some
    # Russian / East-European journals carry NO Volume in PubMed and the citing
    # source stores the ISSUE in <volume>, so RULE D's _volume_agrees can never
    # confirm identity (seed 31: r125/15938103, r97/12698653 -- both translated
    # same-work rows mis-banding review_wrong_paper).  When the resolved volume is
    # genuinely absent, matching FIRST PAGE stands in as the numeric anchor.  Fires
    # ONLY on the full conjunction -- PubMed-bracketed translated title, explicit
    # non-English evidence (language or 'English Abstract' pubtype), EXACT year,
    # resolved volume ABSENT, matching first page, a transliterated first author,
    # and journal-family transliteration -- a near-unique key that replaces the one
    # missing signal.  Tiered title floor (both rule-local; no GLOBAL threshold
    # touched): the 0.85 transliteration floor fires outright (r97); the 0.78 floor
    # fires ONLY with roster containment >= 0.60 as a backstop (r125).  The
    # resolved-volume-ABSENT precondition is load-bearing: when a resolved volume
    # EXISTS this rule DEFERS and RULE D's volume guard still governs, so a genuine
    # volume disagreement stays wrong-paper (seed 29's 12500577 keeps volume on both
    # sides and routes via RULE D, untouched).  The runtime record does not preserve
    # the resolved issue, so this proves only "translated same work, volume anchor
    # missing", never issue-to-volume identity.
    if (_pubmed_bracketed(resolved.title)
            and _has_non_english_evidence(resolved)
            and same_year
            and not re.sub(r"\D", "", resolved.volume or "")
            and _first_pages_agree(claimed, resolved)
            and (_first_author_typo(claimed, resolved)
                 or first_author_equivalent(claimed, resolved))
            and (journal_equivalent(claimed.journal, resolved.journal)
                 or _near_transliteration(claimed.journal, resolved.journal)
                 or _journal_family_transliteration(claimed.journal, resolved.journal))):
        high_tier = title_similarity >= TRANSLATION_TRANSLIT_TITLE_MIN
        low_tier = (title_similarity >= TRANSLATION_MISSING_VOLUME_TITLE_MIN
                    and _roster_containment(claimed, resolved)
                    >= TRANSLATION_MISSING_VOLUME_ROSTER_MIN)
        if high_tier or low_tier:
            return WorkIdentityEvidence(
                True, "translated_title_missing_volume_anchors",
                ("pubmed_translated_title", "non_english", "year",
                 "resolved_volume_absent", "first_page",
                 "transliterated_first_author", "journal_family",
                 ("title_sim>=%.2f" % TRANSLATION_TRANSLIT_TITLE_MIN) if high_tier
                 else ("title_sim>=%.2f+roster" % TRANSLATION_MISSING_VOLUME_TITLE_MIN)))

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
    if (is_abstract_locator(claimed.pages)
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

    # RULE A2 as a LAST RESORT, deliberately after every specific rule so it
    # changes no existing reason code: it only ever converts a row that would
    # otherwise fall through to wrong-paper.
    anchored = _doi_anchored_same_work(claimed, resolved,
                                       title_similarity=title_similarity)
    if anchored is not None:
        return anchored

    return WorkIdentityEvidence(False)
