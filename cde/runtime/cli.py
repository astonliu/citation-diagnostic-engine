"""``python -m cde.runtime.cli`` -- run the two bands over a directory of PMC XML.

WHAT THIS IS NOT, AND THE DISTINCTION MATTERS. It is not
:func:`production_launcher.launch_full`. That function refuses to start unless a
corpus manifest verifies, the model appears in a DECISION-backed allowlist, the
tree matches its recorded commit, the temperature and prefill paths are governed
and the judge is a different family from the generator. Those checks are what
make a run's numbers REPORTABLE, and none of them is convenience -- each exists
because a run without it produced a number somebody nearly believed.

This entry point deliberately skips them, so:

    USE IT TO SEE THE SYSTEM WORK ON A DOCUMENT. Do not use it to produce a
    figure. Every record it writes is real, and the population it was computed
    over is not governed, so no rate over that population is defensible.

It exists because the alternative was worse: a quickstart that hand-rolled the
wiring would drift from the launcher's, and a reader's first run would exercise
something this project does not itself use. The seams below are built exactly as
the band builds them -- the same extractor, the same tri-state coverage judge,
the same abstract fetcher.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys

BAND1_LOG = "band1_lossless_log.jsonl"
BAND1_PREDICTIONS = "band1_predictions.jsonl"
DISPOSITION = "preband_disposition_v2.jsonl"
JUDGMENT = "judgment_predictions.jsonl"


def _band1(args) -> int:
    from ..refs.preband_disposition import write_disposition
    from ..refs.run import run as run_band1

    os.makedirs(args.out_dir, exist_ok=True)
    predictions = os.path.join(args.out_dir, BAND1_PREDICTIONS)
    log = os.path.join(args.out_dir, BAND1_LOG)

    run_band1(args.xml_dir, predictions, log,
              model=args.model, anthropic_key=args.anthropic_key,
              ncbi_key=args.ncbi_key, openalex_mailto=args.mailto,
              f8_timing=True)

    disposition = os.path.join(args.out_dir, DISPOSITION)
    write_disposition(log, disposition, f2_commit="",
                      generated_by="cde.runtime.cli band1")
    print(f"[band1] predictions: {predictions}")
    print(f"[band1] disposition: {disposition}")
    return 0


def _band2(args) -> int:
    from ..claims.abstracts import fetch_abstract
    from ..claims.aggregate import make_coverage_judge
    from ..claims.band_prompts import make_extractor
    from ..diagnose.pipeline import run_natural_judgment
    from .completer import make_completer

    disposition = args.disposition or os.path.join(args.out_dir, DISPOSITION)
    if not os.path.exists(disposition):
        # The join is the thing that fails silently: without a disposition every
        # pair is excluded fail-closed and the run finishes valid and empty.
        print(f"[band2] no disposition at {disposition}; run band1 first",
              file=sys.stderr)
        return 2

    call_llm = make_completer(args.model, args.anthropic_key, stage="band")
    manifest = run_natural_judgment(
        args.xml_dir, args.out_dir,
        extractor=make_extractor(call_llm),
        coverage_judge=make_coverage_judge(call_llm),
        fetch_abstract=lambda pmid: fetch_abstract(pmid, api_key=args.ncbi_key),
        preband_disposition=disposition,
        model=args.model)
    print(f"[band2] records: {os.path.join(args.out_dir, JUDGMENT)}")
    print(f"[band2] cost: {json.dumps(manifest.get('token_usage', {}))[:200]}")
    return 0


def _report(args) -> int:
    """The counts every reported rate divides by, and nothing else.

    Printed as counts rather than as rates on purpose: a rate needs a governed
    population, and this tool cannot tell you that you have one.
    """
    rows = [json.loads(line) for line in
            open(args.predictions, encoding="utf-8").read().splitlines() if line]
    if not rows:
        print(f"no records in {args.predictions}", file=sys.stderr)
        return 1
    for field in ("route", "disposition", "terminal_outcome"):
        counts = collections.Counter(str(r.get(field) or "-") for r in rows)
        print(f"\n{field}")
        for key, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
            print(f"  {key:44s} {n:6d}")
    print(f"\ntotal pairs {len(rows)}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="cde.runtime.cli",
        description="Run the citation diagnosis bands over PMC XML. NOT the "
                    "governed production launcher; see the module docstring.")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p):
        p.add_argument("--xml-dir", required=True, help="directory of PMC*.xml")
        p.add_argument("--out-dir", required=True)
        p.add_argument("--model", default="claude-haiku-4-5",
                       help="model id; the provider is derived from it")
        p.add_argument("--ncbi-key", default=os.environ.get("NCBI_API_KEY", ""))
        p.add_argument("--anthropic-key",
                       default=os.environ.get("ANTHROPIC_API_KEY", ""))
        p.add_argument("--mailto", default=os.environ.get("OPENALEX_MAILTO", ""))

    b1 = sub.add_parser("band1", help="reference identity: F1 / F2 / F8")
    common(b1)
    b1.set_defaults(fn=_band1)

    b2 = sub.add_parser("band2", help="claims, coverage and the discriminators")
    common(b2)
    b2.add_argument("--disposition", default="",
                    help="defaults to the band1 artifact in --out-dir")
    b2.set_defaults(fn=_band2)

    rp = sub.add_parser("report", help="route and disposition counts")
    rp.add_argument("predictions", help=f"path to {JUDGMENT}")
    rp.set_defaults(fn=_report)

    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
