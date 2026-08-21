"""The locked launcher refuses what caller strings cannot verify (CODEX 5).

Every test here names something the launcher checks ITSELF, or a receipt it
forces the adapter to produce -- never a value it is simply told.
"""
from __future__ import annotations

import pytest

from . import production_launcher as pl
from .f5_seams import build_f5_seams, make_f5_evidence_builder
from .f5_supersession import F5Policy
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
              judge_model="gpt-4o-mini",
              temperature=0)   # base model is sonnet, which SUPPORTS the param
    kw.update(over)
    return kw


def production_f5_bundle(model="claude-sonnet-4-5"):
    def fetch_meta(work_id):
        return {
            "id": str(work_id), "pmid": str(work_id),
            "pub_date": "2010-01-01", "pub_date_latest": "2010-01-01",
            "abstract": "abstract",
            "publication_types": ["Randomized Controlled Trial"],
        }

    seams = build_f5_seams(
        fetch_meta=fetch_meta, fetch_abstract=lambda _wid: "abstract",
        search_candidates=lambda *_a, **_k: [], complete=lambda _p: "{}",
        verifier_complete=lambda _p: "{}", judgment_model_id=model,
        verifier_model_id=model)
    builder = make_f5_evidence_builder(
        fetch_meta, as_of_date="2024-01-01")
    policy = F5Policy(
        mode="deployment", generator_model_id=model,
        verifier_model_id=model)
    return seams, builder, policy


# ----------------------------------------------- model authorization (DEC-065)

def test_no_authorized_models_refuses():
    with pytest.raises(LaunchRefused, match="named in a DECISION"):
        pl.launch(**base(authorized_models=[]))


def test_an_unauthorized_model_refuses():
    with pytest.raises(LaunchRefused, match="not in the DECISION-backed allowlist"):
        pl.launch(**base(model="claude-opus-4-7"))


def test_partial_production_f5_configuration_refuses_before_tree_check():
    _seams, builder, policy = production_f5_bundle()
    with pytest.raises(LaunchRefused, match="requires seams, evidence builder"):
        pl.launch(**base(
            f5_seams=None, f5_evidence_builder=builder, f5_policy=policy))


def test_complete_production_f5_configuration_passes_its_preflight():
    seams, builder, policy = production_f5_bundle()
    with pytest.raises(LaunchRefused) as exc:
        pl.launch(**base(
            f5_seams=seams, f5_evidence_builder=builder, f5_policy=policy))
    assert "production F5 configuration invalid" not in str(exc.value)


# ------------------------------------------------- the temperature pin (046B)

@pytest.mark.parametrize("temp", [None, 1, 0.7, ""])
def test_a_nonzero_or_absent_temperature_refuses(temp):
    with pytest.raises(LaunchRefused, match="temperature must be 0"):
        pl.launch(**base(temperature=temp))


# ------------------------------------------- different-family judge (DEC-065)

def test_a_same_family_judge_refuses_without_an_amendment_or_ruling():
    with pytest.raises(LaunchRefused, match="SAME family"):
        pl.launch(**base(judge_model="claude-opus-4-7"))


def test_no_judge_and_no_amendment_refuses():
    with pytest.raises(LaunchRefused, match="no preregistration_scope_ruling"):
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

def _git_stub(toplevel, head, *, status=""):
    """A _git double that also answers --show-toplevel (Change 6)."""
    def fake(repo, *a):
        if a[0] == "status":
            return status
        if len(a) > 1 and a[1] == "--show-toplevel":
            return str(toplevel)
        return head
    return fake


def test_a_dirty_tree_refuses(tmp_path, monkeypatch):
    monkeypatch.setattr(pl, "_git", _git_stub(tmp_path, "a" * 40,
                                              status=" M cre/f1/judgment_run.py"))
    with pytest.raises(LaunchRefused, match="DIRTY"):
        pl.verify_tree(str(tmp_path), str(tmp_path))


def test_untracked_files_do_not_count_as_dirty(tmp_path, monkeypatch):
    monkeypatch.setattr(pl, "_git", _git_stub(tmp_path, "b" * 40,
                                              status="?? scratch.md"))
    out = pl.verify_tree(str(tmp_path), str(tmp_path))
    assert out["code_commit"] == "b" * 40


def test_runtime_bytes_differing_from_the_commit_refuse(tmp_path, monkeypatch):
    """The right commit and the WRONG bytes: a file edited after checkout."""
    pkg = tmp_path / "f1"
    pkg.mkdir()
    (pkg / "judgment_run.py").write_text("edited after checkout",
                                         encoding="utf-8")
    monkeypatch.setattr(pl, "_git", _git_stub(tmp_path, "c" * 40))

    class R:
        stdout = b"the committed bytes"
    monkeypatch.setattr(pl.subprocess, "run", lambda *a, **k: R())
    with pytest.raises(LaunchRefused, match="differ from the recorded commit"):
        pl.verify_tree(str(tmp_path), str(pkg))


def test_the_head_is_read_not_supplied(tmp_path, monkeypatch):
    """The caller never gets to assert the commit."""
    monkeypatch.setattr(pl, "_git", _git_stub(tmp_path, "d" * 40))
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


# =====================================================================
# The DECISION-recorded scope ruling: the third answer on the judge branch
# =====================================================================
#
# DEC-069's actual payload. It supplies NEITHER a different-family judge NOR an
# amendment, on purpose -- not touching the commit-hash-cited file is the whole
# point of a scope ruling. A launcher offering only those two escape hatches
# refuses every call, which is the defect these tests pin closed.
DEC_069 = {
    "decision_id": "DEC-069",
    "date": "2026-08-15",
    "section": "PREREGISTRATION.md §6",
    "ruling": (
        "§6 is titled 'Generation-Mode evaluation' and its different-family-judge "
        "commitment governs the judging of GENERATED candidates. The first paper "
        "has no generation mode (DEC-046A defers repair and generation), so §6 "
        "has nothing to bind and the F3-F7 detection band is outside its scope."),
    "residual_risk": (
        "A reviewer may read §6 as covering any LLM-as-judge step. Inside the "
        "band, claude-opus-5 judges coverage of claims claude-opus-5 itself "
        "extracted. That is a real intra-pipeline conflict of interest and is "
        "NOT the one §6 addresses; the answer is a human-adjudicated sample, not "
        "a second model family."),
}


def test_a_scope_ruling_is_accepted_with_no_judge_and_no_amendment():
    """THE DEFECT, pinned. DEC-069 closed model governance while giving neither
    escape hatch, so launch() refused unconditionally and the corpus run could
    not start."""
    with pytest.raises(LaunchRefused) as exc:
        pl.launch(**base(judge_model="", model="claude-opus-5",
                         authorized_models=["claude-opus-5"],
                         temperature=None,          # DEC-070: not sent
                         preregistration_scope_ruling=DEC_069))
    msg = str(exc.value)
    assert "scope_ruling" not in msg and "SAME family" not in msg
    assert "different-family judge" not in msg      # got past the judge branch


def test_the_scope_ruling_path_is_recorded():
    g = pl.verify_judge_governance(
        model="claude-opus-5", judge_model="",
        preregistration_amendment="", preregistration_scope_ruling=DEC_069)
    assert g["paths_satisfied"] == ["decision_scope_ruling"]
    assert g["scope_ruling"]["decision_id"] == "DEC-069"
    assert g["scope_ruling"]["section"] == "PREREGISTRATION.md §6"
    assert g["different_family_judge"] is False
    assert g["same_family_judge_active"] is False
    assert g["preregistration_amended"] is False
    # It must never read as compliance.
    assert "SCOPE RULING, NOT COMPLIANCE" in g["compliance_note"]


def test_a_different_family_judge_still_reads_as_compliance():
    g = pl.verify_judge_governance(
        model="claude-opus-5", judge_model="gpt-4o-mini",
        preregistration_amendment="", preregistration_scope_ruling=None)
    assert g["paths_satisfied"] == ["different_family_judge"]
    assert g["different_family_judge"] is True
    assert "different-family judge arrangement was used" in g["compliance_note"]


# ------------------------------------- a ruling must be a DECISION, not prose

@pytest.mark.parametrize("bad,pat", [
    ("§6 doesn't apply", "must be a dict"),
    ({}, "missing"),
    ({**DEC_069, "decision_id": "sometime"}, "not a DECISION identifier"),
    ({**DEC_069, "date": "yesterday"}, "not ISO"),
    ({**DEC_069, "ruling": "n/a"}, "too short to be a ruling"),
    ({**DEC_069, "section": "  "}, "missing"),
])
def test_a_malformed_scope_ruling_is_refused(bad, pat):
    with pytest.raises(LaunchRefused, match=pat):
        pl.verify_judge_governance(
            model="claude-opus-5", judge_model="",
            preregistration_amendment="", preregistration_scope_ruling=bad)


# ---------------- a same-family judge may never run SILENTLY on a scope ruling

def test_a_wired_same_family_judge_on_a_ruling_needs_a_written_residual_risk():
    """The one combination that could be read as compliance when it is not. It
    stays permitted -- the ruling governs -- but never silently."""
    quiet = {k: v for k, v in DEC_069.items() if k != "residual_risk"}
    with pytest.raises(LaunchRefused, match="must record its residual_risk"):
        pl.verify_judge_governance(
            model="claude-opus-5", judge_model="claude-sonnet-4-5",
            preregistration_amendment="", preregistration_scope_ruling=quiet)


def test_a_wired_same_family_judge_on_a_ruling_is_allowed_and_flagged_loudly():
    g = pl.verify_judge_governance(
        model="claude-opus-5", judge_model="claude-sonnet-4-5",
        preregistration_amendment="", preregistration_scope_ruling=DEC_069)
    assert g["same_family_judge_active"] is True
    assert g["scope_ruling"]["residual_risk"]
    assert "SCOPE RULING, NOT COMPLIANCE" in g["compliance_note"]


def test_no_judge_wired_needs_no_residual_risk():
    """ZD's actual configuration: one model, no separate judge seam at all."""
    quiet = {k: v for k, v in DEC_069.items() if k != "residual_risk"}
    g = pl.verify_judge_governance(
        model="claude-opus-5", judge_model="",
        preregistration_amendment="", preregistration_scope_ruling=quiet)
    assert g["paths_satisfied"] == ["decision_scope_ruling"]


def test_an_amendment_and_a_ruling_both_record():
    g = pl.verify_judge_governance(
        model="claude-opus-5", judge_model="claude-sonnet-4-5",
        preregistration_amendment="amended 2026-08-15 by ZD",
        preregistration_scope_ruling=DEC_069)
    assert g["paths_satisfied"] == ["dated_amendment", "decision_scope_ruling"]


# =====================================================================
# DEC-070: temperature is provider-deprecated from Claude Opus 4.7 onward
# =====================================================================
# Live pre-flight: HTTP 400 invalid_request_error "temperature is deprecated for
# this model." from claude-opus-5 (req_011Ce3qbp97tLCSVL2rRZtYP). The pin is
# retired ONLY for models that reject the parameter. On those it is not sent and
# is recorded as "unsupported" -- never as 0, which would be a false record of
# what was transmitted.

UNSUP = pl.TEMPERATURE_UNSUPPORTED


def test_only_first_party_measurements_are_in_the_rejecting_table():
    """DEC-070 as amended. Only claude-opus-5 was measured by THIS project."""
    assert pl.TEMPERATURE_REJECTING_MODELS == frozenset({"claude-opus-5"})
    assert pl.temperature_support("claude-opus-5") == UNSUP
    # sonnet ACCEPTS it -- which is why every prior measurement missed this.
    assert pl.temperature_support("claude-sonnet-4-5") == "supported"


@pytest.mark.parametrize("model", ["claude-opus-4-7", "claude-opus-4-8"])
def test_third_party_reports_and_inference_do_not_enter_the_table(model):
    """4-7 is third-party-reported only; 4-8 was inferred from it and the
    assertion is WITHDRAWN. Both stay out, so both are pinned to 0.

    This is the asymmetry deliberately chosen: wrongly listing a model as
    REJECTING silently drops the pin and lets the provider default decide
    sampling; wrongly listing one as SUPPORTING earns a loud 400 before any
    compute is spent."""
    assert model not in pl.TEMPERATURE_REJECTING_MODELS
    assert pl.temperature_support(model) == "supported"
    with pytest.raises(LaunchRefused, match="temperature must be 0"):
        pl.verify_temperature_governance(model=model, temperature=None)
    g = pl.verify_temperature_governance(model=model, temperature=0)
    assert g["recorded_value"] == 0
    assert g["support_evidence"] == pl.EVIDENCE_ASSUMED_ACCEPTS


def test_evidence_tiers_are_three_not_two():
    """'Measured to accept' and 'assumed because nobody looked' behave the same
    and must not READ the same."""
    assert pl.temperature_evidence("claude-opus-5") == pl.EVIDENCE_MEASURED_REJECTS
    assert pl.temperature_evidence("claude-sonnet-4-5") == pl.EVIDENCE_MEASURED_ACCEPTS
    assert pl.temperature_evidence("claude-opus-4-7") == pl.EVIDENCE_ASSUMED_ACCEPTS
    assert pl.temperature_evidence("some-new-model-9") == pl.EVIDENCE_ASSUMED_ACCEPTS


def test_the_evidence_tier_lands_in_the_receipt():
    g = pl.verify_temperature_governance(model="claude-opus-5", temperature=None)
    assert g["support_evidence"] == pl.EVIDENCE_MEASURED_REJECTS
    g = pl.verify_temperature_governance(model="claude-sonnet-4-5", temperature=0)
    assert g["support_evidence"] == pl.EVIDENCE_MEASURED_ACCEPTS


def test_a_rejecting_model_records_unsupported_and_sends_nothing():
    """THE DEFECT, pinned. launch() required temperature == 0 by identity, so
    under DEC-070 it refused every call and the run could not start."""
    g = pl.verify_temperature_governance(model="claude-opus-5", temperature=None)
    assert g["recorded_value"] == UNSUP
    assert g["sent_to_provider"] is False
    assert g["path"] == "not_sent_unsupported"
    assert g["governing_decision"] == "DEC-070"


def test_the_sentinel_may_also_be_passed_explicitly():
    g = pl.verify_temperature_governance(model="claude-opus-5", temperature=UNSUP)
    assert g["recorded_value"] == UNSUP


def test_a_number_on_a_rejecting_model_is_refused():
    """Sending it would 400; recording 0 for a call that never carried it would
    be a false provenance record."""
    for bad in (0, 1, 0.0):
        with pytest.raises(LaunchRefused, match="REJECTS the temperature parameter"):
            pl.verify_temperature_governance(model="claude-opus-5",
                                             temperature=bad)


# --------------------------- the relaxation must NOT widen to "anything goes"

def test_a_supporting_model_is_still_pinned_to_zero():
    g = pl.verify_temperature_governance(model="claude-sonnet-4-5", temperature=0)
    assert g["recorded_value"] == 0
    assert g["sent_to_provider"] is True
    assert g["governing_decision"] == "DEC-046B"


@pytest.mark.parametrize("temp", [None, 1, 0.7, 0.0001])
def test_a_supporting_model_still_refuses_anything_but_zero(temp):
    if temp == 0:
        pytest.skip("zero is the pin")
    with pytest.raises(LaunchRefused, match="temperature must be 0"):
        pl.verify_temperature_governance(model="claude-sonnet-4-5",
                                         temperature=temp)


def test_a_supporting_model_cannot_opt_into_the_unsupported_path():
    """The escape hatch is chosen by the MODEL, never by the caller."""
    with pytest.raises(LaunchRefused, match="SUPPORTS the temperature parameter"):
        pl.verify_temperature_governance(model="claude-sonnet-4-5",
                                         temperature=UNSUP)


def test_an_unknown_model_is_treated_as_supporting():
    """The two errors are not symmetric. Calling a rejecting model 'supporting'
    earns a loud 400 before compute is spent; the reverse silently drops the pin
    and lets the provider default decide sampling."""
    assert pl.temperature_support("some-new-model-9") == "supported"
    with pytest.raises(LaunchRefused, match="temperature must be 0"):
        pl.verify_temperature_governance(model="some-new-model-9",
                                         temperature=None)


# ------------------------------- unsupported means ABSENT, not unchecked

def test_a_receipt_carrying_temperature_on_a_rejecting_model_is_refused():
    r = Receipt([{"model": "claude-opus-5"},
                 {"model": "claude-opus-5", "temperature": 0}])
    with pytest.raises(LaunchRefused, match="call\\(s\\) CARRYING temperature"):
        pl.verify_receipt(r, model="claude-opus-5", temperature=UNSUP)


def test_a_receipt_with_the_field_absent_passes_on_a_rejecting_model():
    r = Receipt([{"model": "claude-opus-5"}, {"model": "claude-opus-5"}])
    out = pl.verify_receipt(r, model="claude-opus-5", temperature=UNSUP)
    assert out["temperature"] == UNSUP
    assert out["calls"] == 2


def test_the_receipt_still_checks_the_model_on_the_unsupported_path():
    r = Receipt([{"model": "claude-opus-5"}, {"model": "gpt-4o"}])
    with pytest.raises(LaunchRefused, match="unauthorized model"):
        pl.verify_receipt(r, model="claude-opus-5", temperature=UNSUP)


def test_an_empty_receipt_still_refuses_on_the_unsupported_path():
    with pytest.raises(LaunchRefused, match="ZERO calls"):
        pl.verify_receipt(Receipt([]), model="claude-opus-5", temperature=UNSUP)


# =====================================================================
# DEC-071: assistant_prefill governance
# =====================================================================
PUNSUP = pl.PREFILL_UNSUPPORTED


def test_only_first_party_measurements_are_in_the_prefill_table():
    assert pl.PREFILL_REJECTING_MODELS == frozenset({"claude-opus-5"})
    assert pl.prefill_support("claude-opus-5") == PUNSUP
    assert pl.prefill_support("claude-sonnet-4-5") == "supported"


def test_a_prefill_on_a_rejecting_model_is_refused():
    """The false provenance record: judgment_run writes assistant_prefill
    verbatim into manifest["adapter"] and never transmits it, so on opus-5 a
    non-empty string claims a prefill that could not have been sent."""
    with pytest.raises(LaunchRefused, match="REJECTS assistant prefill"):
        pl.verify_prefill_governance(model="claude-opus-5",
                                     assistant_prefill="{")


@pytest.mark.parametrize("value", ["", PUNSUP])
def test_a_rejecting_model_records_unsupported(value):
    g = pl.verify_prefill_governance(model="claude-opus-5",
                                     assistant_prefill=value)
    assert g["recorded_value"] == PUNSUP
    assert g["sent_to_provider"] is False
    assert g["path"] == "not_sent_unsupported"
    assert g["governing_decision"] == "DEC-071"


def test_a_supporting_model_cannot_opt_into_the_prefill_escape_hatch():
    with pytest.raises(LaunchRefused, match="SUPPORTS assistant prefill"):
        pl.verify_prefill_governance(model="claude-sonnet-4-5",
                                     assistant_prefill=PUNSUP)


def test_a_supporting_model_records_the_prefill_verbatim():
    g = pl.verify_prefill_governance(model="claude-sonnet-4-5",
                                     assistant_prefill="{")
    assert g["recorded_value"] == "{"
    assert g["sent_to_provider"] is True
    assert g["path"] == "sent"


def test_an_omitted_prefill_is_not_recorded_at_all():
    """Third state: distinguishable from both the string and 'unsupported'."""
    g = pl.verify_prefill_governance(model="claude-opus-5",
                                     assistant_prefill=None)
    assert g["recorded_value"] is None
    assert g["path"] == "not_recorded"


def test_a_non_string_prefill_is_refused():
    with pytest.raises(LaunchRefused, match="must be a string"):
        pl.verify_prefill_governance(model="claude-sonnet-4-5",
                                     assistant_prefill=123)


# =====================================================================
# Change 4: the receipt is refused BEFORE the run
# =====================================================================

def test_a_receipt_without_calls_is_refused_up_front():
    with pytest.raises(LaunchRefused, match="exposes no .calls"):
        pl.assert_receipt_shape(object())


def test_a_missing_receipt_is_refused_up_front():
    with pytest.raises(LaunchRefused, match="no adapter_receipt"):
        pl.assert_receipt_shape(None)


def test_a_non_list_calls_is_refused_up_front():
    class Bad:
        calls = {"a": 1}
    with pytest.raises(LaunchRefused, match="must be a list"):
        pl.assert_receipt_shape(Bad())


def test_an_empty_but_well_shaped_receipt_passes_the_early_gate():
    """It cannot check call CONTENTS -- there are none yet -- and must not
    pretend to. verify_receipt still catches the empty case after the run."""
    pl.assert_receipt_shape(Receipt([]))


def test_launch_refuses_a_malformed_receipt_before_the_run(monkeypatch):
    """Ordering: the refusal must precede run_natural_judgment, not follow it."""
    called = {"ran": False}
    monkeypatch.setattr(pl, "run_natural_judgment",
                        lambda *a, **k: called.__setitem__("ran", True))
    with pytest.raises(LaunchRefused, match="exposes no .calls"):
        pl.launch(**base(model="claude-opus-5",
                         authorized_models=["claude-opus-5"],
                         temperature=None, adapter_receipt=object()))
    assert called["ran"] is False


# =====================================================================
# Change 6: the repo_dir footgun
# =====================================================================

def test_a_non_toplevel_repo_dir_names_its_own_cause(tmp_path, monkeypatch):
    """Pointed at a subdirectory, every rel is unresolvable and all thirteen
    modules read as mismatched -- an integrity failure that is really a
    configuration error."""
    def fake_git(repo, *a):
        if a[0] == "status":
            return ""
        if a[0] == "rev-parse" and a[1] == "HEAD":
            return "e" * 40
        if a[0] == "rev-parse" and a[1] == "--show-toplevel":
            return str(tmp_path)          # toplevel differs from repo_dir
        return ""
    sub = tmp_path / "citation_repair_F1_handoff"
    sub.mkdir()
    monkeypatch.setattr(pl, "_git", fake_git)
    with pytest.raises(LaunchRefused, match="is not the git toplevel"):
        pl.verify_tree(str(sub), str(sub))


def test_unresolvable_paths_are_not_reported_as_an_integrity_failure(
        tmp_path, monkeypatch):
    pkg = tmp_path / "f1"
    pkg.mkdir()
    (pkg / "judgment_run.py").write_text("x", encoding="utf-8")

    def fake_git(repo, *a):
        if a[0] == "status":
            return ""
        if a[1] == "--show-toplevel":
            return str(tmp_path)
        return "f" * 40
    monkeypatch.setattr(pl, "_git", fake_git)

    class Empty:
        stdout = b""                       # git show resolves to nothing
    monkeypatch.setattr(pl.subprocess, "run", lambda *a, **k: Empty())
    with pytest.raises(LaunchRefused, match="configuration error, NOT an integrity"):
        pl.verify_tree(str(tmp_path), str(pkg))


def test_a_genuine_edit_still_refuses(tmp_path, monkeypatch):
    """The integrity check itself is unchanged."""
    pkg = tmp_path / "f1"
    pkg.mkdir()
    (pkg / "judgment_run.py").write_text("edited after checkout", encoding="utf-8")

    def fake_git(repo, *a):
        if a[0] == "status":
            return ""
        if a[1] == "--show-toplevel":
            return str(tmp_path)
        return "a" * 40
    monkeypatch.setattr(pl, "_git", fake_git)

    class R:
        stdout = b"the committed bytes"
    monkeypatch.setattr(pl.subprocess, "run", lambda *a, **k: R())
    with pytest.raises(LaunchRefused, match="differ from the recorded commit"):
        pl.verify_tree(str(tmp_path), str(pkg))
