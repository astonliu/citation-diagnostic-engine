# MINT_DECISIONS_PENDING_ZD — unnumbered CONFIG fields awaiting ZD

Recorded 2026-07-27 by the `mint_v1` build. Every field below is required by
the pinned schema (`F3-F7_FINDER_FREEZE_SCHEMAS.json`,
`3241cbcce6189cf19f278b452b01ed41fb46ec079a8f2588fd110e50409b53b1`), is not
mechanically derivable, and is **not** one of ZD's canonical six inputs — so
without this document nothing records that it is outstanding.
`mint_v1.py --config` fails closed naming each field until
`MINT_INPUTS.json` supplies a decision.

Discipline: **propose, don't decide.** The "fixture value" column is what
`fixtures_v1.py` uses to make tests pass, shown only so the shape is
unambiguous — **REFERENCE ONLY — NOT A PROPOSAL**. Every decision cell is
blank for ZD, with one exception: `temperature` is already settled in the
codebase (see §1) and needs only ZD's countersignature.

Each decision applies **per stage** — `stages.claim_extract` and
`stages.coverage` decide independently (blank columns for each).

## 1. `params` — six per stage, all required; each is a STATE WRAPPER

A param is an object `{"state": ...}` or `{"state": "supplied", "value": ...}`
— so each row is **two** decisions: supplied-vs-omitted first, then the value
only if supplied.

| field | schema constraint | fixture value (REFERENCE ONLY — NOT A PROPOSAL) | state: claim_extract | value | state: coverage | value |
|---|---|---|---|---|---|---|
| `params.temperature` | `no_decimal_param`: state ∈ {omitted, provider_default, unsupported}; **`supplied` is prohibited** (a supplied decimal would send a JSON float, breaking exact-request hashing under float prohibition) | `{"state": "omitted"}` | **CLOSED: omitted** (countersign) | n/a — never supplied | **CLOSED: omitted** (countersign) | n/a — never supplied |
| `params.max_tokens` | `pos_int_param`: state ∈ {supplied, omitted, provider_default, unsupported}; value integer ≥ 1 iff supplied | `{"state": "supplied", "value": 1024}` | ☐ | ☐ | ☐ | ☐ |
| `params.top_p` | `no_decimal_param` (same prohibition as temperature) | `{"state": "omitted"}` | ☐ | n/a — never supplied | ☐ | n/a — never supplied |
| `params.top_k` | `pos_int_param` | `{"state": "omitted"}` | ☐ | ☐ | ☐ | ☐ |
| `params.stop_sequences` | `strlist_param`: value array of strings iff supplied | `{"state": "omitted"}` | ☐ | ☐ | ☐ | ☐ |
| `params.seed` | `safe_int_param`: value within the I-JSON safe integer range iff supplied | `{"state": "omitted"}` | ☐ | ☐ | ☐ | ☐ |

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

| field | fixture value (REFERENCE ONLY — NOT A PROPOSAL) | ZD: claim_extract | ZD: coverage |
|---|---|---|---|
| `endpoint.provider` | `"anthropic"` | ☐ | ☐ |
| `endpoint.base_url` | `"https://api.anthropic.com/v1/messages"` | ☐ | ☐ |
| `endpoint.api_family` | `"messages"` | ☐ | ☐ |
| `endpoint.api_version` | `"2023-06-01"` | ☐ | ☐ |
| `endpoint.region` | `"us"` | ☐ | ☐ |
| `endpoint.sdk_version` | `"1.0"` | ☐ | ☐ |
| `endpoint.jcs_library_version` | `"1.0"` | ☐ | ☐ |
| `endpoint.behavior_headers` | `{}` | ☐ | ☐ |

## 3. `system_message` — one per stage

Schema: `{"state": "omitted"}` or `{"state": "supplied", "text_utf8": ...,
"sha256": ...}` (`sha256` = SHA-256 of `text_utf8`; both required iff
supplied, both prohibited iff omitted).

| field | fixture value (REFERENCE ONLY — NOT A PROPOSAL) | ZD: claim_extract | ZD: coverage |
|---|---|---|---|
| `system_message` | `{"state": "omitted"}` | ☐ | ☐ |

## 4. `tool_schema` (+ derived `tool_schema_sha256`) — one per stage

Schema: `tool_schema` is `string | null`; **`null` is a legal decided value**
(no tool use), distinct from undecided. `tool_schema_sha256` is derived by
`mint_v1` — SHA-256 of the UTF-8 tool schema string, or `null` when
`tool_schema` is `null` — never supplied separately.

| field | fixture value (REFERENCE ONLY — NOT A PROPOSAL) | ZD: claim_extract | ZD: coverage |
|---|---|---|---|
| `tool_schema` | `null` | ☐ | ☐ |

## 5. `retry` — one object per stage, all thirteen keys required

Schema `retry_policy`: `max_attempts` integer 1–10; `retryable_status`
integers 400–599 unique; `retryable_exceptions` from the pinned enum;
the seven `*_seconds` fields are **decimal strings** (`^[0-9]+(\.[0-9]+)?$`
— floats are prohibited artifact-wide); `respect_retry_after` boolean;
`idempotency_preimage_version` const `"CRE_FINDER_IDEMPOTENCY_V1"`;
`record_attempts` const `true`. SV-101 additionally requires
`total_timeout_seconds ≥ max(connect, read)` and
`backoff_cap_seconds ≥ backoff_base_seconds`.

| field | fixture value (REFERENCE ONLY — NOT A PROPOSAL) | ZD: claim_extract | ZD: coverage |
|---|---|---|---|
| `retry.max_attempts` | `3` | ☐ | ☐ |
| `retry.retryable_status` | `[429]` | ☐ | ☐ |
| `retry.retryable_exceptions` | `["connect_timeout"]` | ☐ | ☐ |
| `retry.connect_timeout_seconds` | `"5"` | ☐ | ☐ |
| `retry.read_timeout_seconds` | `"60"` | ☐ | ☐ |
| `retry.total_timeout_seconds` | `"120"` | ☐ | ☐ |
| `retry.backoff_base_seconds` | `"1"` | ☐ | ☐ |
| `retry.backoff_cap_seconds` | `"30"` | ☐ | ☐ |
| `retry.jitter_seconds` | `"1"` | ☐ | ☐ |
| `retry.respect_retry_after` | `true` | ☐ | ☐ |
| `retry.retry_after_cap_seconds` | `"60"` | ☐ | ☐ |

(`idempotency_preimage_version` and `record_attempts` are schema consts —
mechanical, no decision.)

## 6. `response_parser_version` — one per stage

Schema: non-empty string. Names the pinned parser contract for the stage's
response shape.

| field | fixture value (REFERENCE ONLY — NOT A PROPOSAL) | ZD: claim_extract | ZD: coverage |
|---|---|---|---|
| `response_parser_version` | `"v1"` | ☐ | ☐ |

## 7. `module_manifest` — one artifact object (not per stage)

The CONFIG binds `module_manifest_sha256 = canon_sha256(module_manifest)`
(verified by SV-110). The manifest must cover all eleven trust-boundary
roles (`bootstrap.TRUST_BOUNDARY_ROLES`) with real repo paths, blob OIDs,
and on-disk content hashes. Several roles (`renderer`, `parser`,
`provider_adapter`, `evidence_reader`, `runner`, `package_init`) have no
committed module yet, so the real manifest cannot exist before those modules
do — it is an artifact input to `MINT_INPUTS.json`, not a decision cell here.

| input | status |
|---|---|
| `module_manifest` | ☐ outstanding — supplied as a full artifact object |

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
