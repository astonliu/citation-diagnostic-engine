"""Build a deterministic, human-readable F3--F7 adjudication packet.

This module is deliberately downstream of a completed run.  It reads the
durable predictions JSONL and run manifest; it never imports a launcher, calls a
model, fetches evidence, or re-runs a judgment.  The packet is therefore a view
of the recorded run rather than a second inference path.

The unit is one flagged ``(citation_id, claim_index)``.  A claim may carry more
than one finding, but it still gets one stable row id.  Finding discovery uses
the durable ``record["findings"]`` stream plus F4's own
``strength_records[*].derived == "F4"`` audit record.  It intentionally does
not use ``emitted_labels`` or ``seam_status``: label precedence can mask a real
finding there.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Iterable, Mapping, Sequence


NO_SPAN_RECORDED = "No evidence span recorded."
EFFORT_NOT_RECORDED = "NOT RECORDED IN MANIFEST"
_ENGINE_ORDER = ("F7", "F6", "F4", "F3", "F5")
_FORBIDDEN_PACKET_KEYS = frozenset({
    "confidence", "score", "rank", "proposed_answer", "proposed_route",
    "proposed_verdict", "proposed_corrected_id", "proposed_corrected_label",
})


class PacketBuildError(ValueError):
    """A run artifact cannot be represented without silently losing meaning."""


def stable_row_id(citation_id: str, claim_index: int) -> str:
    """Return the stable join key for one ``(citation_id, claim_index)`` pair."""
    if not isinstance(citation_id, str) or not citation_id.strip():
        raise PacketBuildError("citation_id must be a nonblank string")
    if type(claim_index) is not int or claim_index < 0:
        raise PacketBuildError("claim_index must be a nonnegative integer")
    payload = f"{citation_id}\x1f{claim_index}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _nonblank(value) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else ""


def _ordered_labels(labels: Iterable[str]) -> list[str]:
    unique = list(dict.fromkeys(labels))
    known = [label for label in _ENGINE_ORDER if label in unique]
    return known + sorted(label for label in unique if label not in _ENGINE_ORDER)


def _json_key_paths(value, *, suffix: str, prefix: str = "") -> dict[str, str]:
    """Collect nonblank string leaves whose key ends with ``suffix``."""
    out: dict[str, str] = {}
    if not isinstance(value, Mapping):
        return out
    for key, nested in value.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if str(key).endswith(suffix):
            text = _nonblank(nested)
            if text:
                out[path] = text
        if isinstance(nested, Mapping):
            out.update(_json_key_paths(nested, suffix=suffix, prefix=path))
    return out


def _first_nonblank(*values) -> str:
    for value in values:
        text = _nonblank(value)
        if text:
            return text
    return ""


def _header(manifest: Mapping, *, predictions_source: str,
            manifest_source: str) -> dict:
    if not isinstance(manifest, Mapping):
        raise PacketBuildError("manifest must be a JSON object")
    if manifest.get("status") != "complete":
        raise PacketBuildError(
            "adjudication packets require a completed run manifest")

    adapter = manifest.get("adapter")
    adapter = adapter if isinstance(adapter, Mapping) else {}
    launch = manifest.get("launch_receipt")
    launch = launch if isinstance(launch, Mapping) else {}
    model = _first_nonblank(manifest.get("model"), adapter.get("model"),
                            launch.get("model"))
    scope = _first_nonblank(manifest.get("evidence_scope_effective"),
                            manifest.get("evidence_scope"))
    code_commit = _nonblank(manifest.get("code_commit"))
    chain_tip = _nonblank(manifest.get("chain_tip"))
    missing = [name for name, value in (
        ("model", model), ("evidence scope", scope),
        ("code commit", code_commit), ("chain tip", chain_tip),
    ) if not value]
    if missing:
        raise PacketBuildError(
            "manifest is missing packet run identity: " + ", ".join(missing))

    effort = _first_nonblank(
        manifest.get("effort"), manifest.get("reasoning_effort"),
        adapter.get("effort"), adapter.get("reasoning_effort"),
        launch.get("effort"), launch.get("reasoning_effort"),
    ) or EFFORT_NOT_RECORDED
    prompt_versions = _json_key_paths(manifest, suffix="_prompt_version")
    if not prompt_versions:
        raise PacketBuildError("manifest records no prompt versions")
    return {
        "model": model,
        "effort": effort,
        "evidence_scope": scope,
        "prompt_versions": dict(sorted(prompt_versions.items())),
        "code_commit": code_commit,
        "chain_tip": chain_tip,
        "predictions_source": predictions_source,
        "manifest_source": manifest_source,
    }


def _normalize_span(value, *, role: str = "evidence") -> dict | None:
    if isinstance(value, str):
        text = _nonblank(value)
        return {"role": role, "text": text} if text else None
    if not isinstance(value, Mapping):
        return None
    text = _nonblank(value.get("text"))
    if not text:
        return None
    span = {"role": role, "text": text}
    label = _nonblank(value.get("label"))
    if label:
        span["section"] = label
    sentence_ids = value.get("sentence_ids")
    if isinstance(sentence_ids, Sequence) and not isinstance(
            sentence_ids, (str, bytes)):
        ids = [_nonblank(item) for item in sentence_ids]
        ids = [item for item in ids if item]
        if ids:
            span["sentence_ids"] = ids
    source = _nonblank(value.get("span_source"))
    if source:
        span["source"] = source
    return span


def _dedupe_spans(spans: Iterable[dict | None]) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for span in spans:
        if span is None:
            continue
        key = json.dumps(span, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"))
        if key not in seen:
            seen.add(key)
            out.append(span)
    return out


def _coverage_spans(verdict: Mapping) -> list[dict]:
    raw = verdict.get("evidence_spans")
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        return _dedupe_spans(_normalize_span(item) for item in raw)
    return _dedupe_spans([_normalize_span(verdict.get("evidence_span"))])


def _reason(*values) -> str:
    return " | ".join(dict.fromkeys(
        text for text in (_nonblank(value) for value in values) if text))


def _claims_and_verdicts(record: Mapping) -> tuple[list[str], list[Mapping]]:
    claims = record.get("atomic_claims")
    verdicts = record.get("coverage_verdicts")
    if not isinstance(claims, list) or not all(
            isinstance(claim, str) and claim.strip() for claim in claims):
        raise PacketBuildError(
            f"{record.get('citation_id')}: atomic_claims must be nonblank strings")
    if not isinstance(verdicts, list) or not all(
            isinstance(verdict, Mapping) for verdict in verdicts):
        raise PacketBuildError(
            f"{record.get('citation_id')}: coverage_verdicts must be objects")
    if len(claims) != len(verdicts):
        raise PacketBuildError(
            f"{record.get('citation_id')}: claim/verdict length mismatch")
    return claims, list(verdicts)


def _indexed_records(record: Mapping, key: str) -> dict[int, Mapping]:
    raw = record.get(key) or []
    if not isinstance(raw, list):
        raise PacketBuildError(f"{record.get('citation_id')}: {key} must be a list")
    out: dict[int, Mapping] = {}
    for item in raw:
        if not isinstance(item, Mapping):
            raise PacketBuildError(
                f"{record.get('citation_id')}: {key} entries must be objects")
        index = item.get("claim_index")
        if type(index) is not int or index < 0:
            raise PacketBuildError(
                f"{record.get('citation_id')}: invalid {key} claim_index")
        if index in out:
            raise PacketBuildError(
                f"{record.get('citation_id')}: duplicate {key} claim_index {index}")
        out[index] = item
    return out


def _expected_findings(record: Mapping) -> list[str]:
    raw = record.get("findings") or []
    if not isinstance(raw, list) or not all(
            isinstance(label, str) and label.strip() for label in raw):
        raise PacketBuildError(
            f"{record.get('citation_id')}: findings must be nonblank strings")
    labels = list(raw)
    strengths = record.get("strength_records") or []
    if not isinstance(strengths, list):
        raise PacketBuildError(
            f"{record.get('citation_id')}: strength_records must be a list")
    if any(isinstance(item, Mapping) and item.get("derived") == "F4"
           for item in strengths):
        labels.append("F4")
    return _ordered_labels(labels)


def _f6_indices(record: Mapping, claims: list[str],
                verdicts: list[Mapping]) -> list[int]:
    negative = [index for index, verdict in enumerate(verdicts)
                if verdict.get("established") is False]
    cocitation = record.get("cocitation")
    if not isinstance(cocitation, Mapping):
        return negative
    uncovered = cocitation.get("uncovered_claims") or []
    uncovered = {value for value in uncovered if isinstance(value, str)}
    selected = [
        index for index in negative
        if verdicts[index].get("contradicts") is True
        or claims[index] in uncovered
    ]
    # An aggregation-excluded member receives no usable sibling flags and keeps
    # its solo F6.  The compact per-record co-citation summary does not retain
    # that exclusion bit, so a recorded F6 with no group-selected gap falls back
    # to its own negative rows.  This cannot invent a finding: the caller enters
    # this branch only because rec["findings"] already contains F6.
    return selected or negative


def _details_for_record(record: Mapping, claims: list[str],
                        verdicts: list[Mapping], expected: list[str]) -> dict:
    details: dict[int, dict[str, dict]] = {}

    def add(index: int, label: str, reason: str, spans: Iterable[dict | None]):
        if type(index) is not int or not 0 <= index < len(claims):
            raise PacketBuildError(
                f"{record.get('citation_id')}: {label} claim_index {index!r} "
                "is out of range")
        details.setdefault(index, {})[label] = {
            "reason": reason or "No engine reason recorded.",
            "evidence_spans": _dedupe_spans(spans),
        }

    for label in expected:
        if label == "F6":
            for index in _f6_indices(record, claims, verdicts):
                verdict = verdicts[index]
                add(index, label, _reason(verdict.get("rationale")),
                    _coverage_spans(verdict))
        elif label == "F4":
            strengths = _indexed_records(record, "strength_records")
            for index, strength in strengths.items():
                if strength.get("derived") != "F4":
                    continue
                spans = list(_coverage_spans(verdicts[index]))
                spans.extend([
                    _normalize_span(strength.get("citing_strength_span"),
                                    role="model-generated citing anchor"),
                    _normalize_span(strength.get("cited_strength_span"),
                                    role="cited strength evidence"),
                ])
                add(index, label,
                    _reason(strength.get("reason"),
                            strength.get("model_rationale")), spans)
        elif label == "F5":
            for index, temporal in _indexed_records(record, "f5_records").items():
                if temporal.get("temporal_state") != "QUALIFYING_CONTRADICTION":
                    continue
                add(index, label, _reason(temporal.get("reason")), [
                    _normalize_span(temporal.get("cited_finding_span"),
                                    role="cited finding"),
                    _normalize_span(temporal.get("candidate_contradiction_span"),
                                    role="newer contradiction"),
                ])
        elif label == "F7":
            for index, entity in _indexed_records(record, "f7_records").items():
                if entity.get("derived") != "DIFFERENT_ENTITY_SUPPORTED":
                    continue
                spans: list[dict | None] = []
                reasons = [entity.get("reason")]
                for tuple_record in entity.get("tuple_records") or []:
                    if not isinstance(tuple_record, Mapping) or not (
                            tuple_record.get("confirmed_mismatch") is True
                            or tuple_record.get("derived") == "CONFIRMED_MISMATCH"):
                        continue
                    reasons.append(tuple_record.get("reason"))
                    spans.extend([
                        _normalize_span({
                            "text": tuple_record.get("entity_span"),
                            "label": tuple_record.get("entity_section_label"),
                        }, role="entity evidence"),
                        _normalize_span({
                            "text": tuple_record.get("relation_span"),
                            "label": tuple_record.get("relation_section_label"),
                        }, role="relation evidence"),
                    ])
                add(index, label, _reason(*reasons), spans)
        elif label == "F3":
            provenance = record.get("provenance")
            provenance = provenance if isinstance(provenance, Mapping) else {}
            spans = [_normalize_span(value, role="provenance evidence")
                     for value in (provenance.get("evidence_spans") or [])]
            for index in range(len(claims)):
                add(index, label, _reason(provenance.get("rationale")), spans)
        else:
            raise PacketBuildError(
                f"{record.get('citation_id')}: unsupported finding {label!r}; "
                "refusing to omit it")

    represented = {label for per_claim in details.values() for label in per_claim}
    missing = [label for label in expected if label not in represented]
    if missing:
        raise PacketBuildError(
            f"{record.get('citation_id')}: finding(s) produced zero packet rows: "
            + ", ".join(missing))
    return details


def _siblings(record: Mapping) -> list[dict]:
    members = record.get("citance_group_members")
    if not isinstance(members, list):
        cocitation = record.get("cocitation")
        members = cocitation.get("members") if isinstance(cocitation, Mapping) else []
    inferred = record.get("citance_group_inferred_members") or []
    inferred = {value for value in inferred if isinstance(value, str)}
    citation_id = record.get("citation_id")
    out = []
    for member in members or []:
        if not isinstance(member, str) or not member.strip() or member == citation_id:
            continue
        out.append({
            "citation_id": member,
            "provenance": "inferred" if member in inferred else "asserted",
        })
    return out


def _assert_unbiased(value, path: str = "packet") -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if str(key).casefold() in _FORBIDDEN_PACKET_KEYS:
                raise PacketBuildError(
                    f"annotator-bias field {key!r} reached {path}")
            _assert_unbiased(nested, f"{path}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _assert_unbiased(nested, f"{path}[{index}]")


def build_packet(records: Iterable[Mapping], manifest: Mapping, *,
                 predictions_source: str = "", manifest_source: str = "") -> dict:
    """Build the structured packet from already-loaded run artifacts."""
    header = _header(manifest, predictions_source=predictions_source,
                     manifest_source=manifest_source)
    rows: list[dict] = []
    expected_global: set[str] = set()
    represented_global: set[str] = set()
    seen_ids: set[str] = set()
    for record_number, record in enumerate(records, 1):
        if not isinstance(record, Mapping):
            raise PacketBuildError(
                f"prediction record {record_number} must be a JSON object")
        expected = _expected_findings(record)
        if not expected:
            continue
        citation_id = _nonblank(record.get("citation_id"))
        if not citation_id:
            raise PacketBuildError(
                f"prediction record {record_number} has findings but no citation_id")
        citing_sentence = _nonblank(record.get("citing_sentence"))
        if not citing_sentence:
            raise PacketBuildError(
                f"{citation_id}: flagged record has no citing sentence")
        cited_pmid = _nonblank(record.get("cited_pmid"))
        if not cited_pmid:
            raise PacketBuildError(f"{citation_id}: flagged record has no cited PMID")
        cited_claimed = record.get("cited_claimed")
        cited_claimed = cited_claimed if isinstance(cited_claimed, Mapping) else {}
        cited_title = _nonblank(cited_claimed.get("title")) or "NOT RECORDED"
        claims, verdicts = _claims_and_verdicts(record)
        details = _details_for_record(record, claims, verdicts, expected)
        expected_global.update(expected)
        siblings = _siblings(record)
        for claim_index in sorted(details):
            row_id = stable_row_id(citation_id, claim_index)
            if row_id in seen_ids:
                raise PacketBuildError(
                    f"duplicate packet row id for {citation_id} claim {claim_index}")
            seen_ids.add(row_id)
            finding_details = details[claim_index]
            labels = _ordered_labels(finding_details)
            represented_global.update(labels)
            rows.append({
                "row_id": row_id,
                "citation_id": citation_id,
                "claim_index": claim_index,
                "labels": labels,
                "citing_sentence": citing_sentence,
                "atomic_claim": claims[claim_index],
                "cited_pmid": cited_pmid,
                "cited_title": cited_title,
                "findings": {label: finding_details[label] for label in labels},
                "co_cited_siblings": list(siblings),
            })

    missing_global = sorted(expected_global - represented_global)
    if missing_global:
        raise PacketBuildError(
            "run finding(s) produced zero packet rows: " + ", ".join(missing_global))
    packet = {"header": header, "rows": rows}
    _assert_unbiased(packet)
    return packet


def load_jsonl(path: str | Path) -> list[dict]:
    rows: list[dict] = []
    source = Path(path)
    with source.open(encoding="utf-8") as fh:
        for line_number, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise PacketBuildError(
                    f"{source}:{line_number}: invalid JSON: {exc.msg}") from exc
            if not isinstance(row, dict):
                raise PacketBuildError(
                    f"{source}:{line_number}: prediction must be an object")
            rows.append(row)
    return rows


def load_manifest(path: str | Path) -> dict:
    source = Path(path)
    try:
        with source.open(encoding="utf-8") as fh:
            manifest = json.load(fh)
    except json.JSONDecodeError as exc:
        raise PacketBuildError(f"{source}: invalid JSON: {exc.msg}") from exc
    if not isinstance(manifest, dict):
        raise PacketBuildError(f"{source}: manifest must be an object")
    return manifest


def build_packet_from_files(predictions_path: str | Path,
                            manifest_path: str | Path) -> dict:
    predictions = Path(predictions_path).resolve()
    manifest_file = Path(manifest_path).resolve()
    return build_packet(
        load_jsonl(predictions), load_manifest(manifest_file),
        predictions_source=str(predictions), manifest_source=str(manifest_file))


def _inline(value) -> str:
    return str(value).replace("`", "'").replace("\n", " ")


def _quote(text: str) -> list[str]:
    lines = text.splitlines() or [""]
    return [f"> {line}" for line in lines]


def render_markdown(packet: Mapping) -> str:
    """Render a structured packet without annotator-bias fields."""
    _assert_unbiased(packet)
    header = packet.get("header") or {}
    rows = packet.get("rows") or []
    lines = [
        "# F3-F7 adjudication packet", "", "## Run identity", "",
        f"- Model: `{_inline(header.get('model', ''))}`",
        f"- Effort: `{_inline(header.get('effort', ''))}`",
        f"- Evidence scope: `{_inline(header.get('evidence_scope', ''))}`",
        f"- Code commit: `{_inline(header.get('code_commit', ''))}`",
        f"- Chain tip: `{_inline(header.get('chain_tip', ''))}`",
        f"- Predictions: `{_inline(header.get('predictions_source', ''))}`",
        f"- Manifest: `{_inline(header.get('manifest_source', ''))}`",
        "- Prompt versions:",
    ]
    for key, value in (header.get("prompt_versions") or {}).items():
        lines.append(f"  - `{_inline(key)}`: `{_inline(value)}`")
    lines.extend([
        "", "## Reviewer boundary", "",
        "Judge each row from the cited evidence shown. The row order is the "
        "run's document order; adjudicators decide independently.", "",
        "## Flagged claims", "",
    ])
    for position, row in enumerate(rows, 1):
        lines.extend([
            f"### Row {position}: `{_inline(row['row_id'])}`", "",
            f"- Citation ID: `{_inline(row['citation_id'])}`",
            f"- Claim index: `{row['claim_index']}`",
            f"- Label(s): `{', '.join(row['labels'])}`",
            f"- Cited PMID: `{_inline(row['cited_pmid'])}`",
            f"- Cited title: {_inline(row['cited_title'])}",
            "", "#### Citing sentence", "",
            *_quote(row["citing_sentence"]),
            "", "#### Atomic claim", "",
            *_quote(row["atomic_claim"]),
            "", "#### Co-cited siblings", "",
        ])
        siblings = row.get("co_cited_siblings") or []
        if siblings:
            for sibling in siblings:
                lines.append(
                    f"- `{_inline(sibling['citation_id'])}` "
                    f"({_inline(sibling['provenance'])})")
        else:
            lines.append("- None recorded.")
        lines.extend(["", "#### Engine findings and evidence", ""])
        for label in row["labels"]:
            finding = row["findings"][label]
            lines.extend([
                f"##### {label}", "",
                f"Reason: {_inline(finding['reason'])}", "",
            ])
            spans = finding.get("evidence_spans") or []
            if not spans:
                lines.extend([NO_SPAN_RECORDED, ""])
                continue
            for span in spans:
                meta = [span.get("role") or "evidence"]
                if span.get("section"):
                    meta.append(f"section={span['section']}")
                if span.get("sentence_ids"):
                    meta.append("sentences=" + ",".join(span["sentence_ids"]))
                if span.get("source"):
                    meta.append(f"source={span['source']}")
                lines.append(f"- **{' | '.join(_inline(x) for x in meta)}**")
                lines.extend(_quote(span["text"]))
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_packet(predictions_path: str | Path, manifest_path: str | Path,
                 output_path: str | Path) -> dict:
    packet = build_packet_from_files(predictions_path, manifest_path)
    output = Path(output_path)
    output.write_text(render_markdown(packet), encoding="utf-8")
    return packet


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build an offline F3-F7 adjudication packet from run artifacts")
    parser.add_argument("predictions", help="judgment_predictions.jsonl")
    parser.add_argument("manifest", help="judgment_run_manifest.json")
    parser.add_argument("output", help="Markdown packet path")
    args = parser.parse_args(argv)
    write_packet(args.predictions, args.manifest, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
