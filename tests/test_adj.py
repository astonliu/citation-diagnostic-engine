"""The adjudicator: what a human is shown, and what their verdict is allowed to write.

WHY THIS MATTERS TO A NUMBER. The adjudicator produces the GOLD set. Everything
downstream -- precision, recall, the F2 base rate -- is measured against what
comes out of here, so a defect in surfacing (showing the wrong candidates) or in
writing (recording a verdict the reviewer did not give) does not produce a wrong
answer, it produces a wrong YARDSTICK, and every figure measured against it moves
together.

Two properties carry that weight and are pinned below: an ``accurate`` prediction
is never surfaced for adjudication (a reviewer asked to confirm a non-finding
would pad the confirmed count), and the schema invariant still bites on the way
out, so a human verdict cannot write a self-contradictory gold record.

Converted from a script that ran at import time and wrote into a bare mkdtemp.
"""
from __future__ import annotations

import csv
import json

import pytest

from cde.refs import schema as S
from cde.refs.adjudicate import Adjudicator, Candidate
from cde.refs.schema import AtomicClaim, CitedPaper, GoldRecord, SourcePaper

PREDICTIONS = [
    {"citation_id": "c1", "label": "F1", "rationale": "not found anywhere",
     "evidence": {"decided_by": "exact_doi_absent_confirm_not_found_f1",
                  "db_hits": {"pubmed": 0, "crossref": 0, "openalex": 0}},
     "annotations": [{"annotator_id": "llm", "label": "F1", "confidence": 0.95}]},
    {"citation_id": "c2", "label": "F2", "rationale": "found under different id",
     "evidence": {"decided_by": "confirm_found_f2", "db_hits": {"pubmed": 97}},
     "annotations": [{"annotator_id": "llm", "label": "F2", "confidence": 0.7}]},
    # An accurate prediction. Nothing here is a finding, so nothing here is a
    # question for a reviewer.
    {"citation_id": "c3", "label": "accurate", "rationale": "matched",
     "evidence": {"decided_by": "metadata_match"}, "annotations": []},
]

LOGS = [
    {"citation_id": "c1", "label": "F1",
     "claimed": {"title": "Invented quantum neuro synthesis",
                 "authors": ["Smith"], "year": 2024, "claimed_pmid": "111"},
     "retrieved": {"resolved": True, "title": "A real unrelated paper",
                   "authors": ["Lee"], "pmid": "111"},
     "log": {"title_similarity": 12.0, "author_match": False, "year_match": None,
             "llm_verdict": "fabrication",
             "db_hits": {"pubmed": 0, "crossref": 0, "openalex": 0}},
     "citance": "Claim one [1].", "cited_reference_marker": "[1]",
     "source_pmcid": "PMC999", "source_pmid": "999",
     "source_title": "Citing review"},
    {"citation_id": "c2", "label": "F2",
     "claimed": {"title": "Real study of widgets", "authors": ["Jones"],
                 "year": 2021, "claimed_pmid": "222"},
     "retrieved": {"resolved": True, "title": "Totally different paper",
                   "authors": ["Kim"], "pmid": "222"},
     "log": {"title_similarity": 20.0, "author_match": False,
             "llm_verdict": "reference_error", "db_hits": {"pubmed": 97}},
     "citance": "Claim two [2].", "cited_reference_marker": "[2]",
     "source_pmcid": "PMC999", "source_pmid": "999",
     "source_title": "Citing review"},
    {"citation_id": "c3", "label": "accurate", "claimed": {}, "retrieved": {},
     "log": {}},
]


@pytest.fixture
def paths(tmp_path):
    preds = tmp_path / "preds.jsonl"
    logs = tmp_path / "logs.jsonl"
    preds.write_text("\n".join(json.dumps(x) for x in PREDICTIONS), encoding="utf-8")
    logs.write_text("\n".join(json.dumps(x) for x in LOGS), encoding="utf-8")
    return tmp_path, str(preds), str(logs)


def test_only_findings_are_surfaced_for_adjudication(paths):
    """An ``accurate`` row is not a question, and asking it would pad the count."""
    _, preds, logs = paths
    adj = Adjudicator(preds, logs)
    assert {c.citation_id for c in adj.candidates} == {"c1", "c2"}


def test_the_evidence_view_shows_what_the_verdict_turns_on(paths):
    """A reviewer decides from this text, so what it omits, they cannot weigh."""
    _, preds, logs = paths
    view = Adjudicator(preds, logs).candidates[0].evidence_view()
    assert "Invented quantum" in view
    assert "similarity: 12.0" in view
    # The PMID DID resolve -- to an unrelated paper. Saying otherwise would
    # describe a dead identifier, which is a different finding.
    assert "did not resolve" not in view


def test_a_headless_worklist_round_trips_and_a_relabel_is_honoured(paths):
    """The reviewer's label wins over the predicted one, and is recorded as theirs.

    ``c2`` was predicted F2 and the reviewer calls it F1. The gold record must
    carry F1, the reviewer's note as its rationale, and a HUMAN annotator id --
    a relabel attributed to the model would make the gold set agree with the
    detector by construction.
    """
    tmp, preds, logs = paths
    adj = Adjudicator(preds, logs)
    worklist = str(tmp / "wl.csv")
    adj.write_worklist(worklist)

    rows = list(csv.DictReader(open(worklist, encoding="utf-8")))
    assert len(rows) == 2 and rows[0]["predicted_label"] == "F1"
    for row in rows:
        row["verdict"] = "confirm"
        if row["citation_id"] == "c2":
            row["final_label"] = "F1"
            row["note"] = "actually fabricated on review"
    with open(worklist, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    adj.apply_worklist(worklist)
    gold_path = str(tmp / "gold.jsonl")
    assert adj.save_gold(gold_path) == 2

    gold = [json.loads(line) for line in
            open(gold_path, encoding="utf-8").read().splitlines()]
    assert {g["citation_id"]: g["label"] for g in gold} == {"c1": "F1", "c2": "F1"}
    assert gold[1]["rationale"] == "actually fabricated on review"
    assert gold[0]["annotations"][0]["annotator_id"] == "human_1"
    assert adj.summary()["confirmed"] == 2
    assert adj.summary()["gold_written"] == 2


def test_the_interactive_path_records_a_rejection_as_a_rejection(paths):
    """A rejected candidate is not a quieter confirmation.

    Both counters are asserted: a reviewer who rejects a finding has produced
    evidence about the detector, and folding it into "not confirmed" would lose
    the distinction between "the reviewer disagreed" and "nobody looked".
    """
    _, preds, logs = paths
    adj = Adjudicator(preds, logs)
    answers = iter(["reject", "confirm"])
    adj.review(input_fn=lambda *a: next(answers), print_fn=lambda *a: None)
    assert adj.summary()["confirmed"] == 1
    assert adj.summary()["rejected"] == 1


def test_the_schema_invariant_still_bites_on_a_human_written_gold_record():
    """A verdict cannot write a record that contradicts itself.

    ``accurate`` alongside an unsupported atomic claim is incoherent however it
    was arrived at, and a human saying so does not make it true.
    """
    with pytest.raises(ValueError):
        GoldRecord("z", "c", "[1]", CitedPaper(), SourcePaper(),
                   label="accurate",
                   atomic_claims=[AtomicClaim("a", False)]).validate()


def test_a_candidate_carries_the_final_label_the_reviewer_set():
    candidate = Candidate("x", {"label": "F6", "rationale": ""},
                          {"claimed": {"title": "t"}, "retrieved": {}, "log": {}})
    candidate.final_label = "accurate"
    candidate.verdict = "confirm"
    assert candidate.final_label == "accurate"
    assert candidate.verdict == "confirm"
