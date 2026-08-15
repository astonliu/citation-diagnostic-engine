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
* **temperature=0.** DEC-046B pins it. Checked with ``is not None`` and ``== 0``,
  never truthiness.
* **Adapter receipt.** The adapter must record, per call, the model and
  temperature it actually sent. After the run the launcher verifies at least one
  call happened and that EVERY call used the authorized model at temperature 0.
  This is the strongest available check short of owning the transport: it can
  still be forged by a lying adapter, and that is stated rather than hidden.
* **Different-family judge decision.** Either the judge model is from a different
  family than the generator, or an explicit dated preregistration amendment is
  supplied. DEC-065 forbids running one family as both against a commit-pinned
  preregistration that prohibits it.

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


def verify_receipt(receipt, *, model: str, temperature) -> dict:
    """Every recorded call used the authorized model at the pinned temperature."""
    calls = list(getattr(receipt, "calls", None) or [])
    if not calls:
        raise LaunchRefused(
            "the adapter receipt recorded ZERO calls; a reportable run must be "
            "able to show what it actually sent")
    bad_model = sorted({c.get("model") for c in calls
                        if c.get("model") != model})
    bad_temp = sorted({repr(c.get("temperature")) for c in calls
                       if c.get("temperature") != temperature})
    if bad_model:
        raise LaunchRefused(
            f"adapter receipt shows call(s) to unauthorized model(s) {bad_model}; "
            f"the run declared {model!r}")
    if bad_temp:
        raise LaunchRefused(
            f"adapter receipt shows call(s) at temperature {bad_temp}; "
            f"the run declared {temperature!r} (DEC-046B pins 0)")
    return {"calls": len(calls), "models": [model], "temperature": temperature}


def launch(*, repo_dir: str, pkg_dir: str, xml_dir: str, out_dir: str,
           preband_disposition: str, corpus_manifest_path: str,
           model: str, authorized_models, adapter_receipt,
           judge_model: str = "", preregistration_amendment: str = "",
           temperature=0, **run_kwargs) -> dict:
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

    # --- the temperature pin (DEC-046B); 0 is real AND falsy ------------
    if temperature is None or temperature != 0:
        raise LaunchRefused(
            f"temperature must be 0 for a production run (DEC-046B), got "
            f"{temperature!r}")

    # --- different-family judge (DEC-065 / PREREGISTRATION §6) ----------
    if judge_model:
        if _model_family(judge_model) == _model_family(model):
            if not preregistration_amendment.strip():
                raise LaunchRefused(
                    f"judge {judge_model!r} is the SAME family as generator "
                    f"{model!r}; the preregistration commits to a different "
                    "family. Supply a dated preregistration_amendment or use a "
                    "different-family judge (DEC-065)")
    elif not preregistration_amendment.strip():
        raise LaunchRefused(
            "no judge_model and no preregistration_amendment; DEC-065 requires "
            "either the preregistered different-family judge or a formal, dated "
            "amendment before a reportable number exists")

    # --- clean HEAD and runtime bytes ----------------------------------
    tree = verify_tree(repo_dir, pkg_dir)

    manifest = run_natural_judgment(
        xml_dir, out_dir,
        preband_disposition=preband_disposition,
        corpus_manifest_path=corpus_manifest_path,
        code_commit=tree["code_commit"], model=model,
        temperature=temperature, production=True, **run_kwargs)

    receipt = verify_receipt(adapter_receipt, model=model,
                             temperature=temperature)

    manifest["launch_receipt"] = {
        "launcher": "production_launcher.launch",
        "code_commit": tree["code_commit"],
        "tree_clean": True,
        "runtime_module_sha256": tree["runtime_module_sha256"],
        "authorized_models": allowed,
        "model": model,
        "judge_model": judge_model,
        "judge_family_differs": bool(judge_model) and (
            _model_family(judge_model) != _model_family(model)),
        "preregistration_amendment": preregistration_amendment,
        "temperature": temperature,
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
