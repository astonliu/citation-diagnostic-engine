"""F5 and F7 wiring for a REAL corpus run, not a hand-authored packet.

WHY THIS EXISTS. The two instruments in this repo were missing opposite halves of
the ladder, and neither could say so.

``sandbox_wiring`` builds F5 and F7 for one packet, and its F5 candidates come
from a paper bank (or, with ``f5_live_discovery``, the production finder with
every hit forced into that bank). Its coverage is abstract-scope: there is no
``coverage_judge_v3``, so a claim whose evidence lives in the cited work's BODY
can only ever be HELD. ``CRE_MASS_ERROR_HUNT`` is the mirror image -- it passes
``coverage_judge_v3`` and ``fetch_fulltext``, so coverage is full-text, and it
passes no F5 or F7 seams at all, so those two checks provably cannot fire.

The consequence was measured, not theorised. PMC6058482:CIT0083 -- "the PAM from
Neisseria meningitidis Cas9 is reported to be 5'-NNNNGATT-3'", cited to Jiang
2013 -- returns UNJUDGEABLE in the sandbox with ``provenance: null``. The claim
extracts correctly and coverage answers honestly (``engages_subject: false``: the
abstract is about S. pneumoniae and E. coli), so the pair holds and F3 never runs.
The citation looks like a genuine provenance error and no instrument in the repo
could reach it: the one with full-text coverage had no F3-adjacent seams wired
alongside F5/F7, and the one with the seams could not see the body.

WHAT THIS MODULE IS. The F5 half, built for a corpus rather than a packet, plus
one assembler that puts it next to the production F7 bundle ``sandbox_wiring``
already builds. Nothing here reimplements a seam: F7 comes from
``sandbox_wiring.build_f7`` unchanged (it already calls
``validate_production_f7_configuration``), and F5 is ``f5_seams.build_f5_seams``
over live fetchers instead of a bank.

THE ONE REAL DIFFERENCE FROM ``sandbox_wiring.build_f5``. There is no bank.
``fetch_meta`` and ``fetch_abstract`` resolve any PMID live through
``PubMedCandidateFinder.fetch_metadata`` -- the same reader the finder uses for
the cited work -- so a candidate the finder discovers resolves because it is a
real record, not because something copied it into a dict first. That is what lets
``validate_production_f5_configuration`` be called here, and it IS called: the
sandbox deliberately does not call it because its candidate source is
substituted, and this module has no such substitution to declare.

``as_of_date`` IS PER RUN, NOT PER REFERENCE. ``make_f5_evidence_builder`` binds
one as-of date at construction, so a corpus spanning several citing papers must
either run per paper or accept one shared as-of. This module does not hide that:
the parameter is required and has no default, because "superseded by when" is the
question F5 answers and a guessed date answers a different one.

NETWORK AND MODEL. Every fetcher here is live NCBI and every transport is a live
Anthropic call. Nothing is stubbed and nothing is free.
"""
from __future__ import annotations

import os
import threading

from .anthropic_transport import make_anthropic_call
from .f5_candidate_finder import RANKING_DEFAULT, RANKINGS

#: Kept as a name rather than a literal so the provenance block and the finder
#: cannot drift apart on what "the production source" means.
LIVE_F5_CANDIDATE_SOURCE = "live_pubmed_candidate_finder"


class WiringError(ValueError):
    """The requested band wiring cannot be built."""


def make_live_pubmed_readers(*, api_key: str = "", email: str,
                             session=None, cache_dir: "str | None" = None,
                             ranking: str = RANKING_DEFAULT):
    """``(fetch_meta, fetch_abstract, finder)`` over live PubMed, memoized.

    ONE record per PMID per run. The F5 assessor asks for a candidate's metadata
    and its abstract separately, and the deep loop revisits the cited work for
    every comparison, so an unmemoized reader would pay for the same EFetch
    dozens of times per reference and hit the NCBI rate limiter on its own
    traffic. The cache is keyed by PMID and holds ``None`` for an answered-empty
    lookup, so an absent record is not re-fetched either.

    A TRANSPORT FAILURE IS NOT CACHED. ``fetch_metadata`` raises
    ``CandidateFinderError`` on an outage and that exception propagates: caching
    it would turn one timeout into a permanent absence for the rest of the run,
    which is exactly the outage-as-absence confusion DEC-032 forbids.
    """
    from .f5_candidate_finder import PubMedCandidateFinder

    if not isinstance(email, str) or not email.strip():
        raise WiringError("email must be a nonblank string")
    if ranking not in RANKINGS:
        raise WiringError(f"ranking must be one of {sorted(RANKINGS)}")

    finder = PubMedCandidateFinder(
        api_key=api_key, email=email, session=session, cache_dir=cache_dir,
        ranking=ranking)

    cache: dict = {}
    guard = threading.Lock()

    def fetch_meta(pmid):
        key = str(pmid or "").strip()
        if not key:
            return None
        with guard:
            if key in cache:
                return cache[key]
        record = finder.fetch_metadata(key)      # may raise; deliberately uncached
        with guard:
            cache.setdefault(key, record)
            return cache[key]

    def fetch_abstract(pmid):
        """The abstract off the SAME record ``fetch_meta`` returned.

        Not a second reader. ``evidence_reader.fetch_abstract`` would be a second
        round trip and, worse, a second opinion: if the two disagreed about a
        record the F5 assessor would compare an abstract the tier and date came
        from a different fetch of.
        """
        record = fetch_meta(pmid)
        if not record:
            return None
        return record.get("abstract") or None

    return fetch_meta, fetch_abstract, finder


def build_production_f5(*, as_of_date: str, model: str, email: str,
                        api_key: str = "", ncbi_key: str = "", session=None,
                        cache_dir: "str | None" = None,
                        cap: "int | None" = None,
                        max_deep_comparisons: "int | None" = None,
                        screen: bool = True,
                        ranking: str = RANKING_DEFAULT) -> dict:
    """Production F5 seams, evidence builder and policy for a corpus run.

    Validated before return by ``validate_production_f5_configuration``, which is
    the difference between this and the bench builder and the reason the result
    may carry a reportable F5 at all.
    """
    from anthropic import Anthropic

    from .f5_candidate_screen import (CANDIDATE_SCREEN_PROMPT_VERSION,
                                      make_candidate_screen)
    from .f5_seams import (CANDIDATE_CAP, build_f5_seams,
                           make_f5_evidence_builder,
                           validate_production_f5_configuration)
    from .f5_supersession import F5Policy

    as_of = str(as_of_date or "").strip()
    if not as_of:
        raise WiringError(
            "F5 needs an as_of_date: supersession asks whether a superseding "
            "work existed AT A POINT IN TIME and has no answer without one")
    if not str(model or "").strip():
        raise WiringError("F5 needs the model id it will record")
    cap = CANDIDATE_CAP if cap is None else int(cap)
    if cap <= 0:
        raise WiringError("f5 cap must be a positive integer")
    if max_deep_comparisons is not None and int(max_deep_comparisons) < 0:
        raise WiringError("max_deep_comparisons must be >= 0 or None")

    fetch_meta, fetch_abstract, finder = make_live_pubmed_readers(
        api_key=ncbi_key, email=email, session=session, cache_dir=cache_dir,
        ranking=ranking)

    def search_candidates(cited_meta, claim, *, after_date, as_of_date,
                          cap: int = CANDIDATE_CAP):
        """The finder, unwrapped.

        ``sandbox_wiring`` has to copy every hit into its bank here so the
        bench's bank-backed readers can resolve a discovered PMID. There is no
        bank on this path, so the ``CandidateSearchResult`` passes through
        untouched -- including its ok/partial/failure status, which is what stops
        a partial search from being read as a confident negative.
        """
        return finder.search_candidates(
            cited_meta, claim, after_date=after_date, as_of_date=as_of_date,
            cap=cap)

    def _client():
        return Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))

    # THREE DISTINCT TRANSPORTS. build_f5_seams refuses a shared
    # generator/verifier, and merge_token_ledgers refuses to collapse two stages
    # into one row -- so the screen gets its own or the run cannot say what the
    # screen cost, which is the number that decides whether to raise the cap.
    generator = make_anthropic_call(_client(), model, max_tokens=8192,
                                    stage="f5_generator")
    verifier = make_anthropic_call(_client(), model, max_tokens=8192,
                                   stage="f5_verifier")

    screen_transport = None
    screen_candidates = None
    if screen:
        # Same budget rule as the bench: ~160 output tokens per candidate, and a
        # reply that grows with the batch must stream above the SDK's
        # non-streaming ceiling. A truncated screen is not a degraded screen --
        # it is a JSONDecodeError that discards the batch and pays for every deep
        # comparison the screen existed to avoid.
        screen_max_tokens = min(120_000, max(16_384, 160 * cap))
        screen_transport = make_anthropic_call(
            _client(), model, max_tokens=screen_max_tokens,
            stage="f5_candidate_screen", stream=True)
        screen_candidates = make_candidate_screen(screen_transport)
    else:
        screen_max_tokens = 0

    seams = build_f5_seams(
        fetch_meta=fetch_meta, fetch_abstract=fetch_abstract,
        search_candidates=search_candidates,
        complete=generator, verifier_complete=verifier,
        cap=cap, screen_candidates=screen_candidates,
        judgment_model_id=model, verifier_model_id=model)

    # Path A stays hard-gated off and the mode stays "deployment": that pairing
    # plus a distinct verifier is what makes Path B detection meaningful, and the
    # production validator below refuses anything else.
    policy = F5Policy(mode="deployment", deploy_path_a=False,
                      candidate_screen_enabled=screen_candidates is not None,
                      max_deep_comparisons=max_deep_comparisons,
                      generator_model_id=model, verifier_model_id=model)
    evidence_builder = make_f5_evidence_builder(fetch_meta, as_of_date=as_of)

    # THE GATE THE BENCH CANNOT PASS AND THIS MUST. It is the whole reason this
    # module exists rather than a flag on build_f5.
    validate_production_f5_configuration(
        seams=seams, evidence_builder=evidence_builder, policy=policy,
        run_model=model)

    return {
        "f5_seams": seams,
        "f5_evidence_builder": evidence_builder,
        "f5_policy": policy,
        "token_ledgers": [t.token_ledger for t in
                          (generator, verifier, screen_transport)
                          if t is not None],
        "provenance": {
            "f5_candidate_source": LIVE_F5_CANDIDATE_SOURCE,
            "f5_as_of_date": as_of,
            "f5_candidate_cap": cap,
            "f5_max_deep_comparisons": max_deep_comparisons,
            "f5_ranking": ranking,
            "f5_candidate_screen": (
                {"enabled": True,
                 "prompt_version": CANDIDATE_SCREEN_PROMPT_VERSION,
                 "max_output_tokens": screen_max_tokens}
                if screen_candidates is not None else {"enabled": False}),
            # No bank, so no bank count, and the finder has not run yet: the
            # per-reference record carries the real retrieval counts. Reporting a
            # 0 here would read as "retrieval returned nothing".
            "candidates_retrieved": None,
            "production_validator_called": True,
        },
    }


def build_band_seams(*, model: str, email: str, as_of_date: str = "",
                     api_key: str = "", ncbi_key: str = "", session=None,
                     receipt=None,
                     f5: bool = True, f5_cap: "int | None" = None,
                     f5_max_deep_comparisons: "int | None" = None,
                     f5_screen: bool = True, f5_cache_dir: "str | None" = None,
                     f5_ranking: str = RANKING_DEFAULT,
                     f7_authorities_root: str = "",
                     f7_verify: str = "sqlite") -> dict:
    """F5 and F7 kwargs for ``judgment_run.run_natural_judgment``, plus ledgers.

    Splat the ``run_kwargs`` entry into the run; keep ``token_ledgers`` to report
    what each stage spent and ``provenance`` to say how it was wired.

    Each check is OPTIONAL AND EXPLICIT. F7 needs a frozen authority set and
    abstains as ``normalization_ambiguous`` without one, so a silently skipped F7
    and a wired F7 that found nothing would otherwise be the same output -- which
    is the failure mode the mass run's reachability attestation exists to catch.
    Ask for F7 without a root and this raises instead.
    """
    from . import sandbox_wiring as sw

    run_kwargs: dict = {}
    ledgers: list = []
    provenance: dict = {"f5_wired": False, "f7_wired": False}

    if f5:
        built = build_production_f5(
            as_of_date=as_of_date, model=model, email=email, api_key=api_key,
            ncbi_key=ncbi_key, session=session, cache_dir=f5_cache_dir,
            cap=f5_cap, max_deep_comparisons=f5_max_deep_comparisons,
            screen=f5_screen, ranking=f5_ranking)
        for key in ("f5_seams", "f5_evidence_builder", "f5_policy"):
            run_kwargs[key] = built[key]
        ledgers.extend(built["token_ledgers"])
        provenance["f5_wired"] = True
        provenance["f5"] = built["provenance"]

    if f7_authorities_root:
        if receipt is None:
            raise WiringError(
                "F7 needs the run's AdapterReceipt: its seam factory binds the "
                "receipt at construction, so it cannot be attached afterwards")
        built = sw.build_f7(root=f7_authorities_root, model=model,
                            api_key=api_key, receipt=receipt, verify=f7_verify)
        for key in ("f7_seams", "f7_evidence_builder", "f7_policy"):
            run_kwargs[key] = built[key]
        ledgers.extend(built["token_ledgers"])
        provenance["f7_wired"] = True
        provenance["f7_authorities"] = built["provenance"]

    return {"run_kwargs": run_kwargs, "token_ledgers": ledgers,
            "provenance": provenance}


def merge_run_usage(token_ledgers):
    """The per-stage token and cost report, in ``sandbox_judge``'s exact shape.

    ``judgment_run`` already lifts the F5 judge's ledger into the manifest's
    ``cost_counters``, which answers "what did F5's judgment cost". It cannot
    answer "what did this run cost, by stage", because the other transports --
    the screen, the verifier, F7's two -- are held by the caller and never
    reach it. ``sandbox_judge`` reports exactly that for one packet; this is the
    same call so a corpus run can report it too.

    Call it AFTER the run. A ledger snapshotted at wiring time is all zeros, and
    a zero that looks like a measurement is worse than a missing one.
    """
    from .recording_adapter import merge_token_ledgers

    return merge_token_ledgers(list(token_ledgers or []))
