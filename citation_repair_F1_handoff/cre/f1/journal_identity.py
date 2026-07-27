"""F2-G — authoritative journal identity (spec §8).

The containment comparator in ``work_identity.journal_equivalent`` is a useful
weak similarity feature but is NOT strong enough to prove the physical-location
identity F2-C rests on ("Blood" vs "Blood Adv" both contain-match; a
token-alignment rewrite was tested and rejected because it broke
``Antioxidants`` / ``Antioxidants (Basel)`` etc.). Per §8 the fix is to add
STRONGER layers AHEAD of containment, never to rewrite it:

    1. ISSN intersection        (authoritative) -- both sides carry ISSN sets and
                                                    they intersect.
    2. authority alias -> NLM   (authoritative) -- both normalized strings map,
       unique canonical ID                         via a pinned NLM serials
                                                    snapshot, to ONE canonical
                                                    journal ID (equal IDs = same
                                                    journal; different = distinct).
    3. manual alias -> NLM      (authoritative) -- a reviewed, versioned alias
       unique canonical ID                         mapping (ZD's hand-built list).
    4. exact normalized text    (NON-authoritative) -- identical strings alone do
                                                    not prove a unique record.
    5. containment heuristic    (NON-authoritative) -- the unchanged fallback.

Only methods 1-3 set ``authoritative=True``; only those may satisfy F2-C's
``journal_match_authoritative`` gate (§8.1). Every authority lookup is EXACT on
the normalized string -- never fuzzy -- because a fuzzy authority step is exactly
how ``Blood`` ≡ ``Blood Adv`` recurs (§8.2). An alias that maps to more than one
canonical record is AMBIGUOUS and yields ``None``, not ``True``.

DATA: the authority table loads from a PINNED NLM serials snapshot (Serfile) plus
a reviewed manual-alias table; snapshot date/format/license/SHA-256 belong in the
run manifest (§8.2). Absent a snapshot, ``JournalAuthority`` is empty, every
authority/ISSN lookup misses, and journal comparison falls through to the
unchanged containment result -- so behavior is byte-identical to pre-F2-G until a
snapshot is dropped in. Building alias mappings and the residual-census review is
tracked in §8.3; ``EMBO Rep`` (corrupt expansion) and ``Wei Sheng Yan Jiu``
(translated) stay review-only until a stable mapping is proved.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

from .work_identity import canonical_title, journal_equivalent

# journal_match_method values (§8.1).
M_EXACT_TEXT = "exact_text"
M_AUTHORITY_ALIAS = "authority_alias_unique"
M_ISSN = "issn_intersection"
M_NLM_ID = "nlm_unique_id"
M_CONTAINMENT = "containment_heuristic"
M_MANUAL_ALIAS = "manual_alias_unique"
M_AMBIGUOUS = "ambiguous_alias"
M_UNAVAILABLE = "unavailable"

_AUTHORITATIVE_METHODS = frozenset(
    {M_AUTHORITY_ALIAS, M_ISSN, M_NLM_ID, M_MANUAL_ALIAS})


@dataclass
class JournalAuthority:
    """Normalized-alias -> canonical NLM journal ID mapping from a pinned snapshot.

    ``aliases`` maps a normalized journal string to the SET of canonical IDs it
    occurs under; a set of size > 1 is an ambiguous alias (§8.2). ``methods``
    records whether each alias came from the NLM snapshot (authority) or the
    reviewed manual table. Empty by default -- an absent snapshot means every
    lookup misses and comparison falls through to containment."""
    aliases: dict[str, set[str]] = field(default_factory=dict)
    methods: dict[str, str] = field(default_factory=dict)
    snapshot_sha256: str = ""
    snapshot_date: str = ""

    def add(self, alias: str, canonical_id: str, *, method: str = M_AUTHORITY_ALIAS) -> None:
        key = canonical_title(alias)
        if not key or not canonical_id:
            return
        self.aliases.setdefault(key, set()).add(canonical_id)
        # A manual mapping is recorded as such only when no authority mapping
        # already claims the alias; authority wins the method label.
        self.methods.setdefault(key, method)

    def _canonical_ids(self, normalized: str) -> Optional[set[str]]:
        return self.aliases.get(normalized)

    def compare(self, left_norm: str, right_norm: str) -> Optional[tuple[Optional[bool], str]]:
        """Return ``(match, method)`` when the authority can DECIDE, else None.

        ``match`` is True when both sides map to one shared canonical ID, False
        when both map uniquely to DIFFERENT IDs (authoritative distinct journals),
        and None when either side's alias is ambiguous (maps to >1 ID). Returns
        None (authority abstains -> caller falls through) when either side is
        absent from the table."""
        lids, rids = self._canonical_ids(left_norm), self._canonical_ids(right_norm)
        if lids is None or rids is None:
            return None                       # not in the authority -> abstain
        if len(lids) > 1 or len(rids) > 1:
            return (None, M_AMBIGUOUS)         # ambiguous alias -> None, not True
        method = M_MANUAL_ALIAS if M_MANUAL_ALIAS in (
            self.methods.get(left_norm), self.methods.get(right_norm)
        ) else M_AUTHORITY_ALIAS
        return (bool(lids & rids), method)


# Module-level authority. A loader populates this from a pinned snapshot at run
# start; empty here so no unversioned/live lookup ever sneaks in (spec §8.2).
JOURNAL_AUTHORITY = JournalAuthority()


def journal_identity(left: str, right: str, *,
                     left_issns: Optional[list[str]] = None,
                     right_issns: Optional[list[str]] = None,
                     authority: Optional[JournalAuthority] = None
                     ) -> tuple[Optional[bool], str, bool]:
    """Layered journal comparison (§8.1). Returns
    ``(journal_match, journal_match_method, journal_match_authoritative)``.

    Layers are tried strongest-first; only ISSN/authority/manual methods are
    authoritative. The containment fallback delegates UNCHANGED to
    ``journal_equivalent`` so no regression pair (``Antioxidants`` /
    ``Antioxidants (Basel)``, ``Agric. Food Chem.`` / ``J Agric Food Chem``,
    ``Angew. Chem. Int. Ed.`` / ``Angew Chem Int Ed Engl``) is disturbed."""
    L, R = canonical_title(left), canonical_title(right)
    if not L or not R:
        return (None, M_UNAVAILABLE, False)

    # 1. ISSN intersection -- authoritative. Only when BOTH sides carry an ISSN
    # set; a provider's ability to return an ISSN does not create a written-side
    # ISSN (§8.2), so in citation-vs-record comparison this is usually inert.
    if left_issns and right_issns:
        li = {s.strip().lower() for s in left_issns if s and s.strip()}
        ri = {s.strip().lower() for s in right_issns if s and s.strip()}
        if li & ri:
            return (True, M_ISSN, True)

    # 2/3. Authority / manual alias -> canonical NLM ID -- authoritative, EXACT
    # lookup on the normalized string. Ambiguous alias -> None.
    auth = authority if authority is not None else JOURNAL_AUTHORITY
    decided = auth.compare(L, R)
    if decided is not None:
        match, method = decided
        return (match, method, method in _AUTHORITATIVE_METHODS)

    # 4. Exact normalized text -- NON-authoritative (§8.1).
    if L == R:
        return (True, M_EXACT_TEXT, False)

    # 5. Containment heuristic -- NON-authoritative, unchanged comparator.
    return (journal_equivalent(left, right), M_CONTAINMENT, False)


def containment_only_census(pairs) -> list[dict]:
    """Rank the distinct ``(written_journal, resolved_journal)`` pairs that match
    ONLY by the containment heuristic (§8.3) -- the review artifact from which a
    human builds versioned manual aliases. ``pairs`` is an iterable of
    ``(written_journal, resolved_journal)``; a pair is included when
    ``journal_identity`` matches it via ``containment_heuristic`` (True, method ==
    M_CONTAINMENT). Ranked by frequency, most frequent first."""
    counts: Counter = Counter()
    for left, right in pairs:
        match, method, _auth = journal_identity(left or "", right or "")
        if match is True and method == M_CONTAINMENT:
            counts[(left, right)] += 1
    return [{"written_journal": w, "resolved_journal": r, "count": n}
            for (w, r), n in counts.most_common()]
