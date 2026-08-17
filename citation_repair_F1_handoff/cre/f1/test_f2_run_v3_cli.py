"""The offline-reband CLI (spec §20): the FRAME is defined by a hash-pinned
selection manifest, never by --xml-dir contents (Item 1 frame-scoping fix)."""
from __future__ import annotations

import hashlib
import json

import pytest

from cre.f1 import f2_run_v3


def _manifest(tmp_path, ids, name="selection.json"):
    """Write a seed-selection-style manifest and return (path, sha256)."""
    p = tmp_path / name
    p.write_text(json.dumps({"seed": 37, "selected_pmcids": list(ids)}))
    return str(p), hashlib.sha256(p.read_bytes()).hexdigest()


def test_cli_scopes_frame_to_manifest_not_directory(tmp_path, monkeypatch):
    # --xml-dir is a SUPERSET (PMC1,PMC2,PMC3); the manifest names only PMC1,PMC2.
    # The frame must be the manifest, and PMC3 counted as ignored -- the exact
    # contamination the defect allowed.
    xml = tmp_path / "xml"; xml.mkdir()
    for pid in ("PMC1", "PMC2", "PMC3"):
        (xml / f"{pid}.xml").write_text("<a/>")
    cache = tmp_path / "resolved.jsonl"; cache.write_text("")
    man, man_sha = _manifest(tmp_path, ["PMC1", "PMC2"])
    captured = {}

    def fake_reband(**kw):
        captured.update(kw)
        return {"version": kw["version"], "n_records": 5,
                "n_src_pmcids": len(set(kw["src_pmcids"])), "audit": []}
    monkeypatch.setattr(f2_run_v3, "reband_from_cache", fake_reband)

    rc = f2_run_v3._cli(["--reband-from-cache", "--resolved-cache", str(cache),
                         "--xml-dir", str(xml), "--selection-manifest", man,
                         "--out-dir", str(tmp_path), "--seed", "37",
                         "--version", "candidate_02"])
    assert rc == 0
    assert captured["seed"] == 37 and captured["version"] == "candidate_02"
    # frame = the MANIFEST, not the directory (PMC3 excluded)
    assert sorted(captured["src_pmcids"]) == ["PMC1", "PMC2"]
    meta = captured["extra_manifest"]
    assert meta["selection_manifest_sha256"] == man_sha
    assert meta["n_manifest_src_pmcids"] == 2
    assert meta["n_ignored_stems"] == 1          # PMC3 present in dir, not admitted


def test_cli_requires_selection_manifest(tmp_path):
    # --xml-dir alone (no --selection-manifest) must NOT produce a run.
    xml = tmp_path / "xml"; xml.mkdir()
    (xml / "PMC1.xml").write_text("<a/>")
    cache = tmp_path / "c.jsonl"; cache.write_text("")
    with pytest.raises(SystemExit) as e:
        f2_run_v3._cli(["--reband-from-cache", "--resolved-cache", str(cache),
                        "--xml-dir", str(xml)])
    assert e.value.code != 0


def test_cli_requires_its_args():
    with pytest.raises(SystemExit):        # argparse exits non-zero on missing args
        f2_run_v3._cli([])


def test_cli_missing_corpus_exits_2(tmp_path, capsys):
    # Manifest names PMC1 & PMC2 but only PMC1 has XML -> missing corpus -> exit 2
    # (a silently smaller frame is the same defect in the other direction).
    xml = tmp_path / "xml"; xml.mkdir()
    (xml / "PMC1.xml").write_text("<a/>")
    cache = tmp_path / "c.jsonl"; cache.write_text("")
    man, _ = _manifest(tmp_path, ["PMC1", "PMC2"])
    rc = f2_run_v3._cli(["--reband-from-cache", "--resolved-cache", str(cache),
                         "--xml-dir", str(xml), "--selection-manifest", man,
                         "--out-dir", str(tmp_path)])
    assert rc == 2
    assert "missing corpus" in capsys.readouterr().err
    assert list(tmp_path.glob("*_summary.json")) == []


# --- refusal on an empty/absent corpus (the exit-0-reads-as-pass bug) ---------
def test_cli_refuses_empty_xml_dir(tmp_path):
    # No XML files at all -> argparse error (exit 2), before any run.
    empty = tmp_path / "xml"; empty.mkdir()
    cache = tmp_path / "c.jsonl"; cache.write_text("")
    man, _ = _manifest(tmp_path, ["PMC1"])
    with pytest.raises(SystemExit) as e:
        f2_run_v3._cli(["--reband-from-cache", "--resolved-cache", str(cache),
                        "--xml-dir", str(empty), "--selection-manifest", man])
    assert e.value.code != 0


def test_cli_refuses_nonexistent_xml_dir(tmp_path):
    cache = tmp_path / "c.jsonl"; cache.write_text("")
    man, _ = _manifest(tmp_path, ["PMC1"])
    with pytest.raises(SystemExit) as e:
        f2_run_v3._cli(["--reband-from-cache", "--resolved-cache", str(cache),
                        "--xml-dir", str(tmp_path / "does_not_exist"),
                        "--selection-manifest", man])
    assert e.value.code != 0


def test_cli_refuses_zero_byte_stub_corpus(tmp_path, capsys):
    # ZD's case: 0-byte XML stubs + a real cache row -> the frame comes back empty.
    # Must exit NON-ZERO and must NOT print the all-zeros summary to stdout.
    xml = tmp_path / "xml"; xml.mkdir()
    (xml / "PMC1.xml").write_text("")                 # 0-byte stub
    cache = tmp_path / "c.jsonl"
    cache.write_text('{"pmid": "111", "rec": {"resolved": true, "title": "t", '
                     '"pmid": "111"}}\n')
    man, _ = _manifest(tmp_path, ["PMC1"])
    rc = f2_run_v3._cli(["--reband-from-cache", "--resolved-cache", str(cache),
                         "--xml-dir", str(xml), "--selection-manifest", man,
                         "--out-dir", str(tmp_path)])
    assert rc == 3
    out = capsys.readouterr()
    # Nothing on stdout (it would read as a pass). On stderr: the refusal ERROR,
    # and NO summary JSON dumped before it (the ordering ZD flagged) -- only the
    # [parse-skip] line, which is legitimate context for why the frame is empty.
    assert out.out.strip() == ""
    assert "ERROR:" in out.err and "EMPTY frame" in out.err
    assert "denominator_scoreable" not in out.err and "high_band_rate" not in out.err
    # And NO zero-row artifact is left on disk under a real-looking name -- the
    # trap a later glob / hash-pin would pick up.
    assert list(tmp_path.glob("*_summary.json")) == []
    assert list(tmp_path.glob("f2_random_oa_*.jsonl")) == []


def test_reband_from_cache_refuses_empty_frame_without_writing(tmp_path):
    # The guard lives in reband_from_cache (refuse_empty default on), so the
    # `python -c "...reband_from_cache..."` path is protected too, not just the CLI.
    from cre.f1.f2_run_v3 import reband_from_cache, EmptyFrameError
    xml = tmp_path / "xml"; xml.mkdir()
    (xml / "PMC1.xml").write_text("")                 # 0-byte stub -> nothing parses
    cache = tmp_path / "c.jsonl"
    cache.write_text('{"pmid": "111", "rec": {"resolved": true, "title": "t", '
                     '"pmid": "111"}}\n')
    with pytest.raises(EmptyFrameError):
        reband_from_cache(str(xml), str(cache), out_dir=str(tmp_path),
                          version="v3_1", src_pmcids=["PMC1"])
    assert list(tmp_path.glob("*_summary.json")) == []   # nothing written


def test_fresh_draw_runner_refuses_empty_frame_without_writing(tmp_path):
    # The higher-stakes symmetry (ZD): the fresh-draw runner emits the single-use
    # held-out artifact (§16.3), so an empty draw must refuse to write too -- the
    # guard now lives in _write_run, which both entry points funnel through.
    from cre.f1.f2_run_v3 import run_f2_seed7_v3, EmptyFrameError
    with pytest.raises(EmptyFrameError):
        run_f2_seed7_v3([], out_dir=str(tmp_path), version="v3", seed=11)
    assert list(tmp_path.glob("*_summary.json")) == []       # nothing written
    assert list(tmp_path.glob("f2_random_oa_*.jsonl")) == []
    # explicit opt-out still writes (deliberate empty frame)
    summary = run_f2_seed7_v3([], out_dir=str(tmp_path), version="v3", seed=11,
                              refuse_empty=False)
    assert summary["n_records"] == 0
    assert (tmp_path / "f2_random_oa_seed11_v3.jsonl").exists()
