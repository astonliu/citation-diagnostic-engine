"""F8 -- the retraction gate in Band 1 (RESEARCH_PLAN_v2.2 §4.3).

F8 was declared in the taxonomy, reserved in the disposition label vocabulary,
and never assigned by any line of code: a citation to a retracted paper resolved
normally, matched normally, and cleared. This module is the acceptance suite for
the deterministic gate that closes that hole.

The two things it exists to hold still:

  1. THE PT INVERSION. PubMed's "Retracted Publication" (this article WAS
     retracted) and "Retraction of Publication" (this article IS the notice) mean
     opposite things. Matching the wrong one -- or substring-matching "Retract" --
     flags every retraction notice and misses every retracted paper, and would
     still look like a working detector. Both are asserted, in both directions.

  2. THE TRI-STATE. Unknown (no resolved PMID, or a failed lookup) is a third
     state, tested with ``is``. An EFetch outage must never read as "not
     retracted", and must never be cached.

No network: ``ncbi_pubtypes`` is monkeypatched on the CONSUMER module's namespace
(``run``), which is how ncbi_meta's helpers are documented to be faked.

Run:  PYTHONPATH=<repo> python -m pytest cre/f1/test_f8_retraction_gate.py -q
"""
from __future__ import annotations

import pytest

from cre.f1 import run as runmod
from cre.f1 import ncbi_meta, reason_registry as rr
from cre.f1 import schema as S
from cre.f1.decide import decide
from cre.f1.preband_disposition import (DISPOSITION_LABELS, FAULT_LABELS,
                                        build_rows)
from cre.f1.run import process_reference, retraction_state
from cre.f1.schema import ClaimedRef, Reference, RetrievedRecord

RETRACTED = "Retracted Publication"
#: What PubMed emits for a retraction NOTICE today (32967 records on 2026-08-15).
NOTICE = "Retraction Notice"
#: The historical MeSH name for the same thing -- zero live records on
#: 2026-08-15, but pinned so a legacy/cached record still reads correctly.
NOTICE_LEGACY = "Retraction of Publication"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _ref(pmid: str = "12345678", title: str = "A study of something",
         year: int = 2015) -> Reference:
    return Reference(
        citation_id=f"PMC1000000:bibr-{pmid}",
        citance="As shown previously [1].",
        claimed=ClaimedRef(title=title, authors=["Smith"], year=year,
                           journal="J Test", claimed_pmid=pmid))


def _resolved(ref: Reference, title: str | None = None) -> Reference:
    """Give the reference a resolved record that MATCHES its claim, so the only
    thing under test is the retraction gate (not the matcher).

    The log fields a live ``compare_and_flag`` would have set are pre-filled too,
    so the ``decide``-only tests exercise the same state the pipeline produces.
    """
    ref.retrieved = RetrievedRecord(
        resolved=True, title=title if title is not None else ref.claimed.title,
        authors=list(ref.claimed.authors), year=ref.claimed.year,
        journal=ref.claimed.journal, pmid=ref.claimed.claimed_pmid)
    ref.log.pmid_present = bool(ref.claimed.claimed_pmid)
    ref.log.pmid_resolved = True
    ref.log.title_similarity = 100.0
    return ref


def _boom(_prompt: str) -> str:                # the LLM must never run on F8
    raise AssertionError("LLM filter must not run on the F8 path")


class _PubtypeStub:
    """Stands in for ``ncbi_meta.ncbi_pubtypes``; records every PMID it was asked
    about so 'cached' vs 're-attempted' is observable."""

    def __init__(self, table: dict, default=None):
        self.table = table
        self.default = default
        self.calls: list[str] = []

    def __call__(self, pmid, api_key="", email="", session=None):
        self.calls.append(str(pmid))
        return self.table.get(str(pmid), self.default)


@pytest.fixture
def stub(monkeypatch):
    """Patch the consumer namespace (``run``) and hand the test the stub.

    ``fetch_pubmed`` is stubbed alongside it so ``process_reference`` cannot
    reach the network: it returns whatever record the test prepared on the
    reference via :func:`_resolved`.
    """
    holder = _PubtypeStub({})

    def _dispatch(pmid, api_key="", email="", session=None):
        return holder(pmid, api_key, email, session)

    monkeypatch.setattr(runmod, "ncbi_pubtypes", _dispatch)
    monkeypatch.setattr(runmod, "fetch_pubmed",
                        lambda pmid, api_key="", session=None:
                        holder.records.get(str(pmid),
                                           RetrievedRecord(resolved=False,
                                                           pmid=str(pmid))))
    holder.records = {}
    return holder


def _prepared(stub, ref: Reference) -> Reference:
    """Register a reference's prepared resolved record with the fetch stub."""
    stub.records[ref.claimed.claimed_pmid] = ref.retrieved
    return ref


# ==========================================================================
# The PT inversion -- the one thing that must not be got backwards
# ==========================================================================
def test_is_retracted_matches_retracted_publication_exactly():
    assert ncbi_meta.is_retracted(["Journal Article", RETRACTED]) is True
    assert ncbi_meta.is_retracted(["journal article", "retracted publication"]) is True


def test_is_retracted_is_false_for_the_retraction_notice():
    """A notice type marks the article that RETRACTS something else, not a
    retracted work. Both the live and the historical spelling are pinned."""
    for notice in (NOTICE, NOTICE_LEGACY):
        assert ncbi_meta.is_retracted(["Journal Article", notice]) is False
        assert ncbi_meta.is_retracted([notice]) is False
    assert {n.lower() for n in (NOTICE, NOTICE_LEGACY)} == \
        set(ncbi_meta.RETRACTION_NOTICE_PUBTYPES)


def test_is_retracted_does_not_substring_match_on_retract():
    for pt in ("Retraction of Publication", "Retracted Publication as Topic",
               "Retraction Notice", "Retraction", "Published Erratum"):
        assert ncbi_meta.is_retracted([pt]) is (pt == RETRACTED)


def test_is_retracted_is_none_on_unknown_and_false_on_plain_article():
    assert ncbi_meta.is_retracted(None) is None
    assert ncbi_meta.is_retracted(["Journal Article"]) is False


# ==========================================================================
# Acceptance matrix -- the label outcomes
# ==========================================================================
def test_retracted_publication_labels_f8_high(stub):
    stub.table = {"12345678": ["Journal Article", RETRACTED]}
    out = process_reference(_prepared(stub, _resolved(_ref())), _boom)
    assert out.label == S.F8
    assert out.confidence == "HIGH"
    assert out.log.retracted is True
    assert out.log.decided_by == "retracted_publication_pubtype"


def test_retraction_notice_alone_is_not_f8(stub):
    """The notice is a real, existing, correctly-cited paper -- it clears."""
    stub.table = {"12345678": ["Journal Article", NOTICE]}
    out = process_reference(_prepared(stub, _resolved(_ref())), _boom)
    assert out.label != S.F8
    assert out.label == S.CLEARED
    assert out.log.retracted is False


def test_neither_type_present_leaves_the_label_unchanged(stub):
    stub.table = {"12345678": ["Journal Article", "Review"]}
    out = process_reference(_prepared(stub, _resolved(_ref())), _boom)
    assert out.label == S.CLEARED
    assert out.log.retracted is False
    assert out.log.retraction_reason == ""


def test_lookup_outage_is_unknown_not_false(stub):
    """``ncbi_pubtypes`` -> None. The state is None, NOT False, and the label is
    whatever it would have been without the gate."""
    stub.default = None
    out = process_reference(_prepared(stub, _resolved(_ref())), _boom)
    assert out.log.retracted is None
    assert out.log.retracted is not False        # the whole point of the tri-state
    assert out.label == S.CLEARED


def test_empty_pubtype_list_is_unknown_not_false(stub):
    """Every live MEDLINE record carries at least one PT line, so an empty parse
    is a failure to READ the field, not a record that has none."""
    stub.table = {"12345678": []}
    out = process_reference(_prepared(stub, _resolved(_ref())), _boom)
    assert out.log.retracted is None


def test_no_resolved_pmid_attempts_no_lookup(stub):
    """A dead PMID resolves to nothing -- there is no record to ask about."""
    ref = _ref()
    ref.retrieved = RetrievedRecord(resolved=False, pmid="12345678")
    assert retraction_state(ref, cache={}) is None
    assert stub.calls == []


def test_noid_reference_has_no_pmid_to_look_up(stub):
    """fuzzy_biblio_lookup's record always carries ``.pmid == ""``."""
    ref = _ref(pmid="")
    ref.retrieved = RetrievedRecord(resolved=True, title=ref.claimed.title,
                                    pmid="")
    assert retraction_state(ref, cache={}) is None
    assert stub.calls == []


# ==========================================================================
# Precedence
# ==========================================================================
def test_f8_precedes_the_same_work_quarantine():
    ref = _resolved(_ref())
    ref.log.retracted = True
    ref.log.same_work_reason = "near_identical_title"
    out = decide(ref, True, None, None)
    assert out.label == S.F8
    assert out.log.retraction_reason == "retracted_publication"


def test_f8_now_wins_over_an_unscoreable_title():
    """REVERSED, deliberately. This test previously asserted the opposite, on the
    reasoning that "a non-title comparison carries no citation to judge at all".

    That reasoning is about TITLE COMPARABILITY, which is what F2 needs. F8 needs
    a resolved PMID and two dates; a missing or unparseable claimed title says
    nothing about whether the cited work was retracted before it was cited.
    Verified on PMC7474863, which cites two retracted Surgisphere papers 94 and
    93 days after their notices: the ``element-citation`` one was labelled F8 and
    the ``mixed-citation`` one -- no ``<article-title>``, hence
    ``no_claimed_title`` -- was booked UNSCOREABLE. F8 recall was a function of
    the publisher's XML markup rather than of the citation.
    """
    ref = _resolved(_ref())
    ref.log.retracted = True
    ref.log.unscoreable_reason = "journal_as_title"
    out = decide(ref, False, None, None)
    assert out.label == S.F8
    assert out.log.retracted is True
    assert out.log.retraction_reason == "retracted_publication"
    # The unscoreable reason is NOT erased. It was measured and it stays on the
    # log; it simply no longer decides the label.
    assert out.log.unscoreable_reason == "journal_as_title"


def test_unscoreable_still_wins_when_there_is_no_retraction():
    """The other half of the reordering, and the one that keeps it honest: only a
    POSITIVE ``retracted is True`` outranks UNSCOREABLE. False (types fetched,
    not retracted) and None (never learned) must both leave the row unscoreable
    rather than promoting an unknown into an accusation."""
    for state in (False, None):
        ref = _resolved(_ref())
        ref.log.retracted = state
        ref.log.unscoreable_reason = "journal_as_title"
        out = decide(ref, False, None, None)
        assert out.label == S.UNSCOREABLE, state
        assert out.log.decided_by == "unscoreable", state
        assert out.log.retraction_reason == "", state


def test_f8_fires_on_an_unflagged_reference():
    """A retracted source that matches its citation perfectly would otherwise
    clear -- that is exactly the defect F8 closes."""
    ref = _resolved(_ref())
    ref.log.retracted = True
    out = decide(ref, False, None, None)
    assert out.label == S.F8


def test_f8_takes_a_reference_out_of_the_f2_population():
    """The stated downstream cost, asserted rather than assumed: a would-be F2
    that is retracted lands on F8, not F2."""
    ref = _resolved(_ref(), title="An entirely different paper")
    ref.log.pmid_present = True
    ref.log.pmid_resolved = True
    ref.log.retracted = True
    out = decide(ref, True, S.V_REFERENCE_ERROR,
                 {"pubmed": 97, "crossref": 0, "openalex": 0})
    assert out.label == S.F8


def test_unknown_retraction_state_never_becomes_f8():
    for state in (None, False):
        ref = _resolved(_ref())
        ref.log.retracted = state
        assert decide(ref, False, None, None).label == S.CLEARED


def test_f8_does_not_run_the_llm_or_the_confirmation_searches(stub):
    """The gate sits in the existence layer; a retracted reference must not pay
    for an LLM call or three confirmation searches."""
    stub.table = {"12345678": [RETRACTED]}
    ref = _resolved(_ref())
    # A genuine wrong-paper shape: nothing corroborates, so the matcher really
    # does flag it and the run would otherwise reach the LLM + confirm path.
    ref.retrieved = RetrievedRecord(
        resolved=True, title="Photosynthetic yield in arctic lichen",
        authors=["Nakamura"], year=1998, journal="Polar Biol",
        pmid="12345678")
    ref = _prepared(stub, ref)
    out = process_reference(ref, _boom)          # _boom raises if the LLM runs
    assert out.label == S.F8
    assert out.log.mismatch_flagged is True      # it WAS flagged; F8 still wins
    assert out.log.llm_verdict is None
    assert out.log.db_hits == {}


# ==========================================================================
# Caching
# ==========================================================================
def test_known_state_is_cached_once_per_pmid(stub):
    stub.table = {"12345678": ["Journal Article"]}
    cache: dict = {}
    for _ in range(3):
        assert retraction_state(_resolved(_ref()), cache=cache) is False
    assert stub.calls == ["12345678"]            # one EFetch, not three
    assert cache == {"12345678": False}


def test_retracted_state_is_cached_too(stub):
    stub.table = {"12345678": [RETRACTED]}
    cache: dict = {}
    assert retraction_state(_resolved(_ref()), cache=cache) is True
    assert retraction_state(_resolved(_ref()), cache=cache) is True
    assert stub.calls == ["12345678"]
    assert cache == {"12345678": True}


def test_unknown_state_is_not_cached(stub):
    """Caching an outage would freeze a network blip into every later reference
    to the same PMID in the run."""
    stub.default = None
    cache: dict = {}
    assert retraction_state(_resolved(_ref()), cache=cache) is None
    assert cache == {}
    # ...and the next reference to the same PMID re-attempts, and can recover.
    stub.table = {"12345678": [RETRACTED]}
    assert retraction_state(_resolved(_ref()), cache=cache) is True
    assert stub.calls == ["12345678", "12345678"]


# ==========================================================================
# decide() stays pure
# ==========================================================================
def test_decide_makes_no_network_call(monkeypatch):
    import cre.f1.decide as decide_mod
    import inspect
    src = inspect.getsource(decide_mod)
    for forbidden in ("ncbi_pubtypes", "requests", "http", "EFetch", "efetch"):
        assert forbidden not in src, (
            f"decide.py must stay a pure function over accumulated evidence; "
            f"found {forbidden!r}")


# ==========================================================================
# The reason code and the downstream artifact
# ==========================================================================
def test_f8_reason_is_registered_and_routes_to_f8_retracted():
    assert rr.is_registered("retracted_publication")
    assert rr.route_for("retracted_publication") == "f8_retracted"
    assert "retracted_publication" in rr.RETRACTION_REASONS


def test_f8_row_is_accepted_by_the_preband_disposition():
    assert S.F8 in DISPOSITION_LABELS
    assert S.F8 in FAULT_LABELS                  # a fault, not a clearing label
    rows = build_rows([{"citation_id": "PMC1:bibr1", "label": S.F8}])
    assert rows == [{"citation_id": "PMC1:bibr1", "label": "F8",
                     "citing_pmcid": "PMC1", "cleared": False}]


def test_retraction_state_is_on_the_log_and_prediction_records():
    ref = _resolved(_ref())
    ref.log.retracted = True
    decide(ref, False, None, None)
    log_rec = ref.to_log_record()
    assert log_rec["log"]["retracted"] is True
    assert log_rec["log"]["retraction_reason"] == "retracted_publication"
    ev = ref.to_prediction().evidence
    assert ev["retracted"] is True
    assert ev["retraction_reason"] == "retracted_publication"


def test_f8_maps_through_the_taxonomy_unchanged():
    assert S.pipeline_state_to_taxonomy(S.F8) == S.F8


# ==========================================================================
# Guardrail: DEC-049 title normalisation is untouched
# ==========================================================================
def test_retraction_word_is_still_stripped_from_titles():
    """DEC-049: an official document and the notice announcing it are the same
    work. F8 is a new signal from a database field, NOT a re-reading of the
    title, so the normalisation must be exactly as it was."""
    from cre.f1.biblio_match import normalize_title, _TITLE_PREFIX_RE
    assert _TITLE_PREFIX_RE.sub("", "Retraction: Foo bar baz") == "Foo bar baz"
    assert normalize_title("Retraction: Foo bar baz") == "foo bar baz"
    assert normalize_title("Foo bar baz") == "foo bar baz"
