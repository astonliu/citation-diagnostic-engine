# Estimated runtime: ~5-10 minutes per 100-paper batch after Section 5C. Rough —
# a request-count model, not a measurement; the per-paper lines print a real
# p/min within the first minute, so resize from those. ONE BATCH PER EXECUTION:
# rerun this cell for the next 100, up to the SAMPLE_SIZE cap. Checkpointed, so
# a disconnect costs at most one paper.
if not ENABLE_PAID_RUN:
    raise RuntimeError("ENABLE_PAID_RUN is False. Read Section 6, set it True in "
                       "Section 2, rerun that cell, then rerun this one.")

HEARTBEAT_SECONDS = 10          # was 30; this cell is meant to be noisy

# ---- OPENALEX BUDGET GUARD -------------------------------------------------
# openalex_telemetry defines the spent-allowance code as "409", but OpenAlex
# signals insufficient budget with 429 and an "Insufficient budget" body.
# Measured 2026-08-24: 786 calls, 786 x 429, quota_exhausted == 0. Without this,
# an overnight run goes F1-blind and says nothing until morning.
OA_DEAD = threading.Event()
OA_429_TRIP = 25                # refusals before we call the budget spent
OA_WATCH_SECONDS = 30

def openalex_budget(verbose=True):
    """(has_budget, detail). Reads the real body, not just the status code."""
    try:
        r = requests.get("https://api.openalex.org/works",
                         params={"filter": "title.search:hallmarks of cancer",
                                 "per-page": 1, "mailto": EMAIL,
                                 "api_key": OPENALEX_API_KEY}, timeout=20)
    except Exception as exc:
        return None, {"error": "%s: %s" % (type(exc).__name__, exc)}
    if r.status_code == 200:
        if verbose:
            print("OpenAlex: HTTP 200 - budget available", flush=True)
        return True, {"status": 200}
    try:
        body = r.json()
    except Exception:
        body = {"text": r.text[:300]}
    spent = (r.status_code == 429
             and "insufficient budget" in str(body.get("message", "")).lower())
    if verbose:
        print("OpenAlex: HTTP %d | daily $%s | prepaid $%s | retryAfter %s s"
              % (r.status_code, body.get("dailyRemainingUsd"),
                 body.get("prepaidRemainingUsd"), body.get("retryAfter")),
              flush=True)
    return (False if spent else None), {"status": r.status_code, **body}

def openalex_429s(before):
    """Total 429s across EVERY leg. This is what quota_exhausted should report
    and does not. Counting all legs matters: doi and candidates were 46% and 38%
    of demand on 2026-08-24, so they hit the wall before confirm does."""
    legs = openalex_telemetry.delta(before)["legs"]
    return sum(v.get("429", 0) for v in legs.values())

def openalex_watchdog(stop_event, before):
    while not stop_event.wait(OA_WATCH_SECONDS):
        n = openalex_429s(before)
        if n >= OA_429_TRIP and not OA_DEAD.is_set():
            OA_DEAD.set()
            print("\n" + "!" * 74, flush=True)
            print("OPENALEX REFUSED %d CALLS (HTTP 429). Budget is spent." % n,
                  flush=True)
            print("fully_answered() is now False for every reference: F1 is "
                  "BLIND and further papers would be unreportable. Halting new "
                  "papers. Finished papers are unaffected. F8 is unaffected.",
                  flush=True)
            print("!" * 74 + "\n", flush=True)
            return

STATE = {
    "papers_done": 0, "papers_skipped": 0, "papers_failed": 0,
    "references": 0, "F1": 0, "F8": 0, "F2_computed_excluded": 0,
    "quarantined": 0,
}
STATE_LOCK = threading.Lock()
STOP = threading.Event()
STARTED = time.time()

# ---- one lock for the console, so 24 threads cannot interleave a line -------
PRINT_LOCK = threading.Lock()
def say(msg):
    with PRINT_LOCK:
        print(msg, flush=True)

# ---- in-flight registry, so a STALL is visible instead of silent -----------
INFLIGHT = {}                   # pmcid -> (started_epoch, stage)
INFLIGHT_LOCK = threading.Lock()
def mark(pmcid, stage):
    with INFLIGHT_LOCK:
        if stage is None:
            INFLIGHT.pop(pmcid, None)
        else:
            t0 = INFLIGHT.get(pmcid, (time.time(), ""))[0]
            INFLIGHT[pmcid] = (t0, stage)

def already_complete(pmcid):
    status = paper_dir(pmcid) / "status.json"
    if not status.exists():
        return None
    try:
        payload = json.loads(status.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if payload.get("status") in {"complete", "skipped"} else None

def process_paper(pmcid):
    out = paper_dir(pmcid)
    out.mkdir(parents=True, exist_ok=True)
    dataset = out / "band1_predictions.jsonl"
    logpath = out / "band1_lossless_log.jsonl"
    started = time.time()
    mark(pmcid, "fetch")

    xml_path, source = fetch_citing_xml(pmcid)
    mark(pmcid, "parse")
    refs = parser.parse_pmc_xml(str(xml_path), source_pmcid=pmcid)
    if not (MIN_REFERENCES <= len(refs) <= MAX_REFERENCES):
        payload = {"pmcid": pmcid, "status": "skipped", "references": len(refs),
                   "reason": "reference_count_outside_%d_%d"
                             % (MIN_REFERENCES, MAX_REFERENCES), "ts": utc_now()}
        atomic_json(out / "status.json", payload)
        mark(pmcid, None)
        return payload

    for stale in (dataset, logpath):
        if stale.exists():
            stale.unlink()

    mark(pmcid, "band1(%d refs)" % len(refs))
    buf = io.StringIO()
    STDOUT_ROUTER.park(buf)
    try:
        counts = f1_run.run(
            str(xml_path.parent), str(dataset), str(logpath),
            model=MODEL, anthropic_key=ANTHROPIC_API_KEY, ncbi_key=NCBI_API_KEY,
            crossref_mailto=EMAIL, openalex_mailto=EMAIL,
            openalex_api_key=OPENALEX_API_KEY,
            refs=refs, complete=BAND1_COMPLETE,
            f8_timing=True, f8_fetch_meta=f8_fetch_meta,
            f8_resolve_doi=f8_resolve_doi)
    finally:
        STDOUT_ROUTER.release()
    (out / "band1_stdout.txt").write_text(buf.getvalue(), encoding="utf-8")

    rows = read_jsonl(logpath)
    hits = [r for r in rows if r.get("label") in ("F1", "F8")]
    for row in hits:
        log = row.get("log") or {}
        append_jsonl(FINDINGS_LIVE, {
            "ts": utc_now(), "citing_pmcid": pmcid,
            "citation_id": row.get("citation_id"), "label": row.get("label"),
            "confidence": row.get("confidence"), "rationale": row.get("rationale"),
            "decided_by": log.get("decided_by"),
            "f8_timing_status": log.get("f8_timing_status"),
            "f8_timing_reason": log.get("f8_timing_reason"),
            "f8_notice_date": log.get("f8_notice_date"),
            "f8_timing_gap_days": log.get("f8_timing_gap_days"),
            "record": row})
        say("  >>> %s  %s  %s" % (row.get("label"), row.get("citation_id"),
                                  str(row.get("rationale") or "")[:100]))

    payload = {
        "pmcid": pmcid, "status": "complete", "references": len(rows),
        "label_counts": {k: int(v) for k, v in counts.items()},
        "f1": sum(1 for r in rows if r.get("label") == "F1"),
        "f8": sum(1 for r in rows if r.get("label") == "F8"),
        "f2_computed_excluded": sum(1 for r in rows if r.get("label") == "F2"),
        "quarantined": sum(1 for r in rows
                           if (r.get("log") or {}).get("decided_by")
                           == "quarantine_exception"),
        "xml_source": source, "elapsed_s": round(time.time() - started, 2),
        "engine_commit": HEAD, "ts": utc_now(),
    }
    atomic_json(out / "status.json", payload)
    mark(pmcid, None)
    return payload

def fold(payload):
    with STATE_LOCK:
        if payload.get("status") == "skipped":
            STATE["papers_skipped"] += 1
            return dict(STATE)
        STATE["papers_done"] += 1
        STATE["references"] += int(payload.get("references") or 0)
        STATE["F1"] += int(payload.get("f1") or 0)
        STATE["F8"] += int(payload.get("f8") or 0)
        STATE["F2_computed_excluded"] += int(payload.get("f2_computed_excluded") or 0)
        STATE["quarantined"] += int(payload.get("quarantined") or 0)
        return dict(STATE)

def progress_line(payload, snap):
    """ONE line per finished paper, with the running F1/F8 totals on it."""
    attempted = snap["papers_done"] + snap["papers_skipped"] + STATE["papers_failed"]
    elapsed = time.time() - STARTED
    ppm = (attempted / elapsed * 60.0) if elapsed > 0 else 0.0
    eta = ((len(TODO) - attempted) / ppm) if ppm > 0 else float("nan")
    if payload.get("status") == "skipped":
        return ("[%4d/%d] %-12s SKIP %d refs (guard)          | F1 %d F8 %d "
                "| %.1f p/min | eta %.0fm"
                % (attempted, len(TODO), payload["pmcid"], payload["references"],
                   snap["F1"], snap["F8"], ppm, eta))
    return ("[%4d/%d] %-12s %3d refs  f1=%d f8=%d  %5.1fs | F1 %d F8 %d "
            "| $%.2f | %.1f p/min | eta %.0fm"
            % (attempted, len(TODO), payload["pmcid"], payload["references"],
               payload.get("f1", 0), payload.get("f8", 0),
               payload.get("elapsed_s", 0.0), snap["F1"], snap["F8"],
               SPEND.usd(), ppm, eta))

def heartbeat():
    """Fires every HEARTBEAT_SECONDS no matter what the workers are doing, so
    silence is never ambiguous. Names the oldest in-flight paper: if that number
    keeps climbing and nothing completes, the run is stalled, not slow."""
    while not STOP.wait(HEARTBEAT_SECONDS):
        with STATE_LOCK:
            s = dict(STATE)
        with INFLIGHT_LOCK:
            flight = sorted(INFLIGHT.items(), key=lambda kv: kv[1][0])
        elapsed = time.time() - STARTED
        attempted = s["papers_done"] + s["papers_skipped"] + s["papers_failed"]
        rate = (s["references"] / elapsed) if elapsed > 0 else 0.0
        eta = ((len(TODO) - attempted) / (attempted / elapsed)) if attempted else float("nan")
        hit, live = PREWARM_STATS["pubmed_hit"], PREWARM_STATS["pubmed_live"]
        hit_pct = (100.0 * hit / (hit + live)) if (hit + live) else 0.0
        # delta() returns {"legs": {...}, "leg_totals": {...}, "total": n,
        # "quota_exhausted": n} -- the spent-allowance count is already hoisted.
        oa409 = openalex_telemetry.delta(OA_BEFORE)["quota_exhausted"]
        oldest = ""
        if flight:
            pmcid, (t0, stage) = flight[0]
            oldest = " | oldest %s %s %.0fs" % (pmcid, stage, time.time() - t0)
        say("[hb %5.1fm] papers %d/%d (skip %d fail %d) | refs %d | "
            "F1 %d | F8 %d | F2excl %d | $%.2f | %.1f refs/s | cache %.0f%% | "
            "OA409 %d | in-flight %d%s | eta %.0fm"
            % (elapsed / 60.0, s["papers_done"], len(TODO), s["papers_skipped"],
               s["papers_failed"], s["references"], s["F1"], s["F8"],
               s["F2_computed_excluded"], SPEND.usd(), rate, hit_pct,
               oa409, len(flight), oldest, eta / 60.0))

# ---- resume ----------------------------------------------------------------
if "seed_from_drive" in globals():
    seed_from_drive()
say("[run] scanning %d papers for finished work..." % len(SAMPLE))
REMAINING = []
for pmcid in SAMPLE:
    done = already_complete(pmcid)
    if done is None:
        REMAINING.append(pmcid)
    else:
        fold(done)

# ONE BATCH PER EXECUTION. Rerun this cell for the next 100; the resume scan
# above is what makes that safe. SAMPLE_SIZE is the hard cap and is never
# exceeded because REMAINING is drawn from SAMPLE.
TODO = REMAINING[:BATCH_SIZE]
say("[run] cap %d | done %d | remaining %d | THIS BATCH %d"
    % (SAMPLE_SIZE, len(SAMPLE) - len(REMAINING), len(REMAINING), len(TODO)))
say("[run] carried in from finished papers: F1 %d, F8 %d"
    % (STATE["F1"], STATE["F8"]))
if not TODO:
    say("[run] NOTHING LEFT. All %d papers in the cap are done. Go to Section 7."
        % SAMPLE_SIZE)
say("[run] STARTING %s with %d workers. A line per paper, a heartbeat every %ds."
    % (utc_now(), MAX_WORKERS, HEARTBEAT_SECONDS))
say("[run] model: %s | prices checked %s" % (MODEL, PRICE_CHECKED_DATE))

_ok, _detail = openalex_budget()
if _ok is False:
    raise RuntimeError(
        "OpenAlex budget is $0 (resets at midnight UTC, or add prepaid usage in "
        "$1 increments at https://openalex.org/pricing). Starting now would make "
        "every F1 in this batch a hold, exactly like the 327-paper Opus batch. "
        "F8 would still be valid -- if that is what you want, comment out this "
        "check deliberately and record it.")

OA_BEFORE = openalex_telemetry.snapshot()
threading.Thread(target=openalex_watchdog, args=(STOP, OA_BEFORE),
                 daemon=True).start()

hb = threading.Thread(target=heartbeat, daemon=True)
hb.start()
try:
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(process_paper, p): p for p in TODO}
        say("[run] %d papers submitted; waiting on the first completion..."
            % len(futures))
        for future in as_completed(futures):
            pmcid = futures[future]
            try:
                payload = future.result()
                snap = fold(payload)
                say(progress_line(payload, snap))
                if "sync_to_drive" in globals():
                    sync_to_drive("papers", min_interval=300)
                if OA_DEAD.is_set():
                    for other in futures:
                        other.cancel()
                    say("[halt] OpenAlex budget spent - cancelled the remaining "
                        "papers. Finished ones are on disk.")
                    break
            except f1_run.NonRetryableProviderError as exc:
                say("[FATAL] %s: %s" % (type(exc).__name__, exc))
                for other in futures:
                    other.cancel()
                raise
            except Exception as exc:
                with STATE_LOCK:
                    STATE["papers_failed"] += 1
                append_jsonl(FAILURES, {"ts": utc_now(), "pmcid": pmcid,
                                        "exception_type": type(exc).__name__,
                                        "message": str(exc)[:500]})
                say("[fail] %s %s: %s" % (pmcid, type(exc).__name__,
                                          str(exc)[:110]))
            finally:
                mark(pmcid, None)
finally:
    STOP.set()
    hb.join(timeout=2)
    if "sync_to_drive" in globals():
        sync_to_drive("run end")

# ---- THE EXIT GATE ---------------------------------------------------------
# OpenAlex answered at the START of the 2026-08-24 run and was dead by the end,
# which is how an artifact F1=0 got produced over 8,009 references. A probe
# before the batch proves nothing about the batch. Probe again, now.
OA_DELTA = openalex_telemetry.delta(OA_BEFORE)
# 429, not the telemetry's "quota_exhausted" (which keys on 409 and reads 0
# while every call is being budget-refused).
OA_429 = sum(v.get("429", 0) for v in OA_DELTA["legs"].values())
OA_409 = OA_DELTA["quota_exhausted"]
exit_hits = {
    "pubmed":   f1_confirm.search_pubmed(PROBE_TITLE, NCBI_API_KEY),
    "crossref": f1_confirm.search_crossref(PROBE_TITLE, EMAIL),
    "openalex": f1_confirm.search_openalex(PROBE_TITLE, EMAIL,
                                           api_key=OPENALEX_API_KEY),
}
F1_REACHABLE_AFTER = f1_confirm.fully_answered(exit_hits)
CONFIRM_409 = OA_DELTA["legs"].get("confirm", {}).get("409", 0)
CONFIRM_429 = OA_DELTA["legs"].get("confirm", {}).get("429", 0)
F1_REPORTABLE = bool(F1_REACHABLE and F1_REACHABLE_AFTER
                     and CONFIRM_409 == 0 and CONFIRM_429 == 0)
atomic_json(MEASURE_ROOT / "provider_probe_after.json", {
    "schema": "cre_provider_probe_v1", "when": "after_batch",
    "probe_title": PROBE_TITLE, "hits": exit_hits,
    "f1_reachable": F1_REACHABLE_AFTER,
    "f1_reachable_before": bool(F1_REACHABLE),
    "f1_reportable_this_batch": F1_REPORTABLE,
    "openalex_calls_by_leg": OA_DELTA, "confirm_leg_409": CONFIRM_409,
    "confirm_leg_429": CONFIRM_429, "openalex_429_total": OA_429,
    "model": MODEL, "halted_on_openalex": OA_DEAD.is_set(),
    "engine_commit": HEAD, "ts": utc_now()})

atomic_json(MEASURE_ROOT / "run_state.json",
            {**STATE, "sample_size": len(SAMPLE), "queued": len(TODO),
             "elapsed_s": round(time.time() - STARTED, 1),
             "model_calls": SPEND.calls, "spend_usd": round(SPEND.usd(), 4),
             "prewarm_stats": dict(PREWARM_STATS),
             "batch_size": BATCH_SIZE, "cap": SAMPLE_SIZE,
             "f1_reachable_before": bool(F1_REACHABLE),
             "f1_reachable_after": F1_REACHABLE_AFTER,
             "f1_reportable_this_batch": F1_REPORTABLE,
             "openalex_calls_by_leg": OA_DELTA,
             "openalex_authenticated": bool(OPENALEX_API_KEY),
             "openalex_429_total": OA_429, "halted_on_openalex": OA_DEAD.is_set(),
             "model": MODEL, "engine_commit": HEAD, "finished_at": utc_now()})

print("\nRUN COMPLETE")
print("papers done      :", STATE["papers_done"])
print("papers skipped   :", STATE["papers_skipped"], "(reference count guard)")
print("papers failed    :", STATE["papers_failed"])
print("references       :", STATE["references"])
print("F1 findings      :", STATE["F1"])
print("F8 findings      :", STATE["F8"])
print("F2 computed, EXCLUDED from the reported set:", STATE["F2_computed_excluded"])
print("spend            : $%.2f over %d model calls" % (SPEND.usd(), SPEND.calls))
print("prewarm cache    :", dict(PREWARM_STATS))
print("openalex by leg  :", OA_DELTA["leg_totals"],
      "| total", OA_DELTA["total"], "| quota_exhausted", OA_409)
print("\nF1 REPORTABILITY GATE")
print("  reachable BEFORE batch :", bool(F1_REACHABLE))
print("  reachable AFTER  batch :", F1_REACHABLE_AFTER, exit_hits)
print("  confirm-leg 409s / 429s:", CONFIRM_409, "/", CONFIRM_429)
print("  openalex 429s, all legs:", OA_429)
print("  halted on openalex     :", OA_DEAD.is_set())
if F1_REPORTABLE:
    print("  --> F1 IS REPORTABLE for this batch. The F1 count above is a finding.")
else:
    print("  --> F1 IS NOT REPORTABLE for this batch. OpenAlex stopped answering, "
          "so confirm.fully_answered() was False for some or all references and "
          "F1 was held rather than decided. Record F1 as NOT ATTEMPTED for these "
          "papers, never as zero. F8 is unaffected: it never calls confirm().")
say("\n[run] batch done. Rerun this cell for the next %d, or go to Section 7."
    % BATCH_SIZE)