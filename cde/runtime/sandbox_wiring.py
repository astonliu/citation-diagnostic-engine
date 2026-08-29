"""F5 and F7 wiring for the single-packet bench, built from the authority manifest.

WHY A SEPARATE MODULE. ``sandbox_judge`` runs a packet; this decides what the
discriminators are allowed to see. Keeping them apart means the wiring can be
read on its own -- which matters, because two of the choices here are the
difference between a fair bench and a dishonest one.

THE TWO HONEST CHOICES, STATED UP FRONT.

1. F5 CANDIDATES COME FROM THE PAPER BANK, NOT PUBMED, and the record says so.
   ``build_pubmed_f5_runtime`` searches live PubMed, which can never return a
   paper that exists only in a hand-authored bank -- so on a synthetic case it
   returns nothing and F5 "finds no supersession" for a reason that has nothing
   to do with the citation. Every other F5 seam here is the production one: the
   same ``judge_contradiction`` and ``verify_contradiction``, the same deployment
   policy, Path A still hard-gated off.

   ``validate_production_f5_configuration`` is therefore NOT called, and must not
   be -- but NOT for the reason it is tempting to give. Its
   ``evidence_builder.production_f5_evidence_builder is True`` gate does not
   mean "these candidates came from PubMed": that attribute is set by
   ``make_f5_evidence_builder`` itself (``f5_seams.py:795``), which wires only
   ``fetch_meta`` and knows nothing about candidate retrieval. The bench calls
   that same production factory, so its builder carries the flag already and the
   gate would pass on its own. The validator is skipped because it would then
   ATTEST a PubMed-backed run that this is not, and no flag is set or cleared to
   arrange that outcome. The bench records
   ``f5_candidate_source: "bench_paper_bank"`` instead, and nothing it produces
   is reportable.

2. F7 IS FULLY PRODUCTION-WIRED, including its validator. Nothing about F7 needs
   to be faked: the frozen authorities are real, the normalizer is the real
   ``FrozenSQLiteAuthorityNormalizer``, and the only synthetic input is the body
   text the user wrote -- which is exactly the input a real run supplies from
   PMC. So ``validate_production_f7_configuration`` IS called here, and a
   misconfiguration is a hard failure.

ONE MODEL, TWO TRANSPORTS. Both bundles refuse ``generator is verifier`` -- that
is an OBJECT identity test, not a model-id test. Production runs both roles on
``claude-opus-5`` (recorded in every judgment_run_manifest's f4/f5/f7 blocks), so
this builds two distinct callables over the same model. The self-verification
that implies is a documented limitation of the production design, not something
the bench introduces; the launch receipt's scope ruling names it.

NO TEMPERATURE. ``anthropic_transport.make_anthropic_call`` omits the parameter
because the pinned model rejects it (DEC-070). Inherited, not re-decided.
"""
from __future__ import annotations

import hashlib
import json
import os

from .anthropic_transport import make_anthropic_call

#: entity_type -> authority, mirroring f7_seams.SUPPORTED_AUTHORITIES. Repeated
#: here only so a manifest naming a different pairing fails loudly rather than
#: constructing a normalizer the validator would reject with a vaguer message.
EXPECTED_AUTHORITIES = {
    "gene": "HGNC", "variant": "ClinVar",
    "drug": "RxNorm", "disease": "MONDO",
}

#: Sections F7 admits. ``f7_entity.SectionText`` refuses anything else, so the
#: bench refuses it here too -- with a message that names the four, rather than
#: letting a packet fail deep inside the assessor.
F7_SECTION_LABELS = ("methods", "results", "table", "figure")

BENCH_F5_CANDIDATE_SOURCE = "bench_paper_bank"
#: Live discovery mode. The packet supplies NO candidates and the PRODUCTION
#: PubMed finder runs, so the bench is exercising real retrieval. Named
#: differently in provenance because the two answer different questions: the bank
#: asks "can F5 judge this pair", live asks "can F5 FIND the pair at all".
LIVE_F5_CANDIDATE_SOURCE = "live_pubmed_candidate_finder"


class WiringError(ValueError):
    """The authority set or the packet cannot support the requested taxonomy."""


def _sha256_file(path, chunk=8 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


# ==========================================================================
# F7
# ==========================================================================
def load_authorities(root: str, *, verify: str = "sqlite") -> tuple:
    """Build the eight source rows from ``manifest.json`` in ``root``.

    Reads the manifest rather than taking hardcoded hashes: the manifest is the
    artifact the build wrote, so a re-freeze changes one file and this keeps
    working. A manifest whose declared hash disagrees with the file on disk is a
    refusal, never a warning.

    ``verify`` controls WHICH files THIS FUNCTION re-hashes -- and that is a
    narrower claim than it looks, so read the next paragraph before relying on it.

    ALL THREE MODES GIVE THE SAME ASSURANCE ON THE F7 PATH, because
    ``FrozenSQLiteAuthorityNormalizer.__init__`` (``f7_seams.py:759``) hashes
    BOTH the snapshot and the index for all four authorities itself,
    unconditionally, against the same manifest-derived digests, on every
    construction. ``build_f7`` constructs that normalizer immediately after this
    returns, so nothing chosen here can weaken the binding and nothing chosen
    here can avoid reading the 5.0 GB of snapshots either. What ``verify``
    actually selects is how much is hashed TWICE and which error message a
    mismatch produces. Measured on the real authority set (5.8 GB of index,
    5.0 GB of snapshot):

    * ``"sqlite"`` (default) -- hash the four indexes here; the normalizer then
      hashes the indexes AGAIN plus the snapshots. 16.6 GB read in total. The
      redundant index pass buys the better message: this one names the entity
      and both digests, the normalizer's says only "sha256 mismatch".
    * ``"all"`` -- also hash the snapshots here, so both are done twice.
      21.6 GB. Buys the same message quality for a snapshot mismatch.
    * ``"none"`` -- hash nothing HERE, leaving the normalizer's 10.8 GB as the
      only pass. Counter-intuitively the FASTEST mode at identical assurance,
      and still not the default: a mode whose documented contract is "trust the
      manifest" must not be what a run gets by accident, and the assurance it
      currently inherits is the normalizer's to keep or drop, not this
      function's to promise.

    Separately, and this part of the original argument does hold: each index
    carries ``metadata.source_snapshot_sha256`` and the normalizer compares it
    to ``source.sha256``, so the index does attest which snapshot it was built
    from. Verified against the real authority set -- all four indexes match
    their lock on every metadata field.
    """
    from ..diagnose.f7_seams import AuthoritySnapshotSource, AuthoritySQLiteIndexSource

    if verify not in {"sqlite", "all", "none"}:
        raise WiringError("verify must be 'sqlite', 'all' or 'none'")
    manifest_path = os.path.join(root, "manifest.json")
    try:
        with open(manifest_path, encoding="utf-8") as fh:
            manifest = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise WiringError(f"cannot read {manifest_path}: {exc}") from exc
    if not manifest.get("complete") is True:
        raise WiringError(
            f"{manifest_path} does not declare complete=true; an incomplete "
            "authority build must not be loaded")

    rows = manifest.get("sources") or []
    by_type = {row.get("entity_type"): row for row in rows}
    if set(by_type) != set(EXPECTED_AUTHORITIES):
        raise WiringError(
            f"manifest declares {sorted(by_type)}; F7 needs exactly "
            f"{sorted(EXPECTED_AUTHORITIES)}")

    sources, indexes, checked = [], [], {}
    for entity_type, authority in sorted(EXPECTED_AUTHORITIES.items()):
        row = by_type[entity_type]
        if row.get("authority") != authority:
            raise WiringError(
                f"{entity_type} is declared as {row.get('authority')!r}, "
                f"expected {authority!r}")
        snap_path = os.path.join(root, row["path"])
        idx_path = os.path.join(root, row["sqlite_index_path"])
        for path in (snap_path, idx_path):
            if not os.path.isfile(path):
                raise WiringError(f"missing authority file: {path}")

        if verify == "all":
            actual = _sha256_file(snap_path)
            if actual != row["sha256"]:
                raise WiringError(
                    f"{entity_type} snapshot hashes {actual}, manifest declares "
                    f"{row['sha256']}")
            checked[os.path.basename(snap_path)] = actual
        if verify in {"sqlite", "all"}:
            actual = _sha256_file(idx_path)
            if actual != row["sqlite_index_sha256"]:
                raise WiringError(
                    f"{entity_type} index hashes {actual}, manifest declares "
                    f"{row['sqlite_index_sha256']}")
            checked[os.path.basename(idx_path)] = actual

        sources.append(AuthoritySnapshotSource(
            entity_type=entity_type, authority=authority,
            version=row["version"], lookup_date=row["lookup_date"],
            path=snap_path, sha256=row["sha256"],
            accept_synonym_as_equivalent=bool(
                row.get("accept_synonym_as_equivalent", True))))
        indexes.append(AuthoritySQLiteIndexSource(
            entity_type=entity_type, path=idx_path,
            sha256=row["sqlite_index_sha256"]))

    return tuple(sources), tuple(indexes), {
        "manifest_path": manifest_path,
        "authority_manifest_schema": manifest.get("schema"),
        "sqlite_index_builder_commit": manifest.get("sqlite_index_builder_commit"),
        "lookup_date": manifest.get("lookup_date"),
        "verify_mode": verify,
        "rehashed": dict(sorted(checked.items())),
    }


def build_f7(*, root: str, model: str, api_key: str = "", receipt,
             verify: str = "sqlite") -> dict:
    """Production F7 seams, policy and evidence builder. Validated before return."""
    from anthropic import Anthropic
    from ..diagnose.evidence_builder import ProductionF7EvidenceBuilder
    from ..diagnose.f7_seams import (FrozenSQLiteAuthorityNormalizer,
                           make_production_f7_policy,
                           make_production_f7_seams,
                           validate_production_f7_configuration)

    sources, indexes, provenance = load_authorities(root, verify=verify)
    normalizer = FrozenSQLiteAuthorityNormalizer(sources, indexes)

    # TWO CLIENTS, TWO CALLABLES. `make_production_f7_seams` rejects
    # `generator_transport is verifier_transport`, and two closures over one
    # client would still be two objects -- but separate clients also keep the
    # two roles' connection state independent, which is what production does.
    generator = make_anthropic_call(
        Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY")), model,
        stage="f7_generator")
    verifier = make_anthropic_call(
        Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY")), model,
        stage="f7_verifier")

    policy = make_production_f7_policy(
        normalizer, generator_model_id=model, verifier_model_id=model)
    seams = make_production_f7_seams(
        generator_transport=generator, verifier_transport=verifier,
        normalizer=normalizer, adapter_receipt=receipt)
    builder = ProductionF7EvidenceBuilder()

    # The real gate, deliberately kept: nothing about F7 here is synthetic
    # except the body text, which is the same kind of input a real run supplies.
    validate_production_f7_configuration(
        seams=seams, evidence_builder=builder, policy=policy,
        adapter_receipt=receipt)

    return {"f7_seams": seams, "f7_evidence_builder": builder,
            "f7_policy": policy, "provenance": provenance,
            # F7's evidence prompt gained a cache breakpoint after its body
            # sections (f7_evidence_v3), and one claim makes one evidence request
            # PER CLAIMED TUPLE over the same sections. Whether the second
            # request actually reads the first one's write is only knowable from
            # cache_read_input_tokens, so the transports' ledgers come back too.
            "token_ledgers": [generator.token_ledger, verifier.token_ledger]}


def fulltext_from_packet(packet: dict):
    """``pmid -> fulltext dict`` in the exact shape the F7 builder demands.

    ``ProductionF7EvidenceBuilder`` enforces four things and holds
    ``evidence_source_insufficient`` if any fails: the dict's ``pmid`` equals the
    item's ``cited_pmid``, ``resolved`` is True, ``retrieval_complete`` is True,
    and every section's ``content_sha256`` equals ``sha256(text)``. The hash is
    computed here rather than asked of the user -- a hand-typed digest would
    silently disable F7 for that packet.

    A synthetic body is not a shortcut: it is the same object a real run builds
    from PMC, which is what lets the assessor be asked a fair question about it.
    """
    sections = packet.get("cited_sections") or []
    if not sections:
        raise WiringError("F7 needs cited_sections; it asserts nothing without body text")
    rows = []
    for i, sec in enumerate(sections):
        label = str((sec or {}).get("label") or "").strip().lower()
        text = (sec or {}).get("text")
        if label not in F7_SECTION_LABELS:
            raise WiringError(
                f"cited_sections[{i}].label {label!r} is not one of "
                f"{list(F7_SECTION_LABELS)}; F7 reads body evidence only, never "
                "the abstract, introduction or discussion")
        if not isinstance(text, str) or not text.strip():
            raise WiringError(f"cited_sections[{i}].text is empty")
        rows.append({
            "label": label,
            "title": str((sec or {}).get("title") or ""),
            "text": text,
            "content_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        })

    pmcid = str(packet.get("cited_pmcid") or "").strip()
    if not pmcid:
        raise WiringError(
            "F7 needs cited_pmcid: the builder cross-binds the cited PMID to a "
            "resolved PMCID and treats an unresolved work as no evidence")

    def fetch_fulltext(pmid):
        return {
            "pmid": str(pmid or ""),
            "pmcid": pmcid,
            "resolved": True,
            "sections": rows,
            "sections_present": sorted({r["label"] for r in rows}),
            "retrieval_complete": True,
            "incomplete_reasons": [],
            "sanitized_paths": [],
            "source": "bench_packet",
        }

    return fetch_fulltext


# ==========================================================================
# F5
# ==========================================================================
def _bench_date_precision(date_text: str) -> str:
    """``day``/``month``/``year`` for a bench bank row, from the date's OWN shape.

    ``f5_evidence_store`` requires a precision and refuses a blank one, and
    ``build_f5`` never set it -- so every F5 run through the sandbox died at
    ``invalid publication_date_precision`` before the first judgment. The
    precision is DERIVED, never defaulted: claiming day precision for a packet
    that only gave a year would tell the recency comparison it knows a day it
    does not, and F5's whole question is which paper came later.
    """
    parts = str(date_text or "").strip().split("-")
    if len(parts) >= 3 and all(parts[:3]):
        return "day"
    if len(parts) == 2 and all(parts):
        return "month"
    return "year"


def _bench_positive_int(packet: dict, key: str, default,
                        *, allow_zero: bool = False):
    """A packet integer knob, or ``default`` when absent. Refuses junk loudly.

    A mistyped cap that fell back to the default silently would make two runs
    look comparable when one of them searched a different depth -- the exact
    class of quiet substitution this module's docstring is about.
    """
    if key not in packet or packet[key] is None:
        return default
    value = packet[key]
    floor = 0 if allow_zero else 1
    if isinstance(value, bool) or not isinstance(value, int) or value < floor:
        raise WiringError(
            f"{key} must be an integer >= {floor}, not {value!r}")
    return value


def build_f5(*, packet: dict, model: str, api_key: str = "") -> dict:
    """F5 seams and evidence builder, with candidates served from the bank.

    Production wiring in every respect except the candidate source, and that one
    substitution is recorded in the returned provenance so no reader can mistake
    a bench result for a PubMed one. See this module's docstring for why the
    production validator is deliberately not called.
    """
    from anthropic import Anthropic
    from ..diagnose.candidate_screen import (
        CANDIDATE_SCREEN_PROMPT_VERSION, make_candidate_screen)
    from ..diagnose.f5_seams import CANDIDATE_CAP, build_f5_seams, make_f5_evidence_builder
    from ..diagnose.supersession import F5Policy

    as_of = str(packet.get("f5_as_of_date") or "").strip()
    if not as_of:
        raise WiringError(
            "F5 needs f5_as_of_date: supersession asks whether a superseding "
            "work existed AT A POINT IN TIME and has no answer without one")

    bank, key_by_paper_id = {}, {}
    raw_candidates = packet.get("f5_candidates") or []
    if not isinstance(raw_candidates, list):
        raise WiringError("f5_candidates must be a list of paper objects")
    for position, paper in enumerate(raw_candidates):
        if not isinstance(paper, dict):
            raise WiringError(
                f"f5_candidates[{position}] is not an object; a candidate the "
                "bench cannot read is a refusal, not an empty bank row")
        # A COLLIDING id OR pmid IS A REFUSAL, not a last-write-wins overwrite:
        # two bank rows folding into one would silently shrink the candidate set
        # the packet declares, which is the same dishonesty as dropping an id.
        paper_id = paper.get("id")
        if paper_id in key_by_paper_id:
            raise WiringError(
                f"f5_candidates has two papers with id {paper_id!r}; bank ids "
                "must be unique or a candidate is silently lost")
        # The bank row is shaped like a fetched PubMed record on purpose: the
        # seams must not be able to tell a synthetic candidate from a real one.
        key = str(paper.get("pmid") or f"BENCH:{paper_id}")
        if key in bank:
            raise WiringError(
                f"f5_candidates has two papers keyed {key!r}; a repeated pmid "
                "would collapse two candidates into one bank row")
        bank[key] = {
            "id": key, "pmid": paper.get("pmid") or "",
            "title": paper.get("title") or "",
            "abstract": paper.get("abstract") or "",
            "journal": paper.get("journal") or "",
            "pub_date": str(paper.get("year") or "")[:10],
            "pub_date_precision": _bench_date_precision(
                str(paper.get("year") or "")[:10]),
            # classify_evidence_tier reads THESE two and nothing else. Without
            # them every bench paper lands on UNCLASSIFIED_TIER, which stores as
            # "preprint_unreviewed" -- a floor that can never outrank anything,
            # so a real RCT superseder silently loses its standing.
            "publication_types": list(paper.get("publication_types") or []),
            "mesh_terms": list(paper.get("mesh_terms") or []),
            # Independence is judged from author overlap. Without authors every
            # candidate comes back independent="unknown" /
            # "author_info_missing", and F5 requires an INDEPENDENT superseder --
            # so the gate could never be exercised either way.
            "authors": list(paper.get("authors") or []),
            "doi": paper.get("doi") or "",
            "bench_paper_id": paper_id,
        }
        key_by_paper_id[paper_id] = key

    # THE CITED WORK IS A BANK ROW TOO, and it has to be: production's
    # ``make_f5_evidence_builder`` calls ``fetch_meta(item["cited_pmid"])`` and
    # raises if it comes back empty, so a bank holding only the candidates makes
    # every F5 run die before the first judgment. The row is built from the
    # packet's own claimed identity and abstract -- the same text coverage sees.
    cited = packet.get("cited_claimed") or {}
    cited_pmid = str(cited.get("claimed_pmid") or "").strip()
    if not cited_pmid:
        raise WiringError(
            "F5 needs cited_claimed.claimed_pmid: supersession is judged "
            "against the cited work, which has to be identified to be fetched")
    cited_date = str(packet.get("cited_pub_date")
                     or cited.get("year") or "").strip()
    if not cited_date:
        raise WiringError(
            "F5 needs the cited work's publication date (cited_pub_date, or "
            "cited_claimed.year): 'was there a later paper' has no answer "
            "without the date the comparison is against")
    if cited_pmid in bank:
        raise WiringError(
            f"f5_candidates contains the cited work itself (PMID {cited_pmid}); "
            "a paper cannot supersede itself -- remove it from the bank")
    bank[cited_pmid] = {
        "id": cited_pmid, "pmid": cited_pmid,
        "title": cited.get("title") or "",
        "abstract": (packet.get("cited_abstract") or ""),
        "journal": cited.get("journal") or "",
        "pub_date": cited_date[:10],
        "pub_date_precision": _bench_date_precision(cited_date[:10]),
        "publication_types": list(packet.get("cited_publication_types") or []),
        "mesh_terms": list(packet.get("cited_mesh_terms") or []),
        "authors": list(packet.get("cited_authors") or []),
        "doi": str(cited.get("claimed_doi") or ""),
        "bench_paper_id": None,
        "bench_role": "cited_work",
    }

    # ``f5_candidate_ids``, WHEN PRESENT, IS THE RETRIEVAL RESULT. An id naming a
    # paper the bank does not hold is refused, never dropped: quietly judging
    # against a smaller candidate set than the packet declares would answer a
    # different question than the one asked, and the record would not say so.
    declared = packet.get("f5_candidate_ids")
    if declared is None:
        # PACKET ORDER, not sorted order. Insertion order is already total and
        # deterministic; sorting would have to compare ids that may be ints,
        # strings or absent, and a bench is not the place to invent an ordering
        # over a type union.
        retrieved = tuple(key_by_paper_id.values())
    else:
        if not isinstance(declared, list):
            raise WiringError("f5_candidate_ids must be a list of bank paper ids")
        absent = [i for i in declared if i not in key_by_paper_id]
        if absent:
            raise WiringError(
                f"f5_candidate_ids names paper id(s) {absent}, which the bank "
                f"does not hold (f5_candidates ids: "
                f"{sorted(key_by_paper_id, key=repr)}); add them or drop the "
                "ids rather than judging against papers that do not exist")
        retrieved = tuple(key_by_paper_id[i] for i in declared)

    def fetch_meta(work_id):
        row = bank.get(str(work_id or "").strip())
        return dict(row) if row else None

    def fetch_abstract(work_id):
        return str((fetch_meta(work_id) or {}).get("abstract") or "")

    def search_candidates(*_args, **_kwargs):
        """The declared candidate ids, in the packet's order. No network.

        No ranking and no cap: a bench with three candidates has no retrieval
        problem to model, and a fake relevance score would be the one number
        here that means nothing. The cited work's own bank row is never
        returned -- it is there for ``fetch_meta``, not as a candidate.
        """
        # BANK ROWS, NOT BANK KEYS. make_retrieve_superseding_candidates skips
        # every hit that is `not isinstance(hit, dict)`, so returning ids here
        # dropped all candidates silently and every F5 run reported
        # retrieval_empty -- a CONFIDENT-LOOKING "no later evidence exists" for a
        # search that never examined one row. The rows are copied so a seam
        # cannot mutate the bank between claims.
        return tuple(dict(bank[key]) for key in retrieved)

    live_discovery = bool(packet.get("f5_live_discovery"))
    if live_discovery:
        if raw_candidates:
            raise WiringError(
                "f5_live_discovery with a nonempty f5_candidates would judge a "
                "mixture of discovered and hand-fed papers and the record could "
                "not say which was which; supply one or the other")
        from ..diagnose.candidate_finder import PubMedCandidateFinder
        from ..refs.ncbi_meta import DEFAULT_EMAIL
        # `f5_ranking` selects how the three retrieval streams are fused.
        # Default is the shipped `multi_stream_first`; `rrf` is the cap-invariant
        # alternative. Recorded in provenance AND in the finder's query_hash, so
        # two runs of the same packet under different fusions are never confused.
        from ..diagnose.candidate_finder import RANKINGS, RANKING_DEFAULT
        ranking = str(packet.get("f5_ranking") or RANKING_DEFAULT).strip()
        if ranking not in RANKINGS:
            raise WiringError(
                f"f5_ranking must be one of {sorted(RANKINGS)}, not {ranking!r}")
        finder = PubMedCandidateFinder(
            email=str(packet.get("f5_mailto") or "").strip() or DEFAULT_EMAIL,
            cache_dir=str(packet.get("f5_cache_dir") or "") or None,
            ranking=ranking)

        def search_candidates(cited_meta, claim, *, after_date, as_of_date,
                              cap: int = CANDIDATE_CAP):
            """Production finder, with every hit ADMITTED TO THE BANK.

            ``fetch_meta``/``fetch_abstract`` above answer only from the bank, and
            the assessor needs both for each candidate it deep-compares. A
            discovered PMID would otherwise resolve to None and the pair would
            die as unassessable for a reason that has nothing to do with the
            science. The real CandidateSearchResult is returned UNCHANGED so its
            ok/partial/failure status and query_hash still reach the record --
            the bank path had no notion of a partial search, and a partial search
            is exactly what must not become a confident negative.
            """
            result = finder.search_candidates(
                cited_meta, claim, after_date=after_date, as_of_date=as_of_date,
                cap=cap)
            for hit in result.hits:
                key = str((hit or {}).get("id") or "").strip()
                if key and key not in bank:
                    bank[key] = dict(hit)
            return result

    # F5 NEEDS A BIGGER OUTPUT BUDGET THAN THE 1024 DEFAULT. Its contradiction
    # judgment returns TWO verbatim spans (the cited finding and the candidate's
    # contradiction) plus a rationale, and a clinical-trial abstract sentence is
    # long. At 1024 the response came back empty and the stage died on
    # JSONDecodeError "Expecting value: line 1 column 1", which reads like a
    # malformed model rather than a truncated budget.
    generator = make_anthropic_call(
        Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY")), model,
        max_tokens=8192, stage="f5_generator")
    verifier = make_anthropic_call(
        Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY")), model,
        max_tokens=8192, stage="f5_verifier")

    # THE CANDIDATE SCREEN, ON BY DEFAULT. It is the reason the candidate cap can
    # rise at all: without it every structurally admissible candidate goes
    # straight to the deep comparison and retrieval depth costs money linearly.
    # `"f5_candidate_screen": false` turns it off, which is what makes the
    # screened and unscreened cost of the same packet comparable.
    #
    # A THIRD TRANSPORT, NOT THE GENERATOR'S. Sharing it would put screen calls
    # and judgment calls in one token ledger and the run could not say what the
    # screen cost -- which is the number this wiring exists to produce.
    #
    screen_enabled = packet.get("f5_candidate_screen") is not False
    cap = _bench_positive_int(packet, "f5_candidate_cap", CANDIDATE_CAP)
    max_deep = _bench_positive_int(
        packet, "f5_max_deep_comparisons", None, allow_zero=True)

    # THE OUTPUT BUDGET SCALES WITH THE BATCH, AND THEREFORE STREAMS. One call
    # for the whole batch means one reply for the whole batch: about 40 tokens of
    # JSON per candidate, so 400 candidates is ~16K of answer -- and on a model
    # with adaptive thinking on by default, the reasoning tokens come out of the
    # SAME max_tokens. A first attempt at a flat 32768 was cut off mid-string at
    # candidate ~298 of 400, and a truncated reply is not a degraded screen: it
    # is a JSONDecodeError that discards the whole batch, so the run pays for the
    # screen AND for every deep comparison the screen was meant to avoid.
    #
    # 160 tokens per candidate is ~4x the JSON a row needs, with the remainder
    # left for reasoning. Above the SDK's non-streaming ceiling of 21333 this is
    # necessarily a streaming transport (NONSTREAMING_MAX_TOKENS_CEILING).
    screen_max_tokens = min(120_000, max(16_384, 160 * cap))
    screen_transport = None
    screen_candidates = None
    if screen_enabled:
        screen_transport = make_anthropic_call(
            Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY")),
            model, max_tokens=screen_max_tokens,
            stage="f5_candidate_screen", stream=True)
        screen_candidates = make_candidate_screen(screen_transport)

    seams = build_f5_seams(
        fetch_meta=fetch_meta, fetch_abstract=fetch_abstract,
        search_candidates=search_candidates,
        complete=generator, verifier_complete=verifier,
        cap=cap, screen_candidates=screen_candidates,
        judgment_model_id=model, verifier_model_id=model)

    # Path A stays hard-gated off, exactly as production requires. Deployment
    # mode with a distinct verifier is what makes Path B detection meaningful;
    # neither is relaxed for the bench.
    #
    # candidate_screen_enabled MUST agree with the wiring or the detector's own
    # constructor refuses (f5_supersession.py:2119) -- the flag is not a request,
    # it is an assertion about what was wired, so it is derived from the same
    # value rather than set beside it.
    policy = F5Policy(mode="deployment", deploy_path_a=False,
                      candidate_screen_enabled=screen_candidates is not None,
                      max_deep_comparisons=max_deep,
                      generator_model_id=model, verifier_model_id=model)

    return {
        "f5_seams": seams,
        "f5_evidence_builder": make_f5_evidence_builder(fetch_meta, as_of_date=as_of),
        "f5_policy": policy,
        # LIVE LEDGERS, NOT NUMBERS, and deliberately outside "provenance":
        # provenance is built before the run and serialized after it, so a total
        # captured here would be zero. The caller snapshots these once the run
        # is over. Not a run kwarg -- sandbox_judge splats only the three keys
        # above.
        "token_ledgers": [
            transport.token_ledger for transport in
            (generator, verifier, screen_transport) if transport is not None],
        "provenance": {
            "f5_candidate_source": (LIVE_F5_CANDIDATE_SOURCE if live_discovery
                                    else BENCH_F5_CANDIDATE_SOURCE),
            "f5_as_of_date": as_of,
            # The screen's presence is provenance, not configuration: a reader
            # comparing two runs of the same packet has to be able to see which
            # one paid for a triage and which one deep-read everything.
            "f5_candidate_screen": (
                {"enabled": True,
                 "prompt_version": CANDIDATE_SCREEN_PROMPT_VERSION,
                 "max_output_tokens": screen_max_tokens,
                 "render_log": screen_candidates.render_log}
                if screen_candidates is not None else {"enabled": False}),
            "f5_candidate_cap": cap,
            "f5_ranking": (ranking if live_discovery else "not_applicable"),
            "f5_max_deep_comparisons": max_deep,
            "bank_papers": len(bank),
            # In live mode the count is not known at wiring time -- the finder has
            # not run yet -- and reporting the bank's 0 would read as "retrieval
            # returned nothing", which is the exact false negative this bench
            # already produced once. `None` says "ask the record".
            "candidates_retrieved": (None if live_discovery else len(retrieved)),
            "cited_work_from_packet": cited_pmid,
            "production_validator_called": False,
            "reportable": False,
            "note": (("Candidates came from the PRODUCTION PubMed finder over "
                      "live HTTP; the paper bank held only the cited work. Every "
                      "other seam is the production one. Nothing from this run "
                      "is reportable.") if live_discovery else
                     ("Candidates came from the hand-authored paper bank, not "
                      "PubMed. Every other seam is the production one. Nothing "
                      "from this run is reportable.")),
        },
    }
