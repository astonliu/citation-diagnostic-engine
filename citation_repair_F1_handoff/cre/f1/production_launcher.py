"""The locked production launcher: the only sanctioned way to produce a reportable run.

WHY THIS EXISTS. ``run_natural_judgment`` records what it is TOLD -- the model
string, the temperature, the code commit. It makes no model call itself (every
one goes through an injected callable), so it cannot observe what actually ran,
and a caller string is an assertion, not a verification. Everything in this
module is something the launcher can check for itself, or a receipt it forces
the adapter to produce.

WHAT IT VERIFIES, none of it taken from the caller:

* **HEAD and cleanliness.** The commit is read from ``.git`` here, not passed in,
  and the working tree must be clean. A dirty tree means the bytes that ran are
  not the bytes any commit names.
* **Runtime bytes.** Every governing module is hashed on disk and compared to the
  blob the recorded commit actually holds. A file edited after checkout has the
  right commit and the wrong bytes.
* **Authorized model.** The model must appear in a DECISION-backed allowlist
  passed as ``authorized_models``, and the run's model must be one of them.
  DEC-065 requires the production model to be named in a decision; this refuses
  to run until one is.
* **Temperature governance.** Two paths, chosen by the MODEL and never by the
  caller's preference. A model that supports the parameter is still pinned to
  ``0`` (DEC-046B), checked with ``is not None`` and ``== 0``, never truthiness.
  A model that REJECTS it -- Claude Opus 4.7 onward, which 400s with
  ``"temperature is deprecated for this model."`` -- does not send it and
  records the string ``"unsupported"`` (DEC-070), never ``0``. Three states stay
  distinguishable in the manifest: ``0``, ``"unsupported"``, and the key being
  absent. The relaxation is bounded by a measured model table, so it can never
  widen into "any temperature is fine".
* **Adapter receipt.** The adapter must record, per call, the model and
  temperature it actually sent. After the run the launcher verifies at least one
  call happened and that EVERY call used the authorized model at temperature 0.
  This is the strongest available check short of owning the transport: it can
  still be forged by a lying adapter, and that is stated rather than hidden.
* **Judge governance.** THREE accepted answers, no silent fourth: a
  different-family judge; a dated preregistration amendment; or a
  DECISION-recorded scope ruling that the section does not bind this work.
  DEC-069 is the live example -- PREREGISTRATION §6 governs judging of
  GENERATED candidates and the first paper has no generation mode, so §6 has
  nothing to bind. A scope ruling deliberately supplies neither a
  different-family judge nor an amendment (not touching the commit-hash-cited
  file is its whole point), so a launcher offering only those two escape hatches
  refuses every call. The path taken is stamped into the receipt, and a wired
  same-family judge riding on a scope ruling must additionally carry the
  ruling's ``residual_risk`` in writing.

WHAT IT CANNOT DO. It cannot prove the injected callable contacted the provider
it claims, nor that the receipt is honest. Owning the transport is the only
thing that would, and that is outside this repo. Named here so the receipt is
not read as proof.
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess

from . import preband_contract as pc
from .judgment_run import run_natural_judgment

#: Modules whose bytes govern a published number. Verified against the commit.
GOVERNING_MODULES = (
    "judgment_run.py", "judgment_band.py", "judgment_engine.py",
    "band_prompts.py", "parser.py", "schema.py", "f3_provenance.py",
    "f4_strength.py", "f7_entity.py", "preband_contract.py",
    "parser_versions.py", "coverage_prompts_v3.py", "coverage_aggregate.py",
)


class LaunchRefused(RuntimeError):
    """The launch preconditions are not met. No run is started."""


def _git(repo_dir: str, *args: str) -> str:
    out = subprocess.run(("git", "-C", repo_dir) + args, capture_output=True,
                         text=True, check=False)
    if out.returncode != 0:
        raise LaunchRefused(
            f"git {' '.join(args)} failed in {repo_dir}: {out.stderr.strip()}")
    return out.stdout.strip()


def _model_family(model_id: str) -> str:
    """Coarse provider/family key. Deliberately blunt: it only has to answer
    'same family?', and over-merging is the SAFE direction -- it can refuse a
    launch that was fine, never allow one that was not."""
    m = (model_id or "").strip().lower()
    for family in ("claude", "gpt", "o1", "o3", "gemini", "llama", "mistral",
                   "command", "qwen", "deepseek"):
        if family in m:
            return family
    return m.split("-")[0] if m else ""


def verify_tree(repo_dir: str, pkg_dir: str) -> dict:
    """Clean tree + on-disk bytes equal to the recorded commit's blobs."""
    status = _git(repo_dir, "status", "--porcelain")
    dirty = [ln for ln in status.splitlines()
             if ln and not ln.startswith("??")]
    if dirty:
        raise LaunchRefused(
            "working tree is DIRTY; the bytes that would run are not the bytes "
            f"any commit names:\n  " + "\n  ".join(dirty[:10]))
    head = _git(repo_dir, "rev-parse", "HEAD")
    if not re.fullmatch(r"[0-9a-f]{40}", head):
        raise LaunchRefused(f"could not read a 40-hex HEAD from {repo_dir}")

    mismatched: list = []
    digests: dict = {}
    for name in GOVERNING_MODULES:
        path = os.path.join(pkg_dir, name)
        if not os.path.exists(path):
            continue
        rel = os.path.relpath(path, repo_dir)
        with open(path, "rb") as f:
            on_disk = f.read()
        committed = subprocess.run(
            ("git", "-C", repo_dir, "show", f"{head}:{rel}"),
            capture_output=True, check=False).stdout
        digests[name] = hashlib.sha256(on_disk).hexdigest()
        if on_disk != committed:
            mismatched.append(name)
    if mismatched:
        raise LaunchRefused(
            "on-disk module bytes differ from the recorded commit "
            f"({head[:12]}): {mismatched}")
    return {"code_commit": head, "runtime_module_sha256": digests}


#: The recorded value when the provider rejects the parameter. A STRING, so it
#: can never be confused with 0 in a manifest, and distinct from the key being
#: absent (which means "not recorded at all"). Three states, all distinguishable.
TEMPERATURE_UNSUPPORTED = "unsupported"

#: Models measured to REJECT ``temperature`` outright (HTTP 400
#: ``invalid_request_error: "temperature is deprecated for this model."``).
#: DEC-070, from a live pre-flight against ``claude-opus-5``
#: (request id ``req_011Ce3qbp97tLCSVL2rRZtYP``); deprecated provider-side from
#: Claude Opus 4.7 onward.
#:
#: EXPLICIT MEMBERSHIP ONLY -- no version-range matching. The two errors are not
#: symmetric. Mistaking a REJECTING model for a supporting one sends the
#: parameter and earns a loud 400 before any compute is spent. Mistaking a
#: SUPPORTING model for a rejecting one silently omits the pin and lets the
#: provider default decide sampling, which is a quiet wrong number. So a new
#: model is treated as supporting (and must be pinned to 0) until a DECISION
#: adds it here on measured evidence.
TEMPERATURE_REJECTING_MODELS = frozenset({
    "claude-opus-4-7",
    "claude-opus-4-8",
    "claude-opus-5",
})


def temperature_support(model: str) -> str:
    """``"unsupported"`` if the provider rejects the parameter, else ``"supported"``."""
    return (TEMPERATURE_UNSUPPORTED
            if (model or "").strip() in TEMPERATURE_REJECTING_MODELS
            else "supported")


def verify_temperature_governance(*, model: str, temperature) -> dict:
    """Resolve the temperature path, WITHOUT widening into 'anything goes'.

    Two paths, chosen by the model, never by the caller's preference:

    * **supported** -- the pin stands. ``temperature`` must be ``0`` by identity
      (DEC-046B). ``is not None`` and ``== 0``, never truthiness, because 0 is
      both a real value and a falsy one.
    * **unsupported** -- DEC-070. The parameter is NOT SENT and is recorded as
      the string ``"unsupported"``. Passing a NUMBER here is refused: that would
      transmit a parameter the provider rejects, and recording ``0`` for a call
      that never carried it is a false record of what was sent.

    The relaxation is bounded by the model table, so a model that does support
    the parameter is still pinned to 0 and cannot opt into the unsupported path.
    """
    support = temperature_support(model)
    if support == TEMPERATURE_UNSUPPORTED:
        if temperature is None or temperature == TEMPERATURE_UNSUPPORTED:
            recorded = TEMPERATURE_UNSUPPORTED
        else:
            raise LaunchRefused(
                f"model {model!r} REJECTS the temperature parameter (DEC-070), "
                f"but temperature={temperature!r} was supplied. On a rejecting "
                "model the parameter is not sent and is recorded as "
                f"{TEMPERATURE_UNSUPPORTED!r}; recording a number for a call "
                "that never carried one is a false provenance record.")
    else:
        if temperature == TEMPERATURE_UNSUPPORTED:
            raise LaunchRefused(
                f"model {model!r} SUPPORTS the temperature parameter, so the "
                f"DEC-046B pin still applies; {TEMPERATURE_UNSUPPORTED!r} is not "
                "an available answer for it. Pass temperature=0.")
        if temperature is None or temperature != 0:
            raise LaunchRefused(
                f"temperature must be 0 for a production run on {model!r} "
                f"(DEC-046B), got {temperature!r}")
        recorded = 0
    return {
        "model": model,
        "provider_support": support,
        "path": ("pinned_zero" if recorded == 0 else "not_sent_unsupported"),
        "recorded_value": recorded,
        "sent_to_provider": recorded == 0,
        "governing_decision": ("DEC-046B" if recorded == 0 else "DEC-070"),
        "note": (
            "The parameter was NOT SENT: this model rejects it provider-side "
            "(HTTP 400, deprecated from Claude Opus 4.7 onward). Recorded as "
            "'unsupported', never as 0. No greedy-decoding control exists on "
            "this model, so run-to-run variation is bounded only by provider "
            "defaults -- see DEC-070's re-measurement obligation."
            if recorded != 0 else
            "temperature=0 was sent and is pinned by DEC-046B."
        ),
    }


#: A scope ruling must be a DECISION, not a sentence. These are required.
_SCOPE_RULING_KEYS = ("decision_id", "date", "section", "ruling")


def verify_scope_ruling(ruling) -> dict:
    """Validate a DECISION-recorded preregistration scope ruling.

    A scope ruling says a preregistration section does not BIND this work --
    distinct from complying with it, and distinct from amending it. It is the
    only one of the three that changes nothing cited by commit hash, which is
    exactly why it must be recorded as a numbered, dated decision rather than
    asserted in a keyword argument.
    """
    if not isinstance(ruling, dict):
        raise LaunchRefused(
            "preregistration_scope_ruling must be a dict recording the decision "
            f"that made the ruling, got {type(ruling).__name__}")
    missing = [k for k in _SCOPE_RULING_KEYS
               if not str(ruling.get(k, "") or "").strip()]
    if missing:
        raise LaunchRefused(
            f"preregistration_scope_ruling is missing {missing}; a scope ruling "
            "must name the DECISION, its date, the section ruled out of scope, "
            "and the ruling itself")
    did = str(ruling["decision_id"]).strip()
    if not re.fullmatch(r"DEC-\d{3}[A-Z]?", did):
        raise LaunchRefused(
            f"scope ruling decision_id {did!r} is not a DECISION identifier "
            "(expected e.g. 'DEC-069')")
    date = str(ruling["date"]).strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        raise LaunchRefused(
            f"scope ruling date {date!r} is not ISO YYYY-MM-DD")
    text = str(ruling["ruling"]).strip()
    if len(text) < 40:
        raise LaunchRefused(
            "scope ruling text is too short to be a ruling; state what the "
            "section covers and why it does not bind this work")
    return {
        "decision_id": did,
        "date": date,
        "section": str(ruling["section"]).strip(),
        "ruling": text,
        "residual_risk": str(ruling.get("residual_risk", "") or "").strip(),
    }


def verify_judge_governance(*, model: str, judge_model: str,
                            preregistration_amendment: str,
                            preregistration_scope_ruling) -> dict:
    """THREE accepted answers on the judge branch, and no silent fourth.

    1. **A different-family judge.** The preregistered arrangement.
    2. **A dated amendment.** The commitment is changed, in writing, before it is
       cited.
    3. **A DECISION-recorded scope ruling.** The commitment is found not to bind
       this work at all. DEC-069 is the live example: PREREGISTRATION §6 is
       titled "Generation-Mode evaluation" and governs judging of GENERATED
       candidates; the first paper has no generation mode (DEC-046A), so §6 has
       nothing to bind. Its whole point is that nothing cited by commit hash
       changes -- which is precisely why it supplies neither a different-family
       judge nor an amendment, and why a launcher offering only those two escape
       hatches refuses every call.

    THE THING THIS MUST NOT DO is let a same-family judge run SILENTLY against a
    live §6 commitment. So: a scope ruling is structurally validated (a numbered,
    dated DECISION naming the section), the path taken is stamped into the
    receipt, and if a same-family judge is ACTUALLY WIRED under a scope ruling
    the ruling must additionally carry ``residual_risk`` in writing. That is not
    a formality -- it is exactly what DEC-069 itself recorded about
    ``claude-opus-5`` judging coverage of claims ``claude-opus-5`` extracted.
    """
    same_family = bool(judge_model) and (
        _model_family(judge_model) == _model_family(model))
    amended = bool((preregistration_amendment or "").strip())
    ruling = (verify_scope_ruling(preregistration_scope_ruling)
              if preregistration_scope_ruling is not None else None)

    paths: list = []
    if judge_model and not same_family:
        paths.append("different_family_judge")
    if amended:
        paths.append("dated_amendment")
    if ruling is not None:
        paths.append("decision_scope_ruling")

    if not paths:
        if same_family:
            raise LaunchRefused(
                f"judge {judge_model!r} is the SAME family as generator "
                f"{model!r}; the preregistration commits to a different family. "
                "Supply a dated preregistration_amendment, a DECISION-recorded "
                "preregistration_scope_ruling, or use a different-family judge "
                "(DEC-065)")
        raise LaunchRefused(
            "no judge_model, no preregistration_amendment and no "
            "preregistration_scope_ruling; DEC-065 requires the preregistered "
            "different-family judge, or a formal dated amendment, or a "
            "DECISION-recorded scope ruling that the section does not bind this "
            "work (e.g. DEC-069)")

    # A wired same-family judge riding on a scope ruling is the one combination
    # that could be read as compliance when it is not. It stays permitted -- the
    # ruling governs -- but never silently.
    if same_family and ruling is not None and not amended:
        if not ruling["residual_risk"]:
            raise LaunchRefused(
                f"judge {judge_model!r} is the SAME family as generator "
                f"{model!r} and is permitted only by scope ruling "
                f"{ruling['decision_id']}; that ruling must record its "
                "residual_risk in writing. A same-family judge must never run "
                "silently against a preregistration section.")

    return {
        "paths_satisfied": paths,
        "judge_model": judge_model,
        "generator_family": _model_family(model),
        "judge_family": _model_family(judge_model) if judge_model else "",
        "different_family_judge": bool(judge_model) and not same_family,
        "same_family_judge_active": same_family,
        "preregistration_amended": amended,
        "preregistration_amendment": preregistration_amendment,
        "scope_ruling": ruling,
        "compliance_note": (
            "SCOPE RULING, NOT COMPLIANCE: the preregistration section was ruled "
            f"not to bind this work by {ruling['decision_id']} "
            f"({ruling['date']}). Nothing cited by commit hash was changed, and "
            "no different-family judge was used. Do not read this run as having "
            "met the section's requirement."
            if ruling is not None and not paths[:1] == ["different_family_judge"]
            else "The preregistered different-family judge arrangement was used."
        ),
    }


def verify_receipt(receipt, *, model: str, temperature) -> dict:
    """Every recorded call agrees with what the run DECLARED.

    ``temperature`` here is the RESOLVED value from
    ``verify_temperature_governance``: either ``0`` or ``"unsupported"``.

    Unsupported means the field is ABSENT from the call, not unchecked. A call
    that carries a temperature on a rejecting model either never happened as
    recorded, or the adapter transmitted a parameter the provider refuses --
    both are receipts that contradict the run, and both are refused.
    """
    calls = list(getattr(receipt, "calls", None) or [])
    if not calls:
        raise LaunchRefused(
            "the adapter receipt recorded ZERO calls; a reportable run must be "
            "able to show what it actually sent")
    bad_model = sorted({c.get("model") for c in calls
                        if c.get("model") != model})
    if bad_model:
        raise LaunchRefused(
            f"adapter receipt shows call(s) to unauthorized model(s) {bad_model}; "
            f"the run declared {model!r}")

    if temperature == TEMPERATURE_UNSUPPORTED:
        carried = sorted({repr(c.get("temperature")) for c in calls
                          if "temperature" in c})
        if carried:
            raise LaunchRefused(
                f"the run declared temperature {TEMPERATURE_UNSUPPORTED!r} for "
                f"{model!r} (DEC-070: the provider rejects the parameter), but "
                f"the receipt shows call(s) CARRYING temperature {carried}. "
                "Unsupported means the field is absent from the call, not "
                "unchecked.")
    else:
        bad_temp = sorted({repr(c.get("temperature")) for c in calls
                           if c.get("temperature") != temperature})
        if bad_temp:
            raise LaunchRefused(
                f"adapter receipt shows call(s) at temperature {bad_temp}; "
                f"the run declared {temperature!r} (DEC-046B pins 0)")
    return {"calls": len(calls), "models": [model], "temperature": temperature}


def launch(*, repo_dir: str, pkg_dir: str, xml_dir: str, out_dir: str,
           preband_disposition: str, corpus_manifest_path: str,
           model: str, authorized_models, adapter_receipt,
           judge_model: str = "", preregistration_amendment: str = "",
           preregistration_scope_ruling=None,
           temperature=None, **run_kwargs) -> dict:
    """Verify every precondition, run in production mode, verify the receipt.

    Returns the run manifest with a ``launch_receipt`` block. Raises
    ``LaunchRefused`` before starting if any precondition fails, and after the
    run if the adapter receipt contradicts what was declared.
    """
    # --- model authorization (DEC-065) ---------------------------------
    allowed = list(authorized_models or [])
    if not allowed:
        raise LaunchRefused(
            "no authorized_models supplied; DEC-065 requires the production "
            "model to be named in a DECISION before any reportable F3-F7 number")
    if model not in allowed:
        raise LaunchRefused(
            f"model {model!r} is not in the DECISION-backed allowlist {allowed}")

    # --- temperature governance (DEC-046B pin / DEC-070 unsupported) ----
    temperature_governance = verify_temperature_governance(
        model=model, temperature=temperature)
    resolved_temperature = temperature_governance["recorded_value"]

    # --- judge governance (DEC-065 / PREREGISTRATION §6) ----------------
    judge_governance = verify_judge_governance(
        model=model, judge_model=judge_model,
        preregistration_amendment=preregistration_amendment,
        preregistration_scope_ruling=preregistration_scope_ruling)

    # --- clean HEAD and runtime bytes ----------------------------------
    tree = verify_tree(repo_dir, pkg_dir)

    manifest = run_natural_judgment(
        xml_dir, out_dir,
        preband_disposition=preband_disposition,
        corpus_manifest_path=corpus_manifest_path,
        code_commit=tree["code_commit"], model=model,
        temperature=resolved_temperature, production=True, **run_kwargs)

    receipt = verify_receipt(adapter_receipt, model=model,
                             temperature=resolved_temperature)

    manifest["launch_receipt"] = {
        "launcher": "production_launcher.launch",
        "code_commit": tree["code_commit"],
        "tree_clean": True,
        "runtime_module_sha256": tree["runtime_module_sha256"],
        "authorized_models": allowed,
        "model": model,
        "judge_governance": judge_governance,
        "temperature_governance": temperature_governance,
        "adapter_receipt": receipt,
        "limitation": (
            "The receipt is produced by the adapter under test. It proves what "
            "the adapter RECORDED, not what it transmitted; a lying adapter "
            "defeats it. Owning the transport is the only stronger check and is "
            "outside this repo."
        ),
    }
    pc.assert_reportable_run(manifest, manifest["predictions_path"])
    return manifest
