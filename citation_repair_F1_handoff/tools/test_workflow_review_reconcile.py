"""Regression tests for workflow_review_reconcile.

Pins the exact F5-review failure: a non-empty journal (findings + verdicts) whose
aggregated summary came back empty must RAISE, never reconcile silently. Also
covers the healthy mixed confirmed+refuted case, INV-A count shortfalls, INV-B
empty tallies, and off-vocabulary verdicts.

Run: python -m pytest tools/test_workflow_review_reconcile.py -q
"""
from __future__ import annotations

import json

import pytest

from workflow_review_reconcile import (
    ReconciliationError,
    classify_journal,
    reconcile_review_journal,
)


# --------------------------------------------------------------------------
# Fixture journal builders (mirror the real workflow journal line shape:
# {"type": "result", "result": {...}}).
# --------------------------------------------------------------------------
def _review(*findings):
    return {"type": "result", "result": {"findings": list(findings)}}


def _finding(summary, severity="major"):
    return {"severity": severity, "file": "f.py", "line": 1, "summary": summary,
            "failure_scenario": "x"}


def _verdict(verdict, reasoning="r"):
    return {"type": "result", "result": {"verdict": verdict, "reasoning": reasoning}}


def _mixed_journal():
    """1 dimension raising 2 findings -> 1 CONFIRMED + 1 REFUTED."""
    return [
        _review(_finding("engine-boolean tamper"), _finding("style nit")),
        _verdict("CONFIRMED"),
        _verdict("REFUTED"),
    ]


def _write_jsonl(tmp_path, entries):
    path = tmp_path / "journal.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
    return str(path)


# --------------------------------------------------------------------------
# Healthy reconciliation.
# --------------------------------------------------------------------------
def test_mixed_confirmed_refuted_reconciles():
    out = reconcile_review_journal(_mixed_journal(), {"confirmed_count": 1})
    assert out["reconciled"] is True
    assert out["raised"] == 2
    assert out["confirmed"] == 1
    assert out["refuted"] == 1
    assert out["unresolved"] == 0


def test_reconcile_from_jsonl_file(tmp_path):
    path = _write_jsonl(tmp_path, _mixed_journal())
    out = reconcile_review_journal(path, {"confirmed": [{"x": 1}]})  # 1-item list
    assert out["reconciled"] is True and out["confirmed"] == 1


def test_reproduces_real_f5_run_counts():
    # The real F5 review: 8 review dims, 11 findings -> 9 CONFIRMED + 2 REFUTED.
    journal = [_review(*[_finding(f"d{d}-f{i}") for i in range(2 if d < 3 else 1)])
               for d in range(8)]           # 3*2 + 5*1 = 11 findings
    verdicts = ["CONFIRMED"] * 9 + ["REFUTED"] * 2
    journal += [_verdict(v) for v in verdicts]
    out = reconcile_review_journal(journal, {"confirmed_count": 9})
    assert (out["raised"], out["confirmed"], out["refuted"]) == (11, 9, 2)


def test_classify_journal_counts_only():
    tally = classify_journal(_mixed_journal())
    assert tally["raised"] == 2
    assert tally["review_entries"] == 1
    assert tally["verify_entries"] == 2


# --------------------------------------------------------------------------
# The pinned bug: non-empty journal, empty/disagreeing summary -> RAISE.
# --------------------------------------------------------------------------
def test_empty_summary_from_nonempty_journal_raises():
    # This IS the F5 failure: journal has 1 CONFIRMED + 1 REFUTED, summary says 0.
    with pytest.raises(ReconciliationError, match="summary disagrees with journal"):
        reconcile_review_journal(_mixed_journal(), {"confirmed_count": 0, "confirmed": []})


def test_summary_undercount_raises():
    journal = [_review(_finding("a"), _finding("b")),
               _verdict("CONFIRMED"), _verdict("CONFIRMED")]
    with pytest.raises(ReconciliationError, match="dropped verdicts|disagrees"):
        reconcile_review_journal(journal, {"confirmed_count": 1})


def test_summary_refuted_disagreement_raises():
    with pytest.raises(ReconciliationError, match="refuted"):
        reconcile_review_journal(
            _mixed_journal(), {"confirmed_count": 1, "refuted_count": 5})


# --------------------------------------------------------------------------
# INV-A / INV-B / schema drift.
# --------------------------------------------------------------------------
def test_missing_verdict_shortfall_raises():
    # 2 findings raised but only 1 verdict recorded -> counts don't reconcile.
    journal = [_review(_finding("a"), _finding("b")), _verdict("CONFIRMED")]
    with pytest.raises(ReconciliationError, match="do not reconcile"):
        reconcile_review_journal(journal)


def test_findings_raised_but_no_verdicts_raises():
    journal = [_review(_finding("a"))]           # no verify entries at all
    with pytest.raises(ReconciliationError, match="no verdicts|silently empty"):
        reconcile_review_journal(journal)


def test_off_vocabulary_verdict_raises():
    journal = [_review(_finding("a")), _verdict("MAYBE")]
    with pytest.raises(ReconciliationError, match="off-vocabulary"):
        reconcile_review_journal(journal)


def test_unresolved_alias_uncertain_counts():
    journal = [_review(_finding("a"), _finding("b")),
               _verdict("CONFIRMED"), _verdict("UNCERTAIN")]
    out = reconcile_review_journal(journal, {"confirmed_count": 1})
    assert out["unresolved"] == 1 and out["reconciled"] is True


def test_empty_journal_is_vacuously_reconciled():
    # No findings, no verdicts, no summary -> nothing to reconcile, no raise.
    out = reconcile_review_journal([], None)
    assert out["reconciled"] is True and out["raised"] == 0
