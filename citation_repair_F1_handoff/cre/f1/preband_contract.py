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


def enforce_join_reached(counts: dict, total_records: int,
                         missing_disposition_key: str) -> None:
    """Every judged pair fell through the join -> hard failure.

    Distinct from the zero-overlap gate, which compares id DOMAINS before the
    run. This catches the residual case that gate cannot see: the disposition
    overlaps the corpus, but every id it matched is structurally excluded (no
    citance / no cited pmid) before the pre-band gate, so nothing that was
    actually judged had a disposition entry. That run completes with records,
    ``accounting_ok`` true, and an empty queue.

    Deliberately NOT triggered by "the disposition cleared nothing". A corpus
    whose references are all genuinely F1/F2/F8 is a legitimate run with an
    empty queue, and failing it would be a false alarm on real data.
    """
    if total_records > 0 and counts.get(missing_disposition_key, 0) == total_records:
        raise PrebandContractError(
            f"all {total_records} judged pair(s) are "
            f"'{missing_disposition_key}': the disposition overlapped the corpus "
            "id domain but covered nothing that was actually judged. This "
            "completes as a clean empty run and must not be reported.")
