# CRE taxonomy audit loop — rejection register

**Consult this before raising anything.** Re-raising a rejected finding is the loop's main pollution
risk. Every REJECT verdict is logged here with its reason so no auditor spends a round re-finding it.

A **REJECT** means: not real, not reachable, or the cure is worse. It is **not** appended to any spec.
A **DEFER** is recorded here too, marked as such, so the distinction survives — a DEFER is real and
deliberately postponed, and may be re-raised when its blocking reason clears.

---

## Seeded before round 1 — from Amendment 01 §E (ZD, 2026-08-16)

These four were established by live testing against NCBI on 2026-08-16, twice, independently. They
are recorded as CONTRADICTIONS 64. **Do not re-derive them.**

### R-001 — REJECTED: "the leading `[` in a translated title makes the ESearch query malformed"

- **Origin:** the first draft of `F1_FABRICATION_GUARD_SPEC.md`, Defect 4, as a guess.
- **Reason:** tested live. Entrez **tolerates** the bracket — it reports it in
  `warninglist.outputmessages` as `['[', ']']`, drops the bracket characters, and runs the query
  normally. `[Myalgia and statins: Separating the true from the false].[Title]` returns **1 hit, its
  own PMID 31473026**. There is no bracket defect.
- **Class:** a claim about an external service that was reasoned rather than called. Amendment 01 §A.2
  exists because of it.

### R-002 — REJECTED: "quote the ESearch term"

- **Origin:** the same first draft, as the prescribed remedy for R-001.
- **Reason:** tested live, and it is **catastrophic**. Full article titles are not in PubMed's phrase
  index. `"The heat of activation and the heat of shortening in a muscle twitch"[Title]` returns
  `count: 0` with `warninglist.phrasesnotfound` naming the whole phrase.
  `"Purple Urine after Catheterization"[Title]` returns **0** where the unquoted form returns the
  correct **1**. Quoting every title would zero out nearly every confirmation search corpus-wide and
  turn the entire corpus into apparent fabrications — the exact failure mode the F1 spec exists to
  prevent, applied to every reference at once instead of only during an outage.
- **Pinned so it is not "fixed" back:**
  `test_f1_fabrication_guard.py::test_matrix_bracketed_translated_title_is_deliberately_not_quoted`.

### R-003 — REJECTED: "treat Entrez's nested `errorlist` as a fault envelope"

- **Origin:** the natural over-extension of Defect 4 (HTTP-200 error payloads become "found nothing").
- **Reason:** `errorlist` appears on **legitimate zero-hit searches**. Treating it as a fault would
  convert real "searched, answered, found nothing" results into "did not answer", suppressing true
  positives. That is the **mirror defect** of the one Defect 4 fixed — it fails toward silence instead
  of toward accusation, and precision-first forbids both halves.
- **Note:** the implemented Defect 4 fix deliberately excludes `errorlist` for this reason. A finding
  that says the exclusion is a bug must first explain why the mirror defect does not apply.

### R-004 — **DEFERRED, not rejected** — `GOVERNING_MODULES` does not cover the F1-producing modules

- **Status: DEFER → BLOCKED-ON-ZD.** Real, established, and deliberately left alone.
- **The gap:** `production_launcher.GOVERNING_MODULES` hashes 13 modules, all serving the F3–F7 band.
  The seven modules that can actually *produce* an F1 or F2 — `decide.py`, `lookup.py`, `confirm.py`,
  `run.py`, `llm_filter.py`, `unscoreable.py`, `biblio_match.py` — are **all absent**. A production
  run can be launched with locally modified `decide.py` bytes and the launcher reports clean.
- **Why deferred:** what is governed is a **freeze decision**, not an implementation detail. It goes
  to the Relay, never to an auditor. Recorded in `F1_GOVERNANCE_GAP_2026-08-16.md`.
- **Re-raise only** with a *distinct* mechanism — e.g. a branch in the digest comparison that passes
  on error rather than refusing.

---

## Round 1 — F1 (2026-08-17)

Both rejections this round are **duplicates**, not errors. Each was independently reproduced and each
is real — they were rejected because landing them would produce two competing spec items editing the
same code block. The surviving twin carries their substance. **Citation integrity was clean across
the whole round: all three checkers recorded `citation_verified: true` on all twelve candidates.**

### R-005 — REJECTED (duplicate of D-9/L-3): "`f1_status` has no counter for *the confirmation search was never issued*"

- **Cite as submitted:** `cre/f1/eval_report.py:279-282`. Reproduced.
- **Reason:** same defect, same code block and same fix as the finding recorded as **D-9**, whose
  substance is folded into **L-3**. Gated by `if hits:` at `eval_report.py:133-134`, with the warning
  at `:279-282` and the note at `:201-203`. Landing both would spec one edit twice.
- **Not a dismissal.** Reality graded this one LAND and noted it is an **unmet row of the guard
  spec's own acceptance matrix** — the last row reads *"run where the F1 check could not run |
  manifest | distinguishable from zero"*, and it is not satisfied. That observation is carried in
  L-3. **Do not re-raise it separately; verify it against L-3's implemented fix instead.**

### R-006 — REJECTED (duplicate of L-3): "`f1_answered` counts rows whose transport status was never recorded"

- **Cite as submitted:** `cre/f1/eval_report.py:127-132`, helper `cre/f1/schema.py:70-77`. Reproduced.
- **Reason:** identical primary cite, identical helper, identical defect and identical fix to **L-3**.
  An empty or missing `pmid_transport_status` is counted as answered, which also zeroes
  `transport_failed` and so suppresses the only warning `format_report` can print.
- **Do not re-raise.** L-3 is the surviving statement of it.

---

## Standing note for future rounds

A **duplicate** is a REJECT under this loop's rubric ("a restatement of an existing item at a
different line number"), but the register records it as a duplicate rather than as a refutation, and
names the surviving twin. When two finders converge on one defect from different surfaces, that is a
signal the defect is real and central — **it is not a sign either finder was wrong.**

