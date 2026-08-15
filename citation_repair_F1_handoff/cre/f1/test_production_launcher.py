"""The locked launcher refuses what caller strings cannot verify (CODEX 5).

Every test here names something the launcher checks ITSELF, or a receipt it
forces the adapter to produce -- never a value it is simply told.
"""
from __future__ import annotations

import pytest

from . import production_launcher as pl
from .production_launcher import LaunchRefused


class Receipt:
    """The minimum an adapter must record: what it actually sent, per call."""

    def __init__(self, calls=None):
        self.calls = list(calls or [])


OK_CALLS = [{"model": "claude-sonnet-4-5", "temperature": 0},
            {"model": "claude-sonnet-4-5", "temperature": 0}]


def base(**over):
    kw = dict(repo_dir="/nonexistent", pkg_dir="/nonexistent",
              xml_dir="/nonexistent", out_dir="/nonexistent/out",
              preband_disposition="/nonexistent/d.jsonl",
              corpus_manifest_path="/nonexistent/m.json",
              model="claude-sonnet-4-5",
              authorized_models=["claude-sonnet-4-5"],
              adapter_receipt=Receipt(OK_CALLS),
              judge_model="gpt-4o-mini")
    kw.update(over)
    return kw


# ----------------------------------------------- model authorization (DEC-065)

def test_no_authorized_models_refuses():
    with pytest.raises(LaunchRefused, match="named in a DECISION"):
        pl.launch(**base(authorized_models=[]))


def test_an_unauthorized_model_refuses():
    with pytest.raises(LaunchRefused, match="not in the DECISION-backed allowlist"):
        pl.launch(**base(model="claude-opus-4-7"))


# ------------------------------------------------- the temperature pin (046B)

@pytest.mark.parametrize("temp", [None, 1, 0.7, ""])
def test_a_nonzero_or_absent_temperature_refuses(temp):
    with pytest.raises(LaunchRefused, match="temperature must be 0"):
        pl.launch(**base(temperature=temp))


# ------------------------------------------- different-family judge (DEC-065)

def test_a_same_family_judge_refuses_without_an_amendment():
    with pytest.raises(LaunchRefused, match="SAME family"):
        pl.launch(**base(judge_model="claude-opus-4-7"))


def test_no_judge_and_no_amendment_refuses():
    with pytest.raises(LaunchRefused, match="different-family judge or a formal"):
        pl.launch(**base(judge_model=""))


def test_a_same_family_judge_is_allowed_only_with_a_dated_amendment():
    """It must still fail LATER (on the tree), not at the judge gate."""
    with pytest.raises(LaunchRefused) as exc:
        pl.launch(**base(judge_model="claude-opus-4-7",
                         preregistration_amendment="amended 2026-08-14 by ZD"))
    assert "SAME family" not in str(exc.value)


def test_family_detection():
    assert pl._model_family("claude-sonnet-4-5") == "claude"
    assert pl._model_family("claude-opus-4-7") == "claude"
    assert pl._model_family("gpt-4o-mini") == "gpt"
    assert pl._model_family("gemini-2.0-pro") == "gemini"
    assert pl._model_family("claude-sonnet-4-5") != pl._model_family("gpt-4o-mini")


# --------------------------------------------- clean HEAD and runtime bytes

def test_a_dirty_tree_refuses(tmp_path, monkeypatch):
    monkeypatch.setattr(pl, "_git", lambda repo, *a: (
        " M cre/f1/judgment_run.py" if a[0] == "status" else "a" * 40))
    with pytest.raises(LaunchRefused, match="DIRTY"):
        pl.verify_tree(str(tmp_path), str(tmp_path))


def test_untracked_files_do_not_count_as_dirty(tmp_path, monkeypatch):
    monkeypatch.setattr(pl, "_git", lambda repo, *a: (
        "?? scratch.md" if a[0] == "status" else "b" * 40))
    out = pl.verify_tree(str(tmp_path), str(tmp_path))
    assert out["code_commit"] == "b" * 40


def test_runtime_bytes_differing_from_the_commit_refuse(tmp_path, monkeypatch):
    """The right commit and the WRONG bytes: a file edited after checkout."""
    pkg = tmp_path / "f1"
    pkg.mkdir()
    (pkg / "judgment_run.py").write_text("edited after checkout",
                                         encoding="utf-8")
    monkeypatch.setattr(pl, "_git", lambda repo, *a: (
        "" if a[0] == "status" else "c" * 40))

    class R:
        stdout = b"the committed bytes"
    monkeypatch.setattr(pl.subprocess, "run", lambda *a, **k: R())
    with pytest.raises(LaunchRefused, match="differ from the recorded commit"):
        pl.verify_tree(str(tmp_path), str(pkg))


def test_the_head_is_read_not_supplied(tmp_path, monkeypatch):
    """The caller never gets to assert the commit."""
    monkeypatch.setattr(pl, "_git", lambda repo, *a: (
        "" if a[0] == "status" else "d" * 40))
    assert pl.verify_tree(str(tmp_path), str(tmp_path))["code_commit"] == "d" * 40


# ------------------------------------------------------- the adapter receipt

def test_an_empty_receipt_refuses():
    with pytest.raises(LaunchRefused, match="ZERO calls"):
        pl.verify_receipt(Receipt([]), model="m", temperature=0)


def test_a_receipt_naming_another_model_refuses():
    r = Receipt([{"model": "claude-sonnet-4-5", "temperature": 0},
                 {"model": "gpt-4o", "temperature": 0}])
    with pytest.raises(LaunchRefused, match="unauthorized model"):
        pl.verify_receipt(r, model="claude-sonnet-4-5", temperature=0)


def test_a_receipt_showing_a_different_temperature_refuses():
    r = Receipt([{"model": "m", "temperature": 0},
                 {"model": "m", "temperature": 1}])
    with pytest.raises(LaunchRefused, match="temperature"):
        pl.verify_receipt(r, model="m", temperature=0)


def test_a_consistent_receipt_passes():
    out = pl.verify_receipt(Receipt(OK_CALLS), model="claude-sonnet-4-5",
                            temperature=0)
    assert out["calls"] == 2
    assert out["temperature"] == 0


def test_a_missing_receipt_object_refuses():
    with pytest.raises(LaunchRefused, match="ZERO calls"):
        pl.verify_receipt(object(), model="m", temperature=0)
