"""The offline-reband CLI (spec §20) is runnable and forwards args correctly."""
from __future__ import annotations

import pytest

from cre.f1 import f2_run_v3


def test_cli_forwards_args_and_derives_src_pmcids(tmp_path, monkeypatch):
    xml = tmp_path / "xml"; xml.mkdir()
    (xml / "PMC1.xml").write_text("<a/>")
    (xml / "PMC2.nxml").write_text("<a/>")
    cache = tmp_path / "resolved.jsonl"; cache.write_text("")
    captured = {}

    def fake_reband(**kw):
        captured.update(kw)
        return {"version": kw["version"], "n_records": 5, "audit": []}
    monkeypatch.setattr(f2_run_v3, "reband_from_cache", fake_reband)

    rc = f2_run_v3._cli(["--reband-from-cache", "--resolved-cache", str(cache),
                         "--xml-dir", str(xml), "--out-dir", str(tmp_path),
                         "--seed", "37", "--version", "candidate_02"])
    assert rc == 0
    assert captured["seed"] == 37 and captured["version"] == "candidate_02"
    assert captured["resolved_cache_path"] == str(cache)
    # source-PMCID frame derived from the xml-dir stems
    assert sorted(captured["src_pmcids"]) == ["PMC1", "PMC2"]


def test_cli_requires_its_args():
    with pytest.raises(SystemExit):        # argparse exits non-zero on missing args
        f2_run_v3._cli([])


# --- refusal on an empty/absent corpus (the exit-0-reads-as-pass bug) ---------
def test_cli_refuses_empty_xml_dir(tmp_path):
    # No XML files at all -> argparse error (exit 2), before any run.
    empty = tmp_path / "xml"; empty.mkdir()
    cache = tmp_path / "c.jsonl"; cache.write_text("")
    with pytest.raises(SystemExit) as e:
        f2_run_v3._cli(["--reband-from-cache", "--resolved-cache", str(cache),
                        "--xml-dir", str(empty)])
    assert e.value.code != 0


def test_cli_refuses_nonexistent_xml_dir(tmp_path):
    cache = tmp_path / "c.jsonl"; cache.write_text("")
    with pytest.raises(SystemExit) as e:
        f2_run_v3._cli(["--reband-from-cache", "--resolved-cache", str(cache),
                        "--xml-dir", str(tmp_path / "does_not_exist")])
    assert e.value.code != 0


def test_cli_refuses_zero_byte_stub_corpus(tmp_path, capsys):
    # ZD's case: 0-byte XML stubs + a real cache row -> the frame comes back empty.
    # Must exit NON-ZERO and must NOT print the all-zeros summary to stdout.
    xml = tmp_path / "xml"; xml.mkdir()
    (xml / "PMC1.xml").write_text("")                 # 0-byte stub
    cache = tmp_path / "c.jsonl"
    cache.write_text('{"pmid": "111", "rec": {"resolved": true, "title": "t", '
                     '"pmid": "111"}}\n')
    rc = f2_run_v3._cli(["--reband-from-cache", "--resolved-cache", str(cache),
                         "--xml-dir", str(xml), "--out-dir", str(tmp_path)])
    assert rc == 3
    out = capsys.readouterr()
    # The misleading all-zeros summary must NOT reach stdout (it would read as a
    # pass); the diagnostic + summary go to stderr.
    assert "n_records" not in out.out and "denominator_scoreable" not in out.out
    assert "EMPTY frame" in out.err
