"""LIVE rows for the coverage_v3 output-contract fix (ZD 2026-08-11, run 3).

TWO opt-in gates, because the two halves need different credentials:

  * ``CRE_LIVE_NCBI=1`` -- the retrieval half. Runs here.
  * ``ANTHROPIC_API_KEY`` set -- the MODEL half, which is the acceptance row
    "``PMC10115774:CR42`` (6 claims), live -> any route except PARSE_QUARANTINE".
    NOT RUN in the build environment: no key is present there. It is written so
    ZD can run it in Colab, where the notebook already has one.

Skipped by default, because the rest of ``cre/f1`` is offline by construction and a
network test in the default suite turns an outage into a red build.

CODE-PATH fixtures only: nothing here is a gold label or an input to a reported
number. ``PMC10115774:CR42`` is the reference that quarantined in run 3, and its
cited paper is PMID ``33903719`` -> ``PMC8076174``.

Run with:
    CRE_LIVE_NCBI=1 PYTHONPATH=. ../.venv_cre/bin/python -m pytest \\
        tests/test_output_contract_v3_live.py -q -s
"""
from __future__ import annotations

import json
import os

import pytest

from cde.claims import coverage_prompts as v3
from cde.claims import fulltext as fr
from cde.claims import band as jb

live_ncbi = pytest.mark.skipif(
    os.environ.get("CRE_LIVE_NCBI") != "1",
    reason="live NCBI row; set CRE_LIVE_NCBI=1 to run")
live_model = pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="live MODEL row; needs ANTHROPIC_API_KEY (run in Colab)")

#: The reference that quarantined in run 3, and the paper it cites.
CR42_CITED_PMID = "33903719"
CR42_CITED_PMCID = "PMC8076174"

#: Run 3's CR42 citance, verbatim from PMC10115774. Six atomic claims came out of
#: it, the most of any reference in the document -- which is why it drew six replies
#: and why one bad draw cost all six claims.
CR42_CITANCE = (
    "Intriguingly, however, only three of the fungal genera significantly "
    "correlated with lab lignin decomposition have been reported to degrade "
    "lignocellulose (Trichocladium, soft-rot; Mycena and Hypochnicium, white "
    "rot)40-42 (Supplementary Table 4)."
)

#: The claims run 3 judged, as the grading sheet recorded them. Used ONLY to drive
#: the live prompt; no label or verdict here is gold.
CR42_CLAIMS = [
    "Trichocladium has been reported to degrade lignocellulose.",
    "Mycena has been reported to degrade lignocellulose.",
    "Hypochnicium has been reported to degrade lignocellulose.",
    "Trichocladium is a soft-rot fungus.",
    "Mycena is a white-rot fungus.",
    "Hypochnicium is a white-rot fungus.",
]


@pytest.fixture(scope="module")
def cr42_evidence():
    out = fr.fetch_fulltext(CR42_CITED_PMID,
                            api_key=os.environ.get("NCBI_API_KEY", ""))
    assert out is not None
    return out


@live_ncbi
def test_cr42s_cited_paper_retrieves_complete(cr42_evidence):
    """The retrieval half. Run 3's CR42 quarantined on the REPLY, not the fetch --
    so this must stay clean or the acceptance row below is testing the wrong thing."""
    out = cr42_evidence
    print(f"\n  {CR42_CITED_PMID} -> {out['pmcid']} complete="
          f"{out['retrieval_complete']} reasons={out['incomplete_reasons']} "
          f"n_sections={len(out['sections'])}")
    print(f"  sections_present={out['sections_present']}")
    assert out["pmcid"] == CR42_CITED_PMCID
    assert out["retrieval_complete"] is True
    assert out["incomplete_reasons"] == []


@live_ncbi
def test_the_table_label_run_2_mislabelled_is_really_there(cr42_evidence):
    """Run 2's CR42 span-audit miss attributed pipe-delimited table content to
    ``intro``. This is the live proof that ``table`` was available and unused: the
    reader emits it for this exact paper, and the passage the judge quoted is in it."""
    labels = v3.supplied_labels(cr42_evidence["sections"])
    print(f"\n  supplied_labels={labels}")
    assert "table" in labels
    tables = [s for s in cr42_evidence["sections"] if s["label"] == "table"]
    assert tables, "the reader emitted no table section for PMC8076174"
    print(f"  table[0] opens: {tables[0]['text'][:90]!r}")
    assert "Species name | Code | Phyllum" in tables[0]["text"]


@live_ncbi
def test_the_real_sections_segment_into_addressable_units(cr42_evidence):
    """DEC-047 on real data: the whole paper becomes addressable, and the table row
    run 2 mislabelled as ``intro`` now has an id the model can point at instead of
    retyping.

    Also the cost check. Ids add characters to a prompt that was already ~52 KB, and
    if that were expensive the redesign would trade one problem for another."""
    from cde.claims import spans as ss

    units = ss.segment_sections(cr42_evidence["sections"])
    total = sum(len(u) for u in units.values())
    print(f"\n  {total} addressable units across {len(units)} labels")
    for label, group in units.items():
        print(f"    [{label}] {len(group)} units")
    assert total > 100, "a 19-section paper should yield many addressable units"

    # The header row run 2 attributed to `intro`, now one unit under `table`.
    table = units["table"]
    header = [u for u in table if "Species name | Code | Phyllum" in u["text"]]
    assert header, "the table header row was not emitted as its own unit"
    print(f"    table header is {header[0]['id']}: {header[0]['text'][:70]}")
    # A row is never split on the periods inside its cells.
    assert header[0]["text"].count(" | ") >= 5

    # Segmentation is pure, so a second pass over the same sections is identical --
    # the property that lets the prompt and the resolver agree on what s2 means.
    assert ss.segment_sections(cr42_evidence["sections"]) == units


@live_ncbi
def test_ids_cost_little_on_a_real_prompt(cr42_evidence):
    """The id prefixes are ~3% of a prompt this size. Worth stating, because a
    redesign that tripled prompt cost would not be a good trade."""
    rendered = v3.render_evidence_sections(cr42_evidence["sections"])
    raw = sum(len(s["text"]) for s in cr42_evidence["sections"])
    overhead = (len(rendered) - raw) / raw
    print(f"\n  evidence block {len(rendered)} chars vs {raw} raw "
          f"({overhead:.1%} overhead for labels + ids)")
    assert overhead < 0.25


@live_ncbi
def test_the_real_prompt_carries_the_reply_shape_rule(cr42_evidence):
    """What the model actually sees for this reference. The evidence block is large,
    which is the context in which run 3's reply grew a second object -- so the
    reply-shape rule has to be present in the REAL rendered prompt, not just in the
    template."""
    prompt = v3.render_prompt(CR42_CLAIMS[0], cr42_evidence["sections"])
    flat = " ".join(prompt.split())
    print(f"\n  rendered prompt {len(prompt)} chars, evidence block "
          f"{len(v3.render_evidence_sections(cr42_evidence['sections']))} chars")
    assert "EXACTLY ONE bare JSON object" in flat
    assert "You POINT at them by id" in flat
    assert "for the ONE claim above" in flat
    assert "Never emit a second object" in flat
    assert "END OF EXAMPLES" in prompt
    assert CR42_CLAIMS[0] in prompt


@live_ncbi
@live_model
def test_cr42_does_not_quarantine_with_a_live_judge(tmp_path, monkeypatch,
                                                    cr42_evidence):
    """THE ACCEPTANCE ROW: ``PMC10115774:CR42`` (6 claims), live -> any route except
    PARSE_QUARANTINE.

    All six claims go through the real prompt and the real model, exactly as the
    band drives them: one call per claim, six calls, and ONE malformed reply
    quarantines the whole reference. That reference-level amplification is the
    defect -- P(loss) = 1 - (1-p)**6 here -- so six clean parses is the thing being
    demonstrated, not one.

    Requires ANTHROPIC_API_KEY. Model, prefill and the DEC-046 temperature pin are
    recorded into the manifest via run_band's model-identity parameters, so the row is
    reportable rather than conditional on an unrecorded adapter (DEC-020's lesson).

    UNDER DEC-047 THIS ROW ALSO CHECKS SOMETHING NEW: whether the model actually
    POINTS. The span_status tally is printed, because "did CR42 stop quarantining" and
    "did the judge return usable ids" are different questions and only the second one
    tells us the redesign works. A run of six not_found rows would be a pass on the
    acceptance row and a failure of the design."""
    import anthropic

    MODEL = "claude-sonnet-4-5"
    PREFILL = "{"
    TEMPERATURE = 0                      # DEC-046, pinned
    client = anthropic.Anthropic()

    def call_llm(prompt: str) -> str:
        reply = client.messages.create(
            model=MODEL, max_tokens=2000, temperature=TEMPERATURE,
            messages=[{"role": "user", "content": prompt},
                      {"role": "assistant", "content": PREFILL}])
        return PREFILL + reply.content[0].text

    monkeypatch.setattr(jb, "ncbi_pubtypes", lambda *a, **k: [])
    monkeypatch.setattr(jb, "is_review", lambda value: False)

    xml_dir = tmp_path / "xml"
    xml_dir.mkdir()
    (xml_dir / "PMC10115774.xml").write_text(
        '<article><body><p>' + CR42_CITANCE +
        ' <xref ref-type="bibr" rid="CR42">42</xref>.</p></body>'
        '<back><ref-list>'
        '<ref id="CR42"><element-citation><article-title>Cited 42</article-title>'
        f'<pub-id pub-id-type="pmid">{CR42_CITED_PMID}</pub-id>'
        '</element-citation></ref>'
        '</ref-list></back></article>',
        encoding="utf-8")

    out_dir = tmp_path / "out"
    manifest = jb.run_band(
        str(xml_dir), str(out_dir),
        # The six claims are FIXED here rather than extracted, so this row tests the
        # output contract and not extraction nondeterminism (CONTRADICTIONS 36,
        # explicitly out of scope for this spec).
        extractor=lambda sentence: list(CR42_CLAIMS),
        coverage_judge=lambda claims, evidence: [
            {"established": None} for _ in claims],
        fetch_abstract=lambda pmid: "unused on the full-text path",
        fetch_fulltext=lambda pmid: cr42_evidence,
        coverage_judge_v3=v3.make_coverage_judge_v3(call_llm),
        model=MODEL, assistant_prefill=PREFILL, temperature=TEMPERATURE,
        session=object())

    row = json.loads(
        (out_dir / "judgment_band_items.jsonl").read_text(encoding="utf-8")
        .splitlines()[0])
    print(f"\n  route={row['proposed_route']}  claims={len(row['atomic_claims'])}")
    print(f"  parse_error={row.get('parse_error')!r}")
    for verdict in row.get("coverage_verdicts", []):
        print(f"    established={verdict['established']!r} "
              f"span_status={verdict['span_status']} "
              f"ids={[(s['label'], s['sentence_ids']) for s in verdict['evidence_spans']]}")
    print(f"  evidence_selection={manifest.get('evidence_selection')}")

    # THE ACCEPTANCE ROW.
    assert manifest["counts"][jb.ROUTE_PARSE_QUARANTINE] == 0
    assert row["proposed_route"] != jb.ROUTE_PARSE_QUARANTINE
    assert len(row["coverage_verdicts"]) == len(CR42_CLAIMS)

    # Every span the live judge produced resolves verbatim -- which under selection is
    # a check on the RESOLVER, not on the model, and cannot fail for a selected span.
    # Rows with an EMPTY list are skipped rather than failed: an engaged claim with no
    # resolvable span is a recorded miss (DEC-047), not an error.
    for verdict in row["coverage_verdicts"]:
        if verdict["evidence_spans"]:
            assert v3.spans_are_verbatim(verdict["evidence_spans"],
                                         cr42_evidence["sections"])

    # DID IT POINT? Reported separately from the acceptance row, and asserted only
    # weakly: at least one of six engaged claims should yield a usable selection, or
    # the redesign has not achieved what it set out to.
    selection = manifest["evidence_selection"]
    assert selection["segmenter"]["name"]
    if selection["engaged_claims"]:
        assert selection["engaged_claims_with_span"] >= 1, (
            "no engaged claim produced a resolvable span; the judge is not pointing")

    assert manifest["params"]["model"] == MODEL
    assert manifest["params"]["assistant_prefill"] == PREFILL
    assert manifest["params"]["temperature"] == TEMPERATURE
