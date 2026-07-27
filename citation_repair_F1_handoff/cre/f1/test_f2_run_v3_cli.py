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
        return {"version": kw["version"], "n_records": 0, "audit": []}
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
