"""Freeze the DETERMINISTIC terminal outcomes, then prove the paid run reproduced them.

WHY THIS EXISTS. A subset of every cohort reaches its terminal outcome with no
model call and no network call at all: an empty citing sentence is
``empty_claim_input`` on any run, on any day, forever. Those rows are the only
part of the answer that is knowable BEFORE the money is spent -- which makes them
the only part that can be used to check the engine afterwards.

So they are sealed first and asserted second. If a row that was deterministically
``UNJUDGEABLE / cited_text_unavailable`` before the run comes back ``NONE``, the
engine changed a deterministic answer, and every non-deterministic number in the
same file is now suspect too. That is a STOP condition, not a diff to review: the
rows that drifted are not the interesting ones, they are the ones that prove the
rest cannot be trusted.

WHAT IS AND IS NOT IN THE FREEZE. Membership is the CALLER's decision and is
recorded in the artifact, because "deterministic" depends on which seams a run
wires. A reference with no PMID is deterministically unjudgeable only while
nothing can fetch its abstract; wire an OpenAlex seam and the same row becomes
network-dependent and must leave the freeze. Sealing a row whose answer depends
on a seam would turn a legitimate improvement into a false alarm, so the artifact
names the seams it assumed and the verifier reports them back.

THE COMPARISON IS ON (id, outcome, reason), NOT ON ids ALONE. A run that keeps
every id and silently rewrites `empty_claim_input` to `claim_extraction_empty`
has changed what the record MEANS while leaving the id set identical, and an
id-only check would pass it.

This module performs NO network call, NO model call, and no work beyond hashing
and set comparison.
"""
from __future__ import annotations

import hashlib
import json
import os

#: Bump when the artifact shape or the digest convention changes.
FREEZE_SCHEMA = "deterministic_freeze_v1"


class FreezeError(ValueError):
    """The freeze artifact cannot be built, read, or reconciled with a run."""


def _canonical_line(citation_id: str, outcome: str, reason: str) -> str:
    """One row's canonical byte form. Tab-separated, newline-terminated.

    Pinned as a convention because a digest is only an identity if both sides
    agree on the byte sequence, and the separator has to be a character that
    cannot occur inside any of the three fields.
    """
    for field in (citation_id, outcome, reason):
        if "\t" in field or "\n" in field:
            raise FreezeError(
                f"field {field!r} contains a separator character; the canonical "
                "row form would be ambiguous")
    return f"{citation_id}\t{outcome}\t{reason}\n"


def freeze_digest(rows) -> str:
    """sha256 over the rows sorted by citation_id, in canonical form.

    Sorted so the digest is a property of the SET, not of the order a caller
    happened to iterate in.
    """
    ordered = sorted(rows, key=lambda r: r["citation_id"])
    payload = "".join(
        _canonical_line(r["citation_id"], r["terminal_outcome"], r["reason"])
        for r in ordered)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_freeze(rows, *, cohort_sha256: str = "", assumed_seams=(),
                 note: str = "") -> dict:
    """Seal a deterministic-outcome set. Refuses a duplicate id.

    ``assumed_seams`` names what the caller assumed was NOT wired when it decided
    these rows were deterministic. It is recorded rather than checked, because
    this module cannot see a run's wiring -- but a mismatch reported after the
    fact is what turns a confusing drift into an obvious one.
    """
    seen: dict = {}
    clean: list = []
    for row in rows:
        cid = str(row.get("citation_id") or "").strip()
        outcome = str(row.get("terminal_outcome") or "").strip()
        reason = str(row.get("reason") or "").strip()
        if not cid:
            raise FreezeError("a frozen row has no citation_id")
        if not outcome:
            raise FreezeError(f"{cid}: a frozen row has no terminal_outcome")
        if cid in seen:
            raise FreezeError(
                f"duplicate citation_id {cid!r} in the freeze set; a duplicate "
                "would make the digest depend on multiplicity rather than on the "
                "set it claims to describe")
        seen[cid] = True
        clean.append({"citation_id": cid, "terminal_outcome": outcome,
                      "reason": reason})
    if not clean:
        raise FreezeError(
            "refusing to seal an EMPTY freeze set; an empty freeze verifies "
            "trivially against any run and would read as a passed check")
    by_outcome: dict = {}
    by_reason: dict = {}
    for row in clean:
        by_outcome[row["terminal_outcome"]] = by_outcome.get(
            row["terminal_outcome"], 0) + 1
        key = f"{row['terminal_outcome']}/{row['reason']}"
        by_reason[key] = by_reason.get(key, 0) + 1
    return {
        "schema": FREEZE_SCHEMA,
        "note": note,
        "cohort_sha256": cohort_sha256,
        "assumed_seams_absent": sorted(str(s) for s in assumed_seams),
        "frozen_count": len(clean),
        "counts_by_outcome": dict(sorted(by_outcome.items())),
        "counts_by_outcome_reason": dict(sorted(by_reason.items())),
        "freeze_sha256": freeze_digest(clean),
        "rows": sorted(clean, key=lambda r: r["citation_id"]),
    }


def write_freeze(path: str, artifact: dict) -> str:
    """Write atomically and return the file digest."""
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(artifact, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def load_freeze(path: str) -> dict:
    """Load a freeze artifact and RECOMPUTE its digest before trusting it."""
    try:
        with open(path, encoding="utf-8") as fh:
            artifact = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise FreezeError(f"cannot read freeze artifact {path}: {exc}") from exc
    if not isinstance(artifact, dict) or artifact.get("schema") != FREEZE_SCHEMA:
        raise FreezeError(
            f"{path}: schema {artifact.get('schema')!r} is not {FREEZE_SCHEMA!r}")
    rows = artifact.get("rows")
    if not isinstance(rows, list) or not rows:
        raise FreezeError(f"{path}: 'rows' must be a nonempty list")
    computed = freeze_digest(rows)
    if computed != artifact.get("freeze_sha256"):
        raise FreezeError(
            f"{path}: freeze_sha256 does not recompute from the rows "
            f"(declared {artifact.get('freeze_sha256')}, computed {computed}); "
            "the artifact was edited after it was sealed")
    return artifact


def verify_against_run(artifact: dict, records) -> dict:
    """Assert the run reproduced every frozen answer EXACTLY.

    ``records`` is any iterable of durable prediction records (dicts carrying
    ``citation_id``, ``terminal_outcome`` and ``terminal_reason``).

    Returns a report with ``ok`` and three disjoint drift lists. ``ok`` is False
    on ANY of them, and a False here means stop: a deterministic answer that
    moved is evidence about the engine, not about the citation, and it
    invalidates the non-deterministic rows in the same file by association.
    """
    frozen = {r["citation_id"]: (r["terminal_outcome"], r["reason"])
              for r in artifact["rows"]}
    observed: dict = {}
    for record in records:
        cid = str(record.get("citation_id") or "").strip()
        if cid in frozen:
            observed[cid] = (str(record.get("terminal_outcome") or ""),
                             str(record.get("terminal_reason") or ""))

    missing = sorted(set(frozen) - set(observed))
    # Sorted BY citation_id, never by the dicts themselves: ordering dicts raises
    # TypeError, and it would raise only once two or more rows had actually
    # drifted -- i.e. exactly on the run this function exists to check, and never
    # on the runs where it is exercised.
    changed = [
        {"citation_id": cid,
         "frozen": {"terminal_outcome": frozen[cid][0], "reason": frozen[cid][1]},
         "observed": {"terminal_outcome": observed[cid][0],
                      "reason": observed[cid][1]}}
        for cid in sorted(set(frozen) & set(observed))
        if frozen[cid] != observed[cid]
    ]
    # Outcome moved vs reason moved, separated: the first is a routing change,
    # the second is a relabelling, and they are diagnosed in different places.
    outcome_changed = [c for c in changed
                       if c["frozen"]["terminal_outcome"]
                       != c["observed"]["terminal_outcome"]]
    outcome_changed_ids = {c["citation_id"] for c in outcome_changed}
    reason_only_changed = [c for c in changed
                           if c["citation_id"] not in outcome_changed_ids]
    return {
        "ok": not missing and not changed,
        "frozen_count": len(frozen),
        "observed_count": len(observed),
        "freeze_sha256": artifact.get("freeze_sha256"),
        "missing_from_run": missing,
        "outcome_changed": outcome_changed,
        "reason_changed_only": reason_only_changed,
        "assumed_seams_absent": artifact.get("assumed_seams_absent") or [],
        "verdict": (
            "STOP: the engine changed a deterministic answer"
            if (missing or changed) else
            "every frozen deterministic answer reproduced exactly"),
    }
