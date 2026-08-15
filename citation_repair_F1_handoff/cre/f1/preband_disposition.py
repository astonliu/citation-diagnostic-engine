"""The canonical Band-1 -> Band-2 disposition artifact (schema ``preband_disposition_v1``).

Band 1 (F1/F2/F8, deterministic) and Band 2 (F3-F7, judgment) do not share code.
They join through ONE file: a citation_id -> label map that Band 2's orchestrator
consumes and fails closed against. This module is the only sanctioned producer of
that file.

WHY THE LOG, NEVER THE PREDICTION FILE. ``run.run`` writes two artifacts. The
prediction file drops every ``UNVERIFIABLE`` and ``UNSCOREABLE`` reference
(``run.py`` -- "unverifiable AND unscoreable refs are dropped from the prediction
set"), because those carry no taxonomy label. Feeding it to Band 2 would make a
reference Band 1 merely could not score indistinguishable from a reference Band 1
never saw at all: both land in Band 2's ``excluded_preband_disposition_missing``
bucket, which is the alarm for "this document was never covered by Band 1". A
routine ~4% unscoreable rate would keep that alarm permanently lit and therefore
useless. The LOG record is lossless -- one row per reference, carrying the
pipeline state verbatim -- so it is the only correct source.

WHAT THE ARTIFACT BINDS. A disposition is not just rows: it defines the POPULATION
every downstream rate is a fraction of. So the sidecar manifest binds the exact
bytes (sha256 over the artifact as written), the schema version, the F2 commit
that produced it, the corpus it was built over, and the row/label accounting. A
disposition whose provenance is not recorded cannot be reconstructed, and a number
computed from it cannot be defended.

This module performs NO network call and NO model call. It is a pure transform
over records ``run.run`` already wrote.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Iterable

#: Bump when the row shape, the label vocabulary, or the id format changes.
#: Band 2's loader pins this string and refuses an artifact declaring anything else.
DISPOSITION_SCHEMA = "preband_disposition_v1"

# --- the label vocabulary, split by what it AUTHORIZES -------------------
#: Band 2 may judge this reference: Band 1 verified the right, existing work.
CLEARING_LABELS = frozenset({"cleared"})
#: Band 1 asserted a deterministic fault. Band 2 must not judge it.
FAULT_LABELS = frozenset({"F1", "F2", "F8"})
#: Band 1 reached no verdict. Band 2 must not judge it, and must be able to tell
#: this apart from "Band 1 never saw this reference".
OPERATIONAL_LABELS = frozenset({"unverifiable", "unscoreable", "human_review"})
DISPOSITION_LABELS = CLEARING_LABELS | FAULT_LABELS | OPERATIONAL_LABELS

#: ``<citing_pmcid>:<ref_id>`` -- the format both parsers emit
#: (``parser.parse_pmc_xml``). Verified byte-identical across the Band 1 and
#: Band 2 checkouts; pinned here so a drift on either side fails loudly.
CITATION_ID_RE = re.compile(r"^PMC\d+:\S+$")

ARTIFACT_FILENAME = "preband_disposition_v1.jsonl"
MANIFEST_SUFFIX = ".manifest.json"


class DispositionBuildError(ValueError):
    """The log source cannot produce a valid canonical artifact."""


def _sha256_file(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def head_commit(repo_dir: str) -> str:
    """The producing commit, read from ``.git`` without shelling out.

    Returns "" when it cannot be determined -- callers that need provenance must
    treat "" as a hard failure rather than writing an unattributed artifact.
    """
    head_path = os.path.join(repo_dir, ".git", "HEAD")
    try:
        with open(head_path, encoding="utf-8") as f:
            head = f.read().strip()
    except OSError:
        return ""
    if not head.startswith("ref:"):
        return head if re.fullmatch(r"[0-9a-f]{40}", head) else ""
    ref = head.split(":", 1)[1].strip()
    try:
        with open(os.path.join(repo_dir, ".git", ref), encoding="utf-8") as f:
            oid = f.read().strip()
        return oid if re.fullmatch(r"[0-9a-f]{40}", oid) else ""
    except OSError:
        pass
    # Packed refs fallback.
    try:
        with open(os.path.join(repo_dir, ".git", "packed-refs"),
                  encoding="utf-8") as f:
            for line in f:
                if line.startswith("#"):
                    continue
                parts = line.split()
                if len(parts) == 2 and parts[1] == ref:
                    return parts[0]
    except OSError:
        pass
    return ""


def _iter_log_records(source) -> "Iterable[dict]":
    """Accept a list of log-record dicts or a path to the log JSONL."""
    if isinstance(source, (str, os.PathLike)):
        with open(source, encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise DispositionBuildError(
                        f"log line {lineno} is not valid JSON: {exc}") from exc
                if not isinstance(rec, dict):
                    raise DispositionBuildError(
                        f"log line {lineno} is not a JSON object")
                yield rec
        return
    for rec in source:
        yield rec


def build_rows(log_source) -> list:
    """Canonical rows from the LOSSLESS log source, validated as it goes.

    Raises ``DispositionBuildError`` on a duplicate citation_id, a non-canonical
    citation_id, a missing/None label, or a label outside the vocabulary. The
    producer refuses rather than emitting an artifact the consumer would have to
    guess about.
    """
    rows: list = []
    seen: dict = {}
    for i, rec in enumerate(_iter_log_records(log_source)):
        cid = rec.get("citation_id")
        if not isinstance(cid, str) or not cid.strip():
            raise DispositionBuildError(
                f"log record {i} has no usable citation_id: {cid!r}")
        cid = cid.strip()
        if not CITATION_ID_RE.match(cid):
            raise DispositionBuildError(
                f"log record {i} citation_id {cid!r} is not canonical "
                f"'<citing_pmcid>:<ref_id>' (expected e.g. 'PMC12967000:bibr1')")
        label = rec.get("label")
        if not isinstance(label, str) or not label.strip():
            raise DispositionBuildError(
                f"{cid}: label is missing or not a string ({label!r}); an "
                "unprocessed reference must never reach the disposition")
        label = label.strip()
        if label not in DISPOSITION_LABELS:
            raise DispositionBuildError(
                f"{cid}: label {label!r} is outside schema {DISPOSITION_SCHEMA} "
                f"({sorted(DISPOSITION_LABELS)})")
        if cid in seen:
            raise DispositionBuildError(
                f"duplicate citation_id {cid!r}: first seen as "
                f"{seen[cid]!r}, again as {label!r}. A duplicate id is a join "
                "defect -- last-write-wins could clear a known F2.")
        seen[cid] = label
        rows.append({
            "citation_id": cid,
            "label": label,
            "citing_pmcid": cid.split(":", 1)[0],
            "cleared": label in CLEARING_LABELS,
        })
    return rows


def label_counts(rows: "Iterable[dict]") -> dict:
    out: dict = {}
    for r in rows:
        out[r["label"]] = out.get(r["label"], 0) + 1
    return dict(sorted(out.items()))


def write_disposition(log_source, out_path: str, *, f2_commit: str,
                      corpus_manifest_path: str = "",
                      generated_by: str = "",
                      generated_at: str = "") -> dict:
    """Write the canonical artifact plus its binding manifest; return the manifest.

    ``f2_commit`` is REQUIRED and must be a full 40-hex OID: the artifact defines
    the population of every downstream rate, so an unattributed one is refused
    rather than written. ``corpus_manifest_path``, when given, is hashed and
    bound too, so the disposition and the frozen corpus cannot drift apart
    silently.
    """
    if not re.fullmatch(r"[0-9a-f]{40}", (f2_commit or "").strip()):
        raise DispositionBuildError(
            "f2_commit must be a full 40-hex commit OID; an unattributed "
            "disposition cannot be bound to the code that produced it")
    rows = build_rows(log_source)
    if not rows:
        raise DispositionBuildError(
            "refusing to write an EMPTY disposition: a zero-row artifact would "
            "exclude every pair in Band 2 and complete as a clean empty run")

    tmp = out_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    os.replace(tmp, out_path)

    counts = label_counts(rows)
    pmcids = sorted({r["citing_pmcid"] for r in rows})
    manifest = {
        "schema": DISPOSITION_SCHEMA,
        "artifact_path": out_path,
        "artifact_sha256": _sha256_file(out_path),
        "f2_commit": f2_commit.strip(),
        "source": "band1_log_records",
        "source_note": (
            "Built from the LOSSLESS log source. The prediction file is NOT a "
            "valid source: it drops UNVERIFIABLE and UNSCOREABLE rows, which "
            "would make an unscoreable reference indistinguishable from one "
            "Band 1 never saw."
        ),
        "row_count": len(rows),
        "label_counts": counts,
        "cleared_count": sum(1 for r in rows if r["cleared"]),
        "citing_pmcid_count": len(pmcids),
        "citing_pmcids": pmcids,
        "label_vocabulary": sorted(DISPOSITION_LABELS),
        "clearing_labels": sorted(CLEARING_LABELS),
        "citation_id_pattern": CITATION_ID_RE.pattern,
        "generated_by": generated_by,
        "generated_at": generated_at,
    }
    if corpus_manifest_path:
        manifest["corpus_manifest_path"] = corpus_manifest_path
        manifest["corpus_manifest_sha256"] = _sha256_file(corpus_manifest_path)

    manifest_path = out_path + MANIFEST_SUFFIX
    tmp_m = manifest_path + ".tmp"
    with open(tmp_m, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp_m, manifest_path)
    manifest["manifest_path"] = manifest_path
    return manifest
