# Estimated runtime: ~10 seconds (three live title searches).
# Run this AFTER the current batch finishes/crashes. It rebuilds the F1
# reportability verdict that the crashed exit gate never produced. Uses
# OA_BEFORE from the run cell, which survives the exception in Colab.
OA_DELTA = openalex_telemetry.delta(OA_BEFORE)
OA_409 = OA_DELTA["quota_exhausted"]
CONFIRM_409 = OA_DELTA["legs"].get("confirm", {}).get("409", 0)
exit_hits = {
    "pubmed":   f1_confirm.search_pubmed(PROBE_TITLE, NCBI_API_KEY),
    "crossref": f1_confirm.search_crossref(PROBE_TITLE, EMAIL),
    "openalex": f1_confirm.search_openalex(PROBE_TITLE, EMAIL,
                                           api_key=OPENALEX_API_KEY),
}
F1_REACHABLE_AFTER = f1_confirm.fully_answered(exit_hits)
F1_REPORTABLE = bool(F1_REACHABLE and F1_REACHABLE_AFTER and CONFIRM_409 == 0)

atomic_json(MEASURE_ROOT / "provider_probe_after.json", {
    "schema": "cre_provider_probe_v1", "when": "after_batch",
    "probe_title": PROBE_TITLE, "hits": exit_hits,
    "f1_reachable": F1_REACHABLE_AFTER,
    "f1_reachable_before": bool(F1_REACHABLE),
    "f1_reportable_this_batch": F1_REPORTABLE,
    "openalex_calls_by_leg": OA_DELTA, "confirm_leg_409": CONFIRM_409,
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
             "engine_commit": HEAD, "finished_at": utc_now()})
if "sync_to_drive" in globals():
    sync_to_drive("exit gate")

print("papers done   :", STATE["papers_done"], "| refs", STATE["references"])
print("F1            :", STATE["F1"])
print("F8            :", STATE["F8"])
print("F2 (computed, excluded):", STATE["F2_computed_excluded"])
print("spend         : $%.2f over %d model calls" % (SPEND.usd(), SPEND.calls))
print("openalex legs :", OA_DELTA["leg_totals"], "| total", OA_DELTA["total"],
      "| quota_exhausted", OA_409)
print("\nF1 REPORTABILITY GATE")
print("  reachable BEFORE batch :", bool(F1_REACHABLE))
print("  reachable AFTER  batch :", F1_REACHABLE_AFTER, exit_hits)
print("  confirm-leg 409s       :", CONFIRM_409)
print("  --> F1 IS %sREPORTABLE for this batch."
      % ("" if F1_REPORTABLE else "NOT "))
if not F1_REPORTABLE:
    print("      Record F1 as NOT ATTEMPTED for these papers, never as zero.")
    print("      F8 is unaffected: it never calls confirm().")
