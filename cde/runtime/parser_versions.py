"""Names for the frozen response-parser contracts, so a run can record them.

DEC-022 makes prompt version and parser version INDEPENDENT axes: relaxing
``_loads_strict`` or adding a key to the coverage contract is a parser bump even
when no prompt text changes. The vault's DEPENDENCY GRAPH has named both
contracts since 2026-08-06 -- ``strict_claims_v1`` and
``strict_coverage_5key_v1`` -- but neither string existed anywhere in code, so
the abstract path stamped no parser version at all and a contract move was
invisible in every artifact.

This module only NAMES contracts that already exist. It deliberately lives
OUTSIDE ``band_prompts.py`` and ``judgment_band.py``: the first is the root of
the freeze chain (its blob OID is copied into two sealed packages, both universe
fixtures, ``semantic_validator_v1.py`` and ``test_mint_v1.py``), and the second
is digest-pinned by ``F5_SUPERSESSION_SPEC.md``. New behaviour goes in a new
module -- that is the sanctioned bypass, and it is exactly what
``coverage_aggregate.py`` does for the F6 tri-state.

Bump a constant here ONLY when the corresponding parser's output contract
changes, and say so in DECISIONS.
"""
from __future__ import annotations

#: ``judgment_band.extract_atomic_claims`` -- the strict claim-list contract.
CLAIM_PARSER_VERSION = "strict_claims_v1"

#: ``judgment_band.coverage_verdicts`` on the DEFAULT abstract path -- the frozen
#: five-key contract (established / rationale / evidence_span + the two the
#: aggregator reads). The full-text path supersedes this with
#: ``coverage_prompts_v3.RESPONSE_PARSER_VERSION``.
COVERAGE_PARSER_VERSION = "strict_coverage_5key_v1"
