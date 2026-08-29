"""ITEMS 1 AND 2 -- the denominator, and the per-pair answer behind it.

THE SINGLE MOST VALUABLE TEST IN THE SUITE is the route distribution. Every rate
this project reports is a count over a route divided by a denominator assembled
from the others, so a silent change to the distribution invalidates every one of
them at once -- and does it without failing anything, because a pipeline that
routes differently still runs, still writes records and still reports
``accounting_ok``.

The per-pair golden file is the readable companion. A distribution can move by
two pairs swapping buckets and net to zero; the per-pair record cannot. When
either fails, the golden diff names the citation whose answer changed.
"""
from __future__ import annotations

import collections
import json

import pytest

from cre.f1 import judgment_run as jr
from cre.f1.schema import ClaimedRef, Reference

from .conftest import FIXTURES, Recorded, assert_golden


def _references(doc):
    return [
        Reference(
            citation_id=r["citation_id"], citance=r["citance"],
            claimed=ClaimedRef(claimed_pmid=r["pmid"], title=r["title"]),
            cited_reference_marker=r["marker"],
            source_pmcid=doc["source_pmcid"], source_pmid=doc["source_pmid"],
            source_title=doc["source_title"],
        )
        for r in doc["references"]
    ]


@pytest.fixture(scope="module")
def band2_run(tmp_path_factory):
    """Run the REAL orchestrator over the recorded corpus, once per module.

    Every seam is a recorded table that raises on a miss (see ``Recorded``), the
    XML parse is replaced by the fixture's own reference objects, and nothing
    reaches the network, a provider or the wall clock.
    """
    corpus = json.loads(
        (FIXTURES / "band2_corpus.json").read_text(encoding="utf-8"))
    tmp = tmp_path_factory.mktemp("band2")

    docs = corpus["documents"]
    for doc in docs:
        (tmp / f"{doc['source_pmcid']}.xml").write_text("<x/>", encoding="utf-8")
    by_pmcid = {doc["source_pmcid"]: _references(doc) for doc in docs}

    extractor_table = Recorded("extractor", corpus["extractor"])
    coverage_table = Recorded("coverage judge", corpus["coverage"])
    abstract_table = Recorded("abstract fetcher", corpus["abstracts"])
    spans = corpus["evidence_spans"]

    def parse(path, source_pmcid=None):
        stem = str(path).rsplit("/", 1)[-1].removesuffix(".xml")
        return by_pmcid[stem]

    def extractor(sentence):
        return list(extractor_table.answer(sentence))

    def coverage_judge(claims, evidence):
        pmid = str(evidence.get("cited_pmid") or "")
        out = []
        for claim in claims:
            established = coverage_table.answer(f"{pmid} :: {claim}")
            out.append({"established": established,
                        "rationale": f"recorded verdict for {claim!r}",
                        "evidence_span": spans[pmid] if established else ""})
        return out

    monkey = pytest.MonkeyPatch()
    monkey.setattr(jr, "parse_pmc_xml", parse)
    try:
        manifest = jr.run_natural_judgment(
            str(tmp), str(tmp / "out"),
            extractor=extractor, coverage_judge=coverage_judge,
            fetch_abstract=abstract_table.answer,
            preband_disposition=corpus["disposition"], model="test-model")
    finally:
        monkey.undo()
    rows = [json.loads(line) for line in
            (tmp / "out" / "judgment_predictions.jsonl")
            .read_text(encoding="utf-8").splitlines()]
    return manifest, rows


def test_the_route_distribution_over_the_fixed_corpus_is_unchanged(band2_run):
    """THE DENOMINATOR. Every reported rate is a ratio over these counts.

    A refactor that deletes a module and moves one pair from ``F6_FLAGGED`` to
    ``HELD_LOW_CONFIDENCE`` changes the false-alarm rate in the paper and breaks
    nothing else in the suite.
    """
    _, rows = band2_run
    counts = collections.Counter(row.get("route") or "(none)" for row in rows)
    assert_golden("band2_route_distribution.txt",
                  [f"{route}\t{n}" for route, n in counts.items()])


def test_the_disposition_distribution_over_the_fixed_corpus_is_unchanged(band2_run):
    """The exclusion buckets, which the route field does not carry.

    ``route`` is empty for a pair that never reached the coverage stage, so the
    route distribution alone cannot tell "excluded because Band 1 called it F2"
    from "excluded because the citance was empty" -- and those two leave the
    denominator for different reasons.
    """
    _, rows = band2_run
    counts = collections.Counter(row["disposition"] for row in rows)
    assert_golden("band2_disposition_distribution.txt",
                  [f"{d}\t{n}" for d, n in counts.items()])


def test_every_pair_keeps_its_terminal_outcome_and_route_reason(band2_run):
    """ITEM 2. One line per pair, sorted: the readable answer to "what moved?"."""
    _, rows = band2_run
    lines = []
    for row in rows:
        lines.append("\t".join((
            row["citation_id"],
            str(row.get("terminal_outcome") or ""),
            str(row.get("terminal_reason") or ""),
            str(row.get("route") or ""),
            row["disposition"],
            ",".join(row.get("findings") or ()),
            ",".join(row.get("label") or ()) if isinstance(row.get("label"), list)
            else str(row.get("label") or ""),
        )))
    assert_golden("band2_pairs.txt", lines)


def test_the_run_accounts_for_every_reference_it_was_given(band2_run):
    """The accounting identity, pinned separately from the counts it sums.

    ``accounting_ok`` is the manifest's own claim that no pair was dropped
    between parse and output. It is asserted here rather than trusted because a
    restructure that lost a stage could produce a smaller, entirely
    self-consistent run.
    """
    manifest, rows = band2_run
    corpus = json.loads(
        (FIXTURES / "band2_corpus.json").read_text(encoding="utf-8"))
    expected = sum(len(doc["references"]) for doc in corpus["documents"])
    assert len(rows) == expected
    assert manifest.get("accounting_ok") is True


# ---------------------------------------------------------------------------
# item 11 -- the chain and the pinned versions
# ---------------------------------------------------------------------------
def test_every_byte_of_every_record_is_unchanged(band2_run):
    """ONE DIGEST OVER THE WHOLE RECORD STREAM.

    This catches what the route and outcome goldens cannot: a rationale that
    moved, an evidence span that lost a character, a field that quietly stopped
    being written. None of those changes a count, and all of them are read by a
    reviewer.

    NOT the manifest's own ``chain_tip``. The chain anchors on the run as well
    as its records, so its tip differs between two runs over identical inputs --
    correctly, it identifies a run, not a result. Pinning it would give a golden
    file that fails every time it is looked at, which trains a reader to ignore
    a red suite. The record stream itself IS byte-stable, and it is the part
    that carries the answers.

    The manifest lifecycle -- immutability, resume, torn tails -- is pinned
    per-behaviour in the unit suite already; restating it here would be noise.
    What is pinned nowhere else is the record content over a fixed input.
    """
    import hashlib
    _, rows = band2_run
    # `ts` is a wall-clock stamp: it says WHEN the record was written, not what
    # the record says. It is excluded from the digest and asserted separately,
    # so its removal is still caught while its ticking does not turn this into a
    # test that fails whenever it is looked at.
    assert all(isinstance(row.get("ts"), int) for row in rows), (
        "every record carries an integer ts; if that stopped being true the "
        "exclusion below would be silently hiding a real field change")
    stream = "\n".join(
        json.dumps({k: v for k, v in row.items() if k != "ts"},
                   sort_keys=True, ensure_ascii=False)
        for row in rows)
    assert_golden("band2_record_digest.txt", [
        f"records\t{len(rows)}",
        f"sha256\t{hashlib.sha256(stream.encode()).hexdigest()}",
    ])


def test_the_manifest_still_pins_the_prompt_and_parser_versions(band2_run):
    """The version stamps a reported number is only interpretable against.

    A run whose manifest lost its coverage prompt version is not a run with one
    fewer field -- it is a run whose results cannot be attributed to a prompt,
    and no downstream check notices.
    """
    manifest, _ = band2_run
    assert_golden("band2_manifest_versions.txt", [
        f"{key}\t{manifest[key]}" for key in (
            "claim_extract_prompt_version", "claim_extract_parser_version",
            "coverage_prompt_version", "coverage_parser_version")])
