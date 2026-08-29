"""F3 reachability through the bench, offline.

Guards the four independent defects that made F3 unreachable on the bench path
while the manifest reported it wired. Each assertion below corresponds to one of
them; all four had to be fixed before a single F3 could be emitted, which is why
none of them was visible as a failing test.

The packet is the real pinned Kulcenty 2015 / Takahashi & Yamanaka 2006 pair --
real abstracts, the review's real 74-entry bibliography -- so the wiring is
exercised against the data a live run sees. Only the model is stubbed.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cde.diagnose import pipeline as jr
from cde.runtime import sandbox_judge as sj
from cde.diagnose.provenance import _assemble_candidates
from cde.diagnose.engine import ProvenanceState
from .test_judgment_run import disc_llm, extractor_of, f4_json, judge_established

PACKET = Path(__file__).resolve().parent / "fixtures" / "packet_f3_example_hedged.json"

REVIEW_PMID = "25691818"
PRIMARY_PMID = "16904174"


def _packet() -> dict:
    # NOT a skip. The packet is committed beside these tests, so "absent" means
    # the path is wrong, not that the fixture is optional -- and a skip would
    # hide that, which is exactly what it did when the package was restructured
    # underneath it and eight assertions quietly stopped running.
    if not PACKET.exists():                      # pragma: no cover
        raise AssertionError(
            f"the committed packet fixture is missing at {PACKET}; it ships "
            f"with the repo, so this is a broken path rather than an optional "
            f"input")
    return json.loads(PACKET.read_text(encoding="utf-8"))


# --------------------------------------------------- defect 1: reflist shape

def test_reflist_fetcher_returns_the_ncbi_two_tuple():
    """``(candidates, available)``. Returning the bare rows yielded [] from
    _assemble_candidates for any length != 2 -- a silent hold with a full
    bibliography in hand -- and raised in judgment_band's `prov, avail = ...`."""
    fetch = sj._reflist_fetcher(_packet())
    result = fetch(REVIEW_PMID)
    assert isinstance(result, tuple) and len(result) == 2
    candidates = _assemble_candidates(result)
    assert len(candidates) > 1
    assert any(c["claimed_pmid"] == PRIMARY_PMID for c in candidates)


# ------------------------------------------------- defect 2: abstract per pmid

def test_abstract_fetcher_serves_the_candidate_primary_not_the_cited_work():
    """V4 asks whether the CANDIDATE contains the finding and requires the span
    verbatim in what this seam returns. A pmid-blind fetcher handed it the cited
    review's abstract, so a confirmed F3 could quote the work it was accusing."""
    fetch = sj._abstract_fetcher(_packet())
    review, primary = fetch(REVIEW_PMID), fetch(PRIMARY_PMID)
    assert review and primary and review != primary
    assert "we demonstrate induction of pluripotent stem cells" in primary
    assert fetch("99999999") is None      # unknown work -> hold, not wrong text


# ------------------------------------------------ defect 3: load-time refusals

@pytest.mark.parametrize("drop, needle", [
    ("cited_pmcid", "cited_pmcid"),
    ("abstracts", "abstracts map"),
    ("cited_reference_list", "cited_reference_list"),
])
def test_f3_packet_missing_a_precondition_is_refused_at_load(drop, needle):
    """Refuse, do not hold. A hold is indistinguishable from 'the model looked
    and was unsure', which is exactly how this went unnoticed."""
    packet = _packet()
    packet.pop(drop)
    with pytest.raises(sj.PacketError, match=needle):
        sj.judge(packet, model="m", dry_run=True)


def test_dry_run_names_the_f3_seams():
    """The plan is the only artifact a reader gets before paying for a run; it
    reported `wired_seams: []` for an F3 packet."""
    plan = sj.judge(_packet(), model="m", dry_run=True)
    assert set(plan["wired_seams"]) >= {
        "discriminator_call_llm", "f3_fetch_reflist", "f3_resolve_pmcid"}


# ------------------------------------------- defect 4: end-to-end reachability

def test_f3_fires_end_to_end_on_the_pinned_pair():
    """The whole point: real reflist, real abstracts, real spans, stub model."""
    packet = _packet()
    claim = packet["citing_sentence"].rstrip(".")
    review_abstract = packet["abstracts"][REVIEW_PMID]
    primary_abstract = packet["abstracts"][PRIMARY_PMID]

    fetch_reflist = sj._reflist_fetcher(packet)
    candidates = _assemble_candidates(fetch_reflist(REVIEW_PMID))
    index = next(i for i, c in enumerate(candidates)
                 if c["claimed_pmid"] == PRIMARY_PMID)

    restatement_span = ("Overexpression of just four pluripotency-related "
                        "transcription factors")
    origin_span = ("Here, we demonstrate induction of pluripotent stem cells from "
                   "mouse embryonic or adult fibroblasts by introducing four "
                   "factors, Oct3/4, Sox2, c-Myc, and Klf4")
    assert restatement_span in review_abstract      # spans must be verbatim
    assert origin_span in primary_abstract

    record = jr.judge_pair(
        sj.build_item(packet),
        extractor=extractor_of(claim),
        coverage_judge=judge_established(True),
        fetch_abstract=sj._abstract_fetcher(packet),
        fetch_reflist=fetch_reflist,
        discriminator_call_llm=disc_llm(
            f4=f4_json(),
            v2=json.dumps({"verdict": "restatement",
                           "evidence_span": restatement_span, "rationale": "x"}),
            v3=json.dumps({"selected_index": index, "rationale": "x"}),
            v4=json.dumps({"contains_finding": True,
                           "evidence_span": origin_span, "rationale": "x"})),
        f3_fetch_reflist=fetch_reflist,
        f3_resolve_pmcid=lambda _pmid: packet["cited_pmcid"],
    )

    assert record["label"] == ["F3"]
    assert record["provenance"]["state"] == ProvenanceState.MISATTRIBUTED_CONFIRMED.value
    assert record["provenance"]["origin_chain"][-1] == f"PMID:{PRIMARY_PMID}"
    assert record["provenance"]["evidence_spans"][-1] == origin_span


# -------------------------------- F4 and F3 are independent axes, not a ladder

def test_f4_and_f3_are_reported_together():
    """A citing sentence can overstate its source AND cite the wrong origin.

    F4 rewrites a SUPPORTED claim to WEAKER_STRENGTH; F3's gate reads COVERAGE
    support, so the overstatement no longer erases the provenance question.
    Before this, F4 firing suppressed F3 silently -- the pair reported only the
    overstatement and the misattribution was never assessed at all.
    """
    packet = _packet()
    # The UNHEDGED sentence: "is sufficient" against the review's "appears
    # sufficient" is a real epistemic_certainty overstatement.
    claim = ("Expression of OCT3/4, SOX2, KLF4, and c-MYC is sufficient to "
             "reprogram fibroblasts into induced pluripotent stem cells")
    review_abstract = packet["abstracts"][REVIEW_PMID]
    primary_abstract = packet["abstracts"][PRIMARY_PMID]

    fetch_reflist = sj._reflist_fetcher(packet)
    candidates = _assemble_candidates(fetch_reflist(REVIEW_PMID))
    index = next(i for i, c in enumerate(candidates)
                 if c["claimed_pmid"] == PRIMARY_PMID)

    citing_span = "is sufficient to reprogram fibroblasts"
    cited_span = "appears sufficient to produce this new cell type"
    restatement_span = ("Overexpression of just four pluripotency-related "
                        "transcription factors")
    origin_span = ("Here, we demonstrate induction of pluripotent stem cells from "
                   "mouse embryonic or adult fibroblasts by introducing four "
                   "factors, Oct3/4, Sox2, c-Myc, and Klf4")
    assert citing_span in claim
    assert cited_span in review_abstract and restatement_span in review_abstract
    assert origin_span in primary_abstract

    item = sj.build_item(packet)
    item["citing_sentence"] = claim + "."
    record = jr.judge_pair(
        item,
        extractor=extractor_of(claim),
        coverage_judge=judge_established(True),
        fetch_abstract=sj._abstract_fetcher(packet),
        fetch_reflist=fetch_reflist,
        discriminator_call_llm=disc_llm(
            f4=f4_json(load="epistemic_certainty", citing_span=citing_span,
                       cited_span=cited_span,
                       dims={"epistemic_certainty": {"citing": "asserted",
                                                     "cited": "hedged"}}),
            v2=json.dumps({"verdict": "restatement",
                           "evidence_span": restatement_span, "rationale": "x"}),
            v3=json.dumps({"selected_index": index, "rationale": "x"}),
            v4=json.dumps({"contains_finding": True,
                           "evidence_span": origin_span, "rationale": "x"})),
        f3_fetch_reflist=fetch_reflist,
        f3_resolve_pmcid=lambda _pmid: packet["cited_pmcid"],
    )

    assert set(record["findings"]) == {"F4", "F3"}
    assert record["provenance"]["state"] == (
        ProvenanceState.MISATTRIBUTED_CONFIRMED.value)
    assert record["provenance"]["origin_chain"][-1] == f"PMID:{PRIMARY_PMID}"
