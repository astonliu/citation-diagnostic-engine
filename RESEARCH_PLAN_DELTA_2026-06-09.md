# Research Plan Delta — Session of June 9, 2026
*Changes to fold into RESEARCH_PLAN (currently v2.2). Apply these as edits; everything not listed is unchanged. Bump to v2.3 on commit.*

## Scope changes
- **GENERATION mode cut from the submission.** Remove from October deliverables and from the dual-mode framing. Retain a Future Work paragraph noting the pipeline (Stages 2–4) extends to GENERATION without redesign. REPAIR is now the sole evaluated mode. (Prereg Amendment 1.)
- **Headline reframed.** Contribution = first biomedical pipeline combining fine-grained *semantic* failure diagnosis with *automated evidence-backed replacement*. Drop "fine-grained taxonomy" as a standalone novelty; drop "evidence-grounded transparency" as a novelty (now a design property). (Prereg Amendment 8.)

## Verifier changes (§4.4)
- **Three verifiers:** Claude Opus (pinned) + GPT-5 (pinned) + **Med-V1** (3B biomedical, arXiv 2603.05308). Three-way agreement reported as a robustness metric. Add a Med-V1 output parser into Stage 4 (~1 day). (Prereg Amendment 2.)

## Taxonomy changes
- **F5 fully operationalized** via the three-criterion supersession gate + F5/F8 boundary rule. Add the new F5 section to TAXONOMY.md (see TAXONOMY_F5_section.md). This unblocks the pre-pilot. (Prereg Amendment 3.)

## Evaluation changes (§6)
- **100-example human gold subset** as primary validity anchor; three-way LLM agreement secondary. Registered fallback ladder if advisor bandwidth is limited. Confirm Dr. Roberts availability. (Prereg Amendment 4.)
- **Dense-retrieval ablation** on 20–30 examples, outcome-driven reporting thresholds fixed. (Prereg Amendment 7.)
- **Abstract-only 70% threshold** sampling exercise; can run on independent PubMed sample now, before the dataset is complete. (Prereg Amendment 6.)
- Keep: latency (median + p95), natural-vs-synthetic gap reported as an explicit number, single-citation-prevalence corpus statistic. (Already required; now elevated to must-do.)

## Dataset changes (§4, §8)
- **Equal synthetic (~125/category), unequal natural (as-available, aggregated).** Natural stratum collected first and held out entirely from tuning. Per-category synthetic generation uses 3–4 heterogeneous methods (see collection spec). (Prereg Amendment 5.)

## Related work / baselines (§6.1)
- Add BibAgent/MisciteBench, CiteTracer, Med-V1, and the Sarol 2025 ASIS&T case study. Benchmark F1–F8 against BibAgent's 5-category scheme in addition to the existing 3-label (Sarol) and 4-label (SemanticCite) collapses. (Prereg Amendment 8.)

## Unchanged (guard against drift)
- Primary claim + paired-comparison test + n=1000 power.
- κ ≥ 0.60 registered (0.70 "good") — not raised.
- Pre-pilot gate: ~20 examples, two at-risk pairs (F3/F6 and F4/F6), κ ≥ 0.60. (F5/F8 removed per Amendment 1; F8 deterministic, excluded from κ; IAA over F3–F7.)
- 70/30 stratified split; deterministic F1/F2/F8 pre-classifier; two distinct Sarol comparisons.
