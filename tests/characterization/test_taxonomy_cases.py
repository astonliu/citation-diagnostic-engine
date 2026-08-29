"""One worked case per taxonomy category: an input, and the label it must produce.

WHY THIS EXISTS SEPARATELY from the route and hierarchy suites. Those pin
DISTRIBUTIONS and PRECEDENCE. This pins the eight individual answers, each as a
readable, self-contained example a person can check by eye: here is a citing
sentence, here is the cited work, here is why the answer is F-whatever. It is
the file to read first to understand what the system claims to detect, and it is
what the quickstart notebook displays.

THE CASES ARE REAL WHERE A REAL ONE CAN EXIST. F3, F5, F6, F7 and F8 use
published papers with their true PMIDs, taken from this project's own bench
packets and calibration set. Where a real case cannot exist, the reason is
stated on the case rather than papered over:

  F1  A real fabricated citation would mean naming a real paper as invented.
      There is no such thing to point at, and manufacturing one would be
      defamatory. The reference below is synthetic BY NECESSITY -- this is the
      one category where that is the correct choice and not a shortcut.
  F2  The RESOLVED side is a real paper; the claimed side is synthetic, because
      a real F2 means asserting that identifiable authors miscited. The
      mechanism under test -- an identifier that resolves to a different work --
      is exercised exactly as it would be in the wild.
  F4  NOT YET FILLED IN. See the note on ``test_f4_...`` below.

Every case runs offline against the real deciding code: ``refs.decide.decide``
for the database-resolvable categories, ``diagnose.engine.decide_judgment`` for
the judgment band. No network, no model call, no cost.
"""
from __future__ import annotations

import pytest

from cde.diagnose.engine import (
    DecisionStatus, EntityState, ProvenanceState, SupportState, TemporalState,
    decide_judgment,
)
from cde.refs import schema as S
from cde.refs.decide import decide
from cde.refs.retraction import F8_TIMING_INDETERMINATE
from cde.refs.schema import ClaimedRef, Reference

from .test_hierarchy import entity, prov, support, temporal


# ---------------------------------------------------------------------------
# Band 1 -- the database-resolvable categories
# ---------------------------------------------------------------------------
def _band1_ref(title, pmid="", **log):
    ref = Reference("case", "A citing sentence [1].",
                    ClaimedRef(title=title, claimed_pmid=pmid,
                               authors=["Smith J"], year=2020))
    for key, value in log.items():
        setattr(ref.log, key, value)
    return ref


def test_f1_a_reference_no_database_has_ever_heard_of():
    """**F1 — fabricated.** The identifier is dead and no database has the title.

    Synthetic by necessity, as the module docstring explains. Note what it takes
    to get here: the PMID must have RESOLVED to nothing, and all three searches
    must have ANSWERED with zero hits. The engine holds on anything less.
    """
    ref = _band1_ref("A plausible sounding study of nothing that was never written",
                     pmid="99999999", pmid_present=True, pmid_resolved=False)
    out = decide(ref, True, S.V_FABRICATION,
                 {"pubmed": 0, "crossref": 0, "openalex": 0})
    # The accusation is the strongest thing this system says about a person, so
    # what matters is that the label is reached only on complete evidence.
    assert out.label in (S.F1, S.HUMAN_REVIEW)
    assert out.label != S.CLEARED


def test_f2_an_identifier_that_resolves_to_a_different_real_paper():
    """**F2 — wrong reference.** The PMID is live; it is simply the wrong one.

    The resolved side is real: PMID 31665581 is "Purple Urine after
    Catheterization" (N Engl J Med). The claimed side is a different work
    entirely, so the reference names one paper and points at another. The title
    similarity is far below the same-work threshold, which is what separates
    this from a formatting variant of the same paper.
    """
    ref = _band1_ref("A randomised trial of drug X for outcome Y",
                     pmid="31665581", pmid_present=True, pmid_resolved=True,
                     title_similarity=12.0)
    out = decide(ref, True, S.V_REFERENCE_ERROR,
                 {"pubmed": 97, "crossref": 0, "openalex": 0})
    assert out.label == S.F2


def test_f8_a_paper_cited_93_days_after_its_retraction_notice():
    """**F8 — retracted source.** Real, and this project verified it.

    PMC7474863 cites two of the retracted Surgisphere papers 93 and 94 days
    after their notices. The one here is Mehra et al., Lancet 2020, PMID
    32450107 (doi 10.1016/S0140-6736(20)31180-6), which PubMed carries with the
    publication type "Retracted Publication".

    93 days clears the registered 31-day floor, so the citing authors could have
    known. That floor is an annotation-confidence threshold, not part of the
    definition: below it the case is EXCLUDED as indeterminate rather than
    labelled, because publication lags submission.
    """
    ref = _band1_ref("Hydroxychloroquine or chloroquine with or without a "
                     "macrolide for treatment of COVID-19",
                     pmid="32450107", pmid_present=True, pmid_resolved=True,
                     retracted=True, f8_timing_status="qualified",
                     f8_timing_gap_days=93)
    out = decide(ref, False, None, {"pubmed": 100, "crossref": 0, "openalex": 0})
    assert out.label == S.F8
    assert out.log.decided_by == "f8_retracted_before_citation_timing_met"


def test_f8_the_same_paper_cited_two_weeks_after_the_notice_is_excluded():
    """The floor's other side, and the reason it is a floor and not a rule.

    A 14-day gap cannot establish that the authors could have known -- so this
    is a genuine citation to a retracted paper that is nevertheless NOT
    includable as F8. Excluded and counted, never relabelled.
    """
    ref = _band1_ref("Hydroxychloroquine or chloroquine with or without a "
                     "macrolide for treatment of COVID-19",
                     pmid="32450107", pmid_present=True, pmid_resolved=True,
                     title_similarity=99.0, retracted=None,
                     f8_timing_status=F8_TIMING_INDETERMINATE,
                     f8_timing_gap_days=14)
    out = decide(ref, False, None, {"pubmed": 100, "crossref": 0, "openalex": 0})
    assert out.label != S.F8
    assert out.log.decided_by == "f8_timing_indeterminate_excluded"


# ---------------------------------------------------------------------------
# Band 2 -- the judgment band
# ---------------------------------------------------------------------------
def _judge(**kw):
    kw.setdefault("preband_cleared", True)
    kw.setdefault("provenance", None)
    kw.setdefault("temporal", temporal())
    return decide_judgment(**kw)


def test_f3_the_cited_review_restates_a_finding_it_did_not_originate():
    """**F3 — misattribution.** Every claim is established; the origin is wrong.

    From this project's ICU-acquired-weakness bench packet: a citing sentence
    attributes a finding to a review that accurately restates it, when the
    finding originated in an earlier primary study the review itself cites.

    Note the shape. F3 requires FULL coverage first -- the review really does
    say what it is cited for. That is what separates it from F6, and why F3 is
    never inferred from a lack of support.
    """
    d = _judge(claims=("Early mobilisation reduces ICU-acquired weakness",),
               claim_support=support(SupportState.SUPPORTED),
               provenance=prov(ProvenanceState.MISATTRIBUTED_CONFIRMED))
    assert d.findings == ("F3",)
    assert d.primary_label == "F3"
    assert d.status is DecisionStatus.TERMINAL


@pytest.mark.xfail(reason="no real F4 case is available in this repository yet; "
                          "see the module docstring and doc/taxonomy.md",
                   strict=True)
def test_f4_a_hedged_finding_cited_as_a_definite_one():
    """**F4 — overstatement.** NOT YET A REAL CASE.

    The rule is settled (doc/taxonomy.md): F4 fires when the CITED PAPER'S OWN
    language is weaker than the claim -- "may contribute" cited as "causes",
    correlation cited as causation -- and not when the paper is confident while
    the broader literature is mixed.

    What is missing is a published pair to point at. The bench packets cover
    F3, F5 and F7; the Sarol calibration rows cover F6 and the ACCURATE/F4
    boundary, but contain no positive F4. Rather than invent a plausible-looking
    pair and present it as evidence, this is left failing on purpose so that it
    is impossible to overlook.
    """
    raise NotImplementedError("awaiting a real F4 pair")


def test_f6_a_two_clause_sentence_whose_first_clause_is_unsupported():
    """**F6 — partial support.** Real, and a documented baseline false negative.

    Calibration row 3 of ``data/claims_test.jsonl``: the citing sentence makes
    two claims -- that plasma L-carnitine with TMAO correlates with
    cardiovascular events in humans, and that omnivores and vegans differ in
    TMAO. The cited abstract supports the second and is silent on the first.

    Sarol et al. label this ACCURATE. It is F6, and that disagreement is the
    single best illustration of what a coarse label costs: one unsupported
    clause in a two-clause sentence is invisible to a sentence-level verdict.
    """
    d = _judge(claims=("Plasma L-carnitine with TMAO correlates with "
                       "cardiovascular events in humans",
                       "Omnivores and vegans differ in plasma TMAO"),
               claim_support=support(SupportState.UNESTABLISHED,
                                     SupportState.SUPPORTED))
    assert d.findings == ("F6",)
    assert d.primary_label == "F6"


def test_f6_added_specificity_the_cited_paper_does_not_establish():
    """The specificity boundary, from calibration row 1.

    The claim says "in apolipoprotein E-deficient mice"; the cited abstract
    discusses mice generally. The general mechanism is supported and the
    specific model is not confirmed, so one word of unconfirmed specificity
    moves the citation from ACCURATE to F6.
    """
    d = _judge(claims=("The effect was mediated in apoE-deficient mice",),
               claim_support=support(SupportState.UNESTABLISHED))
    assert d.findings == ("F6",)


def test_f5_early_goal_directed_therapy_superseded_by_three_later_trials():
    """**F5 — superseded.** Real, and the textbook case.

    Rivers et al., N Engl J Med 2001, PMID 11794169 (doi
    10.1056/NEJMoa010307) found early goal-directed therapy cut septic-shock
    mortality from 46.5% to 30.5%. ProCESS, ARISE and ProMISe later found no
    benefit. All three supersession criteria fire: a directional contradiction
    (not a refinement of magnitude), a date gap well over two years, and an
    equal-or-higher evidence tier.

    The cited paper is not retracted and was not wrong when written. That is
    exactly what makes this F5 and not F8: the field moved.
    """
    d = _judge(claims=("Early goal-directed therapy reduces mortality in "
                       "septic shock",),
               claim_support=support(SupportState.SUPPORTED),
               provenance=prov(ProvenanceState.PROPER_ORIGIN),
               temporal=temporal(TemporalState.QUALIFYING_CONTRADICTION,
                                 claim_index=0))
    assert d.findings == ("F5",)
    assert d.primary_label == "F5"


def test_f7_an_aspirin_claim_supported_by_a_metformin_paper():
    """**F7 — wrong entity.** Real, and the clearest case in the set.

    The citing sentence says aspirin after diagnosis improves lung-cancer
    survival. The cited paper is Br J Cancer 2020, PMID 33262518 (doi
    10.1038/s41416-020-01186-9), a Norwegian cohort of 22,324 lung-cancer
    patients — about METFORMIN.

    Note why this is not F6. The paper fully supports a claim of that shape:
    post-diagnostic use of the drug improved lung-cancer-specific survival
    (HR 0.83). The relation holds; the entity does not. A coverage judge reading
    for topical support can easily call this established, which is precisely why
    entity identity is a separate discriminator with its own evidence
    requirement -- a confirmed F7 must name both entity keys.
    """
    d = _judge(claims=("Aspirin use after diagnosis improves lung cancer "
                       "specific survival",),
               claim_support=support(SupportState.SUPPORTED),
               entity_assessments=entity(EntityState.DIFFERENT_ENTITY_SUPPORTED),
               provenance=prov(ProvenanceState.PROPER_ORIGIN))
    assert d.findings == ("F7",)
    assert d.primary_label == "F7"


def test_accurate_a_claim_the_cited_paper_fully_establishes():
    """**ACCURATE** — calibration row 2, the clean baseline.

    Every atomic claim of the L-carnitine / TMAO / mouse-atherosclerosis
    sentence is directly supported, the provenance is right and nothing
    supersedes it. Included because a taxonomy tour that only shows faults
    cannot show that the system is capable of not finding one.
    """
    d = _judge(claims=("Dietary L-carnitine is metabolised into TMAO",),
               claim_support=support(SupportState.SUPPORTED),
               provenance=prov(ProvenanceState.PROPER_ORIGIN))
    assert d.status is DecisionStatus.TERMINAL
    assert d.primary_label == "accurate"
    assert d.findings == ()
