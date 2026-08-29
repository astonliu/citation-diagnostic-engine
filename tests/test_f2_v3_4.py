"""F2 v3.4 tests -- seed-parameterize + seed-scope reband_from_cache.

The v3 runner was hardwired to seed 7: _write_run stamped `"seed": 7` and wrote
`*_seed7_*`, and index_claimed_from_xml_dir parsed the WHOLE xml_dir. Held-out
seeds (11/13/17) now share the same XML dir, so v3.4:
  * `_write_run` / `run_f2_seed7_v3` / `reband_from_cache` take a `seed` label that
    parameterizes the output filenames and the summary `"seed"` field. `seed=7`
    (the default) reproduces the frozen seed-7 paths and summary byte-for-byte.
  * `index_claimed_from_xml_dir` / `reband_from_cache` take a `src_pmcids`
    allow-list that scopes the claimed index to one seed's source papers so the
    held-out frame is not contaminated by other seeds' articles.
  * the preserved-version guard is seed-aware: it blocks only when `seed == 7` and
    the version is frozen (v2/v3), so held-out seeds may reband at any version.

Banding logic (build_f2_record, biblio_match, the 0.92 gate, unscoreable rules) is
untouched -- see test_f2_v3_1.py / test_f2_revision.py for that coverage.

Run:  PYTHONPATH=<repo> python -m pytest cre/f1/test_f2_v3_4.py -q
"""
from __future__ import annotations

import json
import os

import pytest


# --------------------------------------------------------------------------
# helpers (mirror test_f2_v3_1.py so this file is self-contained)
# --------------------------------------------------------------------------
def _write_xml(dirpath, pmcid, refs):
    """refs: list of (ref_id, title, author, year, pmid)."""
    body = []
    for rid, title, author, year, pmid in refs:
        title_el = f"<article-title>{title}</article-title>" if title else ""
        body.append(
            f'<ref id="{rid}"><element-citation>'
            f'<person-group person-group-type="author"><name><surname>{author}'
            f'</surname></name></person-group>{title_el}<source>J</source>'
            f'<year>{year}</year><pub-id pub-id-type="pmid">{pmid}</pub-id>'
            f'</element-citation></ref>')
    xml = ('<article><back><ref-list>' + "".join(body)
           + '</ref-list></back></article>')
    with open(os.path.join(dirpath, f"{pmcid}.xml"), "w", encoding="utf-8") as f:
        f.write(xml)


def _write_cache(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _basic_frame(tmp_path):
    """One source paper (PMC0001) citing PMID 111 (a wrong-paper), plus its
    resolved cache line. Returns (xml_dir, cache_path)."""
    xml_dir = tmp_path / "xml"
    xml_dir.mkdir()
    _write_xml(str(xml_dir), "PMC0001", [
        ("r1", "Disseminated varicella infection", "Pannu", 2019, "111")])
    cache = tmp_path / "resolved.jsonl"
    _write_cache(str(cache), [{"src_pmcid": "PMC0001", "pmid": "111", "rec": {
        "resolved": True, "title": "Purple Urine after Catheterization",
        "authors": ["Sabanis"], "year": 2019}}])
    return str(xml_dir), str(cache)


# ==========================================================================
# seed parameterizes the output filename + summary "seed" field
# ==========================================================================
def test_reband_seed7_default_paths_and_summary_unchanged(tmp_path):
    # seed=7 (default) reproduces the frozen seed-7 filenames + summary field.
    from cde.refs.f2_run_v3 import reband_from_cache
    xml_dir, cache = _basic_frame(tmp_path)
    out = tmp_path / "out7"
    summary = reband_from_cache(xml_dir, cache, out_dir=str(out), version="v3_3")
    assert os.path.basename(summary["records_path"]) == "f2_random_oa_seed7_v3_3.jsonl"
    assert summary["seed"] == 7
    assert (out / "f2_random_oa_seed7_v3_3.jsonl").exists()
    assert (out / "f2_random_oa_seed7_v3_3_summary.json").exists()
    # src_pmcids not supplied -> reported as None
    assert summary["n_src_pmcids"] is None


def test_reband_seed_param_changes_filename_and_summary(tmp_path):
    from cde.refs.f2_run_v3 import reband_from_cache
    xml_dir, cache = _basic_frame(tmp_path)
    out = tmp_path / "out11"
    summary = reband_from_cache(xml_dir, cache, out_dir=str(out),
                                version="v3_3", seed=11)
    assert os.path.basename(summary["records_path"]) == "f2_random_oa_seed11_v3_3.jsonl"
    assert summary["seed"] == 11
    assert (out / "f2_random_oa_seed11_v3_3.jsonl").exists()
    assert (out / "f2_random_oa_seed11_v3_3_summary.json").exists()
    # a seed-11 run never writes a seed-7 file
    assert not (out / "f2_random_oa_seed7_v3_3.jsonl").exists()


def test_reband_seed11_does_not_touch_seed7_file(tmp_path):
    # a pre-existing frozen seed-7 file is untouched by a seed-11 reband.
    from cde.refs.f2_run_v3 import reband_from_cache
    xml_dir, cache = _basic_frame(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    frozen = out / "f2_random_oa_seed7_v3_3.jsonl"
    frozen.write_text('{"pmid": "frozen_seed7"}\n')
    reband_from_cache(xml_dir, cache, out_dir=str(out), version="v3_3", seed=11)
    assert frozen.read_text() == '{"pmid": "frozen_seed7"}\n'   # untouched
    assert (out / "f2_random_oa_seed11_v3_3.jsonl").exists()


def test_run_f2_seed7_v3_seed_param_threads_to_filename(tmp_path):
    # fresh-draw runner also threads seed to the output path + summary.
    from cde.refs.f2_run_v3 import run_f2_seed7_v3
    from cde.refs.schema import ClaimedRef, RetrievedRecord
    items = [("1", "PMC1", ClaimedRef(title="A title", authors=["Lee"], year=2020),
              RetrievedRecord(resolved=True, title="A title", authors=["Lee"],
                              year=2020))]
    summary = run_f2_seed7_v3(items, out_dir=str(tmp_path), seed=11)
    assert os.path.basename(summary["records_path"]) == "f2_random_oa_seed11_v3.jsonl"
    assert summary["seed"] == 11
    assert (tmp_path / "f2_random_oa_seed11_v3.jsonl").exists()


# ==========================================================================
# src_pmcids scopes the claimed index to an allow-list
# ==========================================================================
def test_index_src_pmcids_filters_to_allow_list(tmp_path):
    from cde.refs.f2_run_v3 import index_claimed_from_xml_dir
    xml_dir = tmp_path / "xml"; xml_dir.mkdir()
    _write_xml(str(xml_dir), "PMC123", [("r1", "Keep me", "A", 2019, "111")])
    _write_xml(str(xml_dir), "PMC999", [("r1", "Drop me", "B", 2019, "222")])
    index = index_claimed_from_xml_dir(str(xml_dir), src_pmcids={"PMC123"})
    assert set(index) == {("PMC123", "111")}
    assert all(src == "PMC123" for (src, _pmid) in index)


def test_index_src_pmcids_none_is_whole_dir(tmp_path):
    from cde.refs.f2_run_v3 import index_claimed_from_xml_dir
    xml_dir = tmp_path / "xml"; xml_dir.mkdir()
    _write_xml(str(xml_dir), "PMC123", [("r1", "One", "A", 2019, "111")])
    _write_xml(str(xml_dir), "PMC999", [("r1", "Two", "B", 2019, "222")])
    index = index_claimed_from_xml_dir(str(xml_dir))     # None -> whole dir
    assert set(index) == {("PMC123", "111"), ("PMC999", "222")}


def test_index_src_pmcids_accepts_any_iterable(tmp_path):
    # a generator (single-use iterable) must not be consumed before it is applied.
    from cde.refs.f2_run_v3 import index_claimed_from_xml_dir
    xml_dir = tmp_path / "xml"; xml_dir.mkdir()
    _write_xml(str(xml_dir), "PMC123", [("r1", "Keep", "A", 2019, "111")])
    _write_xml(str(xml_dir), "PMC999", [("r1", "Drop", "B", 2019, "222")])
    gen = (p for p in ["PMC123"])
    index = index_claimed_from_xml_dir(str(xml_dir), src_pmcids=gen)
    assert set(index) == {("PMC123", "111")}


def test_reband_src_pmcids_scopes_frame_and_reports_count(tmp_path):
    # two source papers share the XML dir; scope the reband to one of them.
    from cde.refs.f2_run_v3 import reband_from_cache
    xml_dir = tmp_path / "xml"; xml_dir.mkdir()
    _write_xml(str(xml_dir), "PMC_KEEP", [
        ("r1", "Disseminated varicella infection", "Pannu", 2019, "111")])
    _write_xml(str(xml_dir), "PMC_OTHER", [
        ("r1", "Some other seed's article", "Zzz", 2019, "222")])
    cache = tmp_path / "resolved.jsonl"
    _write_cache(str(cache), [
        {"src_pmcid": "PMC_KEEP", "pmid": "111", "rec": {
            "resolved": True, "title": "Purple Urine after Catheterization",
            "authors": ["Sabanis"], "year": 2019}},
        {"src_pmcid": "PMC_OTHER", "pmid": "222", "rec": {
            "resolved": True, "title": "An unrelated resolved title",
            "authors": ["Yyy"], "year": 2019}},
    ])
    summary = reband_from_cache(str(xml_dir), str(cache), out_dir=str(tmp_path),
                                version="v3_3", seed=11,
                                src_pmcids={"PMC_KEEP"})
    recs = {json.loads(l)["pmid"]: json.loads(l)
            for l in open(summary["records_path"])}
    # only the in-scope source paper's citation is in the frame; the other seed's
    # cache line has no claimed match under the allow-list -> dropped, not banded.
    assert set(recs) == {"111"}
    assert summary["n_joined"] == 1
    assert summary["n_unmatched_dropped"] == 1
    assert summary["n_src_pmcids"] == 1


# ==========================================================================
# seed-aware preserved-version guard
# ==========================================================================
def test_reband_seed7_v3_raises_preserved(tmp_path):
    from cde.refs.f2_run_v3 import reband_from_cache
    xml_dir, cache = _basic_frame(tmp_path)
    for frozen in ("v2", "v3", "V3"):
        with pytest.raises(RuntimeError):
            reband_from_cache(xml_dir, cache, out_dir=str(tmp_path),
                              version=frozen, seed=7)


def test_reband_heldout_seed_v3_allowed(tmp_path):
    # seed 11 is NOT preserved -> writing v3 is allowed (guard is seed-aware).
    from cde.refs.f2_run_v3 import reband_from_cache
    xml_dir, cache = _basic_frame(tmp_path)
    out = tmp_path / "out"
    summary = reband_from_cache(xml_dir, cache, out_dir=str(out),
                                version="v3", seed=11)
    assert summary["seed"] == 11
    assert (out / "f2_random_oa_seed11_v3.jsonl").exists()


def test_run_f2_seed7_v3_guard_is_seed_aware(tmp_path):
    # seed 7 v2 is frozen (raises); seed 11 v2 is allowed (held-out).
    from cde.refs.f2_run_v3 import run_f2_seed7_v3
    with pytest.raises(RuntimeError):
        run_f2_seed7_v3([], out_dir=str(tmp_path), version="v2", seed=7)
    summary = run_f2_seed7_v3([], out_dir=str(tmp_path), version="v2", seed=11,
                              refuse_empty=False)
    assert summary["seed"] == 11
    assert (tmp_path / "f2_random_oa_seed11_v2.jsonl").exists()
