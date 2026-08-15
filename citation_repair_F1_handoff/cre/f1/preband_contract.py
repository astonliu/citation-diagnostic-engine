"""The Band-1 -> Band-2 join contract: strict loading, corpus preflight, accounting.

Band 1 and Band 2 share no code. They join through ONE file, so that file is a
CONTRACT and this module is its validator. The prior loader was not a validator:
it silently skipped falsy ids, accepted any label, accepted any id shape, and
resolved duplicates last-write-wins -- so an ``F2`` row followed by a ``cleared``
row for the same citation_id let a deterministic wrong-paper into the judgment
band, and a wholesale id mismatch completed as a clean empty run.

THE FAILURE MODE THIS EXISTS TO STOP. A silent exclusion of every pair looks
exactly like a successful run: every record is written, every pair is accounted
for, ``accounting_ok`` is true, and the annotation queue is empty. Nothing in the
output distinguishes "Band 1 cleared nothing" from "the two sides never joined."
So the join is now preflighted against the ACTUAL corpus id domain, before any
output file exists, and a zero-overlap or all-missing join aborts.

WHAT IS STRICT WHERE. A canonical ARTIFACT (``preband_disposition_v1``, written by
Band 1's ``preband_disposition.write_disposition``) is validated in full: sidecar
manifest, declared schema, byte digest, producing commit, canonical id format,
known labels, no duplicates. A raw DICT passed in-process is a developer/test
injection: structure and labels are still validated and duplicates are impossible
by construction, but the canonical id format is NOT enforced, because an injected
dict declares no schema that would define one. An injected dict is stamped
non-canonical in the run manifest and can never back a reportable population
claim.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field

#: The only artifact schema this consumer accepts. Must match Band 1's
#: ``preband_disposition.DISPOSITION_SCHEMA`` exactly; the two bands share no
#: code, so this string is the whole of the version handshake.
DISPOSITION_SCHEMA = "preband_disposition_v1"

MANIFEST_SUFFIX = ".manifest.json"

#: Band 2 may judge this reference. ``cleared`` is what the canonical artifact
#: emits; ``accurate`` is the taxonomy-vocabulary spelling and stays accepted so
#: this change can never turn a previously-cleared row into an exclusion.
CLEARING_LABELS = frozenset({"cleared", "accurate"})
#: Band 1 asserted a deterministic fault.
FAULT_LABELS = frozenset({"F1", "F2", "F8"})
#: Band 1 reached no verdict -- distinct from "Band 1 never saw this reference".
OPERATIONAL_LABELS = frozenset({"unverifiable", "unscoreable", "human_review"})
DISPOSITION_LABELS = CLEARING_LABELS | FAULT_LABELS | OPERATIONAL_LABELS

#: ``<citing_pmcid>:<ref_id>``, e.g. ``PMC12967000:bibr1-09226028251392269``.
CITATION_ID_RE = re.compile(r"^PMC\d+:\S+$")

SOURCE_ARTIFACT = "artifact"
SOURCE_DICT = "dict_injection"


class PrebandContractError(ValueError):
    """The disposition, or its join to the corpus, violates the contract."""


def _sha256_file(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


@dataclass
class Disposition:
    """A validated disposition plus everything needed to reconstruct it."""
    mapping: dict = field(default_factory=dict)
    source: str = SOURCE_DICT
    schema: str = ""
    path: str = ""
    artifact_sha256: str = ""
    f2_commit: str = ""
    row_count: int = 0
    manifest_sha256: str = ""
    corpus_manifest_sha256: str = ""
    canonical: bool = False

    def provenance(self) -> dict:
        """The manifest block. Absent fields stay empty rather than guessed."""
        return {
            "source": self.source,
            "canonical": self.canonical,
            "schema": self.schema,
            "path": self.path,
            "artifact_sha256": self.artifact_sha256,
            "manifest_sha256": self.manifest_sha256,
            "f2_commit": self.f2_commit,
            "corpus_manifest_sha256": self.corpus_manifest_sha256,
            "row_count": self.row_count,
            "cleared_count": sum(1 for v in self.mapping.values()
                                 if _is_clear(v)),
        }


def _is_clear(label) -> bool:
    return isinstance(label, str) and label.strip().casefold() in CLEARING_LABELS


def _validate_label(label, where: str) -> str:
    if not isinstance(label, str) or not label.strip():
        raise PrebandContractError(
            f"{where}: label is missing or not a string ({label!r})")
    stripped = label.strip()
    if stripped not in DISPOSITION_LABELS:
        raise PrebandContractError(
            f"{where}: label {stripped!r} is outside schema {DISPOSITION_SCHEMA} "
            f"({sorted(DISPOSITION_LABELS)}). An unknown label would be read as "
            "NOT CLEARED and silently exclude the pair.")
    return stripped


def load_artifact(path: str) -> Disposition:
    """Load and fully validate a canonical ``preband_disposition_v1`` artifact."""
    if not os.path.exists(path):
        raise PrebandContractError(f"disposition artifact not found: {path}")

    manifest_path = path + MANIFEST_SUFFIX
    if not os.path.exists(manifest_path):
        raise PrebandContractError(
            f"disposition {path} has no sidecar manifest ({manifest_path}); an "
            "unversioned, unattributed disposition is refused -- it defines the "
            "population every published rate is a fraction of")
    try:
        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        raise PrebandContractError(
            f"disposition manifest {manifest_path} is unreadable: {exc}") from exc
    if not isinstance(manifest, dict):
        raise PrebandContractError(
            f"disposition manifest {manifest_path} is not a JSON object")

    schema = manifest.get("schema")
    if schema != DISPOSITION_SCHEMA:
        raise PrebandContractError(
            f"disposition schema {schema!r} != required {DISPOSITION_SCHEMA!r}; "
            "refusing an artifact this consumer does not understand")

    declared = manifest.get("artifact_sha256", "")
    actual = _sha256_file(path)
    if declared != actual:
        raise PrebandContractError(
            f"disposition bytes do not match the manifest digest "
            f"(declared {declared!r}, actual {actual!r}); the artifact or its "
            "manifest changed after it was produced")

    f2_commit = str(manifest.get("f2_commit", "")).strip()
    if not re.fullmatch(r"[0-9a-f]{40}", f2_commit):
        raise PrebandContractError(
            "disposition manifest carries no full 40-hex f2_commit; a result "
            "cannot be bound to the Band-1 code that produced its population")

    mapping: dict = {}
    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as exc:
                raise PrebandContractError(
                    f"{path}:{lineno} is not valid JSON: {exc}") from exc
            if not isinstance(rec, dict):
                raise PrebandContractError(
                    f"{path}:{lineno} is not a JSON object")
            cid = rec.get("citation_id")
            if not isinstance(cid, str) or not cid.strip():
                raise PrebandContractError(
                    f"{path}:{lineno} has no usable citation_id ({cid!r}); a "
                    "row without an id was previously SKIPPED silently")
            cid = cid.strip()
            if not CITATION_ID_RE.match(cid):
                raise PrebandContractError(
                    f"{path}:{lineno} citation_id {cid!r} is not canonical "
                    f"'<citing_pmcid>:<ref_id>' -- it can never join Band 2")
            if cid in mapping:
                raise PrebandContractError(
                    f"{path}:{lineno} duplicate citation_id {cid!r} "
                    f"(already {mapping[cid]!r}, now {rec.get('label')!r}); "
                    "last-write-wins could clear a known F2")
            mapping[cid] = _validate_label(rec.get("label"), f"{path}:{lineno}")

    declared_rows = manifest.get("row_count")
    if isinstance(declared_rows, int) and declared_rows != len(mapping):
        raise PrebandContractError(
            f"disposition row_count mismatch: manifest says {declared_rows}, "
            f"artifact holds {len(mapping)}")
    if not mapping:
        raise PrebandContractError(
            f"disposition {path} is EMPTY; every pair would be excluded and the "
            "run would complete as a clean empty run")

    return Disposition(
        mapping=mapping, source=SOURCE_ARTIFACT, schema=schema, path=path,
        artifact_sha256=actual, f2_commit=f2_commit, row_count=len(mapping),
        manifest_sha256=_sha256_file(manifest_path),
        corpus_manifest_sha256=str(manifest.get("corpus_manifest_sha256", "")),
        canonical=True)


def load_injected(mapping: dict) -> Disposition:
    """Validate an in-process dict. Structure and labels are strict; the
    canonical id format is not enforced (an injected dict declares no schema)."""
    out: dict = {}
    for cid, label in mapping.items():
        if not isinstance(cid, str) or not cid.strip():
            raise PrebandContractError(
                f"injected disposition has a non-string/empty citation_id "
                f"({cid!r})")
        out[cid.strip()] = _validate_label(label, f"injected[{cid!r}]")
    return Disposition(mapping=out, source=SOURCE_DICT, row_count=len(out),
                       canonical=False)


def load_disposition(preband_disposition) -> "Disposition | None":
    """``None`` -> no disposition supplied. A path loads strictly; a dict is an
    injection. Anything else is a configuration error."""
    if preband_disposition is None:
        return None
    if isinstance(preband_disposition, dict):
        return load_injected(preband_disposition)
    if isinstance(preband_disposition, (str, os.PathLike)):
        return load_artifact(str(preband_disposition))
    raise PrebandContractError(
        f"preband_disposition must be a path or a dict, got "
        f"{type(preband_disposition).__name__}")


# --------------------------------------------------------------------------
# Corpus preflight and join accounting
# --------------------------------------------------------------------------
def collect_expected_ids(xml_dir: str, filenames, parse_fn, pmcid_fn) -> tuple:
    """Parse the corpus for its citation_id domain BEFORE any output exists.

    Returns ``(expected_ids, per_doc, parse_failures)``. Raises when the corpus
    is empty or when EVERY document failed to parse -- both of those otherwise
    finalize as ``status="complete"`` with ``accounting_ok=true`` and zero
    records, which is indistinguishable from a successful run.
    """
    filenames = list(filenames)
    if not filenames:
        raise PrebandContractError(
            f"corpus {xml_dir} contains no .xml/.nxml documents; an empty "
            "corpus would complete as a clean empty run")
    expected: set = set()
    per_doc: dict = {}
    failures: dict = {}
    for fn in filenames:
        pmcid = pmcid_fn(fn)
        try:
            refs = parse_fn(os.path.join(xml_dir, fn), source_pmcid=pmcid)
        except Exception as exc:                       # noqa: BLE001
            failures[pmcid] = f"{type(exc).__name__}: {exc}"
            per_doc[pmcid] = 0
            continue
        ids = [r.citation_id for r in refs]
        per_doc[pmcid] = len(ids)
        expected.update(ids)
    if len(failures) == len(filenames):
        raise PrebandContractError(
            f"every document in {xml_dir} failed to parse "
            f"({len(failures)}/{len(filenames)}); refusing to produce a run "
            f"that would complete with zero records. First: "
            f"{sorted(failures.items())[0]}")
    if not expected:
        raise PrebandContractError(
            f"corpus {xml_dir} parsed but yielded ZERO references; there is "
            "nothing to judge and the run would complete empty")
    return expected, per_doc, failures


def join_accounting(disp: "Disposition | None", expected_ids) -> dict:
    """Matched / missing / extra / coverage between the corpus and the map."""
    expected = set(expected_ids)
    supplied = set(disp.mapping) if disp is not None else set()
    matched = expected & supplied
    missing = expected - supplied
    extra = supplied - expected
    cleared = {cid for cid in matched if _is_clear(disp.mapping[cid])}
    return {
        "expected_ids": len(expected),
        "disposition_ids": len(supplied),
        "matched": len(matched),
        "missing_from_disposition": len(missing),
        "extra_in_disposition": len(extra),
        "matched_cleared": len(cleared),
        "coverage": (len(matched) / len(expected)) if expected else 0.0,
        "missing_sample": sorted(missing)[:10],
        "extra_sample": sorted(extra)[:10],
    }


def enforce_join(acc: dict, *, disp: "Disposition | None",
                 require_full_coverage: bool = False) -> None:
    """Abort on the joins that would publish a number over the wrong population.

    Fails closed on: no disposition at all, zero overlap, and (opt-in) any
    uncovered expected id. A partial-coverage run is allowed but its accounting
    rides in the manifest so the gap is never invisible.
    """
    if disp is None:
        raise PrebandContractError(
            "no preband_disposition supplied; every pair would be excluded "
            "fail-closed and the run would complete with an empty annotation "
            "queue. Supply the canonical Band-1 artifact.")
    if acc["matched"] == 0:
        raise PrebandContractError(
            "ZERO overlap between the corpus and the disposition "
            f"({acc['expected_ids']} corpus ids, {acc['disposition_ids']} "
            f"disposition ids, 0 matched). Every pair would be excluded and "
            f"the run would look like a clean empty run. "
            f"Corpus sample: {acc['missing_sample'][:3]}; "
            f"disposition sample: {acc['extra_sample'][:3]}")
    if require_full_coverage and acc["missing_from_disposition"]:
        raise PrebandContractError(
            f"{acc['missing_from_disposition']} corpus citation_id(s) are "
            f"absent from the disposition (e.g. {acc['missing_sample'][:3]}); "
            "full coverage was required for this run")


def enforce_join_reached(counts: dict, missing_disposition_key: str,
                         structural_keys) -> None:
    """Every pair that REACHED the pre-band gate fell through it -> hard failure.

    Distinct from the zero-overlap gate, which compares id DOMAINS before the
    run. This catches the residual case that gate cannot see: the disposition
    overlaps the corpus, but every id it matched is structurally excluded before
    the pre-band gate, so nothing that was actually judged had an entry.

    THE DENOMINATOR IS ELIGIBLE PAIRS, NOT ALL RECORDS. Comparing against the
    record total was bypassable by any corpus holding a single structurally
    excluded reference -- i.e. every real corpus: one ``excluded_no_citance``
    plus one ``excluded_preband_disposition_missing`` gives 1 != 2 and the run
    completes with ZERO pairs judged. Structural exclusions never reached the
    join and must not dilute it.

    Deliberately NOT triggered by "the disposition cleared nothing". A corpus
    whose references are all genuinely F1/F2/F8 is a legitimate run with an
    empty queue, and failing it would be a false alarm on real data.
    """
    eligible = sum(v for k, v in counts.items() if k not in set(structural_keys))
    missing = counts.get(missing_disposition_key, 0)
    if eligible > 0 and missing == eligible:
        raise PrebandContractError(
            f"all {eligible} pair(s) that reached the pre-band gate are "
            f"'{missing_disposition_key}': the disposition overlapped the corpus "
            "id domain but covered nothing that was actually judged. This "
            "completes as a clean empty run and must not be reported.")


# --------------------------------------------------------------------------
# Production preflight: the same conditions, checked BEFORE any work
# --------------------------------------------------------------------------
def assert_production_launch_shape(*, max_docs, resume_detected: bool,
                                   chain_genesis: str) -> None:
    """Phase 1 of the production preflight: is this a single fresh segment?

    Checked FIRST, before the corpus is even scanned, because an exhausted or
    partially-complete out_dir otherwise surfaces as a confusing "empty corpus"
    error from the corpus preflight -- the resume problem would never be named.
    """
    problems: list = []
    if max_docs is not None:
        problems.append(
            "max_docs is set; a bounded pass leaves the manifest in_progress and "
            "is not a production run")
    if resume_detected:
        problems.append(
            "out_dir already holds run state; production runs may not resume "
            "(resume can duplicate rows, miscount the population, and combine "
            "different models/settings/commits under one manifest). Start a "
            "FRESH out_dir and restart.")
    if (chain_genesis or "").strip():
        problems.append(
            "chain_genesis is set; this run extends a prior segment and is not a "
            "single-segment production run")
    if problems:
        raise PrebandContractError(
            "PRODUCTION PREFLIGHT FAILED -- refusing to start; no output was "
            "written:\n  - " + "\n  - ".join(problems))


def assert_production_preflight(*, disp: "Disposition | None", join_acc: dict,
                                parse_failures: dict, code_commit: str,
                                model: str, corpus_manifest_path: str) -> dict:
    """Phase 2 of the production preflight: is this run BOUND? Returns the bindings.

    Every condition here is ALSO a reportability clause, but reportability is
    evaluated on the finished manifest -- i.e. after the compute is spent, and
    only when the caller remembered to ask. Each of these could be violated by a
    run that still returned ``status="complete"``. Checking them up front makes
    them mandatory rather than advisory, and makes a misconfigured production run
    cost nothing.

    RESUME AND max_docs ARE REFUSED IN PRODUCTION. ``judgment_run`` writes
    predictions per reference but checkpoints per document, so an interrupted
    document is replayed and its rows appended a second time; a resumed
    manifest's counters cover only the final invocation; and a resumed segment
    can combine different models, temperatures and commits under one final
    manifest. Recovery is a FRESH out_dir and a restart, which is safe and cheap
    relative to a corrupted denominator.
    """
    problems: list = []

    if disp is None or not disp.canonical or disp.source != SOURCE_ARTIFACT:
        problems.append(
            "disposition is not a canonical preband_disposition_v1 file artifact "
            "(in-process dictionaries are test/development-only and can never "
            "bind a population)")
    if join_acc.get("missing_from_disposition"):
        problems.append(
            f"{join_acc['missing_from_disposition']} corpus citation_id(s) are "
            f"absent from the disposition, e.g. {join_acc.get('missing_sample', [])[:3]}; "
            "production requires COMPLETE id coverage")
    if parse_failures:
        problems.append(
            f"{len(parse_failures)} document(s) failed to parse: "
            f"{sorted(parse_failures)[:5]}; production requires ZERO parse "
            "failures, because a dropped document silently shrinks the population")
    if not (code_commit or "").strip():
        problems.append("no code_commit supplied")
    if not (model or "").strip():
        problems.append("no model supplied")

    corpus_sha = ""
    if not corpus_manifest_path:
        problems.append(
            "no corpus_manifest_path supplied; the frozen corpus cannot be bound")
    elif not os.path.exists(corpus_manifest_path):
        problems.append(f"corpus manifest not found: {corpus_manifest_path}")
    else:
        corpus_sha = _sha256_file(corpus_manifest_path)
        declared = (disp.corpus_manifest_sha256 if disp is not None else "") or ""
        if not declared:
            problems.append(
                "the disposition manifest carries no corpus_manifest_sha256, so "
                "the population cannot be tied to the corpus being judged")
        elif declared != corpus_sha:
            problems.append(
                f"corpus mismatch: the disposition was built over "
                f"{declared[:12]}… but this run judges {corpus_sha[:12]}…")

    if problems:
        raise PrebandContractError(
            "PRODUCTION PREFLIGHT FAILED -- refusing to start; no output was "
            "written:\n  - " + "\n  - ".join(problems))
    return {"corpus_manifest_sha256": corpus_sha}


# --------------------------------------------------------------------------
# Reportability: the single gate a published number must pass
# --------------------------------------------------------------------------
#: A run may be perfectly valid and still not reportable -- a development pass,
#: a ``max_docs`` slice, a dict-injected fixture. Reportability is a STRICTER
#: predicate than "completed without error", and it is evaluated on the finished
#: manifest plus the predictions file, so it cannot be satisfied by intent.
def reportability_report(manifest: dict, predictions_path: str) -> dict:
    """Return ``{"reportable": bool, "failures": [...], "checks": {...}}``.

    Each clause exists because a specific run that FAILS it would still have
    completed cleanly and produced a publishable-looking number.
    """
    failures: list = []
    checks: dict = {}

    def need(name: str, ok: bool, why: str) -> None:
        checks[name] = bool(ok)
        if not ok:
            failures.append(f"{name}: {why}")

    pb = manifest.get("preband") or {}
    join = pb.get("join") or {}
    corpus = manifest.get("corpus") or {}
    adapter = manifest.get("adapter") or {}
    params = manifest.get("params") or {}

    need("status_complete", manifest.get("status") == "complete",
         f"status is {manifest.get('status')!r}, not 'complete'")

    # PRODUCTION ACCEPTS ONLY THE CANONICAL FILE ARTIFACT. A dict declares no
    # schema, no digest and no producing commit, so it cannot bind a population.
    need("canonical_disposition",
         pb.get("canonical") is True and pb.get("source") == SOURCE_ARTIFACT,
         "disposition was not a canonical preband_disposition_v1 artifact "
         "(dict injection is test/development-only)")

    need("full_coverage", join.get("missing_from_disposition") == 0,
         f"{join.get('missing_from_disposition')} corpus citation_id(s) absent "
         "from the disposition")

    # The disposition's corpus and the corpus actually judged must be the SAME
    # bytes. Recording both without comparing them lets a disposition built over
    # corpus A be run against corpus B with full coverage and no complaint.
    d_corpus = pb.get("corpus_manifest_sha256") or ""
    r_corpus = corpus.get("manifest_sha256") or ""
    need("corpus_cross_bound",
         bool(d_corpus) and bool(r_corpus) and d_corpus == r_corpus,
         f"disposition corpus digest {d_corpus[:12]!r} != run corpus digest "
         f"{r_corpus[:12]!r} (or one is absent)")

    need("code_commit_recorded", bool(manifest.get("code_commit")),
         "no code_commit recorded")
    need("model_recorded", bool(adapter.get("model")), "no adapter.model recorded")
    need("temperature_recorded", "temperature" in adapter,
         "no adapter.temperature recorded (DEC-046B pins 0)")

    # Zero judged pairs is the clean-empty-run failure. Unconditional, so it
    # needs no reasoning about which exclusions dilute which denominator.
    need("pairs_judged", (manifest.get("scoreable_records") or 0) > 0,
         "no scoreable pair was judged")

    need("no_parse_failures", not (pb.get("preflight_parse_failures") or {}),
         f"documents failed to parse: "
         f"{sorted((pb.get('preflight_parse_failures') or {}))}")

    need("modules_stable", manifest.get("module_sha256_stable") is True,
         "a governing module changed during the run")

    # SINGLE SEGMENT. Resume writes predictions per reference but checkpoints per
    # document, so an interrupted document is replayed and its rows appended a
    # second time; and the manifest counters cover only the final invocation.
    # Both are pinned by strict xfails in test_adversarial_judgment_run.
    need("single_segment", not params.get("chain_genesis"),
         "run extends a prior segment; resumed runs are not reportable")

    lines = _read_prediction_ids(predictions_path)
    n = len(lines)
    need("counters_match_file",
         manifest.get("total_records") == n
         and manifest.get("chain_record_count") == n,
         f"manifest total_records={manifest.get('total_records')} / "
         f"chain_record_count={manifest.get('chain_record_count')} vs "
         f"{n} prediction row(s)")
    dupes = sorted({cid for cid in lines if lines.count(cid) > 1})
    need("unique_citation_ids", not dupes,
         f"duplicate citation_id(s) in predictions: {dupes[:5]}")

    return {"reportable": not failures, "failures": failures, "checks": checks}


def _read_prediction_ids(path: str) -> list:
    out: list = []
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line).get("citation_id"))
            except json.JSONDecodeError:
                out.append(None)
    return out


def assert_reportable_run(manifest: dict, predictions_path: str) -> dict:
    """Raise unless every reportability clause holds. Returns the report."""
    report = reportability_report(manifest, predictions_path)
    if not report["reportable"]:
        raise PrebandContractError(
            "this run is NOT reportable and its numbers must not be published:\n  - "
            + "\n  - ".join(report["failures"]))
    return report
