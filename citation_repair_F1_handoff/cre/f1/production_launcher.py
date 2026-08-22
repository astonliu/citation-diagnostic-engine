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
  A model measured to REJECT it -- ``claude-opus-5`` 400s with
  ``"temperature is deprecated for this model."`` -- does not send it and
  records the string ``"unsupported"`` (DEC-070), never ``0``. Three states stay
  distinguishable in the manifest: ``0``, ``"unsupported"``, and the key being
  absent. The relaxation is bounded by a FIRST-PARTY-MEASURED model table, so it
  can never widen into "any temperature is fine"; the receipt also records
  whether the answer was measured or merely assumed.
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
import json
import os
import re
import subprocess
from datetime import date

from . import citation_selection
from . import preband_contract as pc
from .judgment_run import run_natural_judgment

#: Modules whose bytes govern a published number. Verified against the commit.
GOVERNING_MODULES = (
    # Band 1 defines which references Band 2 is allowed to judge.  The full
    # launcher executes these bytes itself, so they are just as load-bearing as
    # the judgment modules below.
    "production_launcher.py", "run.py", "lookup.py", "confirm.py",
    "decide.py", "doi_lookup.py", "biblio_match.py", "resolve_a.py",
    "work_identity.py", "llm_filter.py", "preband_disposition.py",
    "ratelimit.py", "ncbi_meta.py", "textnorm.py", "unscoreable.py",
    "journal_identity.py", "f2_samework_rule.py", "f2_thresholds.py",
    "eval_report.py", "f8_retraction.py", "reason_registry.py",
    "judgment_run.py", "judgment_band.py", "judgment_engine.py", "cocitation.py",
    "band_prompts.py", "parser.py", "schema.py", "f3_provenance.py",
    "f4_strength.py", "f7_entity.py", "preband_contract.py",
    "f7_evidence_builder.py", "f7_seams.py",
    "parser_versions.py", "coverage_prompts_v3.py", "coverage_aggregate.py",
    "f5_activation.py", "f5_candidate_screen.py", "f5_evidence_store.py", "f5_notice.py",
    "f5_study_cluster.py", "f5_controversy_bundle.py",
    "f5_supersession.py", "f5_seams.py",
    "f5_candidate_finder.py",
    "f5_contradiction_prompt.py", "f5_discovery_queue.py",
)


class LaunchRefused(RuntimeError):
    """The launch preconditions are not met. No run is started."""


FULL_LAUNCH_REQUIRED_SEAMS = (
    "extractor", "coverage_judge", "coverage_judge_v3", "fetch_abstract",
    "fetch_fulltext", "discriminator_call_llm", "f3_fetch_reflist",
    "f3_resolve_pmcid", "pubtypes_lookup",
)


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

    # repo_dir MUST be the git toplevel. Point it at a subdirectory and every
    # `rel` below is unresolvable, `git show` returns empty under check=False,
    # and all thirteen governing modules read as mismatched -- sending the
    # operator hunting corruption that does not exist.
    toplevel = _git(repo_dir, "rev-parse", "--show-toplevel")
    if os.path.realpath(toplevel) != os.path.realpath(repo_dir):
        raise LaunchRefused(
            f"repo_dir {repo_dir!r} is not the git toplevel (that is "
            f"{toplevel!r}). Every path would resolve outside the repo and every "
            "governing module would read as mismatched -- an integrity failure "
            "that is really a configuration error. Pass the toplevel.")

    mismatched: list = []
    unresolvable: list = []
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
        if not committed:
            # The file exists on disk but the commit yields nothing for that
            # path: a path-resolution problem, not tampering. Named separately
            # so it is never reported as an integrity failure.
            unresolvable.append(rel)
        elif on_disk != committed:
            mismatched.append(name)
    if unresolvable:
        raise LaunchRefused(
            f"{len(unresolvable)} governing module(s) exist on disk but resolve "
            f"to nothing in commit {head[:12]} (e.g. {unresolvable[:3]}). This "
            "is a path/repo_dir configuration error, NOT an integrity failure: "
            f"check that repo_dir ({repo_dir!r}) and pkg_dir ({pkg_dir!r}) are "
            "the git toplevel and the package directory inside it.")
    if mismatched:
        raise LaunchRefused(
            "on-disk module bytes differ from the recorded commit "
            f"({head[:12]}): {mismatched}")
    return {"code_commit": head, "runtime_module_sha256": digests}


#: The recorded value when the provider rejects the parameter. A STRING, so it
#: can never be confused with 0 in a manifest, and distinct from the key being
#: absent (which means "not recorded at all"). Three states, all distinguishable.
TEMPERATURE_UNSUPPORTED = "unsupported"

#: FIRST-PARTY MEASUREMENTS ONLY (DEC-070). A model enters this table when THIS
#: project has observed it reject the parameter, not when a third party reports
#: it and not by inference from a neighbouring version.
#:
#: * ``claude-opus-5`` -- measured, first-party: HTTP 400
#:   ``invalid_request_error: "temperature is deprecated for this model."``
#:   (request id ``req_011Ce3qbp97tLCSVL2rRZtYP``).
#: * ``claude-opus-4-7`` -- reported by three independent third-party bug
#:   reports, NOT measured here, so it stays OUT.
#: * ``claude-opus-4-8`` -- was asserted by inference from the 4.7 reports and
#:   nothing else; that assertion is WITHDRAWN, so it stays OUT.
#:
#: THE ASYMMETRY THAT GOVERNS THIS TABLE (DEC-070). The two errors are not
#: equally bad. Wrongly listing a model as REJECTING is the SILENT failure: the
#: pin is dropped, the parameter is never sent, and the provider default quietly
#: decides sampling. Wrongly listing one as SUPPORTING is the LOUD failure: the
#: parameter is sent and the provider 400s before any compute is spent. So the
#: table is deliberately under-inclusive -- an unlisted model is treated as
#: supporting (and must be pinned to 0) until a DECISION adds it on first-party
#: measured evidence.
TEMPERATURE_REJECTING_MODELS = frozenset({
    "claude-opus-5",
})

#: Measured first-party to ACCEPT the parameter. Behaviourally identical to an
#: unlisted model -- both are pinned to 0 -- but it keeps "we measured this" and
#: "we assume this" from reading the same in a receipt.
TEMPERATURE_ACCEPTING_MODELS = frozenset({
    "claude-sonnet-4-5",
})

EVIDENCE_MEASURED_REJECTS = "measured_first_party_rejects"
EVIDENCE_MEASURED_ACCEPTS = "measured_first_party_accepts"
EVIDENCE_ASSUMED_ACCEPTS = "unmeasured_assumed_accepts"


def temperature_support(model: str) -> str:
    """``"unsupported"`` if the provider rejects the parameter, else ``"supported"``.

    Binary on purpose: this answers what the launcher must DO. The strength of
    the evidence behind the answer is a separate question -- see
    ``temperature_evidence`` -- and must not blur the behavioural one.
    """
    return (TEMPERATURE_UNSUPPORTED
            if (model or "").strip() in TEMPERATURE_REJECTING_MODELS
            else "supported")


def temperature_evidence(model: str) -> str:
    """How well the support answer is EVIDENCED -- three tiers, not two.

    'Measured to accept' and 'assumed to accept because nobody looked' produce
    the same behaviour and must not read the same in a receipt. Same discipline
    as the manifest's 0 / "unsupported" / absent split.
    """
    m = (model or "").strip()
    if m in TEMPERATURE_REJECTING_MODELS:
        return EVIDENCE_MEASURED_REJECTS
    if m in TEMPERATURE_ACCEPTING_MODELS:
        return EVIDENCE_MEASURED_ACCEPTS
    return EVIDENCE_ASSUMED_ACCEPTS


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
        "support_evidence": temperature_evidence(model),
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


#: Same sentinel, same three-state discipline, for ``assistant_prefill``.
PREFILL_UNSUPPORTED = "unsupported"

#: FIRST-PARTY MEASUREMENTS ONLY (DEC-071), same rule as the temperature table.
#: ``claude-opus-5`` -- measured: HTTP 400 ``invalid_request_error: "This model
#: does not support assistant message prefill. The conversation must end with a
#: user message."`` (request id ``req_011Ce3sXfjxuLxxwAd1VHQAy``). Nothing else
#: goes in; an unlisted model is treated as supporting, so the failure mode is a
#: loud 400 rather than a silently dropped setting.
PREFILL_REJECTING_MODELS = frozenset({
    "claude-opus-5",
})


def prefill_support(model: str) -> str:
    """``"unsupported"`` if the provider rejects assistant prefill, else supported."""
    return (PREFILL_UNSUPPORTED
            if (model or "").strip() in PREFILL_REJECTING_MODELS
            else "supported")


def verify_prefill_governance(*, model: str, assistant_prefill) -> dict:
    """Resolve the prefill path. Chosen by the MODEL, never by the caller.

    ``judgment_run`` writes ``assistant_prefill`` verbatim into
    ``manifest["adapter"]`` and never transmits it -- the adapter does. So on a
    model that REJECTS prefill, a non-empty string there is a false provenance
    record: the manifest claims a prefill was used on calls that could not have
    carried one. Nothing caught that before this gate existed.

    Three distinguishable states, as with temperature: the actual string (sent),
    ``"unsupported"`` (not sent, provider rejects it), key absent (never
    recorded).
    """
    support = prefill_support(model)
    if assistant_prefill is None:
        recorded = None                      # never recorded; key stays absent
    elif support == PREFILL_UNSUPPORTED:
        if assistant_prefill in ("", PREFILL_UNSUPPORTED):
            recorded = PREFILL_UNSUPPORTED
        else:
            raise LaunchRefused(
                f"model {model!r} REJECTS assistant prefill (DEC-071), but "
                f"assistant_prefill={assistant_prefill!r} was supplied. On a "
                "rejecting model it is not sent and is recorded as "
                f"{PREFILL_UNSUPPORTED!r}; recording a prefill string for calls "
                "that never carried one is a false provenance record.")
    else:
        if assistant_prefill == PREFILL_UNSUPPORTED:
            raise LaunchRefused(
                f"model {model!r} SUPPORTS assistant prefill, so "
                f"{PREFILL_UNSUPPORTED!r} is not an available answer for it. "
                "Pass the actual prefill string, or omit it entirely.")
        if not isinstance(assistant_prefill, str):
            raise LaunchRefused(
                f"assistant_prefill must be a string, {PREFILL_UNSUPPORTED!r}, "
                f"or omitted; got {assistant_prefill!r}")
        recorded = assistant_prefill
    return {
        "model": model,
        "provider_support": support,
        "support_evidence": (EVIDENCE_MEASURED_REJECTS
                             if support == PREFILL_UNSUPPORTED
                             else EVIDENCE_ASSUMED_ACCEPTS),
        "path": ("not_recorded" if recorded is None
                 else "not_sent_unsupported" if recorded == PREFILL_UNSUPPORTED
                 else "sent"),
        "recorded_value": recorded,
        "sent_to_provider": bool(recorded) and recorded != PREFILL_UNSUPPORTED,
        "governing_decision": ("DEC-071" if recorded == PREFILL_UNSUPPORTED
                               else "DEC-068"),
        "note": (
            "The prefill was NOT SENT: this model rejects it provider-side. It "
            "existed only because an earlier model fenced its JSON; three "
            "first-party draws on claude-opus-5 with the prefill removed parsed "
            "every time. That was a trivial two-key prompt -- it shows the model "
            "does not fence unprompted, and is NOT a quarantine-rate measurement."
            if recorded == PREFILL_UNSUPPORTED else
            "assistant_prefill was sent and is recorded verbatim."
            if recorded else
            "assistant_prefill was not supplied and is not recorded."
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
            "The preregistered different-family judge arrangement was used."
            if "different_family_judge" in paths else
            "A dated preregistration amendment authorized the judge arrangement."
            if "dated_amendment" in paths else
            "SCOPE RULING, NOT COMPLIANCE: the preregistration section was ruled "
            f"not to bind this work by {ruling['decision_id']} "
            f"({ruling['date']}). Nothing cited by commit hash was changed, and "
            "no different-family judge was used. Do not read this run as having "
            "met the section's requirement."
        ),
    }


def assert_receipt_shape(receipt) -> None:
    """Refuse a mis-shaped receipt BEFORE the run, not after it.

    ``verify_receipt`` runs after ``run_natural_judgment`` returns, so every
    receipt-shape error was otherwise discovered at maximum cost -- a whole
    corpus run burned before the refusal. This checks only what is knowable up
    front: the object exists, exposes ``.calls``, and ``.calls`` is a list. It
    deliberately does NOT inspect call contents; there are none yet, and
    pretending to check them here would be the same false assurance this
    codebase keeps removing.
    """
    if receipt is None:
        raise LaunchRefused(
            "no adapter_receipt supplied; a reportable run must be able to show "
            "what it actually sent")
    calls = getattr(receipt, "calls", None)
    if calls is None:
        raise LaunchRefused(
            f"adapter_receipt {type(receipt).__name__} exposes no .calls "
            "attribute; verify_receipt reads .calls after the run, and a "
            "mis-shaped receipt would burn the whole run before refusing")
    if not isinstance(calls, list):
        raise LaunchRefused(
            f"adapter_receipt.calls must be a list, got {type(calls).__name__}")


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


def _read_jsonl(path: str) -> list[dict]:
    rows: list[dict] = []
    with open(path, encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise LaunchRefused(
                    f"Band-1 log {path}:{lineno} is not JSON: {exc}") from exc
            if not isinstance(row, dict):
                raise LaunchRefused(
                    f"Band-1 log {path}:{lineno} is not an object")
            rows.append(row)
    return rows


def _persist_finished_manifest(manifest: dict) -> None:
    """Atomically persist launcher-added provenance into the durable manifest."""
    path = manifest.get("manifest_path")
    if not isinstance(path, str) or not path.strip():
        raise LaunchRefused(
            "finished run returned no manifest_path; launcher provenance cannot "
            "be made durable")
    tmp = path + ".launcher.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp, path)


def _band1_attestations(log_path: str, *, snapshot_date: str) -> dict:
    """Build honest F1/F2/F8 execution tallies from the lossless Band-1 log."""
    try:
        date.fromisoformat(snapshot_date)
    except (TypeError, ValueError) as exc:
        raise LaunchRefused(
            "band1_snapshot_date must be ISO YYYY-MM-DD") from exc
    rows = _read_jsonl(log_path)
    if not rows:
        raise LaunchRefused("Band 1 emitted an empty lossless log")

    quarantined = sum(
        1 for row in rows
        if ((row.get("log") or {}).get("decided_by") == "quarantine_exception"))
    labels = [str(row.get("label") or "") for row in rows]
    f8_statuses = [str((row.get("log") or {}).get("f8_timing_status") or "")
                   for row in rows]
    f8_attempted = sum(bool(status) for status in f8_statuses)
    f8_answered = sum(status in {
        "qualified", "clear", "timing_indeterminate", "not_applicable"}
        for status in f8_statuses)
    common = {
        "performed": True,
        "snapshot_date": snapshot_date,
        "attempted": len(rows),
        "answered": len(rows) - quarantined,
        "transport_failed": quarantined,
    }
    return {
        "F1": {
            **common,
            "source": (
                "cre.f1.run exact identity pipeline: PubMed, DOI Foundation, "
                "Crossref, DataCite, OpenAlex"),
            "fired": labels.count("F1"),
            "reason": "full Band-1 pipeline executed over the frozen corpus",
        },
        "F2": {
            **common,
            "source": (
                "cre.f1.run work-identity pipeline: PubMed, DOI Foundation, "
                "Crossref, DataCite, OpenAlex"),
            "fired": labels.count("F2"),
            "reason": "full Band-1 pipeline executed over the frozen corpus",
        },
        "F8": {
            "performed": True,
            "source": (
                "PubMed retraction relationship and notice-date lookup with "
                "conservative citing-date interval and 31-day inclusion floor"),
            "snapshot_date": snapshot_date,
            "attempted": f8_attempted,
            "answered": f8_answered,
            "transport_failed": max(0, f8_attempted - f8_answered),
            "fired": labels.count("F8"),
            "reason": (
                "source-bound F8 timing gate executed; unresolved and sub-31-day "
                "cases never become F8"),
        },
    }


def _require_full_launch_wiring(*, out_dir: str, run_kwargs: dict,
                                f5_seams, f5_evidence_builder, f5_policy,
                                f7_seams, f7_evidence_builder, f7_policy) -> None:
    """Reject a nominal full launch when any taxonomy seam cannot fire."""
    missing = [name for name in FULL_LAUNCH_REQUIRED_SEAMS
               if not callable(run_kwargs.get(name))]
    if missing:
        raise LaunchRefused(
            "full F1-F8 launch is missing required callable seam(s): "
            f"{missing}")
    if any(part is None for part in (f5_seams, f5_evidence_builder, f5_policy)):
        raise LaunchRefused(
            "full F1-F8 launch requires production F5 seams, evidence builder, "
            "and policy")
    if any(part is None for part in (f7_seams, f7_evidence_builder, f7_policy)):
        raise LaunchRefused(
            "full F1-F8 launch requires production F7 seams, evidence builder, "
            "and policy")
    if run_kwargs.get("max_docs") is not None:
        raise LaunchRefused("full F1-F8 launch cannot set max_docs")
    if str(run_kwargs.get("chain_genesis") or "").strip():
        raise LaunchRefused("full F1-F8 launch cannot resume a prior segment")
    if os.path.exists(out_dir):
        raise LaunchRefused(
            f"full launch output root already exists: {out_dir}; use a fresh path")


def launch_full(*, repo_dir: str, pkg_dir: str, xml_dir: str, out_dir: str,
                corpus_manifest_path: str, model: str, authorized_models,
                adapter_receipt, band1_snapshot_date: str,
                citation_selection_path: str = "",
                citation_selection_proof_dir: str = "",
                judge_model: str = "", preregistration_amendment: str = "",
                preregistration_scope_ruling=None,
                temperature=None, assistant_prefill=None,
                anthropic_key: str = "", ncbi_key: str = "",
                crossref_mailto: str = "", openalex_mailto: str = "",
                f1_complete=None,
                f5_seams=None, f5_evidence_builder=None, f5_policy=None,
                f7_seams=None, f7_evidence_builder=None, f7_policy=None,
                **run_kwargs) -> dict:
    """The strict end-to-end F1-F8 production entrypoint.

    Unlike :func:`launch`, this function does not accept an externally prepared
    pre-band artifact.  It runs the exact current Band-1 code over ``xml_dir``,
    builds the canonical disposition from its lossless log, and immediately
    consumes that artifact in the production judgment run.

    ``citation_selection_path`` narrows the run to a HASH-PINNED reference set
    (``citation_selection.py``). It is applied after XML parsing and BEFORE Band
    1, so every selected reference runs the real F1/F2/F8 code and the real
    F3-F8 band -- the selection changes the population and substitutes for no
    stage. ``citation_selection_proof_dir``, when given, re-hashes the source-run
    artifacts the selection binds, so "derived from those runs" is verified here
    rather than asserted by the manifest.

    IT IS NOT A BACK DOOR FOR A PRE-BAND MAP. The selection carries ids only --
    no labels, no dispositions, no identifiers -- and the refusal of an injected
    ``preband_disposition`` below is unchanged. A caller wanting to skip Band 1
    still cannot.
    """
    if "preband_disposition" in run_kwargs:
        raise LaunchRefused(
            "launch_full builds its own Band-1 disposition; an injected "
            "preband_disposition would bypass the current F1/F2/F8 code")
    _require_full_launch_wiring(
        out_dir=out_dir, run_kwargs=run_kwargs,
        f5_seams=f5_seams, f5_evidence_builder=f5_evidence_builder,
        f5_policy=f5_policy, f7_seams=f7_seams,
        f7_evidence_builder=f7_evidence_builder, f7_policy=f7_policy)

    # Validate everything the ordinary launcher can know before Band 1 creates
    # even its first file. launch() repeats these checks immediately before Band
    # 2, so a mid-run tree/config change is also caught.
    allowed = list(authorized_models or [])
    if not allowed or model not in allowed:
        raise LaunchRefused(
            f"model {model!r} is not in a nonempty DECISION-backed allowlist")
    resolved_temperature = verify_temperature_governance(
        model=model, temperature=temperature)["recorded_value"]
    verify_prefill_governance(
        model=model, assistant_prefill=assistant_prefill)
    assert_receipt_shape(adapter_receipt)
    if not callable(getattr(adapter_receipt, "record", None)):
        raise LaunchRefused(
            "launch_full requires an adapter receipt with record() so Band-1 "
            "model calls cannot disappear from the launch receipt")
    verify_judge_governance(
        model=model, judge_model=judge_model,
        preregistration_amendment=preregistration_amendment,
        preregistration_scope_ruling=preregistration_scope_ruling)
    tree = verify_tree(repo_dir, pkg_dir)
    try:
        with open(corpus_manifest_path, encoding="utf-8") as fh:
            corpus_manifest = json.load(fh)
        pc.verify_corpus_contents(
            xml_dir, pc.corpus_inventory(corpus_manifest))
    except (OSError, json.JSONDecodeError, pc.PrebandContractError) as exc:
        raise LaunchRefused(f"frozen corpus preflight failed: {exc}") from exc

    # Validate production F5/F7 bundles before Band-1 work or output.
    from .f5_seams import validate_production_f5_configuration
    from .f7_seams import validate_production_f7_configuration
    try:
        validate_production_f5_configuration(
            seams=f5_seams, evidence_builder=f5_evidence_builder,
            policy=f5_policy, run_model=model)
        validate_production_f7_configuration(
            seams=f7_seams, evidence_builder=f7_evidence_builder,
            policy=f7_policy, adapter_receipt=adapter_receipt)
    except (TypeError, ValueError) as exc:
        raise LaunchRefused(
            f"full-launch discriminator configuration invalid: {exc}") from exc

    # THE SELECTION IS LOADED AND VERIFIED BEFORE BAND 1 CREATES ITS FIRST FILE.
    # Its digest must recompute from its own id list, and -- when a proof dir is
    # supplied -- every source-run artifact it binds must re-hash to the recorded
    # value. A selection that cannot prove what it is derived from is refused
    # here, where the refusal costs nothing.
    selection = None
    selection_proof = {}
    if citation_selection_path:
        try:
            selection = citation_selection.load_selection(citation_selection_path)
            if citation_selection_proof_dir:
                selection_proof = citation_selection.verify_source_runs(
                    selection, citation_selection_proof_dir)
        except citation_selection.SelectionError as exc:
            raise LaunchRefused(f"citation selection preflight failed: {exc}") from exc

    band1_dir = os.path.join(out_dir, "band1")
    judgment_dir = os.path.join(out_dir, "judgment")
    os.makedirs(band1_dir)
    dataset_path = os.path.join(band1_dir, "band1_predictions.jsonl")
    log_path = os.path.join(band1_dir, "band1_lossless_log.jsonl")
    disposition_path = os.path.join(
        band1_dir, "preband_disposition_v1.jsonl")

    from .run import make_completer, run as run_band1
    from .parser import iter_pmc_dir
    from .preband_disposition import write_disposition
    complete = f1_complete or make_completer(model, anthropic_key)

    def recorded_band1_complete(prompt):
        adapter_receipt.record(seam="f1_llm_filter")
        return complete(prompt)

    # AFTER XML PARSING, BEFORE BAND 1. `iter_pmc_dir` is the same parse Band 1
    # performs for itself when `refs` is omitted, so the selected references are
    # the real parser's objects and every one of them goes on to run the real
    # F1/F2/F8 code. Selecting here rather than filtering Band 1's OUTPUT is the
    # difference between a smaller run and a censored one.
    band1_refs = None
    if selection is not None:
        parsed = list(iter_pmc_dir(xml_dir))
        citation_selection.assert_selection_covered(
            selection, [ref.citation_id for ref in parsed])
        band1_refs = citation_selection.apply_selection(parsed, selection)
        print(f"[launch-full-selection] {len(band1_refs)} of {len(parsed)} "
              f"parsed references selected "
              f"(cohort {selection.cohort_sha256[:12]})")

    run_band1(
        xml_dir, dataset_path, log_path, model=model,
        anthropic_key=anthropic_key, ncbi_key=ncbi_key,
        crossref_mailto=crossref_mailto,
        openalex_mailto=openalex_mailto, refs=band1_refs,
        complete=recorded_band1_complete, f8_timing=True)
    attestations = _band1_attestations(
        log_path, snapshot_date=band1_snapshot_date)
    tree_commit = tree["code_commit"]
    disposition_manifest = write_disposition(
        log_path, disposition_path, f2_commit=tree_commit,
        corpus_manifest_path=corpus_manifest_path,
        generated_by="production_launcher.launch_full",
        generated_at=band1_snapshot_date,
        check_attestations=attestations)

    manifest = launch(
        repo_dir=repo_dir, pkg_dir=pkg_dir, xml_dir=xml_dir,
        out_dir=judgment_dir, preband_disposition=disposition_path,
        corpus_manifest_path=corpus_manifest_path, model=model,
        authorized_models=allowed, adapter_receipt=adapter_receipt,
        judge_model=judge_model,
        preregistration_amendment=preregistration_amendment,
        preregistration_scope_ruling=preregistration_scope_ruling,
        temperature=resolved_temperature, assistant_prefill=assistant_prefill,
        f5_seams=f5_seams, f5_evidence_builder=f5_evidence_builder,
        f5_policy=f5_policy, f7_seams=f7_seams,
        f7_evidence_builder=f7_evidence_builder, f7_policy=f7_policy,
        citation_selection_path=citation_selection_path,
        **run_kwargs)
    manifest["full_launch"] = {
        "entrypoint": "production_launcher.launch_full",
        "band1_predictions_path": dataset_path,
        "band1_lossless_log_path": log_path,
        "preband_disposition_path": disposition_path,
        "preband_manifest_path": disposition_manifest["manifest_path"],
        "band1_label_counts": disposition_manifest["label_counts"],
        "band1_check_attestations": attestations,
        "all_taxonomies_wired": True,
        "citation_selection": (
            {**selection.binding(), "source_run_proof": selection_proof}
            if selection is not None else {"selection_applied": False}),
    }
    _persist_finished_manifest(manifest)
    return manifest


def launch(*, repo_dir: str, pkg_dir: str, xml_dir: str, out_dir: str,
           preband_disposition: str, corpus_manifest_path: str,
           model: str, authorized_models, adapter_receipt,
           judge_model: str = "", preregistration_amendment: str = "",
           preregistration_scope_ruling=None,
           temperature=None, assistant_prefill=None,
           f5_seams=None, f5_evidence_builder=None, f5_policy=None,
           f7_seams=None, f7_evidence_builder=None, f7_policy=None,
           **run_kwargs) -> dict:
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

    # Formal F5 detection is useful without autonomous replacement, but only
    # with the concrete PubMed evidence path and an independent positive gate.
    f5_parts = (f5_seams, f5_evidence_builder, f5_policy)
    if any(part is not None for part in f5_parts):
        if not all(part is not None for part in f5_parts):
            raise LaunchRefused(
                "production F5 requires seams, evidence builder, and locked "
                "policy together; otherwise leave all three explicitly unwired")
        from .f5_seams import validate_production_f5_configuration
        try:
            validate_production_f5_configuration(
                seams=f5_seams, evidence_builder=f5_evidence_builder,
                policy=f5_policy, run_model=model)
        except (TypeError, ValueError) as exc:
            raise LaunchRefused(
                f"production F5 configuration invalid: {exc}") from exc

    # F7 is either explicitly unwired (all three absent) or fully production
    # wired.  Validate before tree inspection and, critically, before the run
    # can create an output directory.
    f7_parts = (f7_seams, f7_evidence_builder, f7_policy)
    if any(part is not None for part in f7_parts):
        if not all(part is not None for part in f7_parts):
            raise LaunchRefused(
                "production F7 requires seams, evidence builder, and locked policy "
                "together; otherwise leave all three explicitly unwired")
        from .f7_seams import validate_production_f7_configuration
        try:
            validate_production_f7_configuration(
                seams=f7_seams, evidence_builder=f7_evidence_builder,
                policy=f7_policy, adapter_receipt=adapter_receipt)
        except ValueError as exc:
            raise LaunchRefused(f"production F7 configuration invalid: {exc}") from exc
        if f7_policy.generator_model_id != model:
            raise LaunchRefused(
                "production F7 generator_model_id must equal the authorized run model")

    # --- temperature governance (DEC-046B pin / DEC-070 unsupported) ----
    temperature_governance = verify_temperature_governance(
        model=model, temperature=temperature)
    resolved_temperature = temperature_governance["recorded_value"]

    # --- assistant_prefill governance (DEC-071) -------------------------
    prefill_governance = verify_prefill_governance(
        model=model, assistant_prefill=assistant_prefill)
    resolved_prefill = prefill_governance["recorded_value"]

    # --- the receipt must be usable BEFORE the run, not after it --------
    assert_receipt_shape(adapter_receipt)

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
        temperature=resolved_temperature,
        f5_seams=f5_seams, f5_evidence_builder=f5_evidence_builder,
        f5_policy=f5_policy,
        f7_seams=f7_seams, f7_evidence_builder=f7_evidence_builder,
        f7_policy=f7_policy,
        **({"assistant_prefill": resolved_prefill}
           if resolved_prefill is not None else {}),
        production=True, **run_kwargs)

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
        "prefill_governance": prefill_governance,
        "adapter_receipt": receipt,
        "limitation": (
            "The receipt is produced by the adapter under test. It proves what "
            "the adapter RECORDED, not what it transmitted; a lying adapter "
            "defeats it. Owning the transport is the only stronger check and is "
            "outside this repo."
        ),
    }
    pc.assert_reportable_run(manifest, manifest["predictions_path"])
    _persist_finished_manifest(manifest)
    return manifest
