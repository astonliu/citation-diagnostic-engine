# MINT_DECISIONS_PENDING_ZD — unnumbered CONFIG fields awaiting ZD

Recorded 2026-07-27 by the `mint_v1` build. Every field below is required by
the pinned schema (`F3-F7_FINDER_FREEZE_SCHEMAS.json`,
`3241cbcce6189cf19f278b452b01ed41fb46ec079a8f2588fd110e50409b53b1`), is not
mechanically derivable, and is **not** one of ZD's canonical six inputs — so
without this document nothing records that it is outstanding.
`mint_v1.py --config` fails closed naming each field until
`MINT_INPUTS.json` supplies a decision.

**Provenance:** decisions walked and recorded with ZD on 2026-07-27.
§§1–6 are now filled with the decided values and their bases; §7
(`module_manifest`) stays open — it is an artifact input, not a decision.
Two answers were reversed during that walk — `seed` (`omitted` →
`unsupported`) and `top_k` (`provider_default` → `omitted`) — and both
reversals are recorded below with their reasons. This document is a human
record, not a machine input: nothing in `mint_v1.py` reads it, and the
fail-closed report is unchanged until `MINT_INPUTS.json` exists.

Discipline: **propose, don't decide.** The "fixture value" column is what
`fixtures_v1.py` uses to make tests pass, shown only so the shape is
unambiguous — **REFERENCE ONLY — NOT A PROPOSAL**. Where a decided value
corrects a fixture placeholder, the correction and its reason are recorded
beside it; the fixture column itself is never overwritten.

Each decision applies **per stage** — `stages.claim_extract` and
`stages.coverage` decide independently. ZD decided identical values for both
stages everywhere except §6 (`response_parser_version`).

## 1. `params` — six per stage, all required; each is a STATE WRAPPER

A param is an object `{"state": ...}` or `{"state": "supplied", "value": ...}`
— so each row is **two** decisions: supplied-vs-omitted first, then the value
only if supplied.

| field | schema constraint | fixture value (REFERENCE ONLY — NOT A PROPOSAL) | state: claim_extract | value | state: coverage | value |
|---|---|---|---|---|---|---|
| `params.temperature` | `no_decimal_param`: state ∈ {omitted, provider_default, unsupported}; **`supplied` is prohibited** (a supplied decimal would send a JSON float, breaking exact-request hashing under float prohibition) | `{"state": "omitted"}` | **CLOSED: omitted** (ZD 2026-07-27) | n/a — never supplied | **CLOSED: omitted** (ZD 2026-07-27) | n/a — never supplied |
| `params.max_tokens` | `pos_int_param`: state ∈ {supplied, omitted, provider_default, unsupported}; value integer ≥ 1 iff supplied | `{"state": "supplied", "value": 1024}` | supplied | `1024` | supplied | `1024` |
| `params.top_p` | `no_decimal_param` (same prohibition as temperature) | `{"state": "omitted"}` | omitted | n/a — never supplied | omitted | n/a — never supplied |
| `params.top_k` | `pos_int_param` | `{"state": "omitted"}` | omitted | — | omitted | — |
| `params.stop_sequences` | `strlist_param`: value array of strings iff supplied | `{"state": "omitted"}` | omitted | — | omitted | — |
| `params.seed` | `safe_int_param`: value within the I-JSON safe integer range iff supplied | `{"state": "omitted"}` | unsupported | — | unsupported | — |

Basis, per field (ZD 2026-07-27):

- `temperature` — countersigned; the closing paragraph of this section
  stands unchanged.
- `max_tokens = supplied, 1024` — matches
  `band_prompts.make_anthropic_call(max_tokens=1024)`. Measured worst-case
  outputs: `claim_extract` ~146 tokens (5 long claims), `coverage` ~146
  tokens (3 unconfirmed specifics + full-sentence evidence span); the
  estimate is characters÷4, so treat it as order-of-magnitude. 1024 is ~7×
  the worst case. `max_tokens` is a ceiling, not an allocation — billing
  follows tokens produced, so headroom is free while a low cap truncates the
  JSON into `PARSE_QUARANTINE`.
- `top_p = omitted` — `no_decimal_param` prohibits `supplied`.
- `top_k = omitted` — ZD initially chose `provider_default`, then switched
  to `omitted` for consistency: all three not-sent params produce a
  byte-identical request, so describing `top_k` differently would assert an
  intent that cannot be defended.
- `stop_sequences = omitted` — the SDK accepts `stop_sequences`; the code
  never sends it.
- `seed = unsupported` — **not `omitted`.** Verified against the installed
  SDK (anthropic 0.111.0): `anthropic.resources.messages.Messages.create`
  has no `seed` parameter, so no value can be sent. `omitted` would assert a
  declined option that does not exist. ZD reversed an initial `omitted`
  after this was shown.

Note on wording: `temperature`, `top_p`, `top_k` and `stop_sequences` all
resolve to the same wire behavior — the parameter is absent from the request
— and are deliberately described with the same word, `omitted`. `seed`
differs because the SDK offers no such parameter to omit.

**`temperature` is closed, not open.** The omission is not a test default; it
is the documented behavior of the production call path,
`cre/f1/band_prompts.py::make_anthropic_call`: *"The temperature parameter is
deliberately omitted: the pinned Opus model rejects an explicit temperature
argument."* The schema's `no_decimal_param` independently prohibits supplying
it (or `top_p`). Recorded here as a closed decision awaiting only ZD's
countersignature — re-deciding it would re-open something the codebase
already settled.

## 2. `endpoint` — one object per stage, all nine keys required

Schema: `additionalProperties: false`; required `provider`, `base_url`,
`host_allowlisted` (const `true` — recomputed by SV-091 from the out-of-band
allowlist `("api.anthropic.com",)`, never trusted), `api_family`,
`api_version`, `region`, `sdk_version`, `jcs_library_version`,
`behavior_headers` (string map; SV-090 hard-denies credential-bearing
headers). `base_url` must match `^https://[A-Za-z0-9.-]+(:[0-9]+)?(/[^\s]*)?$`
and its host must be in the pinned allowlist.

ZD decided identical endpoint values for both stages (2026-07-27):

| field | fixture value (REFERENCE ONLY — NOT A PROPOSAL) | ZD: claim_extract | ZD: coverage |
|---|---|---|---|
| `endpoint.provider` | `"anthropic"` | `"anthropic"` | `"anthropic"` |
| `endpoint.base_url` | `"https://api.anthropic.com/v1/messages"` | `"https://api.anthropic.com/v1/messages"` | `"https://api.anthropic.com/v1/messages"` |
| `endpoint.api_family` | `"messages"` | `"messages"` | `"messages"` |
| `endpoint.api_version` | `"2023-06-01"` | `"2023-06-01"` | `"2023-06-01"` |
| `endpoint.region` | `"us"` | `"global"` | `"global"` |
| `endpoint.sdk_version` | `"1.0"` | `"0.111.0"` | `"0.111.0"` |
| `endpoint.jcs_library_version` | `"1.0"` | `"canon_v1"` | `"canon_v1"` |
| `endpoint.behavior_headers` | `{}` | `{}` | `{}` |

Basis, per field (ZD 2026-07-27):

- `provider`, `base_url`, `api_family` — facts about the call path; the
  `base_url` host is in `bootstrap.TRUSTED_ENDPOINT_HOSTS`.
- `api_version = "2023-06-01"` — **verified, not copied from the fixture**:
  the only `anthropic-version` literal in the installed SDK's client module
  (anthropic 0.111.0).
- `sdk_version = "0.111.0"` — **corrects the fixture's `"1.0"`
  placeholder.** Read from `.venv_cre` (`anthropic-0.111.0.dist-info`).
  **Re-read at mint time** — see the closing section.
- `jcs_library_version = "canon_v1"` — **corrects the fixture's `"1.0"`.**
  JCS = JSON Canonicalization Scheme, the rules that turn an object into one
  exact byte string. There is no third-party JCS library in this project;
  `cre/f1/freeze/canon_v1.py` is the canonicalizer. `"1.0"` identifies
  nothing.
- `behavior_headers = {}` — the call path sends no additional headers.
  SV-090 hard-denies credential-bearing headers, so an empty map is also the
  safest value.
- `region = "global"` — ZD decision. The schema requires a non-empty string,
  but the public Anthropic endpoint has no region concept. `"global"` states
  that plainly; the fixture's `"us"` would assert a regional deployment that
  is not in use.

`host_allowlisted` is a schema `const true` recomputed by SV-091 —
mechanical, not a decision, and deliberately absent from the table above.

## 3. `system_message` — one per stage

Schema: `{"state": "omitted"}` or `{"state": "supplied", "text_utf8": ...,
"sha256": ...}` (`sha256` = SHA-256 of `text_utf8`; both required iff
supplied, both prohibited iff omitted).

| field | fixture value (REFERENCE ONLY — NOT A PROPOSAL) | ZD: claim_extract | ZD: coverage |
|---|---|---|---|
| `system_message` | `{"state": "omitted"}` | `{"state": "omitted"}` | `{"state": "omitted"}` |

Basis (ZD 2026-07-27): not a judgment call. `make_anthropic_call` sends only
`model`, `max_tokens`, and `messages=[{"role": "user", ...}]`. No `system`
argument is passed, though the SDK accepts one. This row is a fact about the
call path: changing it later requires changing `band_prompts.py` first — the
CONFIG must not claim a system prompt the code does not send.

## 4. `tool_schema` (+ derived `tool_schema_sha256`) — one per stage

Schema: `tool_schema` is `string | null`; **`null` is a legal decided value**
(no tool use), distinct from undecided. `tool_schema_sha256` is derived by
`mint_v1` — SHA-256 of the UTF-8 tool schema string, or `null` when
`tool_schema` is `null` — never supplied separately.

| field | fixture value (REFERENCE ONLY — NOT A PROPOSAL) | ZD: claim_extract | ZD: coverage |
|---|---|---|---|
| `tool_schema` | `null` | `null` | `null` |

Basis (ZD 2026-07-27): same reasoning as §3 — no `tools` argument is passed
by the call path. `null` here means "no tool use", decided; the derived
`tool_schema_sha256` stays mechanical and is not a decision cell.

## 5. `retry` — one object per stage, all thirteen keys required

Schema `retry_policy`: `max_attempts` integer 1–10; `retryable_status`
integers 400–599 unique; `retryable_exceptions` from the pinned enum;
the seven `*_seconds` fields are **decimal strings** (`^[0-9]+(\.[0-9]+)?$`
— floats are prohibited artifact-wide); `respect_retry_after` boolean;
`idempotency_preimage_version` const `"CRE_FINDER_IDEMPOTENCY_V1"`;
`record_attempts` const `true`. SV-101 additionally requires
`total_timeout_seconds ≥ max(connect, read)` and
`backoff_cap_seconds ≥ backoff_base_seconds`.

ZD decided identical retry values for both stages (2026-07-27):

| field | fixture value (REFERENCE ONLY — NOT A PROPOSAL) | ZD: claim_extract | ZD: coverage |
|---|---|---|---|
| `retry.max_attempts` | `3` | `4` | `4` |
| `retry.retryable_status` | `[429]` | `[408, 429, 500, 502, 503, 529]` | `[408, 429, 500, 502, 503, 529]` |
| `retry.retryable_exceptions` | `["connect_timeout"]` | `["connect_timeout", "read_timeout", "connection_reset", "rate_limited", "server_5xx"]` | `["connect_timeout", "read_timeout", "connection_reset", "rate_limited", "server_5xx"]` |
| `retry.connect_timeout_seconds` | `"5"` | `"10"` | `"10"` |
| `retry.read_timeout_seconds` | `"60"` | `"120"` | `"120"` |
| `retry.total_timeout_seconds` | `"120"` | `"600"` | `"600"` |
| `retry.backoff_base_seconds` | `"1"` | `"1"` | `"1"` |
| `retry.backoff_cap_seconds` | `"30"` | `"30"` | `"30"` |
| `retry.jitter_seconds` | `"1"` | `"1"` | `"1"` |
| `retry.respect_retry_after` | `true` | `true` | `true` |
| `retry.retry_after_cap_seconds` | `"60"` | `"60"` | `"60"` |

Basis, per field (ZD 2026-07-27):

- `max_attempts = 4` — 3 retries after the first try. Long serial Opus loops
  (~100–180 calls per document) reliably hit rate limits.
- `retryable_status = [408, 429, 500, 502, 503, 529]` — **widens the
  fixture's `[429]`.** 529 is Anthropic's overloaded status; with `[429]`
  alone a 529 or 503 ends the run. Schema allows 400–599, unique.
- `retryable_exceptions` — the pinned enum also offers `dns_failure` and
  `tls_error`; both are omitted deliberately, because they indicate
  misconfiguration rather than a transient fault and retrying them hides the
  real error.
- `connect_timeout_seconds = "10"` — decimal string; floats are prohibited
  artifact-wide.
- `read_timeout_seconds = "120"` — Opus latency on these prompts is
  single-digit seconds typically; 120 is margin, not an expectation.
- `total_timeout_seconds = "600"` — **corrects an internal inconsistency in
  the fixture.** The fixture pairs `max_attempts: 3` with
  `total_timeout_seconds: "120"` and `read_timeout_seconds: "60"` — the
  total cuts off the retries the policy claims to allow. SV-101 does not
  catch this (it only requires `total ≥ max(connect, read)`, and 120 ≥ 60
  passes). 600 covers 4 attempts × 120 s read plus backoff.
- `respect_retry_after = true` — Anthropic returns `retry-after` on 429;
  honoring it beats guessing.
- `retry_after_cap_seconds = "60"` — bounds a hostile or mistaken
  `retry-after`. SV-101 leaves this unconstrained when `respect_retry_after`
  is false; here it is true, so the cap is load-bearing.

SV-101 pre-check on the decided values: `total 600 ≥ max(connect 10,
read 120)` ✓; `cap 30 ≥ base 1` ✓.

(`idempotency_preimage_version` and `record_attempts` are schema consts —
mechanical, no decision.)

## 6. `response_parser_version` — one per stage

Schema: non-empty string. Names the pinned parser contract for the stage's
response shape.

| field | fixture value (REFERENCE ONLY — NOT A PROPOSAL) | ZD: claim_extract | ZD: coverage |
|---|---|---|---|
| `response_parser_version` | `"v1"` | `"strict_claims_v1"` | `"strict_coverage_5key_v1"` |

Basis (ZD 2026-07-27): the field versions the **response-shape contract**
the parser enforces, not the prompt. `parse_claims` requires exactly one key
`claims`; `parse_coverage` requires exactly the five keys `engages_subject`,
`contradicts`, `unconfirmed_specifics`, `rationale`, `evidence_span` and
derives `established` through `aggregate_coverage`. Two consequences:

- The fixture's `"v1"` for both stages cannot distinguish which contract
  produced a given row.
- Parser version is **not** coupled to prompt version. Prompt text can be
  reworded without changing the output shape, and the parser version must
  not move when that happens. Conversely, relaxing `_loads_strict` or adding
  a sixth coverage key is a parser-version bump even if the prompt is
  untouched.

## 7. `module_manifest` — one artifact object (not per stage)

The CONFIG binds `module_manifest_sha256 = canon_sha256(module_manifest)`
(verified by SV-110). The manifest must cover all eleven trust-boundary
roles (`bootstrap.TRUST_BOUNDARY_ROLES`) with real repo paths, blob OIDs,
and on-disk content hashes. Several roles (`renderer`, `parser`,
`provider_adapter`, `evidence_reader`, `runner`, `package_init`) have no
committed module yet, so the real manifest cannot exist before those modules
do — it is an artifact input to `MINT_INPUTS.json`, not a decision cell here.

This is the actual gate on minting a CONFIG at all: at `b6f09a7` six of the
eleven roles have no committed module, and until those modules exist
`mint_v1 --config` cannot succeed no matter how many rows in §§1–6 are
filled. Do not invent placeholder entries — a manifest that pins bytes that
are not the real trust-boundary modules is exactly the fake pin this
document exists to prevent.

| input | status |
|---|---|
| `module_manifest` | ☐ outstanding — supplied as a full artifact object |

## Values that must be RE-READ at mint time (readings, not choices)

Three values in the tables above are environment readings rather than
choices. This document records what was observed on 2026-07-27; it is never
a source to copy from. The `runtime_profile` readings are derived live by
`mint_v1` itself; the `endpoint.*` readings ride in `MINT_INPUTS.json`, so
whoever assembles that file must read them from the live environment at mint
time, not from this table:

- `endpoint.sdk_version` — `"0.111.0"` observed; changes on any SDK upgrade.
- `endpoint.jcs_library_version` — `"canon_v1"`; changes only if the
  canonicalizer is replaced.
- `runtime_profile.python_version` / `.python_implementation` — already
  mechanical (derived by `mint_v1`). The observed drift is the reason this
  warning exists: `.venv_cre/pyvenv.cfg` records Python `3.14.5` while the
  committed conformance report recorded interpreter `3.14.6`, so the
  interpreter moved under the venv after it was created.

## Not listed here (for completeness)

- `model_snapshot` (canonical input **#1**), `evidence_policy` (**#3**),
  `codebook_sha256` (**#2**), `candidate_protocol` carrying the freeze
  criterion (**#4**) and prohibited cases (**#5**),
  `dependency_lock_sha256` (**#6**) — ZD's canonical six, tracked by number
  in the `mint_v1` fail-closed report.
- `evidence_scope`, `evidence_reader`, `canon`, `scope`, `failure_policy`
  — schema consts; mechanical.
- `response_schema_sha256` — `null` until ZD supplies the committed
  per-stage response-schema files (residual #5); `mint_v1` prints a notice
  on every mint while null.
- `source.*`, `runtime_profile.*` (except the input-#6 lockfile),
  `prompt_packages.*`, `candidate_protocol_sha256`,
  `module_manifest_sha256`, `codebook_sha256` derivations — mechanical,
  derived by `mint_v1`, never supplied.
