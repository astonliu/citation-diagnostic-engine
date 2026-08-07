"""Unencodable model output quarantines the reference instead of killing the batch.

A lone surrogate -- half of a two-part Unicode character -- is legal inside a JSON
string and illegal in UTF-8. So ``json.loads`` accepts it, strict schema validation
accepts it (it is a string, every key is present), the item record is built, and
the failure surfaces only at ``_append_jsonl``. ``UnicodeEncodeError`` is not a
``ValueError``, so it escapes the per-reference guard and aborts the run.

The ordering is what makes it worse than a lost row: a document's rows are
published and THEN the document is checkpointed. A write that raises on row k has
already flushed rows 1..k-1 and never wrote the checkpoint line, so a resume
replays the document, re-appending those rows and hitting the same reply again.

All model and HTTP boundaries are injected. Every string here is a transport
fixture only -- never an evaluation example, a gold label, or an input to any
reported number.
"""
from __future__ import annotations

import json

import pytest

from cre.f1 import band_prompts as bp
from cre.f1 import coverage_aggregate as ca
from cre.f1 import judgment_band as jb


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------
SURROGATE = "\ud800"

# Valid non-ASCII that MUST pass through untouched. Rejecting these would
# silently narrow the corpus -- a worse defect than the crash being fixed.
VALID_NON_ASCII = ["café", "“smart”", "中文", "\U0001f9ec", "אבג"]


def _patch_not_review(monkeypatch):
    monkeypatch.setattr(jb, "ncbi_pubtypes", lambda *args, **kwargs: [])
    monkeypatch.setattr(jb, "is_review", lambda value: False)


def _xml_dir(tmp_path, n_refs=1):
    body = "".join(
        f'<p>Finding {i} <xref ref-type="bibr" rid="R{i}">{i}</xref>.</p>'
        for i in range(1, n_refs + 1))
    refs = "".join(
        f'<ref id="R{i}"><element-citation><article-title>Paper {i}</article-title>'
        f'<pub-id pub-id-type="pmid">{i}</pub-id></element-citation></ref>'
        for i in range(1, n_refs + 1))
    xml_dir = tmp_path / "xml"
    xml_dir.mkdir()
    (xml_dir / "PMC1.xml").write_text(
        "<article><body>" + body + "</body><back><ref-list>"
        + refs + "</ref-list></back></article>",
        encoding="utf-8")
    return str(xml_dir)


def _coverage_reply(span="span", rationale="ok"):
    return json.dumps({
        "engages_subject": True, "contradicts": False,
        "unconfirmed_specifics": [], "rationale": rationale,
        "evidence_span": span,
    }, ensure_ascii=False)


def _scripted(*, bad_coverage_index=None, bad_claim_index=None,
              claim_text="A finding", span_text="span"):
    """One call_llm driving both seams; the Nth call of a kind goes bad."""
    state = {"cov": 0, "claims": 0}

    def call_llm(prompt):
        if "CITED-PAPER ABSTRACT" not in prompt:
            index = state["claims"]
            state["claims"] += 1
            text = (claim_text + " " + SURROGATE
                    if index == bad_claim_index else claim_text)
            return json.dumps({"claims": [text]}, ensure_ascii=False)
        index = state["cov"]
        state["cov"] += 1
        span = SURROGATE if index == bad_coverage_index else span_text
        return _coverage_reply(span=span)

    return call_llm


def _run(tmp_path, monkeypatch, *, call_llm, n_refs=1, abstract="abstract",
         out="out"):
    _patch_not_review(monkeypatch)
    out_dir = tmp_path / out
    manifest = jb.run_band(
        _xml_dir(tmp_path, n_refs), str(out_dir),
        extractor=bp.make_extractor(call_llm),
        coverage_judge=ca.make_coverage_judge(call_llm),
        fetch_abstract=lambda pmid: abstract,
        session=object(),
    )
    return manifest, out_dir


def _rows(out_dir, name):
    path = out_dir / name
    if not path.exists():
        return []
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines()]


def _items(out_dir):
    return _rows(out_dir, "judgment_band_items.jsonl")


def _queue(out_dir):
    return _rows(out_dir, "judgment_band_annotation_queue.jsonl")


def _checkpoint(out_dir):
    return _rows(out_dir, "judgment_band_checkpoint.jsonl")


# --------------------------------------------------------------------------
# the helpers themselves
# --------------------------------------------------------------------------
@pytest.mark.parametrize("text", VALID_NON_ASCII)
def test_reject_unencodable_accepts_every_valid_non_ascii_string(text):
    """The check must reject only what UTF-8 genuinely cannot encode."""
    jb._reject_unencodable({"t": text}, "x")           # must not raise


def test_reject_unencodable_rejects_a_lone_surrogate():
    with pytest.raises(ValueError, match="not JSONL-encodable"):
        jb._reject_unencodable({"t": SURROGATE}, "item record")


def test_reject_unencodable_message_is_itself_encodable():
    """The guard's own message must never re-introduce the poison."""
    with pytest.raises(ValueError) as excinfo:
        jb._reject_unencodable({"t": SURROGATE}, "item record")
    assert str(excinfo.value).encode("utf-8")          # must not raise
    assert str(excinfo.value).isascii()


@pytest.mark.parametrize("text", VALID_NON_ASCII)
def test_safe_json_preserves_valid_non_ascii_verbatim(text):
    assert jb._safe_json({"t": [text]}) == {"t": [text]}


def test_safe_json_fixes_only_the_unencodable_string():
    out = jb._safe_json({"good": "café", "bad": SURROGATE, "n": 1, "b": True})
    assert out["good"] == "café"                       # untouched
    assert out["n"] == 1 and out["b"] is True
    assert SURROGATE not in out["bad"]
    assert json.dumps(out, ensure_ascii=False).encode("utf-8")


def test_safe_json_reaches_into_nested_containers():
    out = jb._safe_json({"a": [{"b": (SURROGATE,)}]})
    assert json.dumps(out, ensure_ascii=False).encode("utf-8")


def test_safe_text_output_always_encodes():
    assert jb._safe_text("café " + SURROGATE + " tail").encode("utf-8")


# --------------------------------------------------------------------------
# the coverage-span case (the original defect)
# --------------------------------------------------------------------------
def test_unencodable_span_quarantines_the_reference(tmp_path, monkeypatch):
    manifest, out_dir = _run(tmp_path, monkeypatch,
                             call_llm=_scripted(bad_coverage_index=0))
    assert manifest["counts"][jb.ROUTE_PARSE_QUARANTINE] == 1


def test_unencodable_span_leaves_the_batch_complete(tmp_path, monkeypatch):
    """No UnicodeEncodeError escapes: the run returns and the doc checkpoints."""
    _manifest, out_dir = _run(tmp_path, monkeypatch,
                              call_llm=_scripted(bad_coverage_index=0))
    assert [row["pmcid"] for row in _checkpoint(out_dir)] == ["PMC1"]


def test_quarantined_reference_reaches_no_annotator(tmp_path, monkeypatch):
    _manifest, out_dir = _run(tmp_path, monkeypatch,
                              call_llm=_scripted(bad_coverage_index=0))
    assert _queue(out_dir) == []


def test_quarantine_row_is_durable_and_its_error_is_pure_ascii(
        tmp_path, monkeypatch):
    _manifest, out_dir = _run(tmp_path, monkeypatch,
                              call_llm=_scripted(bad_coverage_index=0))
    rows = _items(out_dir)
    assert len(rows) == 1
    row = rows[0]
    assert row["proposed_route"] == jb.ROUTE_PARSE_QUARANTINE
    assert row["proposed_verdict"] is None
    assert row["parse_error"].isascii()
    # The row survives a full round trip -- it is not a crash deferred to a reader.
    assert json.dumps(row, ensure_ascii=False).encode("utf-8")
    assert SURROGATE not in json.dumps(row, ensure_ascii=False)


# --------------------------------------------------------------------------
# the same defect arriving through the other two channels
# --------------------------------------------------------------------------
def test_unencodable_extracted_claim_takes_the_same_path(tmp_path, monkeypatch):
    manifest, out_dir = _run(tmp_path, monkeypatch,
                             call_llm=_scripted(bad_claim_index=0))
    assert manifest["counts"][jb.ROUTE_PARSE_QUARANTINE] == 1
    assert _items(out_dir)[0]["proposed_route"] == jb.ROUTE_PARSE_QUARANTINE
    assert _queue(out_dir) == []


def test_unencodable_fetched_abstract_takes_the_same_path(tmp_path, monkeypatch):
    """The check covers the whole record, not just model output: a poisoned
    abstract is assembled into evidence BEFORE the guard opens."""
    manifest, out_dir = _run(tmp_path, monkeypatch, call_llm=_scripted(),
                             abstract="An abstract " + SURROGATE)
    assert manifest["counts"][jb.ROUTE_PARSE_QUARANTINE] == 1
    row = _items(out_dir)[0]
    assert row["proposed_route"] == jb.ROUTE_PARSE_QUARANTINE
    assert SURROGATE not in json.dumps(row, ensure_ascii=False)
    assert _queue(out_dir) == []


def test_unencodable_text_inside_a_parser_error_message_does_not_escape(
        tmp_path, monkeypatch):
    """A strict loader interpolates a raw duplicate JSON key into its message, so
    the message itself can carry the surrogate. Both the print inside the handler
    and the stored value would re-raise from within the guard."""
    duplicate_key = ('{"claims": ["A finding"], "' + SURROGATE + '": 1, "'
                     + SURROGATE + '": 2}')

    def call_llm(prompt):
        if "CITED-PAPER ABSTRACT" not in prompt:
            return duplicate_key
        return _coverage_reply()

    manifest, out_dir = _run(tmp_path, monkeypatch, call_llm=call_llm)
    assert manifest["counts"][jb.ROUTE_PARSE_QUARANTINE] == 1
    assert _items(out_dir)[0]["parse_error"].isascii()


# --------------------------------------------------------------------------
# one bad reference among good ones, and resume
# --------------------------------------------------------------------------
def test_one_bad_reference_does_not_cost_the_other_three(tmp_path, monkeypatch):
    manifest, out_dir = _run(tmp_path, monkeypatch, n_refs=4,
                             call_llm=_scripted(bad_coverage_index=1))
    assert manifest["counts"][jb.ROUTE_PARSE_QUARANTINE] == 1
    assert manifest["counts"]["items_built"] == 3
    rows = _items(out_dir)
    assert len(rows) == 4
    quarantined = [r for r in rows
                   if r["proposed_route"] == jb.ROUTE_PARSE_QUARANTINE]
    judged = [r for r in rows
              if r["proposed_route"] != jb.ROUTE_PARSE_QUARANTINE]
    assert len(quarantined) == 1 and len(judged) == 3
    # Only the judged references reach the annotator.
    assert len(_queue(out_dir)) == 3
    assert quarantined[0]["citation_id"] not in [
        r["item_key"] for r in _queue(out_dir)]


def test_resuming_the_same_document_duplicates_nothing(tmp_path, monkeypatch):
    """Before the fix the write raised mid-publish: earlier rows were already
    flushed and the checkpoint line never written, so a resume replayed the whole
    document. Now the doc checkpoints, and a second pass is a no-op."""
    _manifest, out_dir = _run(tmp_path, monkeypatch, n_refs=4,
                              call_llm=_scripted(bad_coverage_index=1))
    first = _items(out_dir)
    assert len(first) == 4

    # Second pass over the SAME xml dir and out dir: the checkpoint must skip it.
    jb.run_band(
        str(tmp_path / "xml"), str(out_dir),
        extractor=bp.make_extractor(_scripted(bad_coverage_index=1)),
        coverage_judge=ca.make_coverage_judge(_scripted(bad_coverage_index=1)),
        fetch_abstract=lambda pmid: "abstract", session=object())

    assert _items(out_dir) == first                      # no duplicate rows
    assert [r["pmcid"] for r in _checkpoint(out_dir)] == ["PMC1"]
    ids = [r["citation_id"] for r in _items(out_dir)]
    assert len(ids) == len(set(ids))


# --------------------------------------------------------------------------
# the document-level error path
# --------------------------------------------------------------------------
def test_document_error_carrying_a_surrogate_still_checkpoints(
        tmp_path, monkeypatch):
    _patch_not_review(monkeypatch)

    def exploding_parse(path, source_pmcid=None):
        raise ValueError("bad doc " + SURROGATE + " here")

    monkeypatch.setattr(jb, "parse_pmc_xml", exploding_parse)
    out_dir = tmp_path / "out"
    jb.run_band(
        _xml_dir(tmp_path), str(out_dir),
        extractor=bp.make_extractor(_scripted()),
        coverage_judge=ca.make_coverage_judge(_scripted()),
        fetch_abstract=lambda pmid: "abstract", session=object())

    rows = _checkpoint(out_dir)
    assert len(rows) == 1
    assert rows[0]["pmcid"] == "PMC1"
    assert rows[0]["error"].isascii()
    assert SURROGATE not in rows[0]["error"]


# --------------------------------------------------------------------------
# the well-formed run is untouched
# --------------------------------------------------------------------------
def test_well_formed_run_is_unchanged(tmp_path, monkeypatch):
    """Golden counts captured by running this fixture at 0d9a458 and at the fix;
    the two dumps (manifest + items + queue, ts and tmp paths normalized) were
    compared whole and were byte-identical."""
    manifest, out_dir = _run(tmp_path, monkeypatch, n_refs=3,
                             call_llm=_scripted())
    counts = manifest["counts"]
    assert counts[jb.ROUTE_PARSE_QUARANTINE] == 0
    assert counts["items_built"] == 3
    assert counts["refs_seen"] == 3
    assert counts["docs_processed"] == 1
    assert len(_items(out_dir)) == 3
    assert len(_queue(out_dir)) == 3
    assert all(r["proposed_route"] != jb.ROUTE_PARSE_QUARANTINE
               for r in _items(out_dir))
    assert all("parse_error" not in r for r in _items(out_dir))


@pytest.mark.parametrize("text", VALID_NON_ASCII)
def test_valid_non_ascii_is_preserved_in_both_artifacts(
        text, tmp_path, monkeypatch):
    """ensure_ascii=False output is preserved exactly: no ASCII-folding and no
    escaping, in the item record OR the blind annotation payload."""
    manifest, out_dir = _run(
        tmp_path, monkeypatch,
        call_llm=_scripted(claim_text="A finding " + text,
                           span_text="span " + text),
        abstract="An abstract with " + text)

    assert manifest["counts"][jb.ROUTE_PARSE_QUARANTINE] == 0
    row = _items(out_dir)[0]
    assert row["atomic_claims"] == ["A finding " + text]
    assert row["coverage_verdicts"][0]["evidence_span"] == "span " + text
    assert row["evidence"]["cited_abstract"] == "An abstract with " + text
    payload = _queue(out_dir)[0]
    assert payload["atomic_claims"] == ["A finding " + text]
    # Written unescaped, exactly as ensure_ascii=False produces it.
    raw = (out_dir / "judgment_band_items.jsonl").read_text(encoding="utf-8")
    assert text in raw
