"""A hash-pinned CITATION SELECTION: the exact reference set a run may process.

WHY THIS EXISTS. A rerun that targets a subset of a corpus has one dangerous
degree of freedom: the subset itself. If the launcher is handed a list of ids it
cannot verify, then "we re-ran the 408 that failed" and "we re-ran 408 references"
are the same artifact, and the second is not a finding about anything. Worse, the
cheapest way to make a subset run is to hand the launcher a prepared pre-band map
-- which skips the real F1/F2/F8 code and turns the rerun into a replay of the
decisions being questioned.

So a selection is an ARTIFACT, not an argument:

* It carries its own cohort digest -- sha256 over the sorted ids joined by "\\n"
  with a trailing "\\n" -- and the loader recomputes it. A hand-edited id list
  fails to load.
* It BINDS THE SOURCE RUNS it was derived from, each with the sha256 of the
  artifacts that produced it. The selection cannot be re-pointed at a different
  run's population without the binding failing.
* It is applied AFTER XML PARSING and BEFORE BAND 1. Every selected reference
  therefore runs the real F1/F2/F8 code and the real F3-F8 band; the selection
  narrows the POPULATION and never substitutes for a stage.
* It supplies NO labels and NO dispositions. There is deliberately no field a
  caller could use to inject a pre-band map through this door
  (``production_launcher.launch_full`` refuses one at the front door; this module
  must not become the back door).

Fail-closed everywhere: a missing id, a duplicate id, an id the corpus does not
contain, or a digest that does not recompute is a refusal, never a smaller run.

This module performs NO network call and NO model call.
"""
from __future__ import annotations

import hashlib
import json
import os
import re

#: Bump when the manifest shape or the digest convention changes. The loader pins
#: this string and refuses an artifact declaring anything else.
SELECTION_SCHEMA = "citation_selection_v1"

#: ``<citing_pmcid>:<ref_id>`` -- the same id format ``parser.parse_pmc_xml``
#: emits and ``preband_disposition`` pins, repeated here so a drift on any side
#: fails loudly rather than producing an empty intersection.
CITATION_ID_RE = re.compile(r"^PMC\d+:\S+$")


class SelectionError(ValueError):
    """The selection artifact cannot be loaded, verified, or applied."""


def cohort_digest(citation_ids) -> str:
    """sha256 over the sorted UNIQUE ids joined by newline, with a trailing one.

    Pinned as a convention because a digest is only an identity if both sides
    agree on the byte sequence; the trailing newline is the detail that silently
    differs between ``"\\n".join(ids)`` and a file written line by line, so it is
    stated here rather than left to whichever side wrote first.
    """
    ordered = sorted(set(str(cid).strip() for cid in citation_ids))
    payload = "".join(f"{cid}\n" for cid in ordered)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


class CitationSelection:
    """A loaded, digest-verified selection. Immutable by construction."""

    __slots__ = ("ids", "cohort_sha256", "source_runs", "path",
                 "manifest_sha256", "note")

    def __init__(self, *, ids, cohort_sha256: str, source_runs, path: str,
                 manifest_sha256: str, note: str = ""):
        self.ids = tuple(ids)
        self.cohort_sha256 = cohort_sha256
        self.source_runs = tuple(source_runs)
        self.path = path
        self.manifest_sha256 = manifest_sha256
        self.note = note

    def __len__(self) -> int:
        return len(self.ids)

    def __contains__(self, citation_id) -> bool:
        return citation_id in self._as_set

    @property
    def _as_set(self) -> frozenset:
        return frozenset(self.ids)

    def binding(self) -> dict:
        """The provenance block a run manifest records verbatim."""
        return {
            "schema": SELECTION_SCHEMA,
            "path": self.path,
            "manifest_sha256": self.manifest_sha256,
            "cohort_sha256": self.cohort_sha256,
            "selected_count": len(self.ids),
            "source_runs": [dict(run) for run in self.source_runs],
            "applied_after": "xml_parse",
            "applied_before": "band1",
            "note": self.note,
        }


def _require_source_runs(raw) -> list:
    """Every source run must name itself AND hash at least one artifact.

    A run id with no artifact hash is a label, not a binding: it would let the
    same selection be claimed as derived from any run with that name.
    """
    if not isinstance(raw, list) or not raw:
        raise SelectionError(
            "selection must bind a nonempty 'source_runs' list; an unbound id "
            "set cannot be attributed to the run it was derived from")
    runs = []
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise SelectionError(f"source_runs[{index}] must be an object")
        run_id = str(entry.get("run_id") or "").strip()
        if not run_id:
            raise SelectionError(f"source_runs[{index}] has no run_id")
        artifacts = entry.get("artifacts")
        if not isinstance(artifacts, dict) or not artifacts:
            raise SelectionError(
                f"source_runs[{index}] ({run_id}) hashes no artifact; a run id "
                "with no artifact digest binds nothing")
        clean = {}
        for name, digest in sorted(artifacts.items()):
            text = str(digest or "").strip().lower()
            if len(text) != 64 or any(c not in "0123456789abcdef" for c in text):
                raise SelectionError(
                    f"source_runs[{index}] ({run_id}) artifact {name!r} digest "
                    "is not 64 lowercase hex characters")
            clean[str(name)] = text
        runs.append({"run_id": run_id, "artifacts": clean})
    return runs


def load_selection(path: str) -> CitationSelection:
    """Load and VERIFY a selection manifest. Every failure mode is a refusal."""
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise SelectionError(f"cannot read selection manifest {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise SelectionError(f"{path}: selection manifest must be a JSON object")
    schema = raw.get("schema")
    if schema != SELECTION_SCHEMA:
        raise SelectionError(
            f"{path}: schema {schema!r} is not {SELECTION_SCHEMA!r}")

    ids = raw.get("citation_ids")
    if not isinstance(ids, list) or not ids:
        raise SelectionError(f"{path}: 'citation_ids' must be a nonempty list")
    seen: set = set()
    ordered: list = []
    for index, cid in enumerate(ids):
        if not isinstance(cid, str) or not cid.strip():
            raise SelectionError(f"{path}: citation_ids[{index}] is not a string")
        cid = cid.strip()
        if not CITATION_ID_RE.match(cid):
            raise SelectionError(
                f"{path}: citation_ids[{index}] {cid!r} is not canonical "
                "'<citing_pmcid>:<ref_id>'")
        if cid in seen:
            # A duplicate is never harmless: it changes every denominator the
            # run reports while leaving the id set looking correct.
            raise SelectionError(
                f"{path}: duplicate citation_id {cid!r}; a duplicated id "
                "silently inflates the population every rate is computed over")
        seen.add(cid)
        ordered.append(cid)

    declared = str(raw.get("cohort_sha256") or "").strip().lower()
    computed = cohort_digest(ordered)
    if declared != computed:
        raise SelectionError(
            f"{path}: cohort_sha256 {declared!r} does not recompute from the id "
            f"list (computed {computed}); the selection was edited after it was "
            "sealed")
    declared_count = raw.get("selected_count")
    if declared_count is not None and int(declared_count) != len(ordered):
        raise SelectionError(
            f"{path}: selected_count {declared_count} != {len(ordered)} ids")

    return CitationSelection(
        ids=sorted(ordered), cohort_sha256=computed,
        source_runs=_require_source_runs(raw.get("source_runs")),
        path=os.path.abspath(path), manifest_sha256=sha256_file(path),
        note=str(raw.get("note") or ""))


def verify_source_runs(selection: CitationSelection, proof_dir: str) -> dict:
    """Re-hash every bound artifact on disk and refuse on any mismatch.

    Optional at load time (a selection is verifiable on its own digest) and
    MANDATORY before a production launch, where "derived from those four runs" is
    a claim the manifest makes on the reader's behalf.
    """
    verified: dict = {}
    for run in selection.source_runs:
        for name, expected in run["artifacts"].items():
            candidate = os.path.join(proof_dir, name)
            if not os.path.isfile(candidate):
                raise SelectionError(
                    f"selection source run {run['run_id']} binds {name!r}, "
                    f"which is not present in {proof_dir}")
            actual = sha256_file(candidate)
            if actual != expected:
                raise SelectionError(
                    f"selection source run {run['run_id']} artifact {name!r} "
                    f"hashes {actual}, manifest binds {expected}")
            verified[name] = actual
    return {"proof_dir": os.path.abspath(proof_dir),
            "artifacts_verified": len(verified),
            "artifact_sha256": dict(sorted(verified.items()))}


def apply_selection(refs, selection: "CitationSelection | None") -> list:
    """Narrow one document's PARSED references to the selection.

    Applied to the output of ``parse_pmc_xml`` -- after parsing, before any
    Band-1 work -- so every surviving reference is the real parser's object and
    goes on to run the real F1/F2/F8 code. ``None`` returns the input unchanged,
    which is the ordinary whole-corpus run.
    """
    if selection is None:
        return list(refs)
    chosen = selection._as_set
    return [ref for ref in refs if ref.citation_id in chosen]


def assert_selection_covered(selection: "CitationSelection | None",
                             parsed_ids) -> dict:
    """Every selected id must exist in the parsed corpus. Fail closed if not.

    Without this, a selection naming ids the corpus does not contain produces a
    SMALLER run that still completes cleanly -- the same failure shape the
    pre-band join gate exists to prevent, one layer earlier.
    """
    if selection is None:
        return {"selection_applied": False}
    parsed = set(parsed_ids)
    missing = sorted(set(selection.ids) - parsed)
    if missing:
        raise SelectionError(
            f"{len(missing)} selected citation_id(s) are absent from the parsed "
            f"corpus; refusing to run a silently smaller population. "
            f"First: {missing[:5]}")
    return {
        "selection_applied": True,
        "selected_count": len(selection.ids),
        "parsed_corpus_count": len(parsed),
        "excluded_by_selection": len(parsed) - len(selection.ids),
        "cohort_sha256": selection.cohort_sha256,
    }


def write_selection(path: str, citation_ids, *, source_runs,
                    note: str = "") -> dict:
    """Seal a selection manifest. The producing side of :func:`load_selection`.

    Refuses a duplicate id here too, so a malformed selection cannot be written
    and then blamed on the loader.
    """
    ordered = sorted(str(cid).strip() for cid in citation_ids)
    if len(set(ordered)) != len(ordered):
        raise SelectionError(
            "refusing to write a selection containing duplicate citation_ids")
    manifest = {
        "schema": SELECTION_SCHEMA,
        "note": note,
        "selected_count": len(ordered),
        "cohort_sha256": cohort_digest(ordered),
        "source_runs": _require_source_runs(list(source_runs)),
        "citation_ids": ordered,
    }
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    manifest["manifest_sha256"] = sha256_file(path)
    manifest["path"] = os.path.abspath(path)
    return manifest
