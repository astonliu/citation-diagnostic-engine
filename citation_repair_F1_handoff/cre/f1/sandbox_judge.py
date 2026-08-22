"""Single-packet F3-F7 judgment sandbox: the production engine, one hand-authored pair.

WHY THIS EXISTS. Every reportable F3-F7 number comes out of
``run_natural_judgment`` over a frozen corpus. That is the right instrument for a
corpus and the wrong one for a question like "what would the band do with THIS
claim against THIS evidence". Answering that by reading the prompts and reasoning
about them is worthless -- it measures the reader, not the engine. So this module
runs the SAME seams the production launcher wires, over one packet the user
authored, and prints the durable record verbatim.

WHAT IT IS NOT. It is not a launcher and it produces nothing reportable. It skips
every corpus-level gate on purpose: no frozen-corpus verification, no pre-band
disposition, no hash chain, no join accounting, no reportability check. A packet
is authored by hand, so the population it belongs to is "one", and no rate can be
computed from it. Use it to interrogate the engine's behavior, never to produce a
figure.

ADAPTER IDENTITY IS PRODUCTION'S. ``band_prompts.make_anthropic_call`` sends no
temperature parameter, because the pinned Opus model rejects one --
``production_launcher`` records that first-party measurement as the string
``"unsupported"`` (DEC-070), never as ``0``. This module inherits that exactly.
Pinning ``temperature=0`` here would make the sandbox answer a question about a
DIFFERENT adapter than the one production runs, which is the one thing a sandbox
must not do. Determinism comes from the pinned prompt versions and the frozen
authorities, not from a temperature argument.

WHICH TAXONOMIES RUN. Exactly the ones whose seams are supplied, which is the
same rule ``judge_pair`` enforces for the corpus path. An unwired discriminator
stays silent -- it is never handed a confident negative. Coverage (and therefore
F6) always runs; F4 needs a discriminator callable; F3 additionally needs the
cited work's reference list; F5 needs its seam bundle and evidence builder; F7
needs its seam bundle, evidence builder, and the frozen SQLite authorities.

F7 ALSO MOVES COVERAGE. Its body text is the same ``fetch_fulltext`` seam the
full-text coverage path reads, so selecting F7 shifts coverage off the cited
abstract and onto the retrieved body: the v3 judge, the ``coverage_v3`` prompt
version and the full-text parser, one model call per claim. That is production's
own coupling, not a sandbox convenience -- ``judge_pair_coverage`` branches on
``fetch_fulltext`` alone. It is why F7 is the one selection that changes what a
DIFFERENT taxonomy (F6, the coverage route) is asked.

NO NETWORK IN THE DEFAULT PATH except the model calls the selected taxonomies
make, each one receipted through the same ``AdapterReceipt`` production uses.

READ THE RECEIPT PER SEAM, NOT ITS TOTAL, AND DO NOT READ THE RECORD'S LEDGER AT
ALL. ``receipt.summary()["total_calls"]`` counts every seam INVOCATION, including
``fetch_abstract``, which this module serves from the packet and which costs
nothing -- the paid count is the sum of the model-backed seams. The record's own
``paid_calls`` ledger is worse than loose: ``judgment_run._count_paid_call`` has
exactly two live call sites and both book ``"claim_extraction"`` (the third sits
in ``_run_with_retry``, which nothing calls), so coverage, F3, F4, F5 and F7's
assessor calls are never booked. On the F7 packet the ledger reports ``total: 1``
against seven paid calls the receipt names one by one. That gap is production's
and is left alone here; it is reported, not patched, because a sandbox that
edited the engine to make its own output tidy would stop measuring the engine.

Usage:
    python -m cre.f1.sandbox_judge packet.json --model claude-opus-5
    python -m cre.f1.sandbox_judge packet.json --taxonomies F4,F6 --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from . import judgment_run as jr
from . import terminal_outcome as tox
from .band_prompts import (
    CLAIM_EXTRACT_PROMPT_VERSION,
    COVERAGE_PROMPT_VERSION,
    make_anthropic_call,
    make_coverage_judge,
    make_extractor,
)
from .coverage_prompts_v3 import (
    COVERAGE_PROMPT_VERSION_V3,
    make_coverage_judge_v3,
)
from .recording_adapter import AdapterReceipt
from . import sandbox_wiring as sw

#: ONE receipt per run, shared by the coverage seams and the F7 bundle. F7's
#: factory binds the receipt at construction, so it has to exist before the
#: wiring is built -- hence a memoized accessor rather than a local.
_RECEIPT = {}


def jr_receipt(model: str) -> AdapterReceipt:
    if "r" not in _RECEIPT:
        _RECEIPT["r"] = AdapterReceipt(
            model=model, temperature=jr.TEMPERATURE_UNSUPPORTED)
    return _RECEIPT["r"]

#: The taxonomies a packet may select. F6 is not separately wirable: it is the
#: coverage route, so it is on whenever coverage runs, which is always.
SELECTABLE = ("F3", "F4", "F5", "F7")

#: Mirrors production_launcher's resolved value for the pinned model. Kept as a
#: module constant rather than a literal so a reader can see it is a decision.
TEMPERATURE_RECORDED = jr.TEMPERATURE_UNSUPPORTED

PACKET_SCHEMA = "cre_sandbox_packet_v1"


class PacketError(ValueError):
    """The packet cannot be turned into a judgeable item."""


def _require(packet: dict, key: str):
    value = packet.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise PacketError(f"packet is missing required field {key!r}")
    return value


def build_item(packet: dict) -> dict:
    """The judgeable unit, in the exact shape ``judgment_band.build_item`` emits.

    Constructed directly rather than by parsing XML: a hand-authored packet has
    no source document. Every key the corpus path sets is set here, with the same
    default for the ones a single pair cannot have (no co-citation group, no
    marker inference), so a record produced from a packet is readable against a
    record produced from a real run without reconciling two shapes.
    """
    if packet.get("schema") != PACKET_SCHEMA:
        raise PacketError(
            f"packet schema {packet.get('schema')!r} is not {PACKET_SCHEMA!r}")
    citation_id = _require(packet, "citation_id")
    cited = packet.get("cited_claimed") or {}
    if not isinstance(cited, dict):
        raise PacketError("cited_claimed must be an object")
    return {
        "item_key": citation_id,
        "citation_id": citation_id,
        "citing_pmcid": packet.get("citing_pmcid") or "",
        "citing_pmid": packet.get("citing_pmid") or "",
        "citing_title": packet.get("citing_title") or "",
        "citing_sentence": _require(packet, "citing_sentence"),
        **({"citing_source_section": packet["citing_source_section"]}
           if packet.get("citing_source_section") else {}),
        # A hand-authored pair is a singleton by construction. Empty is read
        # downstream as "no co-citation context", which is the truth here, and
        # is the same value the corpus path gives an uncited-alone reference.
        "citance_group_id": "",
        "citance_group_members": [],
        "citance_group_inferred_members": [],
        "citance_marker_inferred": False,
        "cited_marker": packet.get("cited_marker") or "",
        "cited_pmid": str(cited.get("claimed_pmid") or ""),
        "cited_claimed": {
            "title": cited.get("title") or "",
            "authors": list(cited.get("authors") or []),
            "year": cited.get("year"),
            "journal": cited.get("journal") or "",
            "claimed_pmid": str(cited.get("claimed_pmid") or ""),
            "claimed_doi": str(cited.get("claimed_doi") or ""),
        },
        "cited_is_review": packet.get("cited_is_review"),
        "atomic_claims": [],
        "evidence": {},
        "coverage_verdicts": [],
        "proposed_route": None,
        "proposed_verdict": None,
    }


def _abstract_fetcher(packet: dict):
    """``pmid -> abstract`` served from the packet, never from the network.

    The packet IS the evidence: a sandbox that fetched a live abstract would be
    answering a question about today's PubMed rather than about the text the user
    put in front of the judge.
    """
    abstract = (packet.get("cited_abstract") or "").strip()

    def fetch_abstract(_pmid):
        return abstract or None

    return fetch_abstract


def _reflist_fetcher(packet: dict):
    """``pmid -> tuple`` over the CITED work's reference list, for F3.

    F3 is provenance-only: it asks whether the citing sentence attributes to this
    work something the work itself attributed to another. That question is
    unanswerable without the cited work's own bibliography, which is why F3 is
    wired separately from coverage.
    """
    rows = packet.get("cited_reference_list")
    if rows is None:
        return None
    if not isinstance(rows, list):
        raise PacketError("cited_reference_list must be a list")

    def fetch_reflist(_pmid):
        return tuple(rows)

    return fetch_reflist


def _selected(packet: dict, override: "str | None") -> tuple:
    raw = (override.split(",") if override
           else packet.get("taxonomies") or [])
    picked = tuple(t.strip().upper() for t in raw if t and t.strip())
    unknown = [t for t in picked if t not in SELECTABLE]
    if unknown:
        raise PacketError(
            f"unselectable taxonomies {unknown}; choose from {list(SELECTABLE)} "
            "(F6 is the coverage route and always runs)")
    return picked


def judge(packet: dict, *, model: str, api_key: str = "",
          taxonomies: "str | None" = None, dry_run: bool = False,
          authorities_root: str = "", verify: str = "sqlite") -> dict:
    """Run one packet through the real engine. Returns ``(record, receipt)``.

    ``dry_run`` builds the item and resolves the seam wiring WITHOUT making a
    single model call, and returns the plan instead of a record. It is the honest
    answer to "what would this run do" when no key is present: an empty record
    would look like a verdict.
    """
    item = build_item(packet)
    picked = _selected(packet, taxonomies)
    fetch_reflist = _reflist_fetcher(packet)

    if "F3" in picked and fetch_reflist is None:
        raise PacketError(
            "F3 selected but the packet carries no cited_reference_list; F3 is "
            "provenance-only and cannot be asked without the cited work's own "
            "bibliography")

    plan = {
        "citation_id": item["citation_id"],
        "model": model,
        "temperature_recorded": TEMPERATURE_RECORDED,
        "claim_extract_prompt_version": CLAIM_EXTRACT_PROMPT_VERSION,
        # NAME THE VERSION THE RUN WILL ACTUALLY STAMP. F7 supplies a retrieved
        # body, which moves coverage off the cited abstract and onto the full
        # text: judgment_run OVERWRITES both the prompt and the parser version on
        # that path. A plan printing the abstract version for a full-text run
        # would be the same false provenance stamp that overwrite exists to
        # prevent -- and the plan is the only thing --dry-run gives a reader.
        "coverage_prompt_version": (COVERAGE_PROMPT_VERSION_V3 if "F7" in picked
                                    else COVERAGE_PROMPT_VERSION),
        "coverage_evidence_scope": ("cited full text (F7 supplies the body)"
                                    if "F7" in picked else "cited abstract"),
        "coverage_and_F6": "always on",
        "selected": list(picked),
        "silent_unwired": [t for t in SELECTABLE if t not in picked],
        "evidence_from": "packet (no network fetch)",
    }
    # F5/F7 wiring is resolved BEFORE the dry-run return, so --dry-run proves
    # the authorities load, the validators pass and the packet is sufficient --
    # all without a model call. A dry run that skipped this would report a plan
    # for a run that cannot start.
    extra, prov = {}, {}
    # EVERY PACKET-LEVEL REFUSAL COMES FIRST, before anything expensive. On the
    # default verify="sqlite" the F7 authority load hashes 16.6 GB -- 5.8 of
    # SQLite index in load_authorities, then the same 5.8 again plus 5.0 of JSON
    # snapshot inside FrozenSQLiteAuthorityNormalizer -- so validating the packet
    # after it would make a mistyped section label cost minutes to learn about.
    # Same reason F5 (two clients, no disk) is wired before F7, not after it.
    fetch_fulltext = sw.fulltext_from_packet(packet) if "F7" in picked else None
    if "F5" in picked:
        f5 = sw.build_f5(packet=packet, model=model, api_key=api_key)
        extra.update({k: f5[k] for k in
                      ("f5_seams", "f5_evidence_builder", "f5_policy")})
        prov["f5"] = f5["provenance"]
    if "F7" in picked:
        f7 = sw.build_f7(root=authorities_root, model=model, api_key=api_key,
                         receipt=jr_receipt(model), verify=verify)
        extra.update({k: f7[k] for k in
                      ("f7_seams", "f7_evidence_builder", "f7_policy")})
        extra["fetch_fulltext"] = fetch_fulltext
        prov["f7"] = f7["provenance"]
    if prov:
        plan["wiring_provenance"] = prov

    if dry_run:
        return {"dry_run": True, "plan": plan, "item": item,
                "wired_seams": sorted(extra)}

    # ONE receipt for the whole run, constructed exactly as the launcher does:
    # the model is fixed at construction and the resolved temperature is the
    # string production records, so a call carrying a temperature key at all
    # would show up as unauthorized rather than being absorbed.
    from anthropic import Anthropic
    client = Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))
    call_llm = make_anthropic_call(client, model)

    seams = {
        "extractor": make_extractor(call_llm),
        "coverage_judge": make_coverage_judge(call_llm),
        # THE FULL-TEXT COVERAGE JUDGE, PAIRED WITH fetch_fulltext ABOVE. Wiring
        # a body without the judge that reads it is not a degraded run, it is a
        # crash: judge_pair_coverage takes the full-text branch on fetch_fulltext
        # alone and calls coverage_judge_v3 unconditionally, so a None arrives at
        # judgment_band.coverage_verdicts as `None(claims, evidence)`. Derived
        # from the SAME condition as fetch_fulltext so the two cannot diverge;
        # the gate below is the backstop if a later edit separates them.
        "coverage_judge_v3": (make_coverage_judge_v3(call_llm)
                              if fetch_fulltext is not None else None),
        "fetch_abstract": _abstract_fetcher(packet),
        "fetch_reflist": fetch_reflist,
        # F4 and F3 share the discriminator callable, exactly as run_band wires
        # it. Supplying it is what turns F4 on; F3 additionally needs the two
        # reflist/pmcid seams below.
        "discriminator_call_llm": call_llm if picked else None,
        "f3_fetch_reflist": fetch_reflist if "F3" in picked else None,
    }
    receipt = jr_receipt(model)
    wired = receipt.wrap_all(seams)

    # THE PAIRING GATE run_natural_judgment APPLIES, applied here too. The bench
    # calls judge_pair DIRECTLY, and judge_pair takes coverage_judge_v3=None
    # without complaint -- every guard against half-enabling the full-text path
    # lives in the launchers this module deliberately skips. Same rule and same
    # wording as run_band's and run_natural_judgment's gate, because the failure
    # it prevents is identical: judging full text with the abstract-scoped
    # prompt, or fetching a body nothing reads.
    if (extra.get("fetch_fulltext") is None) != (wired["coverage_judge_v3"] is None):
        raise PacketError(
            "the full-text path needs BOTH fetch_fulltext and coverage_judge_v3; "
            "supplying one alone would silently judge full text with the "
            "abstract-scoped prompt, or fetch a body nothing reads")

    record = jr.judge_pair(
        item,
        extractor=wired["extractor"],
        coverage_judge=wired["coverage_judge"],
        coverage_judge_v3=wired["coverage_judge_v3"],
        fetch_abstract=wired["fetch_abstract"],
        fetch_reflist=wired["fetch_reflist"],
        discriminator_call_llm=(
            wired["discriminator_call_llm"]
            if ("F4" in picked or "F3" in picked) else None),
        f3_fetch_reflist=wired["f3_fetch_reflist"],
        **extra,
    )

    outcome, reason = tox.resolve(record)
    tox.assert_valid(outcome, reason)
    record["terminal_outcome"] = outcome
    record["terminal_reason"] = reason
    record["human_review_required"] = tox.is_human_review(outcome)
    return {"plan": plan, "record": record, "receipt": receipt.summary()}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="cre.f1.sandbox_judge",
        description="Run one hand-authored packet through the real F3-F7 band.")
    parser.add_argument("packet", help="path to a cre_sandbox_packet_v1 JSON file")
    parser.add_argument("--model", default="claude-opus-5",
                        help="model id; recorded, and sent with NO temperature")
    parser.add_argument("--taxonomies", default=None,
                        help="comma list overriding the packet, e.g. F4,F3")
    parser.add_argument("--dry-run", action="store_true",
                        help="resolve wiring and print the plan; zero model calls")
    parser.add_argument("--api-key", default="",
                        help="defaults to $ANTHROPIC_API_KEY")
    parser.add_argument("--authorities", default="",
                        help="folder holding manifest.json + the four snapshots "
                             "and sqlite_indexes/ (required for F7)")
    parser.add_argument("--verify", default="sqlite",
                        choices=("sqlite", "all", "none"),
                        help="which authority files to re-hash; 'sqlite' skips "
                             "the 5.0 GB of snapshots the indexes already attest")
    args = parser.parse_args(argv)

    with open(args.packet, encoding="utf-8") as fh:
        packet = json.load(fh)

    try:
        result = judge(packet, model=args.model, api_key=args.api_key,
                       taxonomies=args.taxonomies, dry_run=args.dry_run,
                       authorities_root=args.authorities, verify=args.verify)
    except (PacketError, sw.WiringError) as exc:
        # A malformed packet is a user error, not a verdict. Fail loudly rather
        # than emitting a record that would read as a judgment.
        print(f"[sandbox-packet-error] {exc}", file=sys.stderr)
        return 2

    print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
