"""Adjudication harness — turn F1-stage output into gold CitationRepair-1000 records.

Pairs each PredictionRecord with its per-reference log (by citation_id), shows the
evidence a human needs (claimed vs resolved metadata, similarity, LLM verdict, the
three database search results), records a verdict, and writes validated GoldRecords.
The F6 invariant + taxonomy check run on write via GoldRecord.validate().

Two ways to use it:

  Notebook (interactive)::
      from cde.refs.adjudicate import Adjudicator
      adj = Adjudicator("preds.jsonl", "logs.jsonl")
      adj.review()                       # prompts for each candidate
      adj.save_gold("gold_f1.jsonl")

  Headless (apply a decisions file you filled out)::
      adj = Adjudicator("preds.jsonl", "logs.jsonl")
      adj.write_worklist("worklist.csv") # one row per candidate, blank verdict col
      # ... edit worklist.csv: verdict in {confirm, reject, uncertain}, fix label if needed ...
      adj.apply_worklist("worklist.csv")
      adj.save_gold("gold_f1.jsonl")

Only candidates worth a human look are surfaced by default: labels in REVIEW_LABELS
(F1, F2) plus anything the pipeline sent to human_review. `accurate`/cleared and
`unverifiable` are skipped (the first are controls, the second are out of scope).
"""
from __future__ import annotations
import csv
import json
from dataclasses import dataclass, field
from typing import Optional

from .schema import (GoldRecord, CitedPaper, SourcePaper, Repair, Annotation,
                     AtomicClaim, write_jsonl, TAXONOMY_LABELS,
                     F1, F2, ACCURATE, check_f6_invariant)

REVIEW_LABELS = {F1, F2, "human_review"}
VERDICTS = {"confirm", "reject", "uncertain"}


def _taxonomy_label(value: str) -> str | None:
    folded = (value or "").strip().casefold()
    return next((label for label in TAXONOMY_LABELS
                 if label.casefold() == folded), None)


def _required(record: dict, key: str, where: str):
    if key not in record:
        raise ValueError(f"{where} is missing required provenance key {key!r}")
    return record[key]


def _load_jsonl(path: str) -> list[dict]:
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


@dataclass
class Candidate:
    citation_id: str
    pred: dict
    log: dict
    verdict: Optional[str] = None         # confirm | reject | uncertain
    final_label: Optional[str] = None     # taxonomy label the human assigns
    note: str = ""

    @property
    def predicted_label(self) -> str:
        return self.pred.get("label", "")

    def evidence_view(self) -> str:
        claimed = self.log.get("claimed", {})
        retr = self.log.get("retrieved", {})
        lg = self.log.get("log", {})
        ev = self.pred.get("evidence", {})
        transport = lg.get("pmid_transport_status")
        lines = [
            f"[{self.citation_id}]  predicted: {self.predicted_label}"
            f"  (decided_by={ev.get('decided_by','?')})",
            f"  rationale : {self.pred.get('rationale','')}",
            "  CLAIMED   : " + _fmt(claimed.get("title"), claimed.get("authors"),
                                    claimed.get("year"), claimed.get("claimed_pmid")),
            "  RESOLVED  : " + (_fmt(retr.get("title"), retr.get("authors"),
                                     retr.get("year"), retr.get("pmid"))
                                if retr.get("resolved") else
                                f"(unresolved; transport_status={transport or 'unrecorded'})"),
            f"  similarity: {lg.get('title_similarity')}   "
            f"author_match={lg.get('author_match')}   year_match={lg.get('year_match')}",
            f"  llm       : {lg.get('llm_verdict')}",
            f"  db_hits   : {ev.get('db_hits') or lg.get('db_hits')}",
            f"  decided_by: {ev.get('decided_by') or lg.get('decided_by')}",
        ]
        return "\n".join(lines)

    def to_gold(self) -> GoldRecord:
        claimed = self.log.get("claimed", {})
        src_pred = self.pred
        label = self.final_label or self.predicted_label
        g = GoldRecord(
            citation_id=self.citation_id,
            citance=_required(self.log, "citance", "log record"),
            cited_reference_marker=_required(
                self.log, "cited_reference_marker", "log record"),
            cited_paper=CitedPaper(
                pmid=claimed.get("claimed_pmid", ""),
                doi=claimed.get("claimed_doi", ""),
                title=claimed.get("title", ""),
                authors=claimed.get("authors", []) or [],
                year=claimed.get("year"),
            ),
            source_paper=SourcePaper(
                pmid=_required(self.log, "source_pmid", "log record"),
                title=_required(self.log, "source_title", "log record"),
            ),
            label=label,
            atomic_claims=[],                 # F1/F2 are existence-level
            repair=Repair(),
            rationale=self.note or src_pred.get("rationale", ""),
            annotations=[Annotation(annotator_id="human_1", label=label,
                                    confidence=1.0)],
            source="adjudicated_from_f1_detector",
        )
        g.validate()                          # enforces taxonomy + F6 invariant
        return g


def _fmt(title, authors, year, pid) -> str:
    a = ", ".join(authors or []) if authors else ""
    return f"{title!r}  | {a} | {year} | id={pid or '-'}"


class Adjudicator:
    def __init__(self, predictions_path: str, logs_path: str):
        preds = {p["citation_id"]: p for p in _load_jsonl(predictions_path)}
        logs = {l["citation_id"]: l for l in _load_jsonl(logs_path)}
        self.candidates: list[Candidate] = []
        for cid, pred in preds.items():
            if pred.get("label") not in REVIEW_LABELS:
                continue
            self.candidates.append(Candidate(cid, pred, logs.get(cid, {})))
        self.gold: list[GoldRecord] = []

    # ---- interactive ----
    def review(self, input_fn=input, print_fn=print) -> None:
        """Prompt for a verdict on each candidate (notebook/terminal)."""
        for cand in self.candidates:
            if cand.verdict is not None:
                continue
            print_fn("\n" + cand.evidence_view())
            v = ""
            while v not in VERDICTS:
                v = input_fn("verdict [confirm/reject/uncertain] "
                             "(or 'relabel F2' etc.): ").strip().lower()
                if v.startswith("relabel "):
                    raw_label = v.split(maxsplit=1)[1]
                    lbl = _taxonomy_label(raw_label)
                    if lbl is not None:
                        cand.final_label = lbl
                        print_fn(f"  -> relabeled to {lbl}; now give a verdict")
                    else:
                        print_fn(f"  !! {lbl} not in taxonomy")
                    v = ""
            cand.verdict = v
        self._collect()

    # ---- headless ----
    def write_worklist(self, path: str) -> None:
        cols = ["citation_id", "predicted_label", "title_similarity",
                "llm_verdict", "claimed_title", "resolved_title",
                "pmid_transport_status", "decided_by", "rationale",
                "db_hits", "verdict", "final_label", "note"]
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            for c in self.candidates:
                lg = c.log.get("log", {})
                w.writerow({
                    "citation_id": c.citation_id,
                    "predicted_label": c.predicted_label,
                    "title_similarity": lg.get("title_similarity"),
                    "llm_verdict": lg.get("llm_verdict"),
                    "claimed_title": c.log.get("claimed", {}).get("title", ""),
                    "resolved_title": c.log.get("retrieved", {}).get("title", ""),
                    "pmid_transport_status": lg.get("pmid_transport_status", ""),
                    "decided_by": (c.pred.get("evidence", {}).get("decided_by")
                                   or lg.get("decided_by", "")),
                    "rationale": c.pred.get("rationale", ""),
                    "db_hits": json.dumps(c.pred.get("evidence", {}).get("db_hits", {})),
                    "verdict": "", "final_label": "", "note": "",
                })

    def apply_worklist(self, path: str) -> None:
        by_id = {c.citation_id: c for c in self.candidates}
        with open(path, newline="") as f:
            for row in csv.DictReader(f):
                c = by_id.get(row["citation_id"])
                if not c:
                    continue
                v = (row.get("verdict") or "").strip().lower()
                if v in VERDICTS:
                    c.verdict = v
                raw_label = (row.get("final_label") or "").strip()
                if raw_label:
                    fl = _taxonomy_label(raw_label)
                    if fl is None:
                        raise ValueError(
                            f"{c.citation_id}: unrecognised final_label "
                            f"{raw_label!r}")
                    c.final_label = fl
                c.note = row.get("note", "") or c.note
        self._collect()

    # ---- output ----
    def _collect(self) -> None:
        self.gold = []
        errors = []
        for c in self.candidates:
            if c.verdict != "confirm":
                continue
            try:
                self.gold.append(c.to_gold())
            except ValueError as e:
                errors.append(str(e))
        if errors:
            print(f"[adjudicate] {len(errors)} record(s) failed validation:")
            for e in errors:
                print("  -", e)

    def summary(self) -> dict:
        out = {"confirmed": 0, "rejected": 0, "uncertain": 0, "pending": 0}
        for c in self.candidates:
            out[{"confirm": "confirmed", "reject": "rejected",
                 "uncertain": "uncertain"}.get(c.verdict, "pending")] += 1
        out["gold_written"] = len(self.gold)
        return out

    def save_gold(self, path: str) -> int:
        self._collect()
        write_jsonl([g.to_dict() for g in self.gold], path)
        return len(self.gold)
