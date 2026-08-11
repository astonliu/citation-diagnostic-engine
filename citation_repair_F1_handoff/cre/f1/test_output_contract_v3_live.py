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
        cre/f1/test_output_contract_v3_live.py -q -s
"""
from __future__ import annotations

import json
import os

import pytest

from cre.f1 import coverage_prompts_v3 as v3
from cre.f1 import fulltext_reader as fr
from cre.f1 import judgment_band as jb

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

    Requires ANTHROPIC_API_KEY. Model and prefill are recorded into the manifest via
    run_band's model-identity parameters, so the row is reportable rather than
    conditional on an unrecorded adapter (DEC-020's lesson)."""
    import anthropic

    MODEL = "claude-sonnet-4-5"
    PREFILL = "{"
    client = anthropic.Anthropic()

    def call_llm(prompt: str) -> str:
        reply = client.messages.create(
            model=MODEL, max_tokens=2000,
            messages=[{"role": "user", "content": prompt},
                      {"role": "assistant", "content": PREFILL}])
        return PREFILL + reply.content[0].text

    monkeypatch.setattr(jb, "ncbi_pubtypes", lambda *a, **k: [])
    monkeypatch.setattr(jb, "is_review", lambda value: False)

    xml_dir = tmp_path / "xml"
    xml_dir.mkdir()
    refs = "".join(
        f'<ref id="CR{i}"><element-citation><article-title>Cited {i}</article-title>'
        f'<pub-id pub-id-type="pmid">{CR42_CITED_PMID}</pub-id>'
        f'</element-citation></ref>' for i in (42,))
    (xml_dir / "PMC10115774.xml").write_text(
        '<article><body><p>' + CR42_CITANCE +
        ' <xref ref-type="bibr" rid="CR42">42</xref>.</p></body>'
        '<back><ref-list>' + refs + '</ref-list></back></article>',
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
        model=MODEL, assistant_prefill=PREFILL,
        session=object())

    row = json.loads(
        (out_dir / "judgment_band_items.jsonl").read_text(encoding="utf-8")
        .splitlines()[0])
    print(f"\n  route={row['proposed_route']}  claims={len(row['atomic_claims'])}")
    print(f"  parse_error={row.get('parse_error')!r}")
    for verdict in row.get("coverage_verdicts", []):
        print(f"    established={verdict['established']!r} "
              f"spans={len(verdict['evidence_spans'])} "
              f"labels={[s['label'] for s in verdict['evidence_spans']]}")
    assert manifest["counts"][jb.ROUTE_PARSE_QUARANTINE] == 0
    assert row["proposed_route"] != jb.ROUTE_PARSE_QUARANTINE
    assert len(row["coverage_verdicts"]) == len(CR42_CLAIMS)
    # Every span the live judge emitted must audit verbatim, and none may carry an
    # ellipsis -- the parser would have raised, so reaching here already proves it.
    for verdict in row["coverage_verdicts"]:
        if verdict["engages_subject"] is True:
            assert v3.spans_are_verbatim(verdict["evidence_spans"],
                                         cr42_evidence["sections"])
    assert manifest["params"]["model"] == MODEL
    assert manifest["params"]["assistant_prefill"] == PREFILL
